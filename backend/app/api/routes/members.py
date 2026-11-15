import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_admin
from app.database import get_db
from app.models import GroupMember, MemberFact
from app.schemas import (
    GroupMemberDetailOut,
    GroupMemberOut,
    GroupMemberUpdate,
    MemberFactCreate,
    MemberFactOut,
    MemberFactUpdate,
)

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

    mem.chat_nickname = payload.chat_nickname.strip()
    mem.nickname_locked = payload.nickname_locked
    mem.character_summary = payload.character_summary.strip()
    mem.character_locked = payload.character_locked
    mem.bot_attitude = payload.bot_attitude.strip()
    mem.attitude_locked = payload.attitude_locked
    mem.admin_notes = payload.admin_notes.strip()

    await session.commit()
    return await get_member_detail(group_id, member_id, session)


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
        # Автоматически закрепляем факт при ручной правке админом, чтобы ИИ его не затёр
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

