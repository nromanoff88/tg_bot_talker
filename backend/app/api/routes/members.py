import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_admin
from app.database import get_db
from app.models import GroupMember, MemberFact, Message
from app.schemas import (
    GroupMemberDetailOut,
    GroupMemberOut,
    GroupMemberUpdate,
    MemberFactCreate,
    MemberFactOut,
    MemberFactUpdate,
    MergeMemberRequest,
)
from app.services.import_service import SYNTHETIC_TG_ID_BASE

router = APIRouter(
    prefix="/api/groups/{group_id}/members",
    tags=["members"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=list[GroupMemberOut])
async def list_group_members(
    group_id: int,
    session: AsyncSession = Depends(get_db),
) -> list[GroupMemberOut]:
    stmt = (
        select(GroupMember)
        .where(GroupMember.group_id == group_id)
        .order_by(GroupMember.message_count.desc(), GroupMember.last_seen_at.desc().nullslast())
    )
    members = list((await session.execute(stmt)).scalars().all())

    fact_counts_rows = (
        await session.execute(
            select(MemberFact.member_id, func.count(MemberFact.id))
            .where(MemberFact.group_id == group_id)
            .group_by(MemberFact.member_id)
        )
    ).all()
    fact_counts = {mid: int(cnt) for mid, cnt in fact_counts_rows}

    return [
        GroupMemberOut(
            id=m.id,
            group_id=m.group_id,
            telegram_user_id=m.telegram_user_id,
            username=m.username,
            display_name=m.display_name,
            chat_nickname=m.chat_nickname or "",
            nickname_locked=m.nickname_locked,
            character_summary=m.character_summary or "",
            character_locked=m.character_locked,
            bot_attitude=m.bot_attitude or "",
            attitude_locked=m.attitude_locked,
            admin_notes=m.admin_notes or "",
            message_count=m.message_count or 0,
            last_seen_at=m.last_seen_at,
            facts_count=fact_counts.get(m.id, 0),
        )
        for m in members
    ]


@router.get("/{member_id}", response_model=GroupMemberDetailOut)
async def get_member_detail(
    group_id: int,
    member_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> GroupMemberDetailOut:
    stmt = (
        select(GroupMember)
        .options(selectinload(GroupMember.facts))
        .where(GroupMember.id == member_id, GroupMember.group_id == group_id)
    )
    mem = (await session.execute(stmt)).scalars().first()
    if not mem:
        raise HTTPException(status_code=404, detail="Участник не найден в этой группе")

    sorted_facts = sorted(
        mem.facts,
        key=lambda f: (1 if f.is_pinned else 0, f.updated_at.timestamp() if f.updated_at else 0),
        reverse=True,
    )

    return GroupMemberDetailOut(
        id=mem.id,
        group_id=mem.group_id,
        telegram_user_id=mem.telegram_user_id,
        username=mem.username,
        display_name=mem.display_name,
        chat_nickname=mem.chat_nickname or "",
        nickname_locked=mem.nickname_locked,
        character_summary=mem.character_summary or "",
        character_locked=mem.character_locked,
        bot_attitude=mem.bot_attitude or "",
        attitude_locked=mem.attitude_locked,
        admin_notes=mem.admin_notes or "",
        message_count=mem.message_count or 0,
        last_seen_at=mem.last_seen_at,
        facts_count=len(sorted_facts),
        facts=[MemberFactOut.model_validate(f) for f in sorted_facts],
    )


@router.put("/{member_id}", response_model=GroupMemberDetailOut)
async def update_member_profile(
    group_id: int,
    member_id: uuid.UUID,
    payload: GroupMemberUpdate,
    session: AsyncSession = Depends(get_db),
) -> GroupMemberDetailOut:
    mem = await session.get(GroupMember, member_id)
    if not mem or mem.group_id != group_id:
        raise HTTPException(status_code=404, detail="Участник не найден в этой группе")

    if payload.display_name is not None and payload.display_name.strip():
        mem.display_name = payload.display_name.strip()[:255]
    mem.chat_nickname = payload.chat_nickname.strip()
    mem.nickname_locked = payload.nickname_locked
    mem.character_summary = payload.character_summary.strip()
    mem.character_locked = payload.character_locked
    mem.bot_attitude = payload.bot_attitude.strip()
    mem.attitude_locked = payload.attitude_locked
    mem.admin_notes = payload.admin_notes.strip()

    await session.commit()
    return await get_member_detail(group_id, member_id, session)


@router.post("/{target_member_id}/merge", response_model=GroupMemberDetailOut)
async def merge_group_members(
    group_id: int,
    target_member_id: uuid.UUID,
    payload: MergeMemberRequest,
    session: AsyncSession = Depends(get_db),
) -> GroupMemberDetailOut:
    """
    Объединяет карточку-дубль (source_member_id) в целевую карточку (target_member_id):
    - переносит все сообщения и дедуплицированные факты;
    - сохраняет реальный telegram_user_id и @username (если у целевой карточки был синтетический ID из HTML);
    - объединяет пустые поля портрета и заметки админа;
    - удаляет карточку-дубль.
    """
    if payload.source_member_id == target_member_id:
        raise HTTPException(status_code=400, detail="Нельзя объединить участника самого с собой")

    stmt = (
        select(GroupMember)
        .options(selectinload(GroupMember.facts))
        .where(
            GroupMember.group_id == group_id,
            GroupMember.id.in_([target_member_id, payload.source_member_id]),
        )
    )
    rows = {m.id: m for m in (await session.execute(stmt)).scalars().all()}
    target = rows.get(target_member_id)
    source = rows.get(payload.source_member_id)

    if not target or not source:
        raise HTTPException(status_code=404, detail="Один из участников не найден в этой группе")

    # 1. Переносим сообщения с дубля на целевого участника
    await session.execute(
        update(Message)
        .where(Message.group_id == group_id, Message.member_id == source.id)
        .values(member_id=target.id)
    )

    # 2. Переносим и дедуплицируем факты
    target_facts_by_norm = {f.fact_text.strip().lower(): f for f in target.facts}
    for sf in list(source.facts):
        norm_txt = sf.fact_text.strip().lower()
        existing = target_facts_by_norm.get(norm_txt)
        if existing:
            if sf.is_pinned and not existing.is_pinned:
                existing.is_pinned = True
            if sf.confidence > existing.confidence:
                existing.confidence = sf.confidence
            source.facts.remove(sf)
            await session.delete(sf)
        else:
            sf.member = target
            sf.member_id = target.id
            target_facts_by_norm[norm_txt] = sf

    # 3. Объединяем поля портрета и заметки админа
    if not target.username and source.username:
        target.username = source.username

    if not target.chat_nickname and source.chat_nickname:
        target.chat_nickname = source.chat_nickname
        target.nickname_locked = source.nickname_locked

    if not target.character_summary and source.character_summary:
        target.character_summary = source.character_summary
        target.character_locked = source.character_locked
    elif (
        target.character_summary
        and source.character_summary
        and source.character_summary not in target.character_summary
        and not target.character_locked
    ):
        target.character_summary = f"{target.character_summary}\n{source.character_summary}".strip()

    if not target.bot_attitude and source.bot_attitude:
        target.bot_attitude = source.bot_attitude
        target.attitude_locked = source.attitude_locked

    if source.admin_notes and source.admin_notes not in (target.admin_notes or ""):
        target.admin_notes = f"{target.admin_notes}\n{source.admin_notes}".strip() if target.admin_notes else source.admin_notes

    target.message_count = (target.message_count or 0) + (source.message_count or 0)
    if source.last_seen_at and (not target.last_seen_at or source.last_seen_at > target.last_seen_at):
        target.last_seen_at = source.last_seen_at

    # Сохраняем реальный telegram_user_id, если целевая карточка была создана из HTML-импорта
    real_tg_id_to_adopt: int | None = None
    if target.telegram_user_id >= SYNTHETIC_TG_ID_BASE and source.telegram_user_id < SYNTHETIC_TG_ID_BASE:
        real_tg_id_to_adopt = source.telegram_user_id

    # 4. Сначала фиксируем UPDATE фактов/сообщений и отсоединение от дубля, затем удаляем дубль
    await session.flush()
    await session.delete(source)
    await session.flush()

    if real_tg_id_to_adopt is not None:
        target.telegram_user_id = real_tg_id_to_adopt

    await session.commit()
    return await get_member_detail(group_id, target_member_id, session)


@router.delete("/{member_id}")
async def delete_group_member(
    group_id: int,
    member_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> dict:
    mem = await session.get(GroupMember, member_id)
    if not mem or mem.group_id != group_id:
        raise HTTPException(status_code=404, detail="Участник не найден в этой группе")
    await session.delete(mem)
    await session.commit()
    return {"deleted": True}


@router.post("/{member_id}/facts", response_model=MemberFactOut)
async def create_member_fact(
    group_id: int,
    member_id: uuid.UUID,
    payload: MemberFactCreate,
    session: AsyncSession = Depends(get_db),
) -> MemberFactOut:
    mem = await session.get(GroupMember, member_id)
    if not mem or mem.group_id != group_id:
        raise HTTPException(status_code=404, detail="Участник не найден в этой группе")

    fact = MemberFact(
        member_id=member_id,
        group_id=group_id,
        category=payload.category.strip() or "other",
        fact_text=payload.fact_text.strip(),
        confidence=1.0,
        is_pinned=payload.is_pinned,
    )
    session.add(fact)
    await session.commit()
    await session.refresh(fact)
    return MemberFactOut.model_validate(fact)


@router.put("/{member_id}/facts/{fact_id}", response_model=MemberFactOut)
async def update_member_fact(
    group_id: int,
    member_id: uuid.UUID,
    fact_id: uuid.UUID,
    payload: MemberFactUpdate,
    session: AsyncSession = Depends(get_db),
) -> MemberFactOut:
    fact = await session.get(MemberFact, fact_id)
    if not fact or fact.member_id != member_id or fact.group_id != group_id:
        raise HTTPException(status_code=404, detail="Факт не найден")

    text_edited = False
    if payload.category is not None:
        fact.category = payload.category.strip() or "other"
    if payload.fact_text is not None and payload.fact_text.strip() != fact.fact_text:
        fact.fact_text = payload.fact_text.strip()
        text_edited = True

    if payload.is_pinned is not None:
        fact.is_pinned = payload.is_pinned
    elif text_edited:
        fact.is_pinned = True

    await session.commit()
    await session.refresh(fact)
    return MemberFactOut.model_validate(fact)


@router.delete("/{member_id}/facts/{fact_id}")
async def delete_member_fact(
    group_id: int,
    member_id: uuid.UUID,
    fact_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> dict:
    fact = await session.get(MemberFact, fact_id)
    if not fact or fact.member_id != member_id or fact.group_id != group_id:
        raise HTTPException(status_code=404, detail="Факт не найден")
    await session.delete(fact)
    await session.commit()
    return {"deleted": True}
