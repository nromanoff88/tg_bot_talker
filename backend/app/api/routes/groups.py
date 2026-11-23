import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_admin
from app.database import get_db
from app.models import Group, GroupMember, MemoryEpisode, Message
from app.redis_client import get_arq_pool, publish_bot_command
from app.schemas import (
    GeneratePollRequest,
    GroupContextSchema,
    GroupDetailOut,
    GroupPersonaConfigUpdate,
    GroupSettingsSchema,
    GroupSummaryOut,
    ImportHistoryResponse,
    ManualSendMessageRequest,
    MemoryEpisodeCreate,
    MemoryEpisodeOut,
    MemoryEpisodeUpdate,
    PollPayloadSchema,
    SandboxChatRequest,
    SandboxChatResponse,
    SandboxProactiveRequest,
    SandboxProactiveResponse,
)
from app.services.decision_service import generate_poll_via_llm, sanitize_poll_params, simulate_tagged_reply
from app.services.import_service import import_telegram_desktop_files
from app.services.llm_service import create_embedding
from app.services.memory_service import (
    build_episode_embedding_text,
    ensure_group_initialized,
    resolve_effective_persona,
    search_relevant_episodes,
)
from app.services.proactive_service import generate_and_send_proactive_message, simulate_proactive_message

router = APIRouter(prefix="/api/groups", tags=["groups"], dependencies=[Depends(require_admin)])


class RegisterGroupRequest(BaseModel):
    group_id: int
    title: str = "Новая группа"


@router.get("", response_model=list[GroupSummaryOut])
async def list_groups(session: AsyncSession = Depends(get_db)) -> list[GroupSummaryOut]:
    stmt = (
        select(Group)
        .options(selectinload(Group.persona_template))
        .order_by(Group.last_message_at.desc().nullslast(), Group.created_at.desc())
    )
    groups = list((await session.execute(stmt)).scalars().all())

    mem_counts_rows = (
        await session.execute(select(GroupMember.group_id, func.count(GroupMember.id)).group_by(GroupMember.group_id))
    ).all()
    mem_counts = {gid: int(cnt) for gid, cnt in mem_counts_rows}

    msg_counts_rows = (
        await session.execute(select(Message.group_id, func.count(Message.id)).group_by(Message.group_id))
    ).all()
    msg_counts = {gid: int(cnt) for gid, cnt in msg_counts_rows}

    out: list[GroupSummaryOut] = []
    for g in groups:
        persona = await resolve_effective_persona(session, g)
        p_label = persona.get("display_name", "Бот")
        if g.persona_override and g.persona_override.get("use_custom"):
            p_label += " (Своя роль)"
        elif g.persona_template:
            p_label = f"{g.persona_template.name} ({p_label})"

        out.append(
            GroupSummaryOut(
                id=g.id,
                title=g.title,
                chat_type=g.chat_type,
                is_active=g.is_active,
                persona_template_id=g.persona_template_id,
                persona_name=p_label,
                member_count=mem_counts.get(g.id, 0),
                message_count=msg_counts.get(g.id, 0),
                unanalyzed_msg_count=g.unanalyzed_msg_count or 0,
                last_message_at=g.last_message_at,
                last_bot_message_at=g.last_bot_message_at,
            )
        )
    return out


@router.post("/register", response_model=GroupDetailOut)
async def register_group_manually(
    payload: RegisterGroupRequest,
    session: AsyncSession = Depends(get_db),
) -> GroupDetailOut:
    group = await ensure_group_initialized(session, payload.group_id, title=payload.title)
    await session.commit()
    return await get_group_detail(group.id, session)


@router.get("/{group_id}", response_model=GroupDetailOut)
async def get_group_detail(group_id: int, session: AsyncSession = Depends(get_db)) -> GroupDetailOut:
    stmt = (
        select(Group)
        .options(selectinload(Group.settings), selectinload(Group.context))
        .where(Group.id == group_id)
    )
    group = (await session.execute(stmt)).scalars().first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    if not group.settings or not group.context:
        group = await ensure_group_initialized(session, group_id, title=group.title)
        await session.commit()

    return GroupDetailOut(
        id=group.id,
        title=group.title,
        chat_type=group.chat_type,
        is_active=group.is_active,
        persona_template_id=group.persona_template_id,
        persona_override=group.persona_override or {},
        unanalyzed_msg_count=group.unanalyzed_msg_count or 0,
        unanswered_initiatives=group.unanswered_initiatives or 0,
        last_message_at=group.last_message_at,
        last_bot_message_at=group.last_bot_message_at,
        last_proactive_at=group.last_proactive_at,
        last_analyzed_at=group.last_analyzed_at,
        settings=GroupSettingsSchema.model_validate(group.settings),
        context=GroupContextSchema.model_validate(group.context),
    )


@router.put("/{group_id}/persona", response_model=GroupDetailOut)
async def update_group_persona(
    group_id: int,
    payload: GroupPersonaConfigUpdate,
    session: AsyncSession = Depends(get_db),
) -> GroupDetailOut:
    group = await ensure_group_initialized(session, group_id)
    group.is_active = payload.is_active
    group.persona_template_id = payload.persona_template_id
    group.persona_override = payload.persona_override
    await session.commit()
    return await get_group_detail(group_id, session)


@router.put("/{group_id}/settings", response_model=GroupSettingsSchema)
async def update_group_settings(
    group_id: int,
    payload: GroupSettingsSchema,
    session: AsyncSession = Depends(get_db),
) -> GroupSettingsSchema:
    group = await ensure_group_initialized(session, group_id)
    for k, v in payload.model_dump().items():
        setattr(group.settings, k, v)
    await session.commit()
    await session.refresh(group.settings)
    return GroupSettingsSchema.model_validate(group.settings)


@router.put("/{group_id}/context", response_model=GroupContextSchema)
async def update_group_context(
    group_id: int,
    payload: GroupContextSchema,
    session: AsyncSession = Depends(get_db),
) -> GroupContextSchema:
    group = await ensure_group_initialized(session, group_id)
    ctx = group.context
    ctx.summary = payload.summary
    ctx.summary_locked = payload.summary_locked
    ctx.inside_jokes_and_rules = [item.model_dump() for item in payload.inside_jokes_and_rules]
    ctx.active_topics = payload.active_topics
    await session.commit()
    await session.refresh(ctx)
    return GroupContextSchema.model_validate(ctx)


@router.delete("/{group_id}")
async def delete_group(group_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    group = await session.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    await session.delete(group)
    await session.commit()
    return {"deleted": True}


# ==========================================
# Vector Episodes (pgvector)
# ==========================================
@router.get("/{group_id}/episodes", response_model=list[MemoryEpisodeOut])
async def list_or_search_episodes(
    group_id: int,
    q: Optional[str] = Query(default=None, description="Семантический поисковый запрос для проверки pgvector"),
    session: AsyncSession = Depends(get_db),
) -> list[MemoryEpisodeOut]:
    if q and q.strip():
        matches = await search_relevant_episodes(session, group_id, q.strip(), top_k=15, min_similarity=0.1)
        return [
            MemoryEpisodeOut(
                id=ep.id,
                group_id=ep.group_id,
                title=ep.title,
                episode_summary=ep.episode_summary,
                participant_names=ep.participant_names or [],
                is_locked=ep.is_locked,
                has_embedding=ep.embedding is not None,
                similarity=sim,
                happened_at=ep.happened_at,
                created_at=ep.created_at,
            )
            for ep, sim in matches
        ]

    stmt = (
        select(MemoryEpisode)
        .where(MemoryEpisode.group_id == group_id)
        .order_by(MemoryEpisode.happened_at.desc())
        .limit(100)
    )
    episodes = list((await session.execute(stmt)).scalars().all())
    return [
        MemoryEpisodeOut(
            id=ep.id,
            group_id=ep.group_id,
            title=ep.title,
            episode_summary=ep.episode_summary,
            participant_names=ep.participant_names or [],
            is_locked=ep.is_locked,
            has_embedding=ep.embedding is not None,
            similarity=None,
            happened_at=ep.happened_at,
            created_at=ep.created_at,
        )
        for ep in episodes
    ]


@router.post("/{group_id}/episodes", response_model=MemoryEpisodeOut)
async def create_episode_manually(
    group_id: int,
    payload: MemoryEpisodeCreate,
    session: AsyncSession = Depends(get_db),
) -> MemoryEpisodeOut:
    group = await ensure_group_initialized(session, group_id)
    tz_name = group.settings.timezone if group.settings else None
    now_dt = datetime.now(timezone.utc)
    title = payload.title.strip()
    summary = payload.episode_summary.strip()
    emb_text = build_episode_embedding_text(
        title=title,
        summary=summary,
        happened_at=now_dt,
        tz_name=tz_name,
        participants=payload.participant_names,
    )
    emb = await create_embedding(session, emb_text, emb_type="doc")
    ep = MemoryEpisode(
        group_id=group_id,
        title=title,
        episode_summary=summary,
        embedding=emb,
        participant_names=payload.participant_names,
        is_locked=payload.is_locked,
        happened_at=now_dt,
    )
    session.add(ep)
    await session.commit()
    await session.refresh(ep)
    return MemoryEpisodeOut(
        id=ep.id,
        group_id=ep.group_id,
        title=ep.title,
        episode_summary=ep.episode_summary,
        participant_names=ep.participant_names or [],
        is_locked=ep.is_locked,
        has_embedding=ep.embedding is not None,
        happened_at=ep.happened_at,
        created_at=ep.created_at,
    )


@router.put("/{group_id}/episodes/{episode_id}", response_model=MemoryEpisodeOut)
async def update_episode(
    group_id: int,
    episode_id: uuid.UUID,
    payload: MemoryEpisodeUpdate,
    session: AsyncSession = Depends(get_db),
) -> MemoryEpisodeOut:
    ep = await session.get(MemoryEpisode, episode_id)
    if not ep or ep.group_id != group_id:
        raise HTTPException(status_code=404, detail="Эпизод не найден")

    reembed = False
    if payload.title is not None and payload.title != ep.title:
        ep.title = payload.title.strip()
        reembed = True
    if payload.episode_summary is not None and payload.episode_summary != ep.episode_summary:
        ep.episode_summary = payload.episode_summary.strip()
        reembed = True
    if payload.participant_names is not None:
        ep.participant_names = payload.participant_names
    if payload.is_locked is not None:
        ep.is_locked = payload.is_locked
    elif reembed:
        ep.is_locked = True

    if reembed:
        group = await ensure_group_initialized(session, group_id)
        tz_name = group.settings.timezone if group.settings else None
        emb_text = build_episode_embedding_text(
            title=ep.title,
            summary=ep.episode_summary,
            happened_at=ep.happened_at,
            tz_name=tz_name,
            participants=ep.participant_names or [],
        )
        ep.embedding = await create_embedding(session, emb_text, emb_type="doc")

    await session.commit()
    await session.refresh(ep)
    return MemoryEpisodeOut(
        id=ep.id,
        group_id=ep.group_id,
        title=ep.title,
        episode_summary=ep.episode_summary,
        participant_names=ep.participant_names or [],
        is_locked=ep.is_locked,
        has_embedding=ep.embedding is not None,
        happened_at=ep.happened_at,
        created_at=ep.created_at,
    )


@router.delete("/{group_id}/episodes/{episode_id}")
async def delete_episode(
    group_id: int,
    episode_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> dict:
    ep = await session.get(MemoryEpisode, episode_id)
    if not ep or ep.group_id != group_id:
        raise HTTPException(status_code=404, detail="Эпизод не найден")
    await session.delete(ep)
    await session.commit()
    return {"deleted": True}


# ==========================================
# Manual Actions & Telegram History Import
# ==========================================
@router.post("/{group_id}/actions/send-message")
async def manual_send_message(
    group_id: int,
    payload: ManualSendMessageRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    group = await session.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    await publish_bot_command(
        {
            "command": "send_message",
            "group_id": group_id,
            "text": payload.text.strip(),
            "reply_to_tg_msg_id": payload.reply_to_tg_msg_id,
            "simulate_typing": False,
            "source": "manual",
        }
    )
    return {"queued": True}


@router.post("/{group_id}/actions/send-poll")
async def manual_send_poll(
    group_id: int,
    payload: PollPayloadSchema,
    session: AsyncSession = Depends(get_db),
) -> dict:
    group = await session.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    clean_poll = sanitize_poll_params(payload.model_dump())
    await publish_bot_command(
        {
            "command": "send_poll",
            "group_id": group_id,
            "poll": clean_poll,
            "reply_to_tg_msg_id": clean_poll.get("reply_to_tg_msg_id"),
            "simulate_typing": False,
            "source": "manual",
        }
    )
    return {"queued": True, "poll": clean_poll}


@router.post("/{group_id}/actions/generate-poll", response_model=PollPayloadSchema)
async def generate_poll_draft(
    group_id: int,
    payload: GeneratePollRequest,
    session: AsyncSession = Depends(get_db),
) -> PollPayloadSchema:
    await ensure_group_initialized(session, group_id)
    poll_data, _, _ = await generate_poll_via_llm(
        session=session,
        group_id=group_id,
        topic_hint=payload.topic_hint.strip(),
        preferred_type=payload.type,
        preferred_anonymous=payload.is_anonymous,
        preferred_multiple=payload.allows_multiple_answers,
    )
    return PollPayloadSchema(**poll_data)


@router.post("/{group_id}/actions/analyze-memory")
async def force_analyze_memory(group_id: int) -> dict:
    arq = await get_arq_pool()
    await arq.enqueue_job("process_all_pending_memory_job", group_id, 10)
    return {"queued": True, "message": "Задача анализа памяти поставлена в очередь memory-worker."}


@router.post("/{group_id}/actions/trigger-proactive")
async def force_trigger_proactive(
    group_id: int,
    session: AsyncSession = Depends(get_db),
) -> dict:
    return await generate_and_send_proactive_message(session, group_id, manual_trigger=True)


# ==========================================
# Sandbox (Admin Test Chat without DB writes)
# ==========================================
@router.post("/{group_id}/sandbox/chat", response_model=SandboxChatResponse)
async def sandbox_chat_reply(
    group_id: int,
    payload: SandboxChatRequest,
    session: AsyncSession = Depends(get_db),
) -> SandboxChatResponse:
    try:
        res = await simulate_tagged_reply(
            session=session,
            group_id=group_id,
            sandbox_messages=[m.model_dump() for m in payload.messages],
            include_real_history=payload.include_real_history,
            tag_bot=payload.tag_bot,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return SandboxChatResponse(**res)


@router.post("/{group_id}/sandbox/proactive", response_model=SandboxProactiveResponse)
async def sandbox_proactive_test(
    group_id: int,
    payload: SandboxProactiveRequest,
    session: AsyncSession = Depends(get_db),
) -> SandboxProactiveResponse:
    try:
        res = await simulate_proactive_message(
            session=session,
            group_id=group_id,
            mode=payload.mode,
            silence_hours_override=payload.silence_hours_override,
            sandbox_messages=[m.model_dump() for m in payload.sandbox_messages],
            include_real_history=payload.include_real_history,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return SandboxProactiveResponse(**res)


@router.post("/{group_id}/import", response_model=ImportHistoryResponse)
async def import_group_history(
    group_id: int,
    files: Optional[list[UploadFile]] = File(default=None),
    file: Optional[UploadFile] = File(default=None),
    trigger_analysis: bool = Query(default=True),
    session: AsyncSession = Depends(get_db),
) -> ImportHistoryResponse:
    upload_list: list[UploadFile] = []
    if files:
        upload_list.extend(files)
    if file:
        upload_list.append(file)
    if not upload_list:
        raise HTTPException(status_code=400, detail="Не выбран ни один файл для импорта.")

    files_bytes = [await f.read() for f in upload_list]
    try:
        stats = await import_telegram_desktop_files(
            session=session,
            group_id=group_id,
            files_bytes=files_bytes,
            trigger_analysis=trigger_analysis,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения файла экспорта: {exc}")

    return ImportHistoryResponse(**stats)

