import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import DecisionLog, Group, GroupMember, GroupSettings, Message, SystemConfig
from app.redis_client import publish_group_event
from app.services.llm_service import LLMUsage, complete_json, complete_text
from app.services.memory_service import (
    build_persona_generation_context,
    ensure_group_initialized,
    format_current_datetime_context,
    format_message_local_ts,
    resolve_effective_persona,
)
from app.services.prompt_service import render_prompt

logger = logging.getLogger(__name__)


def is_within_quiet_hours(tz_name: str, start_hhmm: str, end_hhmm: str, now_utc: Optional[datetime] = None) -> bool:
    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        tz = ZoneInfo(tz_name or "Europe/Moscow")
    except Exception:
        tz = ZoneInfo("Europe/Moscow")

    local_now = now_utc.astimezone(tz)
    cur_hm = local_now.strftime("%H:%M")

    start = (start_hhmm or "23:30").strip()[:5]
    end = (end_hhmm or "09:30").strip()[:5]
    if start == end:
        return False
    if start < end:
        return start <= cur_hm < end
    return cur_hm >= start or cur_hm < end


def humanize_bot_text(text: str, persona_name: str = "") -> str:
    """Убирает типичные артефакты LLM (кавычки вокруг всей реплики, префикс имени, шаблонные вступления)."""
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] in ('"', "«") and cleaned[-1] in ('"', "»"):
        cleaned = cleaned[1:-1].strip()

    if persona_name:
        prefix_pattern = rf"^{re.escape(persona_name)}\s*:\s*"
        cleaned = re.sub(prefix_pattern, "", cleaned, flags=re.IGNORECASE).strip()

    # Убираем искусственные ассистентские зачины, если модель всё-таки сорвалась
    cliches = [
        r"^конечно[,!\.]?\s+я\s+могу\s+помочь[^\.!\n]*[\.!\n]\s*",
        r"^рада?\s+помочь[,!\.]?\s*",
        r"^как\s+искусственный\s+интеллект[^\.!\n]*[\.!\n]\s*",
    ]
    for pat in cliches:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()

    return cleaned


@dataclass
class BotDecisionResult:
    action: Literal["ignore", "react", "reply", "message", "poll"]
    target_tg_msg_id: Optional[int] = None
    emoji: Optional[str] = None
    text: Optional[str] = None
    poll_data: Optional[dict[str, Any]] = None
    simulate_typing: bool = True
    reasoning: str = ""


def sanitize_poll_params(raw: dict[str, Any]) -> dict[str, Any]:
    """Нормализует параметры опроса под ограничения Telegram Bot API sendPoll."""
    question = str(raw.get("question") or "").strip()[:300]
    if not question:
        question = "Голосование в чате"

    raw_opts = raw.get("options") or []
    options: list[str] = []
    seen: set[str] = set()
    for item in raw_opts:
        opt = str(item or "").strip()[:100]
        if opt and opt.lower() not in seen:
            options.append(opt)
            seen.add(opt.lower())
        if len(options) >= 10:
            break

    while len(options) < 2:
        fallback_opt = "Да" if len(options) == 0 else "Нет"
        if fallback_opt.lower() in seen:
            fallback_opt = f"Вариант {len(options) + 1}"
        options.append(fallback_opt)
        seen.add(fallback_opt.lower())

    poll_type = str(raw.get("type") or "regular").strip().lower()
    if poll_type not in ("regular", "quiz"):
        poll_type = "regular"

    is_anonymous = bool(raw.get("is_anonymous", False))
    is_closed = bool(raw.get("is_closed", False))
    protect_content = bool(raw.get("protect_content", False))

    raw_open_period = raw.get("open_period")
    open_period: Optional[int] = None
    if isinstance(raw_open_period, (int, float)) and not isinstance(raw_open_period, bool):
        val = int(raw_open_period)
        if val >= 5:
            open_period = min(600, max(5, val))

    if poll_type == "quiz":
        allows_multiple = False
        raw_correct = raw.get("correct_option_id")
        if isinstance(raw_correct, (int, str)) and str(raw_correct).lstrip("-").isdigit():
            idx = int(raw_correct)
            correct_option_id: Optional[int] = idx if 0 <= idx < len(options) else 0
        else:
            correct_option_id = 0
        raw_expl = str(raw.get("explanation") or "").strip()[:200]
        explanation: Optional[str] = raw_expl or None
    else:
        allows_multiple = bool(raw.get("allows_multiple_answers", False))
        correct_option_id = None
        explanation = None

    reply_to = raw.get("reply_to_tg_msg_id")
    reply_to_int = int(reply_to) if isinstance(reply_to, int) and not isinstance(reply_to, bool) else None

    return {
        "question": question,
        "options": options,
        "is_anonymous": is_anonymous,
        "type": poll_type,
        "allows_multiple_answers": allows_multiple,
        "correct_option_id": correct_option_id,
        "explanation": explanation,
        "open_period": open_period,
        "is_closed": is_closed,
        "protect_content": protect_content,
        "reply_to_tg_msg_id": reply_to_int,
    }


async def generate_poll_via_llm(
    session: AsyncSession,
    group_id: int,
    recent_msgs: Optional[list[Message]] = None,
    topic_hint: str = "",
    preferred_type: Optional[str] = None,
    preferred_anonymous: Optional[bool] = None,
    preferred_multiple: Optional[bool] = None,
) -> tuple[dict[str, Any], dict[str, Any], LLMUsage]:
    """Генерирует параметры Telegram-опроса в стиле Персоны группы и её текущего контекста."""
    group = await ensure_group_initialized(session, group_id)
    persona = await resolve_effective_persona(session, group)

    if recent_msgs is None:
        recent_stmt = (
            select(Message)
            .where(Message.group_id == group_id)
            .order_by(Message.created_at.desc())
            .limit(20)
        )
        recent_msgs = list(reversed((await session.execute(recent_stmt)).scalars().all()))

    sys_prompt, context_meta = await build_persona_generation_context(
        session=session,
        group=group,
        recent_messages=recent_msgs,
        memory_query=topic_hint or (recent_msgs[-1].text_content[:200] if recent_msgs else ""),
    )

    tz_name = group.settings.timezone if group.settings else None
    chat_lines = []
    for m in recent_msgs[-20:]:
        who = f"{persona['display_name']} (ТЫ)" if m.is_from_bot else m.sender_name
        ts = format_message_local_ts(m.created_at, tz_name)
        chat_lines.append(f"#{m.telegram_message_id} [{ts}] {who}: {m.text_content}")

    hints_lines = []
    if topic_hint:
        hints_lines.append(f"- Пожелание/повод к опросу: {topic_hint}")
    if preferred_type in ("regular", "quiz"):
        hints_lines.append(f"- Желаемый тип: {preferred_type}")
    if preferred_anonymous is not None:
        hints_lines.append(f"- Анонимность: {preferred_anonymous}")
    if preferred_multiple is not None:
        hints_lines.append(f"- Множественный выбор: {preferred_multiple}")

    user_prompt = (
        f"ПОСЛЕДНИЕ СООБЩЕНИЯ В ЧАТЕ:\n{chr(10).join(chat_lines) or 'Пока нет сообщений.'}\n\n"
        + (f"УКАЗАНИЯ ПО ПАРАМЕТРАМ:\n{chr(10).join(hints_lines)}\n\n" if hints_lines else "")
        + """ЗАДАЧА: Придумай живой, естественный опрос или викторину в Telegram в своём характере (без официоза и канцелярита).
Если участники в чате прямо указали параметры (например, «анонимный», «несколько вариантов», «викторина», «таймер 60 сек»), обязательно учти их.

Верни СТРОГО JSON следующей структуры:
{
  "question": "Вопрос опроса (до 300 символов)",
  "options": ["Вариант 1", "Вариант 2", "Вариант 3"],
  "type": "regular | quiz",
  "is_anonymous": false,
  "allows_multiple_answers": false,
  "correct_option_id": 0,
  "explanation": "Короткое пояснение правильного ответа только если type=quiz (до 200 символов), иначе null",
  "open_period": null
}
Ограничения Telegram API: от 2 до 10 вариантов ответа (каждый до 100 символов). Для quiz allows_multiple_answers всегда false, а correct_option_id — индекс правильного ответа (0..N-1). Для open_period укажи число 5..600 только если нужен автотаймер, иначе null."""
    )

    raw_poll, usage = await complete_json(
        session=session,
        tier="smart",
        system_prompt=sys_prompt,
        user_prompt=user_prompt,
        temperature=0.75,
        max_tokens=1600,
    )
    if preferred_type in ("regular", "quiz"):
        raw_poll["type"] = preferred_type
    if preferred_anonymous is not None:
        raw_poll["is_anonymous"] = preferred_anonymous
    if preferred_multiple is not None and raw_poll.get("type") != "quiz":
        raw_poll["allows_multiple_answers"] = preferred_multiple

    sanitized = sanitize_poll_params(raw_poll)
    context_meta["llm_prompts"] = {
        "smart_system_prompt": sys_prompt,
        "smart_user_prompt": user_prompt,
    }
    return sanitized, context_meta, usage


def _build_persona_name_pattern(name: str) -> str:
    """Строит regex для имени Персоны с учётом русских падежей и окончаний."""
    clean = name.strip().lower()
    if len(clean) >= 4 and clean[-1] in "аяоеиыуэю":
        stem = clean[:-1]
        return rf"\b{re.escape(stem)}[а-яё]{{0,3}}\b"
    return rf"\b{re.escape(clean)}[а-яё]{{0,3}}\b"


async def _run_router_and_plan(
    session: AsyncSession,
    group: Group,
    g_settings: GroupSettings,
    persona: dict[str, Any],
    recent_msgs: list[Message],
    trigger_msgs: list[Message],
    trigger_set: set[int],
    effective_chattiness: float,
    router_direct_ping: bool,
    now_str: str,
) -> tuple[str, int, str, dict[str, Any], dict[str, str], LLMUsage]:
    """Собирает контекст для Fast LLM Router и возвращает (action, target_msg_id, reasoning, router_json, router_prompts, router_usage)."""
    author_ids = {m.member_id for m in recent_msgs[-15:] if m.member_id}
    brief_authors = []
    if author_ids:
        m_rows = (
            await session.execute(select(GroupMember).where(GroupMember.id.in_(author_ids)))
        ).scalars().all()
        for mr in m_rows:
            brief_authors.append(
                f"- {mr.display_name}: {mr.character_summary[:140] or 'участник чата'}"
                + (f" (отношение: {mr.bot_attitude[:80]})" if mr.bot_attitude else "")
            )

    chat_lines = []
    for m in recent_msgs[-22:]:
        who = f"{persona['display_name']} [ТЫ]" if m.is_from_bot else m.sender_name
        r_tag = f" (reply->#{m.reply_to_tg_msg_id})" if m.reply_to_tg_msg_id else ""
        new_tag = " [НОВОЕ]" if m.telegram_message_id in trigger_set else ""
        ts = format_message_local_ts(m.created_at, g_settings.timezone)
        chat_lines.append(f"#{m.telegram_message_id}{new_tag} [{ts}] {who}{r_tag}: {m.text_content}")

    router_sys_prompt = await render_prompt(
        session,
        "router_system_prompt",
        {"group_title": group.title, "current_datetime": now_str},
    )
    router_user_prompt = await render_prompt(
        session,
        "router_user_prompt",
        {
            "group_title": group.title,
            "current_datetime": now_str,
            "persona": persona,
            "chattiness_level": effective_chattiness,
            "is_direct_ping": router_direct_ping,
            "reactions_enabled": g_settings.reactions_enabled,
            "allowed_emojis": g_settings.allowed_emojis,
            "brief_authors": "\n".join(brief_authors) or "нет данных",
            "chat_messages": "\n".join(chat_lines),
        },
    )

    router_json, router_usage = await complete_json(
        session=session,
        tier="fast",
        system_prompt=router_sys_prompt,
        user_prompt=router_user_prompt,
        temperature=0.35,
        max_tokens=450,
    )

    raw_action = str(router_json.get("action") or "ignore").strip().lower()
    if raw_action not in ("ignore", "react", "reply", "message", "poll"):
        raw_action = "ignore"

    reasoning = str(router_json.get("reasoning") or "Решение принято классификатором.").strip()
    target_id_raw = router_json.get("target_tg_msg_id")
    valid_msg_ids = {m.telegram_message_id for m in recent_msgs}
    target_msg_id = (
        int(target_id_raw)
        if isinstance(target_id_raw, (int, str)) and str(target_id_raw).lstrip("-").isdigit() and int(target_id_raw) in valid_msg_ids
        else trigger_msgs[-1].telegram_message_id
    )

    if raw_action == "react" and not g_settings.reactions_enabled:
        raw_action = "ignore"
        reasoning += " (Реакции отключены в настройках группы)."

    router_prompts = {
        "router_system_prompt": router_sys_prompt,
        "router_user_prompt": router_user_prompt,
    }
    return raw_action, target_msg_id, reasoning, router_json, router_prompts, router_usage


async def _generate_reply_text(
    session: AsyncSession,
    group: Group,
    g_settings: GroupSettings,
    persona: dict[str, Any],
    recent_msgs: list[Message],
    trigger_msgs: list[Message],
    raw_action: str,
    target_msg_id: int,
    reasoning: str,
    router_json: dict[str, Any],
    router_prompts: dict[str, str],
    is_direct_ping_meta: bool,
    now_str: str,
) -> tuple[str, dict[str, Any], LLMUsage]:
    """Строит 3-слойный контекст памяти и генерирует текстовый ответ Smart LLM."""
    need_mem = bool(router_json.get("need_memory_search"))
    mem_query = str(router_json.get("memory_query") or "").strip() if need_mem else ""
    if not mem_query and len(trigger_msgs[-1].text_content) > 20:
        mem_query = trigger_msgs[-1].text_content[:200]

    sys_prompt, context_meta = await build_persona_generation_context(
        session=session,
        group=group,
        recent_messages=recent_msgs,
        memory_query=mem_query,
    )
    context_meta["is_direct_ping"] = is_direct_ping_meta
    context_meta["router_action"] = raw_action

    full_chat_lines = []
    for m in recent_msgs:
        who = f"{persona['display_name']} (ТЫ)" if m.is_from_bot else m.sender_name
        r_tag = f" [ответ на #{m.reply_to_tg_msg_id}]" if m.reply_to_tg_msg_id else ""
        ts = format_message_local_ts(m.created_at, g_settings.timezone)
        full_chat_lines.append(f"#{m.telegram_message_id} [{ts}] {who}{r_tag}: {m.text_content}")

    reply_instruction = (
        f"Напиши естественный живой ответ реплаем на сообщение #{target_msg_id}."
        if raw_action == "reply"
        else "Напиши естественную реплику в общий разговор чата."
    )

    gen_user_prompt = await render_prompt(
        session,
        "reply_user_prompt",
        {
            "chat_history": "\n".join(full_chat_lines),
            "reasoning": reasoning,
            "reply_instruction": reply_instruction,
            "action": raw_action,
            "target_msg_id": target_msg_id,
            "current_datetime": now_str,
        },
    )
    context_meta["llm_prompts"] = {
        **router_prompts,
        "smart_system_prompt": sys_prompt,
        "smart_user_prompt": gen_user_prompt,
    }

    reply_text, smart_usage = await complete_text(
        session=session,
        tier="smart",
        system_prompt=sys_prompt,
        user_prompt=gen_user_prompt,
        temperature=0.85,
        max_tokens=500,
    )
    reply_text = humanize_bot_text(reply_text, persona.get("display_name", ""))
    return reply_text, context_meta, smart_usage


async def build_ephemeral_messages(
    session: AsyncSession,
    group_id: int,
    sandbox_messages: list[dict[str, Any]],
    persona_name: str,
    start_msg_id: int = 100001,
    now_utc: Optional[datetime] = None,
) -> list[Message]:
    """Создаёт временные объекты Message для песочницы без добавления их в SQLAlchemy session."""
    if not sandbox_messages:
        return []

    now_utc = now_utc or datetime.now(timezone.utc)
    member_uuids: set[uuid.UUID] = set()
    for item in sandbox_messages:
        raw_mid = item.get("member_id")
        if isinstance(raw_mid, uuid.UUID):
            member_uuids.add(raw_mid)
        elif isinstance(raw_mid, str) and raw_mid.strip():
            try:
                member_uuids.add(uuid.UUID(raw_mid.strip()))
            except ValueError:
                pass

    members_by_id: dict[uuid.UUID, GroupMember] = {}
    if member_uuids:
        rows = (
            await session.execute(
                select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.id.in_(member_uuids))
            )
        ).scalars().all()
        members_by_id = {m.id: m for m in rows}

    ephemeral: list[Message] = []
    total = len(sandbox_messages)
    for idx, item in enumerate(sandbox_messages):
        role = item.get("role", "user")
        is_from_bot = role == "bot"
        member_obj: Optional[GroupMember] = None
        if not is_from_bot and item.get("member_id"):
            try:
                mid = item["member_id"] if isinstance(item["member_id"], uuid.UUID) else uuid.UUID(str(item["member_id"]))
                member_obj = members_by_id.get(mid)
            except ValueError:
                member_obj = None

        if is_from_bot:
            sender_name = persona_name or "Бот"
            sender_username = None
        elif member_obj is not None:
            sender_name = member_obj.display_name
            sender_username = member_obj.username
        else:
            sender_name = (str(item.get("sender_name") or "").strip() or "Админ")[:255]
            sender_username = None

        raw_dt = item.get("created_at")
        if isinstance(raw_dt, datetime):
            msg_dt = raw_dt if raw_dt.tzinfo is not None else raw_dt.replace(tzinfo=timezone.utc)
        else:
            msg_dt = now_utc - timedelta(seconds=(total - idx))

        ephemeral.append(
            Message(
                group_id=group_id,
                telegram_message_id=start_msg_id + idx,
                member_id=member_obj.id if member_obj else None,
                sender_name=sender_name,
                sender_username=sender_username,
                is_from_bot=is_from_bot,
                reply_to_tg_msg_id=None,
                text_content=str(item.get("text") or "").strip(),
                reactions={},
                is_imported=False,
                is_processed_for_memory=True,
                created_at=msg_dt,
            )
        )
    return ephemeral


async def evaluate_group_batch_and_decide(
    session: AsyncSession,
    group_id: int,
    trigger_tg_msg_ids: list[int],
) -> BotDecisionResult:
    """
    Полный цикл принятия решения после окончания паузы Debounce:
    Hard Gates -> Fast LLM Router -> (опционально) pgvector + Досье + Smart LLM.
    """
    group = await ensure_group_initialized(session, group_id)
    if not group.is_active:
        return BotDecisionResult(action="ignore", reasoning="Бот выключен в настройках этой группы.")

    g_settings = group.settings
    sys_cfg = await session.get(SystemConfig, 1)
    persona = await resolve_effective_persona(session, group)

    bot_username = (sys_cfg.bot_username or "").lower().lstrip("@") if sys_cfg else ""
    persona_name_lower = (persona.get("display_name") or "").strip().lower()
    persona_name_pattern = _build_persona_name_pattern(persona_name_lower) if len(persona_name_lower) >= 3 else None

    # Загружаем окно последних сообщений
    window_size = max(10, g_settings.context_window_messages)
    recent_stmt = (
        select(Message)
        .where(Message.group_id == group_id)
        .order_by(Message.created_at.desc())
        .limit(window_size)
    )
    recent_msgs = list(reversed((await session.execute(recent_stmt)).scalars().all()))
    if not recent_msgs:
        return BotDecisionResult(action="ignore", reasoning="Нет сообщений в окне контекста.")

    # Если последнее сообщение — от самого бота, не отвечаем сами себе
    if recent_msgs[-1].is_from_bot:
        return BotDecisionResult(action="ignore", reasoning="Последнее сообщение в чате уже принадлежит боту.")

    bot_tg_msg_ids = {m.telegram_message_id for m in recent_msgs if m.is_from_bot}
    trigger_set = set(trigger_tg_msg_ids)
    trigger_msgs = [m for m in recent_msgs if m.telegram_message_id in trigger_set] or [recent_msgs[-1]]

    # Проверяем прямое обращение к боту (реплай боту, @username или обращение по имени персоны)
    is_direct_ping = False
    for tm in trigger_msgs:
        txt_low = tm.text_content.lower()
        if tm.reply_to_tg_msg_id and tm.reply_to_tg_msg_id in bot_tg_msg_ids:
            is_direct_ping = True
            break
        if bot_username and f"@{bot_username}" in txt_low:
            is_direct_ping = True
            break
        if persona_name_pattern and re.search(persona_name_pattern, txt_low):
            is_direct_ping = True
            break

    now_utc = datetime.now(timezone.utc)

    # Hard Gate 1: часовой лимит сообщений бота
    hour_ago = now_utc - timedelta(hours=1)
    bot_msgs_last_hour = (
        await session.execute(
            select(func.count(Message.id)).where(
                Message.group_id == group_id,
                Message.is_from_bot.is_(True),
                Message.created_at >= hour_ago,
            )
        )
    ).scalar_one()

    if bot_msgs_last_hour >= g_settings.max_msgs_per_hour:
        reason = f"Сработал лимит активности: {bot_msgs_last_hour}/{g_settings.max_msgs_per_hour} сообщений бота за час."
        await _save_decision_log(
            session, group_id, trigger_tg_msg_ids, "ignore", reason, {}, {"gate": "max_msgs_per_hour"}, LLMUsage()
        )
        return BotDecisionResult(action="ignore", reasoning=reason)

    # Hard Gate 2: кулдаун для не-адресных сообщений
    if not is_direct_ping and group.last_bot_message_at:
        elapsed = (now_utc - group.last_bot_message_at).total_seconds()
        if elapsed < g_settings.cooldown_seconds:
            reason = (
                f"Кулдаун после прошлой реплики бота ({int(elapsed)}с < {g_settings.cooldown_seconds}с), "
                f"прямого обращения к боту не было."
            )
            await _save_decision_log(
                session, group_id, trigger_tg_msg_ids, "ignore", reason, {}, {"gate": "cooldown"}, LLMUsage()
            )
            return BotDecisionResult(action="ignore", reasoning=reason)

    # В тихие часы снижаем расчётную общительность для роутера; окончательное решение о вмешательстве принимает сама модель
    effective_chattiness = g_settings.chattiness_level
    if (
        not is_direct_ping
        and g_settings.quiet_hours_enabled
        and is_within_quiet_hours(g_settings.timezone, g_settings.quiet_hours_start, g_settings.quiet_hours_end, now_utc)
    ):
        effective_chattiness = round(effective_chattiness * 0.35, 2)

    # Собираем контекст и спрашиваем Fast LLM Роутер
    now_str = format_current_datetime_context(g_settings.timezone, now_utc)
    raw_action, target_msg_id, reasoning, router_json, router_prompts, router_usage = await _run_router_and_plan(
        session=session,
        group=group,
        g_settings=g_settings,
        persona=persona,
        recent_msgs=recent_msgs,
        trigger_msgs=trigger_msgs,
        trigger_set=trigger_set,
        effective_chattiness=effective_chattiness,
        router_direct_ping=is_direct_ping and g_settings.reply_on_mention,
        now_str=now_str,
    )

    # Ветка 1: Промолчать
    if raw_action == "ignore":
        await _save_decision_log(
            session,
            group_id,
            trigger_tg_msg_ids,
            "ignore",
            reasoning,
            {},
            {"is_direct_ping": is_direct_ping, "chattiness": effective_chattiness, "llm_prompts": router_prompts},
            router_usage,
        )
        return BotDecisionResult(action="ignore", reasoning=reasoning)

    # Ветка 2: Поставить реакцию эмодзи
    if raw_action == "react":
        allowed = g_settings.allowed_emojis or ["👍"]
        chosen_emoji = str(router_json.get("emoji") or "").strip()
        if chosen_emoji not in allowed:
            chosen_emoji = allowed[0]

        await _save_decision_log(
            session,
            group_id,
            trigger_tg_msg_ids,
            "react",
            reasoning,
            {"emoji": chosen_emoji, "target_tg_msg_id": target_msg_id},
            {"is_direct_ping": is_direct_ping, "llm_prompts": router_prompts},
            router_usage,
        )
        return BotDecisionResult(
            action="react",
            target_tg_msg_id=target_msg_id,
            emoji=chosen_emoji,
            reasoning=reasoning,
        )

    # Ветка 2.5: Создать опрос или викторину в группе
    if raw_action == "poll":
        poll_data, poll_ctx_meta, poll_usage = await generate_poll_via_llm(
            session=session,
            group_id=group_id,
            recent_msgs=recent_msgs,
            topic_hint=f"{reasoning} | Последняя реплика: {trigger_msgs[-1].text_content[:200]}",
        )
        poll_ctx_meta["is_direct_ping"] = is_direct_ping
        poll_ctx_meta["router_action"] = "poll"
        poll_ctx_meta["llm_prompts"] = {
            **router_prompts,
            **(poll_ctx_meta.get("llm_prompts") or {}),
        }
        total_usage = router_usage + poll_usage
        reply_id = target_msg_id if is_direct_ping else None

        await _save_decision_log(
            session,
            group_id,
            trigger_tg_msg_ids,
            "poll",
            reasoning,
            {"poll": poll_data, "target_tg_msg_id": reply_id},
            poll_ctx_meta,
            total_usage,
        )
        return BotDecisionResult(
            action="poll",
            target_tg_msg_id=reply_id,
            poll_data=poll_data,
            simulate_typing=g_settings.simulate_typing,
            reasoning=reasoning,
        )

    # Ветка 3: Ответить текстом (reply или message) через Smart LLM + 3-слойную память
    reply_text, context_meta, smart_usage = await _generate_reply_text(
        session=session,
        group=group,
        g_settings=g_settings,
        persona=persona,
        recent_msgs=recent_msgs,
        trigger_msgs=trigger_msgs,
        raw_action=raw_action,
        target_msg_id=target_msg_id,
        reasoning=reasoning,
        router_json=router_json,
        router_prompts=router_prompts,
        is_direct_ping_meta=is_direct_ping,
        now_str=now_str,
    )
    total_usage = router_usage + smart_usage

    if not reply_text:
        await _save_decision_log(
            session, group_id, trigger_tg_msg_ids, "ignore", "Модель вернула пустой ответ.", {}, context_meta, total_usage
        )
        return BotDecisionResult(action="ignore", reasoning="Пустой ответ от модели.")

    await _save_decision_log(
        session,
        group_id,
        trigger_tg_msg_ids,
        raw_action,
        reasoning,
        {"text": reply_text, "target_tg_msg_id": target_msg_id if raw_action == "reply" else None},
        context_meta,
        total_usage,
    )

    return BotDecisionResult(
        action=raw_action,  # type: ignore[arg-type]
        target_tg_msg_id=target_msg_id if raw_action == "reply" else None,
        text=reply_text,
        simulate_typing=g_settings.simulate_typing,
        reasoning=reasoning,
    )


async def load_group_readonly(session: AsyncSession, group_id: int) -> Group:
    """Загружает группу с настройками, контекстом и шаблоном персоны без создания новых записей."""
    stmt = (
        select(Group)
        .options(
            selectinload(Group.settings),
            selectinload(Group.context),
            selectinload(Group.persona_template),
        )
        .where(Group.id == group_id)
    )
    group = (await session.execute(stmt)).scalars().first()
    if not group:
        raise ValueError("Группа не найдена")
    return group


async def simulate_tagged_reply(
    session: AsyncSession,
    group_id: int,
    sandbox_messages: list[dict[str, Any]],
    include_real_history: bool = True,
) -> dict[str, Any]:
    """
    Эфемерная симуляция ответа бота в песочнице админки (как при прямом теге в Telegram):
    - читает текущую память группы, участников и настройки;
    - НЕ сохраняет сообщения, НЕ пишет DecisionLog и НЕ отправляет ничего в Telegram.
    """
    group = await load_group_readonly(session, group_id)

    g_settings = group.settings or GroupSettings(group_id=group_id)
    persona = await resolve_effective_persona(session, group)
    persona_name = str(persona.get("display_name") or "Бот")

    real_msgs: list[Message] = []
    window_size = max(10, g_settings.context_window_messages)
    if include_real_history:
        recent_stmt = (
            select(Message)
            .where(Message.group_id == group_id)
            .order_by(Message.created_at.desc())
            .limit(window_size)
        )
        real_msgs = list(reversed((await session.execute(recent_stmt)).scalars().all()))

    now_utc = datetime.now(timezone.utc)
    max_real_id = max((m.telegram_message_id for m in real_msgs), default=0)
    ephemeral_msgs = await build_ephemeral_messages(
        session=session,
        group_id=group_id,
        sandbox_messages=sandbox_messages,
        persona_name=persona_name,
        start_msg_id=max(max_real_id + 1, 100001),
        now_utc=now_utc,
    )
    if not ephemeral_msgs:
        raise ValueError("Передайте хотя бы одно сообщение для диалога.")

    recent_msgs = (real_msgs + ephemeral_msgs)[-window_size:]
    trigger_msgs = [ephemeral_msgs[-1]]
    trigger_set = {m.telegram_message_id for m in trigger_msgs}
    now_str = format_current_datetime_context(g_settings.timezone, now_utc)

    raw_action, target_msg_id, reasoning, router_json, router_prompts, router_usage = await _run_router_and_plan(
        session=session,
        group=group,
        g_settings=g_settings,
        persona=persona,
        recent_msgs=recent_msgs,
        trigger_msgs=trigger_msgs,
        trigger_set=trigger_set,
        effective_chattiness=g_settings.chattiness_level,
        router_direct_ping=True,
        now_str=now_str,
    )

    if raw_action == "ignore":
        return {
            "action": "ignore",
            "text": None,
            "emoji": None,
            "poll_data": None,
            "reasoning": reasoning,
            "persona_name": persona_name,
            "model_used": router_usage.model,
            "prompt_tokens": router_usage.prompt_tokens,
            "completion_tokens": router_usage.completion_tokens,
            "cost_usd": router_usage.cost_usd,
            "retrieved_context_meta": {"is_direct_ping": True, "llm_prompts": router_prompts},
        }

    if raw_action == "react":
        allowed = g_settings.allowed_emojis or ["👍"]
        chosen_emoji = str(router_json.get("emoji") or "").strip()
        if chosen_emoji not in allowed:
            chosen_emoji = allowed[0]
        return {
            "action": "react",
            "text": None,
            "emoji": chosen_emoji,
            "poll_data": None,
            "reasoning": reasoning,
            "persona_name": persona_name,
            "model_used": router_usage.model,
            "prompt_tokens": router_usage.prompt_tokens,
            "completion_tokens": router_usage.completion_tokens,
            "cost_usd": router_usage.cost_usd,
            "retrieved_context_meta": {"is_direct_ping": True, "llm_prompts": router_prompts},
        }

    if raw_action == "poll":
        poll_data, poll_ctx_meta, poll_usage = await generate_poll_via_llm(
            session=session,
            group_id=group_id,
            recent_msgs=recent_msgs,
            topic_hint=f"{reasoning} | Последняя реплика: {trigger_msgs[-1].text_content[:200]}",
        )
        poll_ctx_meta["is_direct_ping"] = True
        poll_ctx_meta["router_action"] = "poll"
        poll_ctx_meta["llm_prompts"] = {
            **router_prompts,
            **(poll_ctx_meta.get("llm_prompts") or {}),
        }
        total_usage = router_usage + poll_usage
        return {
            "action": "poll",
            "text": None,
            "emoji": None,
            "poll_data": poll_data,
            "reasoning": reasoning,
            "persona_name": persona_name,
            "model_used": total_usage.model,
            "prompt_tokens": total_usage.prompt_tokens,
            "completion_tokens": total_usage.completion_tokens,
            "cost_usd": total_usage.cost_usd,
            "retrieved_context_meta": poll_ctx_meta,
        }

    reply_text, context_meta, smart_usage = await _generate_reply_text(
        session=session,
        group=group,
        g_settings=g_settings,
        persona=persona,
        recent_msgs=recent_msgs,
        trigger_msgs=trigger_msgs,
        raw_action=raw_action,
        target_msg_id=target_msg_id,
        reasoning=reasoning,
        router_json=router_json,
        router_prompts=router_prompts,
        is_direct_ping_meta=True,
        now_str=now_str,
    )
    total_usage = router_usage + smart_usage

    if not reply_text:
        return {
            "action": "ignore",
            "text": None,
            "emoji": None,
            "poll_data": None,
            "reasoning": "Пустой ответ от модели.",
            "persona_name": persona_name,
            "model_used": total_usage.model,
            "prompt_tokens": total_usage.prompt_tokens,
            "completion_tokens": total_usage.completion_tokens,
            "cost_usd": total_usage.cost_usd,
            "retrieved_context_meta": context_meta,
        }

    return {
        "action": raw_action,
        "text": reply_text,
        "emoji": None,
        "poll_data": None,
        "reasoning": reasoning,
        "persona_name": persona_name,
        "model_used": total_usage.model,
        "prompt_tokens": total_usage.prompt_tokens,
        "completion_tokens": total_usage.completion_tokens,
        "cost_usd": total_usage.cost_usd,
        "retrieved_context_meta": context_meta,
    }


async def _save_decision_log(
    session: AsyncSession,
    group_id: int,
    trigger_ids: list[int],
    decision_type: str,
    reasoning: str,
    action_payload: dict[str, Any],
    context_meta: dict[str, Any],
    usage: LLMUsage,
) -> DecisionLog:
    entry = DecisionLog(
        group_id=group_id,
        trigger_tg_msg_ids=trigger_ids,
        decision_type=decision_type,
        router_reasoning=reasoning,
        action_payload=action_payload,
        retrieved_context_meta=context_meta,
        model_used=usage.model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_usd=usage.cost_usd,
    )
    session.add(entry)
    await session.commit()
    await publish_group_event(
        group_id,
        "decision_logged",
        {
            "id": str(entry.id),
            "decision_type": entry.decision_type,
            "router_reasoning": entry.router_reasoning,
            "action_payload": entry.action_payload,
            "model_used": entry.model_used,
            "cost_usd": entry.cost_usd,
            "created_at": entry.created_at.isoformat(),
        },
    )
    return entry

