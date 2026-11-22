import html
import json
import logging
import re
import zlib
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroupMember, Message, SystemConfig
from app.redis_client import get_arq_pool
from app.services.memory_service import ensure_group_initialized

logger = logging.getLogger(__name__)

SYNTHETIC_TG_ID_BASE = 9_000_000_000_000


def make_synthetic_user_id(sender_name: str) -> int:
    """Генерирует стабильный синтетический ID для автора из HTML-экспорта (где нет числового user_id)."""
    norm = sender_name.strip().lower().encode("utf-8")
    return SYNTHETIC_TG_ID_BASE + (zlib.crc32(norm) & 0xFFFFFFFF)


def _flatten_tg_text(raw_text: Any) -> str:
    """Преобразует полиморфное поле text из экспорта Telegram Desktop (строка или массив сущностей) в текст."""
    if isinstance(raw_text, str):
        return raw_text.strip()
    if isinstance(raw_text, list):
        parts: list[str] = []
        for item in raw_text:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "".join(parts).strip()
    return ""


def _parse_from_user_id(from_id_raw: Any) -> Optional[int]:
    """В JSON-экспорте Telegram Desktop from_id имеет вид 'user123456789'."""
    if isinstance(from_id_raw, int):
        return from_id_raw
    if isinstance(from_id_raw, str) and from_id_raw.startswith("user"):
        digits = from_id_raw.removeprefix("user").strip()
        if digits.lstrip("-").isdigit():
            return int(digits)
    return None


def _parse_tg_datetime(msg_obj: dict[str, Any]) -> datetime:
    unixtime = msg_obj.get("date_unixtime")
    if unixtime and str(unixtime).isdigit():
        return datetime.fromtimestamp(int(unixtime), tz=timezone.utc)
    date_str = msg_obj.get("date")
    if isinstance(date_str, str):
        dt = _parse_html_date_title(date_str)
        if dt:
            return dt
    return datetime.now(timezone.utc)


def _parse_html_date_title(date_str: str) -> Optional[datetime]:
    """Парсит дату из атрибута title='15.11.2026 14:22:10 UTC+05:00' или ISO-строки."""
    s = (date_str or "").strip()
    if not s:
        return None

    m = re.search(
        r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})(?:\s*UTC([+-]\d{1,2})(?::?(\d{2}))?)?",
        s,
    )
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
        tz_h = m.group(7)
        tz_m = m.group(8)
        if tz_h is not None:
            offset_hours = int(tz_h)
            offset_mins = int(tz_m or 0)
            if offset_hours < 0:
                offset_mins = -offset_mins
            tz = timezone(timedelta(hours=offset_hours, minutes=offset_mins))
        else:
            tz = timezone.utc
        try:
            return datetime(year, month, day, hour, minute, second, tzinfo=tz).astimezone(timezone.utc)
        except ValueError:
            pass

    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _extract_reactions_dict(msg_obj: dict[str, Any]) -> dict[str, int]:
    reactions_out: dict[str, int] = {}
    raw_reactions = msg_obj.get("reactions")
    if isinstance(raw_reactions, list):
        for r in raw_reactions:
            if isinstance(r, dict) and r.get("emoji"):
                reactions_out[str(r["emoji"])] = int(r.get("count") or 1)
    elif isinstance(raw_reactions, dict):
        for r in raw_reactions.get("results") or []:
            if isinstance(r, dict) and r.get("emoji"):
                reactions_out[str(r["emoji"])] = int(r.get("count") or 1)
    return reactions_out


VOID_HTML_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class _TelegramHTMLParser(HTMLParser):
    """Парсер стандартного HTML-экспорта истории чата из Telegram Desktop (messages.html)."""

    def __init__(self, initial_last_sender: str = "Участник") -> None:
        super().__init__(convert_charrefs=True)
        self.chat_title: str = ""
        self.messages: list[dict[str, Any]] = []

        self._stack: list[tuple[str, set[str], dict[str, Optional[str]]]] = []
        self._in_page_header_text: bool = False

        self._cur_msg: Optional[dict[str, Any]] = None
        self._cur_msg_depth: int = 0
        self._last_sender_name: str = initial_last_sender or "Участник"

        self._cur_reaction_emoji: list[str] = []
        self._cur_reaction_count: list[str] = []
        self._cur_reaction_userpics: int = 0
        self._in_reaction_depth: int = 0

    def _has_class_in_stack(self, cls_name: str, min_depth: int = 0) -> bool:
        for idx in range(min_depth, len(self._stack)):
            if cls_name in self._stack[idx][1]:
                return True
        return False

    def _finalize_current_message(self) -> None:
        if self._cur_msg is None:
            return
        msg = self._cur_msg
        self._cur_msg = None
        self._cur_msg_depth = 0
        self._in_reaction_depth = 0

        sender = html.unescape("".join(msg["_from_parts"])).strip()
        if sender:
            self._last_sender_name = sender
        elif msg["is_joined"]:
            sender = self._last_sender_name
        else:
            sender = "Участник"

        main_text = html.unescape("".join(msg["_text_parts"])).strip()
        fwd_text = html.unescape("".join(msg["_fwd_parts"])).strip()
        if not main_text and fwd_text:
            main_text = f"[Переслано] {fwd_text}"

        if not main_text:
            return

        self.messages.append(
            {
                "type": "message",
                "id": msg["id"],
                "from": sender,
                "from_id": make_synthetic_user_id(sender),
                "date": msg["date"],
                "reply_to_message_id": msg["reply_to_message_id"],
                "text": main_text,
                "reactions": msg["reactions"],
            }
        )

    def close(self) -> None:
        super().close()
        self._finalize_current_message()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_low = tag.lower()
        attr_dict = dict(attrs)
        classes = set((attr_dict.get("class") or "").split())

        # Пустые HTML-теги (<br>, <img>, <hr>, <meta>...) не имеют закрывающего тега и не должны попадать в стек
        if tag_low in VOID_HTML_TAGS:
            if self._cur_msg is not None:
                if tag_low == "br" and self._has_class_in_stack("text", self._cur_msg_depth):
                    if self._has_class_in_stack("forwarded", self._cur_msg_depth):
                        self._cur_msg["_fwd_parts"].append("\n")
                    else:
                        self._cur_msg["_text_parts"].append("\n")
                if self._in_reaction_depth > 0 and "userpic" in classes:
                    self._cur_reaction_userpics += 1
            return

        # Заголовок чата в шапке экспорта
        if "text" in classes and "bold" in classes and self._has_class_in_stack("page_header"):
            if not self.chat_title:
                self._in_page_header_text = True

        # Начало карточки сообщения: <div class="message default clearfix ..." id="message12345">
        if (
            tag_low == "div"
            and "message" in classes
            and "default" in classes
            and "service" not in classes
            and not (self._cur_msg is not None and self._has_class_in_stack("forwarded", self._cur_msg_depth))
        ):
            raw_id = attr_dict.get("id") or ""
            m_id = re.match(r"^message(-?\d+)$", raw_id)
            if m_id and int(m_id.group(1)) > 0:
                if self._cur_msg is not None:
                    self._finalize_current_message()
                self._stack.append((tag_low, classes, attr_dict))
                self._cur_msg = {
                    "id": int(m_id.group(1)),
                    "is_joined": "joined" in classes,
                    "from": "",
                    "_from_parts": [],
                    "date": "",
                    "reply_to_message_id": None,
                    "_text_parts": [],
                    "_fwd_parts": [],
                    "reactions": {},
                }
                self._cur_msg_depth = len(self._stack)
                return

        self._stack.append((tag_low, classes, attr_dict))
        depth = len(self._stack)

        if self._cur_msg is None:
            return

        in_forwarded = self._has_class_in_stack("forwarded", self._cur_msg_depth)

        # Дата сообщения
        if "date" in classes and "details" in classes and not in_forwarded:
            title_val = attr_dict.get("title")
            if title_val and not self._cur_msg["date"]:
                self._cur_msg["date"] = title_val

        # Ссылка на сообщение, на которое отвечают (reply_to)
        if tag_low == "a" and not in_forwarded and self._has_class_in_stack("reply_to", self._cur_msg_depth):
            href = attr_dict.get("href") or ""
            onclick = attr_dict.get("onclick") or ""
            m_rep = re.search(r"go_to_message(\d+)", href) or re.search(r"GoToMessage\((\d+)\)", onclick)
            if m_rep:
                self._cur_msg["reply_to_message_id"] = int(m_rep.group(1))

        # Реакция на сообщении
        if "reaction" in classes and "reactions" not in classes and self._in_reaction_depth == 0:
            self._in_reaction_depth = depth
            self._cur_reaction_emoji = []
            self._cur_reaction_count = []
            self._cur_reaction_userpics = 0
        elif self._in_reaction_depth > 0 and "userpic" in classes:
            self._cur_reaction_userpics += 1

    def handle_data(self, data: str) -> None:
        if self._in_page_header_text:
            self.chat_title += data

        if self._cur_msg is None:
            return

        in_forwarded = self._has_class_in_stack("forwarded", self._cur_msg_depth)

        # Внутри блока реакции
        if self._in_reaction_depth > 0:
            if self._has_class_in_stack("emoji", self._in_reaction_depth):
                self._cur_reaction_emoji.append(data)
            elif self._has_class_in_stack("count", self._in_reaction_depth):
                self._cur_reaction_count.append(data)
            return

        # Имя автора (исключая вложенный span.date, если он внутри from_name)
        if (
            not in_forwarded
            and self._has_class_in_stack("from_name", self._cur_msg_depth)
            and not self._has_class_in_stack("date", self._cur_msg_depth)
        ):
            self._cur_msg["_from_parts"].append(data)
            return

        # Основной текст сообщения
        if self._has_class_in_stack("text", self._cur_msg_depth):
            if in_forwarded:
                self._cur_msg["_fwd_parts"].append(data)
            else:
                self._cur_msg["_text_parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag_low = tag.lower()
        if tag_low in VOID_HTML_TAGS or not self._stack:
            return

        # Ищем ближайший открытый тег с тем же именем (защита от незакрытых тегов в разметке)
        match_idx: Optional[int] = None
        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx][0] == tag_low:
                match_idx = idx
                break
        if match_idx is None:
            return

        while len(self._stack) > match_idx:
            depth = len(self._stack)
            _, classes, _ = self._stack.pop()

            if self._in_page_header_text and "text" in classes and "bold" in classes:
                self._in_page_header_text = False
                self.chat_title = self.chat_title.strip()

            if self._cur_msg is None:
                continue

            # Закрытие одной реакции
            if self._in_reaction_depth > 0 and depth == self._in_reaction_depth:
                emoji_str = "".join(self._cur_reaction_emoji).strip()
                count_str = "".join(self._cur_reaction_count).strip()
                if emoji_str:
                    cnt = int(count_str) if count_str.isdigit() else max(1, self._cur_reaction_userpics)
                    self._cur_msg["reactions"][emoji_str] = cnt
                self._in_reaction_depth = 0

            # Закрытие карточки сообщения
            if depth == self._cur_msg_depth:
                self._finalize_current_message()


def _parse_export_file_bytes(
    raw_bytes: bytes,
    initial_last_sender: str = "Участник",
) -> tuple[str, list[dict[str, Any]], str]:
    """Автоматически определяет формат файла (JSON или HTML из Telegram Desktop) и возвращает (title, messages, last_sender)."""
    text = raw_bytes.decode("utf-8-sig", errors="replace").lstrip()
    if not text:
        raise ValueError("Загруженный файл пуст.")

    # 1. Если файл начинается с '<' или содержит HTML-разметку Telegram Desktop
    if text.startswith("<") or "<div class=\"message" in text[:4000]:
        parser = _TelegramHTMLParser(initial_last_sender=initial_last_sender)
        parser.feed(text)
        parser.close()
        if not parser.messages and "page_header" not in text and "history" not in text:
            raise ValueError("В HTML-файле не найдено сообщений формата экспорта Telegram Desktop.")
        return parser.chat_title, parser.messages, parser._last_sender_name

    # 2. Иначе парсим как JSON (result.json)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Не удалось распознать файл как HTML или JSON экспорт Telegram Desktop ({exc})."
        ) from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise ValueError("Некорректный структура JSON: ожидается экспорт чата Telegram Desktop с полем 'messages'.")

    return str(payload.get("name") or "").strip(), payload["messages"], initial_last_sender


async def import_telegram_desktop_files(
    session: AsyncSession,
    group_id: int,
    files_bytes: list[bytes],
    trigger_analysis: bool = True,
) -> dict[str, int]:
    """
    Импортирует историю сообщений из одного или нескольких файлов экспорта Telegram Desktop
    (поддерживаются и HTML-файлы messages.html / messages2.html, и JSON result.json).
    """
    all_raw_messages: list[dict[str, Any]] = []
    export_title = ""
    last_sender = "Участник"

    for raw_bytes in files_bytes:
        title, msgs, last_sender = _parse_export_file_bytes(raw_bytes, initial_last_sender=last_sender)
        if title and not export_title:
            export_title = title
        all_raw_messages.extend(msgs)

    # Сортируем по id сообщения, чтобы порядок был строго хронологическим даже при загрузке пачки файлов
    all_raw_messages.sort(key=lambda m: int(m.get("id") or 0) if isinstance(m, dict) else 0)

    group = await ensure_group_initialized(session, group_id, title=export_title or "Telegram Group")
    sys_cfg = await session.get(SystemConfig, 1)
    bot_tg_id = sys_cfg.bot_telegram_id if sys_cfg else None
    bot_display = (sys_cfg.bot_display_name or "").strip().lower() if sys_cfg else ""
    bot_uname = (sys_cfg.bot_username or "").strip().lower() if sys_cfg else ""

    existing_msgs_rows = (
        await session.execute(select(Message).where(Message.group_id == group_id))
    ).scalars().all()
    existing_msgs_by_tg_id: dict[int, Message] = {m.telegram_message_id: m for m in existing_msgs_rows}

    existing_members_rows = (
        await session.execute(select(GroupMember).where(GroupMember.group_id == group_id))
    ).scalars().all()
    members_by_tg_id: dict[int, GroupMember] = {m.telegram_user_id: m for m in existing_members_rows}
    members_by_name: dict[str, GroupMember] = {
        m.display_name.strip().lower(): m for m in existing_members_rows if m.display_name
    }

    discovered_members = 0
    imported_count = 0
    repaired_count = 0
    skipped_duplicates = 0
    new_messages_buffer: list[Message] = []

    for raw_msg in all_raw_messages:
        if not isinstance(raw_msg, dict) or raw_msg.get("type") != "message":
            continue

        tg_msg_id = raw_msg.get("id")
        if not isinstance(tg_msg_id, int):
            continue

        text_content = _flatten_tg_text(raw_msg.get("text"))
        if not text_content:
            continue

        existing_msg = existing_msgs_by_tg_id.get(tg_msg_id)
        if existing_msg is not None:
            # Если ранее импортированное сообщение попало в БД искажённым/обрезанным из-за старого парсера — чиним текст и переанализируем
            updated = False
            if existing_msg.is_imported and text_content != (existing_msg.text_content or ""):
                existing_msg.text_content = text_content
                updated = True
            new_sender = str(raw_msg.get("from") or "").strip()[:255]
            if (
                existing_msg.is_imported
                and new_sender
                and new_sender != "Участник"
                and existing_msg.sender_name in ("Участник", "", None)
            ):
                existing_msg.sender_name = new_sender
                updated = True
            if updated:
                existing_msg.is_processed_for_memory = False
                repaired_count += 1
            else:
                skipped_duplicates += 1
            continue

        user_tg_id = _parse_from_user_id(raw_msg.get("from_id"))
        sender_name = str(raw_msg.get("from") or "Участник").strip()[:255]
        sender_norm = sender_name.lower()
        msg_dt = _parse_tg_datetime(raw_msg)

        is_from_bot = bool(
            (bot_tg_id and user_tg_id == bot_tg_id)
            or (bot_display and sender_norm == bot_display)
            or (bot_uname and sender_norm == bot_uname)
        )

        member_obj: Optional[GroupMember] = None
        if not is_from_bot:
            if user_tg_id is not None and user_tg_id in members_by_tg_id:
                member_obj = members_by_tg_id[user_tg_id]
            elif sender_norm in members_by_name:
                member_obj = members_by_name[sender_norm]
            elif user_tg_id is not None:
                member_obj = GroupMember(
                    group_id=group_id,
                    telegram_user_id=user_tg_id,
                    display_name=sender_name,
                    message_count=0,
                    last_seen_at=msg_dt,
                )
                session.add(member_obj)
                await session.flush()
                members_by_tg_id[user_tg_id] = member_obj
                members_by_name[sender_norm] = member_obj
                discovered_members += 1

            if member_obj:
                member_obj.message_count = (member_obj.message_count or 0) + 1
                if not member_obj.last_seen_at or msg_dt > member_obj.last_seen_at:
                    member_obj.last_seen_at = msg_dt

        reply_to = raw_msg.get("reply_to_message_id")
        reply_to_int = int(reply_to) if isinstance(reply_to, int) else None

        msg_entity = Message(
            group_id=group_id,
            telegram_message_id=tg_msg_id,
            member_id=member_obj.id if member_obj else None,
            sender_name=sender_name,
            sender_username=member_obj.username if member_obj else None,
            is_from_bot=is_from_bot,
            reply_to_tg_msg_id=reply_to_int,
            text_content=text_content,
            reactions=_extract_reactions_dict(raw_msg),
            is_imported=True,
            is_processed_for_memory=False,
            created_at=msg_dt,
        )
        new_messages_buffer.append(msg_entity)
        existing_msgs_by_tg_id[tg_msg_id] = msg_entity
        imported_count += 1

        if not group.last_message_at or msg_dt > group.last_message_at:
            group.last_message_at = msg_dt

        if len(new_messages_buffer) >= 500:
            session.add_all(new_messages_buffer)
            await session.flush()
            new_messages_buffer.clear()

    if new_messages_buffer:
        session.add_all(new_messages_buffer)
        await session.flush()

    unanalyzed = (
        await session.execute(
            select(func.count(Message.id)).where(
                Message.group_id == group_id, Message.is_processed_for_memory.is_(False)
            )
        )
    ).scalar_one()
    group.unanalyzed_msg_count = int(unanalyzed)
    await session.commit()

    queued_batches = 0
    total_changed = imported_count + repaired_count
    if trigger_analysis and total_changed > 0:
        queued_batches = max(1, (total_changed + 34) // 35)
        try:
            arq = await get_arq_pool()
            await arq.enqueue_job("process_all_pending_memory_job", group_id, max_batches=25)
        except Exception as exc:
            logger.warning("Failed to enqueue ARQ memory job after import: %s", exc)

    return {
        "imported_messages": total_changed,
        "skipped_duplicates": skipped_duplicates,
        "discovered_members": discovered_members,
        "queued_analysis_batches": queued_batches,
    }


async def import_telegram_desktop_json(
    session: AsyncSession,
    group_id: int,
    raw_bytes: bytes,
    trigger_analysis: bool = True,
) -> dict[str, int]:
    """Совместимая обёртка для одиночного файла (HTML или JSON)."""
    return await import_telegram_desktop_files(
        session=session,
        group_id=group_id,
        files_bytes=[raw_bytes],
        trigger_analysis=trigger_analysis,
    )
