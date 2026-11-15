import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    DecisionLog,
    Group,
    GroupContext,
    GroupMember,
    GroupSettings,
    MemberFact,
    MemoryEpisode,
    Message,
    PersonaTemplate,
)
from app.redis_client import get_redis, publish_group_event
from app.services.llm_service import complete_json, create_embedding

logger = logging.getLogger(__name__)


async def ensure_group_initialized(
    session: AsyncSession,
    group_id: int,
    title: str = "Telegram Group",
    chat_type: str = "supergroup",
) -> Group:
    """Гарантирует наличие записи группы, её настроек и контекста в БД."""
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
    if group:
        if title and title != "Telegram Group" and group.title != title:
            group.title = title
        if not group.settings:
            group.settings = GroupSettings(group_id=group_id)
        if not group.context:
            group.context = GroupContext(group_id=group_id)
        return group

    default_persona = (
        await session.execute(select(PersonaTemplate).where(PersonaTemplate.is_default.is_(True)).limit(1))
    ).scalars().first()
    if not default_persona:
        default_persona = (await session.execute(select(PersonaTemplate).limit(1))).scalars().first()

    group = Group(
        id=group_id,
        title=title,
        chat_type=chat_type,
        is_active=True,
        persona_template_id=default_persona.id if default_persona else None,
        persona_override={"use_custom": False},
    )
    session.add(group)
    await session.flush()

    session.add(GroupSettings(group_id=group_id))
    session.add(GroupContext(group_id=group_id))
    await session.flush()

    group = (await session.execute(stmt)).scalars().one()
    return group


async def resolve_effective_persona(session: AsyncSession, group: Group) -> dict[str, Any]:
    """Собирает итоговую Персону группы (шаблон + индивидуальные переопределения чата)."""
    template = group.persona_template
    if not template and group.persona_template_id:
        template = await session.get(PersonaTemplate, group.persona_template_id)
    if not template:
        template = (
            await session.execute(select(PersonaTemplate).where(PersonaTemplate.is_default.is_(True)).limit(1))
        ).scalars().first()

    base = {
        "display_name": template.display_name if template else "Макс",
        "backstory": template.backstory if template else "Обычный живой участник этого чата.",
        "communication_style": (
            template.communication_style
            if template
            else "Пиши коротко и естественно, разговорным языком без канцелярита и списков."
        ),
        "taboos": template.taboos if template else "Никаких фраз «Как ИИ» и маркированных списков.",
        "example_phrases": list(template.example_phrases) if template and template.example_phrases else [],
    }

    override = group.persona_override or {}
    if override.get("use_custom"):
        for key in ("display_name", "backstory", "communication_style", "taboos", "example_phrases"):
            val = override.get(key)
            if val not in (None, "", []):
                base[key] = val

    return base


async def search_relevant_episodes(
    session: AsyncSession,
    group_id: int,
    query: str,
    top_k: int = 4,
    min_similarity: float = 0.26,
) -> list[tuple[MemoryEpisode, float]]:
    """Ищет релевантные воспоминания строго внутри group_id через pgvector (с текстовым fallback)."""
    if not query or not query.strip():
        return []

    query_vec = await create_embedding(session, query)
    if query_vec is not None:
        distance_expr = MemoryEpisode.embedding.cosine_distance(query_vec)
        stmt = (
            select(MemoryEpisode, (1.0 - distance_expr).label("similarity"))
            .where(
                MemoryEpisode.group_id == group_id,
                MemoryEpisode.embedding.is_not(None),
            )
            .order_by(distance_expr.asc())
            .limit(top_k)
        )
        rows = (await session.execute(stmt)).all()
        results = [(ep, round(float(sim), 4)) for ep, sim in rows if sim is not None and float(sim) >= min_similarity]
        if results:
            return results

    # Fallback: поиск по ключевым словам внутри той же группы
    words = [w.strip() for w in query.split() if len(w.strip()) >= 3][:5]
    if not words:
        return []

    conds = []
    for w in words:
        pattern = f"%{w}%"
        conds.append(MemoryEpisode.title.ilike(pattern))
        conds.append(MemoryEpisode.episode_summary.ilike(pattern))

    stmt = (
        select(MemoryEpisode)
        .where(MemoryEpisode.group_id == group_id, or_(*conds))
        .order_by(MemoryEpisode.happened_at.desc())
        .limit(top_k)
    )
    eps = (await session.execute(stmt)).scalars().all()
    return [(ep, 0.5) for ep in eps]


async def build_persona_generation_context(
    session: AsyncSession,
    group: Group,
    recent_messages: list[Message],
    memory_query: str = "",
) -> tuple[str, dict[str, Any]]:
    """
    Формирует системный промпт для Smart LLM со всеми 3 слоями памяти конкретной группы:
    1) Персона и контекст группы
    2) Досье участников недавнего обсуждения (с приоритетом закреплённых фактов и заметок админа)
    3) Релевантные эпизоды из pgvector
    Возвращает (system_prompt, retrieved_context_meta для лога мыслей).
    """
    persona = await resolve_effective_persona(session, group)
    ctx = group.context or await session.get(GroupContext, group.id)

    member_ids = {m.member_id for m in recent_messages if m.member_id is not None}
    members: list[GroupMember] = []
    if member_ids:
        stmt = (
            select(GroupMember)
            .options(selectinload(GroupMember.facts))
            .where(GroupMember.group_id == group.id, GroupMember.id.in_(member_ids))
        )
        members = list((await session.execute(stmt)).scalars().all())

    episodes_with_sim = await search_relevant_episodes(session, group.id, memory_query, top_k=4)

    # Сборка секций системного промпта
    jokes_lines = []
    if ctx and ctx.inside_jokes_and_rules:
        for item in ctx.inside_jokes_and_rules[:15]:
            kind_label = {"meme": "Локальный мем", "rule": "Правило чата", "norm": "Традиция"}.get(
                item.get("kind", "meme"), "Деталь"
            )
            jokes_lines.append(f"- [{kind_label}] {item.get('text', '')}")

    member_blocks = []
    used_facts_meta = []
    for m in members:
        sorted_facts = sorted(
            m.facts,
            key=lambda f: (1 if f.is_pinned else 0, f.confidence, f.updated_at.timestamp() if f.updated_at else 0),
            reverse=True,
        )[:10]
        fact_strs = [f"• {f.fact_text}" for f in sorted_facts]
        for f in sorted_facts:
            used_facts_meta.append(
                {"member": m.display_name, "fact": f.fact_text, "pinned": f.is_pinned, "category": f.category}
            )

        name_line = m.display_name
        if m.chat_nickname:
            name_line += f" (в чате зовут: {m.chat_nickname})"
        if m.username:
            name_line += f" [@{m.username}]"

        lines = [f"Участник: {name_line}"]
        if m.character_summary:
            lines.append(f"  Характер и стиль: {m.character_summary}")
        if m.bot_attitude:
            lines.append(f"  Твоё отношение к нему: {m.bot_attitude}")
        if m.admin_notes:
            lines.append(f"  ВАЖНАЯ ЗАМЕТКА О ЧЕЛОВЕКЕ: {m.admin_notes}")
        if fact_strs:
            lines.append("  Известные факты:\n    " + "\n    ".join(fact_strs))
        member_blocks.append("\n".join(lines))

    episode_lines = []
    used_episodes_meta = []
    for ep, sim in episodes_with_sim:
        date_str = ep.happened_at.strftime("%Y-%m-%d")
        episode_lines.append(f"- [{date_str}] {ep.title}: {ep.episode_summary}")
        used_episodes_meta.append({"id": str(ep.id), "title": ep.title, "similarity": sim})

    examples_block = "\n".join(f"- «{ex}»" for ex in persona.get("example_phrases", [])[:6])

    system_prompt = f"""Ты — живой участник группового чата «{group.title}» в Telegram.
Твоё имя/ник в общении: {persona['display_name']}.

ТВОЯ ЛИЧНОСТЬ И ЛЕГЕНДА:
{persona['backstory']}

ТВОЯ МАНЕРА ОБЩЕНИЯ:
{persona['communication_style']}

СТРОГИЕ ТАБУ (ЧЕГО ДЕЛАТЬ НЕЛЬЗЯ):
{persona['taboos']}
- Никогда не пиши как ассистент поддержки, не предлагай «помочь разобраться» и не ставь нумерованные/маркированные списки.
- Не начинай ответ с имени собеседника и запятой в каждом сообщении, если и так делаешь реплай.
- Отвечай только текстом самого сообщения (без кавычек вокруг ответа и без префикса со своим именем).

ПРИМЕРЫ ТВОЕЙ ЖИВОЙ РЕЧИ:
{examples_block or '- общайся коротко, метко и по-человечески'}

КОНТЕКСТ И АТМОСФЕРА ЭТОЙ ГРУППЫ:
Описание чата: {ctx.summary if ctx and ctx.summary else 'Обычный живой групповой чат.'}
Активные темы: {', '.join(ctx.active_topics) if ctx and ctx.active_topics else 'разные повседневные темы'}
Внутренние мемы, сленг и правила чата:
{chr(10).join(jokes_lines) if jokes_lines else '- пока не зафиксированы'}

ЧТО ТЫ ЗНАЕШЬ О ТЕКУЩИХ СОБЕСЕДНИКАХ В ЭТОЙ ГРУППЕ:
{chr(10).join(member_blocks) if member_blocks else 'Пока мало данных об участниках — присматривайся и общайся естественно.'}

ВОСПОМИНАНИЯ О ПРОШЛЫХ ОБСУЖДЕНИЯХ В ЭТОМ ЧАТЕ:
{chr(10).join(episode_lines) if episode_lines else 'Нет специфичных старых эпизодов по этой теме.'}
"""

    meta = {
        "persona_name": persona["display_name"],
        "memory_query": memory_query,
        "members_loaded": [m.display_name for m in members],
        "facts_used": used_facts_meta[:15],
        "episodes_used": used_episodes_meta,
    }
    return system_prompt, meta


MEMORY_EXTRACTION_SYSTEM_PROMPT = """Ты — аналитик социальной динамики и памяти группового чата.
Твоя задача — внимательно прочитать очередную пачку сообщений группы и обновить:
1) Общее саммари группы, внутренние мемы/правила и текущие активные темы.
2) Психологический портрет, манеру речи, как человека зовут в чате, отношение бота к нему и конкретные атомарные факты по каждому участнику.
3) Запомнить 0, 1 или 2 ярких смысловых эпизода (только если в пачке было содержательное обсуждение, история, спор или событие, которое стоит помнить спустя недели; пустой трёп в эпизоды не записывай).

КРИТИЧЕСКИЕ ПРАВИЛА:
- Все пометки [LOCKED BY ADMIN] являются абсолютной истиной: НЕ дублируй их и НЕ пытайся их опровергнуть.
- Извлекай только реальные факты о людях из их слов и контекста (работа, город, хобби, вкусы, характер, жизненные события, взгляды, манера речи). Не выдумывай то, чего не было!
- Пиши всё лаконично на русском языке.

Верни строго валидный JSON следующего формата:
{
  "group_summary_update": "обновлённое краткое описание атмосферы и тематики группы (2-4 предложения) или пустая строка, если менять не нужно",
  "active_topics": ["тема 1", "тема 2"],
  "new_jokes_or_rules": [
    {"kind": "meme", "text": "описание локальной шутки, сленга или негласного правила"}
  ],
  "member_updates": [
    {
      "member_id": "UUID участника из входных данных",
      "chat_nickname": "как к нему обращаются в чате (или пустая строка)",
      "character_summary": "обновлённый портрет характера, темперамента и манеры общения (1-3 предложения)",
      "bot_attitude": "естественное отношение бота к этому человеку (например: свой парень, интересно спорить про технику, стоит общаться бережно)",
      "new_facts": [
        {
          "category": "work | hobby | bio | opinion | style | relationship | other",
          "fact_text": "конкретный атомарный факт об участнике",
          "confidence": 0.9,
          "source_message_id": 12345
        }
      ]
    }
  ],
  "new_episodes": [
    {
      "title": "Короткий заголовок обсуждения/события",
      "episode_summary": "Ёмкая выжимка в 2-4 предложениях: кто что рассказал/обсуждал и к чему пришли",
      "participant_names": ["Имя 1", "Имя 2"]
    }
  ]
}"""


async def analyze_group_memory_batch(
    session: AsyncSession,
    group_id: int,
    force: bool = False,
    batch_limit: int = 60,
) -> dict[str, Any]:
    """
    Анализирует непрочитанный буфер сообщений группы и обновляет гибридную память,
    гарантируя неприкосновенность всех полей с Admin Lock / Pin.
    """
    r = get_redis()
    lock_key = f"bot_talker:lock:memory:{group_id}"
    acquired = await r.set(lock_key, "1", ex=180, nx=True)
    if not acquired:
        return {"status": "already_running", "processed": 0}

    try:
        group = await ensure_group_initialized(session, group_id)
        min_batch = 1 if force else group.settings.memory_batch_size

        msg_stmt = (
            select(Message)
            .where(Message.group_id == group_id, Message.is_processed_for_memory.is_(False))
            .order_by(Message.created_at.asc())
            .limit(batch_limit)
        )
        batch_msgs = list((await session.execute(msg_stmt)).scalars().all())

        if len(batch_msgs) < min_batch:
            return {"status": "not_enough_messages", "pending": len(batch_msgs), "required": min_batch}

        # Загружаем профили авторов этой пачки
        member_ids = {m.member_id for m in batch_msgs if m.member_id is not None}
        members_by_id: dict[str, GroupMember] = {}
        if member_ids:
            m_stmt = (
                select(GroupMember)
                .options(selectinload(GroupMember.facts))
                .where(GroupMember.group_id == group_id, GroupMember.id.in_(member_ids))
            )
            for mem in (await session.execute(m_stmt)).scalars().all():
                members_by_id[str(mem.id)] = mem

        ctx = group.context

        # Формируем входные данные для Fast LLM
        existing_members_payload = []
        for mid_str, mem in members_by_id.items():
            existing_members_payload.append(
                {
                    "member_id": mid_str,
                    "display_name": mem.display_name,
                    "username": mem.username,
                    "chat_nickname": (
                        f"[LOCKED BY ADMIN] {mem.chat_nickname}" if mem.nickname_locked else mem.chat_nickname
                    ),
                    "character_summary": (
                        f"[LOCKED BY ADMIN] {mem.character_summary}"
                        if mem.character_locked
                        else mem.character_summary
                    ),
                    "bot_attitude": (
                        f"[LOCKED BY ADMIN] {mem.bot_attitude}" if mem.attitude_locked else mem.bot_attitude
                    ),
                    "admin_notes": f"[LOCKED BY ADMIN] {mem.admin_notes}" if mem.admin_notes else "",
                    "existing_facts": [
                        f"{'[LOCKED BY ADMIN] ' if f.is_pinned else ''}[{f.category}] {f.fact_text}"
                        for f in mem.facts[:25]
                    ],
                }
            )

        transcript_lines = []
        for m in batch_msgs:
            role_tag = "[БОТ]" if m.is_from_bot else f"[member_id={m.member_id}]"
            reply_part = f" (reply to #{m.reply_to_tg_msg_id})" if m.reply_to_tg_msg_id else ""
            react_part = f" [реакции: {m.reactions}]" if m.reactions else ""
            ts = m.created_at.strftime("%Y-%m-%d %H:%M")
            transcript_lines.append(
                f"#{m.telegram_message_id} [{ts}] {m.sender_name} {role_tag}{reply_part}: {m.text_content}{react_part}"
            )

        user_prompt = (
            f"ГРУППА: {group.title}\n"
            f"ТЕКУЩЕЕ САММАРИ ГРУППЫ {'[LOCKED BY ADMIN]' if ctx.summary_locked else ''}: {ctx.summary}\n"
            f"ИЗВЕСТНЫЕ МЕМЫ И ПРАВИЛА: {[i.get('text') for i in (ctx.inside_jokes_and_rules or [])]}\n\n"
            f"ТЕКУЩИЕ ДОСЬЕ УЧАСТНИКОВ ПАЧКИ:\n{existing_members_payload}\n\n"
            f"НОВЫЕ СООБЩЕНИЯ ДЛЯ АНАЛИЗА ({len(batch_msgs)} шт.):\n" + "\n".join(transcript_lines)
        )

        extracted, usage = await complete_json(
            session=session,
            tier="fast",
            system_prompt=MEMORY_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=3000,
        )

        facts_added = 0
        episodes_added = 0

        # 1. Обновление контекста группы с защитой Admin Lock
        summary_upd = (extracted.get("group_summary_update") or "").strip()
        if summary_upd and not ctx.summary_locked:
            ctx.summary = summary_upd

        active_topics = extracted.get("active_topics")
        if isinstance(active_topics, list) and active_topics:
            ctx.active_topics = [str(t).strip() for t in active_topics if str(t).strip()][:10]

        new_jokes = extracted.get("new_jokes_or_rules") or []
        if isinstance(new_jokes, list) and new_jokes:
            current_items = list(ctx.inside_jokes_and_rules or [])
            existing_norm = {str(i.get("text", "")).strip().lower() for i in current_items}
            for item in new_jokes:
                if not isinstance(item, dict):
                    continue
                txt = str(item.get("text", "")).strip()
                if txt and txt.lower() not in existing_norm:
                    current_items.append(
                        {
                            "id": str(uuid.uuid4()),
                            "kind": item.get("kind") if item.get("kind") in ("meme", "rule", "norm") else "meme",
                            "text": txt,
                            "is_locked": False,
                        }
                    )
                    existing_norm.add(txt.lower())
            # Сохраняем все залоченные админом элементы + до 25 авто-элементов
            locked_items = [i for i in current_items if i.get("is_locked")]
            unlocked_items = [i for i in current_items if not i.get("is_locked")][-25:]
            ctx.inside_jokes_and_rules = locked_items + unlocked_items

        # 2. Обновление карточек участников и фактов с защитой Admin Lock / Pin
        for m_upd in extracted.get("member_updates") or []:
            if not isinstance(m_upd, dict):
                continue
            mid = str(m_upd.get("member_id", ""))
            mem = members_by_id.get(mid)
            if not mem:
                continue

            nick = str(m_upd.get("chat_nickname") or "").strip()
            if nick and not mem.nickname_locked and not nick.startswith("[LOCKED"):
                mem.chat_nickname = nick[:255]

            char_sum = str(m_upd.get("character_summary") or "").strip()
            if char_sum and not mem.character_locked and not char_sum.startswith("[LOCKED"):
                mem.character_summary = char_sum

            att = str(m_upd.get("bot_attitude") or "").strip()
            if att and not mem.attitude_locked and not att.startswith("[LOCKED"):
                mem.bot_attitude = att

            existing_fact_texts = {f.fact_text.strip().lower() for f in mem.facts}
            for nf in m_upd.get("new_facts") or []:
                if not isinstance(nf, dict):
                    continue
                f_text = str(nf.get("fact_text") or "").strip()
                if not f_text or f_text.lower() in existing_fact_texts or f_text.startswith("[LOCKED"):
                    continue
                cat = str(nf.get("category") or "other").strip().lower()
                if cat not in ("work", "hobby", "bio", "opinion", "style", "relationship", "other"):
                    cat = "other"
                conf = float(nf.get("confidence") or 0.8)
                src_msg = nf.get("source_message_id")
                src_msg_int = int(src_msg) if isinstance(src_msg, (int, str)) and str(src_msg).isdigit() else None

                new_fact = MemberFact(
                    member_id=mem.id,
                    group_id=group_id,
                    category=cat,
                    fact_text=f_text,
                    confidence=max(0.1, min(1.0, conf)),
                    source_message_id=src_msg_int,
                    is_pinned=False,
                )
                session.add(new_fact)
                existing_fact_texts.add(f_text.lower())
                facts_added += 1

        # 3. Создание новых векторных эпизодов (pgvector)
        happened_ts = batch_msgs[-1].created_at if batch_msgs else datetime.now(timezone.utc)
        for ep_data in (extracted.get("new_episodes") or [])[:2]:
            if not isinstance(ep_data, dict):
                continue
            title = str(ep_data.get("title") or "").strip()[:255]
            summary = str(ep_data.get("episode_summary") or "").strip()
            if not summary:
                continue
            participants = [str(p).strip() for p in (ep_data.get("participant_names") or []) if str(p).strip()]
            emb = await create_embedding(session, f"{title}. {summary}")
            session.add(
                MemoryEpisode(
                    group_id=group_id,
                    title=title or "Обсуждение в чате",
                    episode_summary=summary,
                    embedding=emb,
                    participant_names=participants,
                    is_locked=False,
                    happened_at=happened_ts,
                )
            )
            episodes_added += 1

        # 4. Помечаем сообщения обработанными и пересчитываем остаток
        for m in batch_msgs:
            m.is_processed_for_memory = True

        await session.flush()

        remaining = (
            await session.execute(
                select(func.count(Message.id)).where(
                    Message.group_id == group_id, Message.is_processed_for_memory.is_(False)
                )
            )
        ).scalar_one()
        group.unanalyzed_msg_count = int(remaining)
        group.last_analyzed_at = datetime.now(timezone.utc)

        # Записываем расход токенов и результат анализа в DecisionLog
        log_entry = DecisionLog(
            group_id=group_id,
            trigger_tg_msg_ids=[m.telegram_message_id for m in batch_msgs[-5:]],
            decision_type="memory_extract",
            router_reasoning=(
                f"Фоновый анализ пачки из {len(batch_msgs)} сообщений: "
                f"добавлено фактов: {facts_added}, эпизодов памяти: {episodes_added}."
            ),
            action_payload={"processed_messages": len(batch_msgs), "facts_added": facts_added, "episodes_added": episodes_added},
            retrieved_context_meta={"remaining_unanalyzed": int(remaining)},
            model_used=usage.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=usage.cost_usd,
        )
        session.add(log_entry)
        await session.commit()

        await publish_group_event(
            group_id,
            "memory_updated",
            {
                "processed_messages": len(batch_msgs),
                "facts_added": facts_added,
                "episodes_added": episodes_added,
                "remaining": int(remaining),
            },
        )
        return {
            "status": "ok",
            "processed": len(batch_msgs),
            "facts_added": facts_added,
            "episodes_added": episodes_added,
            "remaining": int(remaining),
        }
    finally:
        await r.delete(lock_key)

