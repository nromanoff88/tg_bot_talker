import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroupMember, Message, SystemConfig
from app.redis_client import get_arq_pool
from app.services.memory_service import ensure_group_initialized

logger = logging.getLogger(__name__)


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
    """В экспорте Telegram Desktop from_id имеет вид 'user123456789'."""
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
        try:
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return datetime.now(timezone.utc)


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


async def import_telegram_desktop_json(
    session: AsyncSession,
    group_id: int,
    raw_bytes: bytes,
    trigger_analysis: bool = True,
) -> dict[str, int]:
    """
    Импортирует историю сообщений из файла result.json (Telegram Desktop Export) в указанную группу,
    создаёт изолированные профили участников и ставит фоновое изучение истории в очередь ARQ.
    """
    payload = json.loads(raw_bytes.decode("utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise ValueError("Некорректный формат файла: ожидается JSON-экспорт чата Telegram Desktop с полем 'messages'.")

    export_title = str(payload.get("name") or "").strip()
    group = await ensure_group_initialized(session, group_id, title=export_title or "Telegram Group")
    sys_cfg = await session.get(SystemConfig, 1)
    bot_tg_id = sys_cfg.bot_telegram_id if sys_cfg else None

    # Существующие telegram_message_id в этой группе для быстрой дедупликации
    existing_ids_rows = (
        await session.execute(select(Message.telegram_message_id).where(Message.group_id == group_id))
    ).scalars().all()
    existing_msg_ids = set(existing_ids_rows)

    # Существующие участники этой группы
    existing_members_rows = (
        await session.execute(select(GroupMember).where(GroupMember.group_id == group_id))
    ).scalars().all()
    members_by_tg_id: dict[int, GroupMember] = {m.telegram_user_id: m for m in existing_members_rows}

    discovered_members = 0
    imported_count = 0
    skipped_duplicates = 0
    new_messages_buffer: list[Message] = []

    for raw_msg in payload["messages"]:
        if not isinstance(raw_msg, dict) or raw_msg.get("type") != "message":
            continue

        tg_msg_id = raw_msg.get("id")
        if not isinstance(tg_msg_id, int):
            continue

        if tg_msg_id in existing_msg_ids:
            skipped_duplicates += 1
            continue

        text_content = _flatten_tg_text(raw_msg.get("text"))
        if not text_content:
            continue

        user_tg_id = _parse_from_user_id(raw_msg.get("from_id"))
        sender_name = str(raw_msg.get("from") or "Участник").strip()[:255]
        msg_dt = _parse_tg_datetime(raw_msg)
        is_from_bot = bool(bot_tg_id and user_tg_id == bot_tg_id)

        member_obj: Optional[GroupMember] = None
        if user_tg_id is not None and not is_from_bot:
            member_obj = members_by_tg_id.get(user_tg_id)
            if not member_obj:
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
                discovered_members += 1

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
        existing_msg_ids.add(tg_msg_id)
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
    if trigger_analysis and imported_count > 0:
        # Ставим фоновую задачу пакетного изучения всей импортированной истории
        queued_batches = max(1, (imported_count + 59) // 60)
        try:
            arq = await get_arq_pool()
            await arq.enqueue_job("process_all_pending_memory_job", group_id, max_batches=30)
        except Exception as exc:
            logger.warning("Failed to enqueue ARQ memory job after import: %s", exc)

    return {
        "imported_messages": imported_count,
        "skipped_duplicates": skipped_duplicates,
        "discovered_members": discovered_members,
        "queued_analysis_batches": queued_batches,
    }

