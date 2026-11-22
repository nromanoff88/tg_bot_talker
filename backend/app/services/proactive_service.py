import logging
import random
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import DecisionLog, Group, GroupMember, GroupSettings, Message
from app.redis_client import publish_bot_command, publish_group_event
from app.services.decision_service import (
    build_ephemeral_messages,
    humanize_bot_text,
    is_within_quiet_hours,
    load_group_readonly,
)
from app.services.llm_service import LLMUsage, complete_text
from app.services.memory_service import (
    build_persona_generation_context,
    ensure_group_initialized,
    format_current_datetime_context,
    format_message_local_ts,
    resolve_effective_persona,
)
from app.services.prompt_service import render_prompt

logger = logging.getLogger(__name__)


async def _generate_proactive_draft(
    session: AsyncSession,
    group: Group,
    g_settings: GroupSettings,
    persona: dict[str, Any],
    recent_msgs: list[Message],
    start_new_topic: bool,
    silence_hours: float,
    now_utc: datetime,
) -> tuple[str, dict[str, Any], LLMUsage]:
    """Собирает промпт инициативы (revive или new_topic) и вызывает Smart LLM без побочных эффектов."""
    top_members_stmt = (
        select(GroupMember)
        .options(selectinload(GroupMember.facts))
        .where(GroupMember.group_id == group.id)
        .order_by(GroupMember.message_count.desc())
        .limit(8)
    )
    top_members = list((await session.execute(top_members_stmt)).scalars().all())

    sys_prompt, context_meta = await build_persona_generation_context(
        session=session,
        group=group,
        recent_messages=recent_msgs if not start_new_topic else [],
        memory_query="интересные факты хобби работа увлечения участников чата",
    )
    context_meta["silence_hours"] = round(silence_hours, 2)
    context_meta["new_topic_mode"] = start_new_topic

    member_hooks = []
    for m in top_members:
        facts_preview = "; ".join(f.fact_text for f in m.facts[:5])
        if facts_preview or m.character_summary:
            uname = f" (@{m.username})" if m.username else ""
            nick = f" (в чате: {m.chat_nickname})" if m.chat_nickname else ""
            member_hooks.append(f"- {m.display_name}{nick}{uname}: {m.character_summary} | Факты: {facts_preview}")

    now_str = format_current_datetime_context(g_settings.timezone, now_utc)
    recent_preview = "\n".join(
        f"[{format_message_local_ts(m.created_at, g_settings.timezone)}] {m.sender_name}: {m.text_content[:140]}"
        for m in recent_msgs[-8:]
    )

    prompt_key = "proactive_new_topic_prompt" if start_new_topic else "proactive_revive_prompt"
    user_prompt = await render_prompt(
        session,
        prompt_key,
        {
            "current_datetime": now_str,
            "group_title": group.title,
            "silence_hours": f"{silence_hours:.1f}",
            "silence_minutes": f"{silence_hours * 60:.0f}",
            "recent_preview": recent_preview or ("Чат пока пуст." if start_new_topic else "Чат пока почти пуст."),
            "member_hooks": "\n".join(member_hooks) or "Пока мало фактов об участниках.",
        },
    )

    text_out, usage = await complete_text(
        session=session,
        tier="smart",
        system_prompt=sys_prompt,
        user_prompt=user_prompt,
        temperature=0.9,
        max_tokens=350,
    )
    text_out = humanize_bot_text(text_out, persona.get("display_name", ""))
    context_meta["llm_prompts"] = {
        "smart_system_prompt": sys_prompt,
        "smart_user_prompt": user_prompt,
    }
    return text_out, context_meta, usage


async def generate_and_send_proactive_message(
    session: AsyncSession,
    group_id: int,
    manual_trigger: bool = False,
) -> dict[str, Any]:
    """Генерирует естественный повод начать диалог в затихшей группе и отправляет через bot-gateway."""
    group = await ensure_group_initialized(session, group_id)
    persona = await resolve_effective_persona(session, group)
    g_settings = group.settings

    recent_stmt = (
        select(Message)
        .where(Message.group_id == group_id)
        .order_by(Message.created_at.desc())
        .limit(20)
    )
    recent_msgs = list(reversed((await session.execute(recent_stmt)).scalars().all()))

    now_utc = datetime.now(timezone.utc)
    last_msg_dt = group.last_message_at or (recent_msgs[-1].created_at if recent_msgs else None)
    silence_hours = ((now_utc - last_msg_dt).total_seconds() / 3600.0) if last_msg_dt else 999.0
    threshold_hours = float(getattr(g_settings, "new_topic_after_hours", 4.0) or 0.0)
    start_new_topic = silence_hours >= threshold_hours

    text_out, context_meta, usage = await _generate_proactive_draft(
        session=session,
        group=group,
        g_settings=g_settings,
        persona=persona,
        recent_msgs=recent_msgs,
        start_new_topic=start_new_topic,
        silence_hours=silence_hours,
        now_utc=now_utc,
    )
    if not text_out:
        return {"status": "empty_response"}

    group.last_proactive_at = now_utc
    group.unanswered_initiatives = (group.unanswered_initiatives or 0) + 1

    mode_label = (
        f"новая тема (тишина {silence_hours:.1f}ч >= {threshold_hours:g}ч)"
        if start_new_topic
        else f"продолжение/оживление (тишина {silence_hours:.1f}ч < {threshold_hours:g}ч)"
    )
    reason = (
        f"Принудительный запуск инициативы из админки: {mode_label}."
        if manual_trigger
        else f"Спонтанная инициатива бота в период затишья: {mode_label}."
    )
    log_entry = DecisionLog(
        group_id=group_id,
        trigger_tg_msg_ids=[],
        decision_type="proactive",
        router_reasoning=reason,
        action_payload={"text": text_out, "new_topic_mode": start_new_topic},
        retrieved_context_meta=context_meta,
        model_used=usage.model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_usd=usage.cost_usd,
    )
    session.add(log_entry)
    await session.commit()

    await publish_bot_command(
        {
            "command": "send_message",
            "group_id": group_id,
            "text": text_out,
            "reply_to_tg_msg_id": None,
            "simulate_typing": group.settings.simulate_typing,
            "source": "proactive",
        }
    )
    await publish_group_event(
        group_id,
        "decision_logged",
        {
            "id": str(log_entry.id),
            "decision_type": "proactive",
            "router_reasoning": reason,
            "action_payload": {"text": text_out, "new_topic_mode": start_new_topic},
            "model_used": usage.model,
            "cost_usd": usage.cost_usd,
            "created_at": log_entry.created_at.isoformat(),
        },
    )
    return {"status": "sent", "text": text_out, "new_topic_mode": start_new_topic}


async def simulate_proactive_message(
    session: AsyncSession,
    group_id: int,
    mode: str = "auto",
    silence_hours_override: Optional[float] = None,
    sandbox_messages: Optional[list[dict[str, Any]]] = None,
    include_real_history: bool = True,
) -> dict[str, Any]:
    """
    Эфемерная симуляция спонтанной инициативы бота для песочницы админки:
    - НЕ меняет last_proactive_at / unanswered_initiatives;
    - НЕ пишет DecisionLog и НЕ отправляет сообщение в Telegram;
    - дополнительно возвращает диагностику gate_notes о готовности реального планировщика.
    """
    group = await load_group_readonly(session, group_id)

    g_settings = group.settings or GroupSettings(group_id=group_id)
    persona = await resolve_effective_persona(session, group)
    persona_name = str(persona.get("display_name") or "Бот")
    now_utc = datetime.now(timezone.utc)

    gate_notes: list[str] = []
    if not group.is_active:
        gate_notes.append("Бот на паузе в этой группе (is_active = false)")
    if not g_settings.proactive_enabled or g_settings.proactive_chance_per_hour <= 0:
        gate_notes.append("Инициатива выключена или шанс равен 0% в настройках группы")
    if (group.unanswered_initiatives or 0) >= 1:
        gate_notes.append(
            f"Anti-Spam Guard: уже есть {group.unanswered_initiatives} безответная инициатива — бот ждёт реакции в реальном чате"
        )
    if g_settings.quiet_hours_enabled and is_within_quiet_hours(
        g_settings.timezone, g_settings.quiet_hours_start, g_settings.quiet_hours_end, now_utc
    ):
        gate_notes.append(
            f"Сейчас тихие часы ({g_settings.quiet_hours_start}–{g_settings.quiet_hours_end}, {g_settings.timezone})"
        )
    if group.last_message_at:
        silence_min = (now_utc - group.last_message_at).total_seconds() / 60.0
        if silence_min < g_settings.min_silence_minutes:
            gate_notes.append(
                f"В чате ещё не прошло {g_settings.min_silence_minutes} мин тишины (сейчас {int(silence_min)} мин)"
            )
    if group.last_proactive_at:
        hours_since = (now_utc - group.last_proactive_at).total_seconds() / 3600.0
        if hours_since < g_settings.proactive_cooldown_hours:
            gate_notes.append(
                f"Ещё действует кулдаун инициативы ({hours_since:.1f}ч < {g_settings.proactive_cooldown_hours}ч)"
            )
    if not gate_notes:
        gate_notes.append("Все условия планировщика выполнены — инициатива может сработать в реале")

    real_msgs: list[Message] = []
    if include_real_history:
        recent_stmt = (
            select(Message)
            .where(Message.group_id == group_id)
            .order_by(Message.created_at.desc())
            .limit(20)
        )
        real_msgs = list(reversed((await session.execute(recent_stmt)).scalars().all()))

    max_real_id = max((m.telegram_message_id for m in real_msgs), default=0)
    ephemeral_msgs = await build_ephemeral_messages(
        session=session,
        group_id=group_id,
        sandbox_messages=sandbox_messages or [],
        persona_name=persona_name,
        start_msg_id=max(max_real_id + 1, 100001),
        now_utc=now_utc,
    )
    recent_msgs = (real_msgs + ephemeral_msgs)[-20:]

    if silence_hours_override is not None:
        silence_hours = float(silence_hours_override)
    else:
        last_msg_dt = (
            ephemeral_msgs[-1].created_at
            if ephemeral_msgs
            else (group.last_message_at or (real_msgs[-1].created_at if real_msgs else None))
        )
        silence_hours = max(0.0, (now_utc - last_msg_dt).total_seconds() / 3600.0) if last_msg_dt else 999.0

    threshold_hours = float(getattr(g_settings, "new_topic_after_hours", 4.0) or 0.0)
    if mode == "new_topic":
        start_new_topic = True
    elif mode == "revive":
        start_new_topic = False
    else:
        start_new_topic = silence_hours >= threshold_hours

    text_out, context_meta, usage = await _generate_proactive_draft(
        session=session,
        group=group,
        g_settings=g_settings,
        persona=persona,
        recent_msgs=recent_msgs,
        start_new_topic=start_new_topic,
        silence_hours=silence_hours,
        now_utc=now_utc,
    )
    if not text_out:
        gate_notes.append("Модель вернула пустой ответ")

    return {
        "text": text_out,
        "new_topic_mode": start_new_topic,
        "silence_hours": round(silence_hours, 2),
        "persona_name": persona_name,
        "gate_notes": gate_notes,
        "model_used": usage.model,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cost_usd": usage.cost_usd,
        "retrieved_context_meta": context_meta,
    }


async def check_all_groups_for_proactive_initiative(session: AsyncSession) -> int:
    """
    Вызывается планировщиком каждые 5 минут.
    Проверяет тихие часы, тишину в чате, кулдаун, Anti-Spam Guard и вероятность инициативы.
    """
    stmt = (
        select(Group)
        .options(selectinload(Group.settings))
        .where(Group.is_active.is_(True))
    )
    groups = list((await session.execute(stmt)).scalars().all())
    now_utc = datetime.now(timezone.utc)
    triggered = 0

    for g in groups:
        s = g.settings
        if not s or not s.proactive_enabled or s.proactive_chance_per_hour <= 0:
            continue

        # Anti-Spam Guard: не пишем повторно в пустоту, если на прошлый вброс никто не ответил
        if (g.unanswered_initiatives or 0) >= 1:
            continue

        # Проверка тихих часов по часовому поясу группы
        if s.quiet_hours_enabled and is_within_quiet_hours(s.timezone, s.quiet_hours_start, s.quiet_hours_end, now_utc):
            continue

        # Проверка тишины в чате (не перебиваем активную беседу)
        if g.last_message_at:
            silence_min = (now_utc - g.last_message_at).total_seconds() / 60.0
            if silence_min < s.min_silence_minutes:
                continue

        # Проверка интервала между собственными инициативами бота
        if g.last_proactive_at:
            hours_since_proactive = (now_utc - g.last_proactive_at).total_seconds() / 3600.0
            if hours_since_proactive < s.proactive_cooldown_hours:
                continue

        # При 100% вероятности срабатываем гарантированно на первом же тике после прохождения условий
        if s.proactive_chance_per_hour >= 0.999:
            p_tick = 1.0
        else:
            p_hour = max(0.0, s.proactive_chance_per_hour)
            p_tick = 1.0 - ((1.0 - p_hour) ** (5.0 / 60.0))

        if random.random() <= p_tick:
            try:
                res = await generate_and_send_proactive_message(session, g.id, manual_trigger=False)
                if res.get("status") == "sent":
                    triggered += 1
            except Exception as exc:
                logger.warning("Proactive initiative failed for group %s: %s", g.id, exc)

    return triggered
