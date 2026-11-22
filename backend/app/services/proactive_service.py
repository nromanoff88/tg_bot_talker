import logging
import random
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import DecisionLog, Group, GroupMember, Message
from app.redis_client import publish_bot_command, publish_group_event
from app.services.decision_service import humanize_bot_text, is_within_quiet_hours
from app.services.llm_service import complete_text
from app.services.memory_service import (
    build_persona_generation_context,
    ensure_group_initialized,
    format_current_datetime_context,
    format_message_local_ts,
    resolve_effective_persona,
)

logger = logging.getLogger(__name__)


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

    # Также загружаем самых активных участников группы, даже если в последних 20 сообщениях их было мало
    top_members_stmt = (
        select(GroupMember)
        .options(selectinload(GroupMember.facts))
        .where(GroupMember.group_id == group_id)
        .order_by(GroupMember.message_count.desc())
        .limit(8)
    )
    top_members = list((await session.execute(top_members_stmt)).scalars().all())

    now_utc = datetime.now(timezone.utc)
    last_msg_dt = group.last_message_at or (recent_msgs[-1].created_at if recent_msgs else None)
    silence_hours = ((now_utc - last_msg_dt).total_seconds() / 3600.0) if last_msg_dt else 999.0
    threshold_hours = float(getattr(g_settings, "new_topic_after_hours", 4.0) or 0.0)
    start_new_topic = silence_hours >= threshold_hours

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

    if start_new_topic:
        user_prompt = (
            f"ТЕКУЩЕЕ МЕСТНОЕ ВРЕМЯ: {now_str}\n"
            f"В чате «{group.title}» тишина уже более {silence_hours:.1f} ч. "
            f"Предыдущий разговор давно закончился, и ты решил сам закинуть в чат НОВУЮ тему.\n\n"
            f"О ЧЁМ ГОВОРИЛИ В ПРОШЛЫЙ РАЗ (ТОЛЬКО ДЛЯ СПРАВКИ, ЧТОБЫ НЕ ПОВТОРЯТЬСЯ):\n"
            f"{recent_preview or 'Чат пока пуст.'}\n\n"
            f"ДОСЬЕ И ИНТЕРЕСЫ УЧАСТНИКОВ ГРУППЫ:\n"
            f"{chr(10).join(member_hooks) or 'Пока мало фактов об участниках.'}\n\n"
            f"СТРОГОЕ ПРАВИЛО: НЕ продолжай и НЕ комментируй старую тему из прошлых сообщений выше — тот разговор уже закрыт!\n"
            f"ЗАДАЧА: Напиши ОДНО короткое живое сообщение (1–2 предложения) на СОВЕРШЕННО НОВУЮ тему в своём характере. "
            f"Можешь: вбросить свежий жизненный вопрос/историю/наблюдение по интересам тусовки, "
            f"или обратиться к кому-то из участников по факту из его досье (не связанному с последним диалогом). "
            f"Пиши естественно, без штампов вроде «Всем привет! Как дела? Давайте сменим тему»."
        )
    else:
        user_prompt = (
            f"ТЕКУЩЕЕ МЕСТНОЕ ВРЕМЯ: {now_str}\n"
            f"В чате «{group.title}» небольшая пауза ({silence_hours * 60:.0f} мин). "
            f"Ты решил сам непринуждённо написать в чат и поддержать или оживить разговор.\n\n"
            f"ЧТО НЕДАВНО ПИСАЛИ В ЧАТЕ:\n{recent_preview or 'Чат пока почти пуст.'}\n\n"
            f"ЗАЦЕПКИ ПО УЧАСТНИКАМ ГРУППЫ:\n{chr(10).join(member_hooks) or 'Пока мало фактов об участниках.'}\n\n"
            f"ЗАДАЧА: Напиши ОДНО короткое живое сообщение (1–2 предложения), чтобы естественно оживить чат. "
            f"Варианты: развить недавнюю мысль/историю под новым углом, поинтересоваться у кого-то из ребят деталями, "
            f"или поделиться короткой мыслью в своём характере. Без банального «Всем привет! Как дела?»."
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
    context_meta["llm_prompts"] = {
        "smart_system_prompt": sys_prompt,
        "smart_user_prompt": user_prompt,
    }
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
