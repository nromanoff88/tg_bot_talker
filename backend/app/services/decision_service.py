import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DecisionLog, GroupMember, Message, SystemConfig
from app.redis_client import publish_group_event
from app.services.llm_service import LLMUsage, complete_json, complete_text
from app.services.memory_service import (
    build_persona_generation_context,
    ensure_group_initialized,
    resolve_effective_persona,
)

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
    action: Literal["ignore", "react", "reply", "message"]
    target_tg_msg_id: Optional[int] = None
    emoji: Optional[str] = None
    text: Optional[str] = None
    simulate_typing: bool = True
    reasoning: str = ""


ROUTER_SYSTEM_PROMPT = """Ты — внутренний фильтр социальной уместности живого участника группового чата Telegram.
Твоя задача — посмотреть на последние сообщения в чате и решить, как поведёт себя живой человек с заданной личностью:
1. "ignore" — промолчать (если люди обсуждают что-то узко личное между собой, короткие реплики «ок/спасибо/+», тема совсем чужая или твоё вмешательство будет выглядеть навязчиво).
2. "react" — просто поставить эмодзи-реакцию на конкретное сообщение (когда шутка смешная, новость классная или хочется поддержать без лишних слов).
3. "reply" — ответить реплаем на конкретное сообщение (когда обратились к тебе, задали интересный вопрос, пошла близкая тебе тема или уместно вставить живой комментарий).
4. "message" — написать в общий поток без привязки к одному сообщению (когда комментируешь сразу весь диалог нескольких людей).

Учитывай параметр `chattiness_level` (от 0.0 — почти всегда молчит, до 1.0 — очень разговорчивый душа компании).
Если `is_direct_ping = true` (тебя упомянули или ответили на твоё сообщение), почти всегда выбирай "reply" (кроме совсем пустых «ок/понял», где уместна реакция "react" или "ignore").

Верни СТРОГО JSON:
{
  "action": "ignore | react | reply | message",
  "target_tg_msg_id": 12345,
  "emoji": "🔥",
  "need_memory_search": false,
  "memory_query": "поисковый запрос для вспоминания старых обсуждений чата, если нужно",
  "reasoning": "короткое объяснение на русском (1-2 предложения), почему принято именно такое решение"
}"""


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
    persona_name_lower = (persona.get("display_name") or "").lower()

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
        if persona_name_lower and len(persona_name_lower) >= 3:
            if re.search(rf"\b{re.escape(persona_name_lower)}\b", txt_low):
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

    # Собираем краткие карточки авторов для быстрого роутера
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
        chat_lines.append(f"#{m.telegram_message_id}{new_tag} {who}{r_tag}: {m.text_content}")

    effective_chattiness = g_settings.chattiness_level
    if (
        not is_direct_ping
        and g_settings.quiet_hours_enabled
        and is_within_quiet_hours(g_settings.timezone, g_settings.quiet_hours_start, g_settings.quiet_hours_end, now_utc)
    ):
        # В тихие часы не встреваем сами без веского повода
        effective_chattiness = round(effective_chattiness * 0.35, 2)

    router_user_prompt = (
        f"ГРУППА: {group.title}\n"
        f"ТВОЯ РОЛЬ: {persona['display_name']} — {persona['backstory'][:280]}\n"
        f"ПАРАМЕТРЫ ПОВЕДЕНИЯ:\n"
        f"- chattiness_level: {effective_chattiness}\n"
        f"- is_direct_ping (обратились ли лично к тебе): {is_direct_ping and g_settings.reply_on_mention}\n"
        f"- reactions_enabled: {g_settings.reactions_enabled}\n"
        f"- allowed_emojis: {g_settings.allowed_emojis}\n\n"
        f"КРАТКО О СОБЕСЕДНИКАХ:\n{chr(10).join(brief_authors) or 'нет данных'}\n\n"
        f"ПОСЛЕДНИЕ СООБЩЕНИЯ ЧАТА:\n" + "\n".join(chat_lines)
    )

    router_json, router_usage = await complete_json(
        session=session,
        tier="fast",
        system_prompt=ROUTER_SYSTEM_PROMPT,
        user_prompt=router_user_prompt,
        temperature=0.35,
        max_tokens=450,
    )

    raw_action = str(router_json.get("action") or "ignore").strip().lower()
    if raw_action not in ("ignore", "react", "reply", "message"):
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

    # Ветка 1: Промолчать
    if raw_action == "ignore":
        await _save_decision_log(
            session,
            group_id,
            trigger_tg_msg_ids,
            "ignore",
            reasoning,
            {},
            {"is_direct_ping": is_direct_ping, "chattiness": effective_chattiness},
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
            {"is_direct_ping": is_direct_ping},
            router_usage,
        )
        return BotDecisionResult(
            action="react",
            target_tg_msg_id=target_msg_id,
            emoji=chosen_emoji,
            reasoning=reasoning,
        )

    # Ветка 3: Ответить текстом (reply или message) через Smart LLM + 3-слойную память
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
    context_meta["is_direct_ping"] = is_direct_ping
    context_meta["router_action"] = raw_action

    full_chat_lines = []
    for m in recent_msgs:
        who = f"{persona['display_name']} (ТЫ)" if m.is_from_bot else m.sender_name
        r_tag = f" [ответ на #{m.reply_to_tg_msg_id}]" if m.reply_to_tg_msg_id else ""
        full_chat_lines.append(f"#{m.telegram_message_id} {who}{r_tag}: {m.text_content}")

    reply_instruction = (
        f"Напиши естественный живой ответ реплаем на сообщение #{target_msg_id}."
        if raw_action == "reply"
        else "Напиши естественную реплику в общий разговор чата."
    )

    gen_user_prompt = (
        f"ИСТОРИЯ ПОСЛЕДНИХ СООБЩЕНИЙ В ЧАТЕ:\n"
        + "\n".join(full_chat_lines)
        + f"\n\nТВОЯ ВНУТРЕННЯЯ МОТИВАЦИЯ СЕЙЧАС: {reasoning}\n"
        + f"ЗАДАЧА: {reply_instruction}"
    )

    reply_text, smart_usage = await complete_text(
        session=session,
        tier="smart",
        system_prompt=sys_prompt,
        user_prompt=gen_user_prompt,
        temperature=0.85,
        max_tokens=500,
    )
    reply_text = humanize_bot_text(reply_text, persona.get("display_name", ""))
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

