import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_db
from app.models import DecisionLog, Message
from app.redis_client import GROUP_EVENTS_CHANNEL_PREFIX, get_redis
from app.schemas import DecisionLogOut, MessageOut

router = APIRouter(
    prefix="/api/groups/{group_id}",
    tags=["logs"],
    dependencies=[Depends(require_admin)],
)


@router.get("/messages", response_model=list[MessageOut])
async def list_recent_messages(
    group_id: int,
    limit: int = Query(default=80, ge=1, le=300),
    session: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    stmt = (
        select(Message)
        .where(Message.group_id == group_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = list(reversed((await session.execute(stmt)).scalars().all()))
    return [MessageOut.model_validate(m) for m in rows]


@router.get("/decisions", response_model=list[DecisionLogOut])
async def list_decision_logs(
    group_id: int,
    limit: int = Query(default=60, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> list[DecisionLogOut]:
    stmt = (
        select(DecisionLog)
        .where(DecisionLog.group_id == group_id)
        .order_by(DecisionLog.created_at.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return [DecisionLogOut.model_validate(d) for d in rows]


@router.get("/stream")
async def stream_group_events(group_id: int, request: Request) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        r = get_redis()
        pubsub = r.pubsub()
        channel = f"{GROUP_EVENTS_CHANNEL_PREFIX}{group_id}"
        await pubsub.subscribe(channel)
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if msg and msg.get("type") == "message":
                    yield f"data: {msg['data']}\n\n"
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.05)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

