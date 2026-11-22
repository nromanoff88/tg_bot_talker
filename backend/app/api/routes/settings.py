import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    PromptPreviewRequest,
    PromptPreviewResponse,
    PromptTemplateSpecOut,
    PromptTemplatesUpdateRequest,
    PromptVariableDoc,
    SystemConfigResponse,
    SystemConfigUpdate,
)
from app.services.llm_service import invalidate_llm_config_cache, test_llm_connectivity
from app.services.memory_service import (
    collect_persona_prompt_vars,
    ensure_group_initialized,
    format_current_datetime_context,
    format_message_local_ts,
    resolve_effective_persona,
)
from app.services.prompt_service import (
    PROMPT_SPECS,
    PROMPT_SPECS_BY_KEY,
    get_prompt_overrides,
    invalidate_prompt_templates_cache,
    render_prompt,
    validate_jinja2_template,
)

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


def _serialize_prompt_specs(overrides: dict[str, str]) -> list[PromptTemplateSpecOut]:
    out: list[PromptTemplateSpecOut] = []
    for spec in PROMPT_SPECS:
        custom = overrides.get(spec.key)
        is_custom = bool(custom and custom.strip() and custom.strip() != spec.default_template.strip())
        out.append(
            PromptTemplateSpecOut(
                key=spec.key,
                title=spec.title,
                description=spec.description,
                template=custom if is_custom else spec.default_template,
                default_template=spec.default_template,
                is_custom=is_custom,
                variables=[PromptVariableDoc(name=n, description=d) for n, d in spec.variables],
            )
        )
    return out


@router.get("/settings/prompts", response_model=list[PromptTemplateSpecOut])
async def get_prompt_templates(session: AsyncSession = Depends(get_db)) -> list[PromptTemplateSpecOut]:
    overrides = await get_prompt_overrides(session)
    return _serialize_prompt_specs(overrides)


@router.put("/settings/prompts", response_model=list[PromptTemplateSpecOut])
async def update_prompt_templates(
    payload: PromptTemplatesUpdateRequest,
    session: AsyncSession = Depends(get_db),
) -> list[PromptTemplateSpecOut]:
    cfg = await session.get(SystemConfig, 1)
    if not cfg:
        cfg = SystemConfig(id=1)
        session.add(cfg)

    next_overrides: dict[str, str] = dict(cfg.prompt_templates or {})
    for key, raw_val in payload.templates.items():
        spec = PROMPT_SPECS_BY_KEY.get(key)
        if not spec:
            raise HTTPException(status_code=400, detail=f"Неизвестный ключ шаблона: {key}")

        cleaned = (raw_val or "").strip()
        if not cleaned or cleaned == spec.default_template.strip():
            next_overrides.pop(key, None)
            continue

        try:
            validate_jinja2_template(cleaned)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{spec.title}: {exc}") from exc

        next_overrides[key] = cleaned

    cfg.prompt_templates = next_overrides
    await session.commit()
    invalidate_prompt_templates_cache()
    overrides = await get_prompt_overrides(session)
    return _serialize_prompt_specs(overrides)


@router.post("/settings/prompts/preview", response_model=PromptPreviewResponse)
async def preview_prompt_template(
    payload: PromptPreviewRequest,
    session: AsyncSession = Depends(get_db),
) -> PromptPreviewResponse:
    if payload.key not in PROMPT_SPECS_BY_KEY:
        raise HTTPException(status_code=400, detail=f"Неизвестный ключ шаблона: {payload.key}")

    try:
        validate_jinja2_template(payload.template)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    group: Group | None = None
    if payload.group_id is not None:
        group = await ensure_group_initialized(session, payload.group_id)
    else:
        first_group_id = (
            await session.execute(select(Group.id).order_by(Group.is_active.desc(), Group.last_message_at.desc().nullslast()).limit(1))
        ).scalar_one_or_none()
        if first_group_id is not None:
            group = await ensure_group_initialized(session, int(first_group_id))

    if group is not None:
        recent_msgs = list(
            reversed(
                (
                    await session.execute(
                        select(Message)
                        .where(Message.group_id == group.id)
                        .order_by(Message.created_at.desc())
                        .limit(12)
                    )
                )
                .scalars()
                .all()
            )
        )
        sample_eps = [
            (ep, 0.85)
            for ep in (
                await session.execute(
                    select(MemoryEpisode)
                    .where(MemoryEpisode.group_id == group.id)
                    .order_by(MemoryEpisode.happened_at.desc())
                    .limit(4)
                )
            )
            .scalars()
            .all()
        ]
        prompt_vars, _ = await collect_persona_prompt_vars(session, group, recent_msgs, sample_eps)
        tz_name = group.settings.timezone if group.settings else "Europe/Moscow"
        now_str = prompt_vars["current_datetime"]
        persona = prompt_vars["persona"]
        chat_lines = [
            f"#{m.telegram_message_id} [{format_message_local_ts(m.created_at, tz_name)}] "
            f"{persona['display_name'] + ' (ТЫ)' if m.is_from_bot else m.sender_name}: {m.text_content}"
            for m in recent_msgs
        ]
        top_members = list(
            (
                await session.execute(
                    select(GroupMember)
                    .options(selectinload(GroupMember.facts))
                    .where(GroupMember.group_id == group.id)
                    .order_by(GroupMember.message_count.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        member_hooks = [
            f"- {m.display_name}: {m.character_summary} | Факты: {'; '.join(f.fact_text for f in m.facts[:4])}"
            for m in top_members
            if m.character_summary or m.facts
        ]
        brief_authors = "\n".join(
            f"- {m.display_name}: {m.character_summary[:140] or 'участник чата'}" for m in top_members
        )
        last_msg_id = recent_msgs[-1].telegram_message_id if recent_msgs else 101
        ctx: dict = {
            **prompt_vars,
            "chat_history": "\n".join(chat_lines) or "#101 [12:00] Участник: Привет чат!",
            "chat_messages": "\n".join(chat_lines) or "#101 [12:00] Участник: Привет чат!",
            "reasoning": "Пример: к боту обратились или в чате обсуждают близкую тему.",
            "action": "reply",
            "target_msg_id": last_msg_id,
            "reply_instruction": f"Напиши естественный живой ответ реплаем на сообщение #{last_msg_id}.",
            "chattiness_level": group.settings.chattiness_level if group.settings else 0.35,
            "is_direct_ping": True,
            "reactions_enabled": group.settings.reactions_enabled if group.settings else True,
            "allowed_emojis": group.settings.allowed_emojis if group.settings else ["👍", "🔥"],
            "brief_authors": brief_authors or "нет данных",
            "silence_hours": "4.5",
            "silence_minutes": "45",
            "recent_preview": "\n".join(chat_lines[-5:]) or "Чат пока пуст.",
            "member_hooks": "\n".join(member_hooks) or "Пока мало фактов об участниках.",
        }
        _ = resolve_effective_persona  # ensure import used cleanly if needed
    else:
        now_str = format_current_datetime_context("Europe/Moscow")
        ctx = {
            "group_title": "Тестовый чат",
            "group_id": -100123456789,
            "current_datetime": now_str,
            "persona": {
                "display_name": "Макс",
                "backstory": "Обычный живой участник чата.",
                "communication_style": "Пиши коротко и живо.",
                "taboos": "Никаких фраз «Как ИИ».",
                "example_phrases": ["звучит как план!"],
            },
            "examples_block": "- «звучит как план!»",
            "group_summary": "Обычный живой групповой чат друзей.",
            "active_topics": ["планы на выходные"],
            "active_topics_str": "планы на выходные",
            "jokes_and_rules": [],
            "jokes_block": "- пока не зафиксированы",
            "members": [],
            "members_block": "Пока мало данных об участниках.",
            "episodes": [],
            "episodes_block": "Нет специфичных старых эпизодов.",
            "chat_history": "#1 [18:00] Никита: Всем привет!",
            "chat_messages": "#1 [18:00] Никита: Всем привет!",
            "reasoning": "Пример мотивации роутера.",
            "action": "reply",
            "target_msg_id": 1,
            "reply_instruction": "Напиши естественный живой ответ реплаем на сообщение #1.",
            "chattiness_level": 0.35,
            "is_direct_ping": True,
            "reactions_enabled": True,
            "allowed_emojis": ["👍", "🔥"],
            "brief_authors": "- Никита: участник чата",
            "silence_hours": "5.0",
            "silence_minutes": "60",
            "recent_preview": "Никита: Всем привет!",
            "member_hooks": "- Никита: любит лыжи",
        }

    try:
        rendered = await render_prompt(session, payload.key, ctx, custom_template=payload.template)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Ошибка рендера Jinja2: {exc}") from exc

    spec = PROMPT_SPECS_BY_KEY[payload.key]
    variable_values: dict[str, str] = {}
    for var_name, _ in spec.variables:
        val: object = ctx
        for part in var_name.split("."):
            if isinstance(val, dict):
                val = val.get(part)
            else:
                val = getattr(val, part, None)
        if val is None:
            variable_values[var_name] = ""
        elif isinstance(val, (dict, list)):
            variable_values[var_name] = json.dumps(val, ensure_ascii=False, indent=2)
        else:
            variable_values[var_name] = str(val)

    return PromptPreviewResponse(rendered=rendered, variable_values=variable_values)


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

