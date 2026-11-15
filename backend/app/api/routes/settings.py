import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_db
from app.models import (
    DecisionLog,
    Group,
    GroupMember,
    MemberFact,
    MemoryEpisode,
    Message,
    PersonaTemplate,
    SystemConfig,
)
from app.schemas import (
    DashboardStatsResponse,
    PersonaTemplateCreate,
    PersonaTemplateOut,
    PersonaTemplateUpdate,
    SystemConfigResponse,
    SystemConfigUpdate,
)
from app.services.llm_service import invalidate_llm_config_cache, test_llm_connectivity

router = APIRouter(prefix="/api", tags=["settings"], dependencies=[Depends(require_admin)])


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "*" * len(key)
    return f"{key[:6]}...{key[-4:]}"


def _is_bot_online(last_hb: datetime | None) -> bool:
    if not last_hb:
        return False
    return (datetime.now(timezone.utc) - last_hb).total_seconds() < 65


@router.get("/settings/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(session: AsyncSession = Depends(get_db)) -> DashboardStatsResponse:
    total_groups = (await session.execute(select(func.count(Group.id)))).scalar_one()
    active_groups = (
        await session.execute(select(func.count(Group.id)).where(Group.is_active.is_(True)))
    ).scalar_one()
    total_members = (await session.execute(select(func.count(GroupMember.id)))).scalar_one()
    total_messages = (await session.execute(select(func.count(Message.id)))).scalar_one()
    total_facts = (await session.execute(select(func.count(MemberFact.id)))).scalar_one()
    total_episodes = (await session.execute(select(func.count(MemoryEpisode.id)))).scalar_one()

    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    row_24h = (
        await session.execute(
            select(
                func.coalesce(func.sum(DecisionLog.prompt_tokens + DecisionLog.completion_tokens), 0),
                func.coalesce(func.sum(DecisionLog.cost_usd), 0.0),
            ).where(DecisionLog.created_at >= day_ago)
        )
    ).one()

    row_total = (
        await session.execute(
            select(
                func.coalesce(func.sum(DecisionLog.prompt_tokens + DecisionLog.completion_tokens), 0),
                func.coalesce(func.sum(DecisionLog.cost_usd), 0.0),
            )
        )
    ).one()

    cfg = await session.get(SystemConfig, 1)

    return DashboardStatsResponse(
        total_groups=int(total_groups),
        active_groups=int(active_groups),
        total_members=int(total_members),
        total_messages=int(total_messages),
        total_facts=int(total_facts),
        total_episodes=int(total_episodes),
        tokens_24h=int(row_24h[0]),
        cost_24h_usd=round(float(row_24h[1]), 4),
        tokens_total=int(row_total[0]),
        cost_total_usd=round(float(row_total[1]), 4),
        bot_online=_is_bot_online(cfg.bot_last_heartbeat if cfg else None),
        bot_username=cfg.bot_username if cfg else None,
    )


@router.get("/settings/llm", response_model=SystemConfigResponse)
async def get_llm_settings(session: AsyncSession = Depends(get_db)) -> SystemConfigResponse:
    cfg = await session.get(SystemConfig, 1)
    if not cfg:
        cfg = SystemConfig(id=1)
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)

    return SystemConfigResponse(
        llm_base_url=cfg.llm_base_url,
        llm_api_key_masked=_mask_key(cfg.llm_api_key),
        has_llm_api_key=bool(cfg.llm_api_key),
        llm_fast_model=cfg.llm_fast_model,
        llm_smart_model=cfg.llm_smart_model,
        embedding_base_url=cfg.embedding_base_url,
        embedding_api_key_masked=_mask_key(cfg.embedding_api_key),
        has_embedding_api_key=bool(cfg.embedding_api_key),
        embedding_model=cfg.embedding_model,
        bot_telegram_id=cfg.bot_telegram_id,
        bot_username=cfg.bot_username,
        bot_display_name=cfg.bot_display_name,
        bot_online=_is_bot_online(cfg.bot_last_heartbeat),
        bot_last_heartbeat=cfg.bot_last_heartbeat,
    )


@router.put("/settings/llm", response_model=SystemConfigResponse)
async def update_llm_settings(
    payload: SystemConfigUpdate,
    session: AsyncSession = Depends(get_db),
) -> SystemConfigResponse:
    cfg = await session.get(SystemConfig, 1)
    if not cfg:
        cfg = SystemConfig(id=1)
        session.add(cfg)

    cfg.llm_base_url = payload.llm_base_url.strip()
    if payload.llm_api_key is not None and payload.llm_api_key.strip() and "..." not in payload.llm_api_key:
        cfg.llm_api_key = payload.llm_api_key.strip()
        if not cfg.embedding_api_key:
            cfg.embedding_api_key = cfg.llm_api_key

    cfg.llm_fast_model = payload.llm_fast_model.strip()
    cfg.llm_smart_model = payload.llm_smart_model.strip()
    cfg.embedding_base_url = payload.embedding_base_url.strip() or cfg.llm_base_url
    if (
        payload.embedding_api_key is not None
        and payload.embedding_api_key.strip()
        and "..." not in payload.embedding_api_key
    ):
        cfg.embedding_api_key = payload.embedding_api_key.strip()
    cfg.embedding_model = payload.embedding_model.strip()

    await session.commit()
    await session.refresh(cfg)
    invalidate_llm_config_cache()
    return await get_llm_settings(session)


@router.post("/settings/llm/test")
async def test_llm_settings(session: AsyncSession = Depends(get_db)) -> dict:
    return await test_llm_connectivity(session)


# ==========================================
# Persona Templates CRUD
# ==========================================
@router.get("/personas", response_model=list[PersonaTemplateOut])
async def list_personas(session: AsyncSession = Depends(get_db)) -> list[PersonaTemplate]:
    stmt = select(PersonaTemplate).order_by(PersonaTemplate.is_default.desc(), PersonaTemplate.created_at.asc())
    return list((await session.execute(stmt)).scalars().all())


@router.post("/personas", response_model=PersonaTemplateOut)
async def create_persona(
    payload: PersonaTemplateCreate,
    session: AsyncSession = Depends(get_db),
) -> PersonaTemplate:
    if payload.is_default:
        await session.execute(update(PersonaTemplate).values(is_default=False))

    persona = PersonaTemplate(**payload.model_dump())
    session.add(persona)
    await session.commit()
    await session.refresh(persona)
    return persona


@router.put("/personas/{persona_id}", response_model=PersonaTemplateOut)
async def update_persona(
    persona_id: uuid.UUID,
    payload: PersonaTemplateUpdate,
    session: AsyncSession = Depends(get_db),
) -> PersonaTemplate:
    persona = await session.get(PersonaTemplate, persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Шаблон персоны не найден")

    if payload.is_default and not persona.is_default:
        await session.execute(update(PersonaTemplate).values(is_default=False))

    for k, v in payload.model_dump().items():
        setattr(persona, k, v)

    await session.commit()
    await session.refresh(persona)
    return persona


@router.delete("/personas/{persona_id}")
async def delete_persona(persona_id: uuid.UUID, session: AsyncSession = Depends(get_db)) -> dict:
    persona = await session.get(PersonaTemplate, persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Шаблон персоны не найден")
    await session.delete(persona)
    await session.commit()
    return {"deleted": True}

