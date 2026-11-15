import json
import logging
from typing import Any, Optional
from urllib.parse import urlparse

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

BOT_COMMANDS_CHANNEL = "bot_talker:bot_commands"
GROUP_EVENTS_CHANNEL_PREFIX = "bot_talker:group_events:"

_redis_client: Optional[aioredis.Redis] = None
_arq_pool: Optional[ArqRedis] = None


def parse_arq_redis_settings(redis_url: str) -> RedisSettings:
    parsed = urlparse(redis_url)
    db = 0
    if parsed.path and parsed.path.strip("/").isdigit():
        db = int(parsed.path.strip("/"))
    return RedisSettings(
        host=parsed.hostname or "redis",
        port=parsed.port or 6379,
        password=parsed.password,
        database=db,
    )


def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def get_arq_pool() -> ArqRedis:
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(parse_arq_redis_settings(settings.redis_url))
    return _arq_pool


async def publish_bot_command(payload: dict[str, Any]) -> None:
    """Публикует команду для bot-gateway (например, отправить сообщение или реакцию в группу)."""
    r = get_redis()
    await r.publish(BOT_COMMANDS_CHANNEL, json.dumps(payload, ensure_ascii=False))


async def publish_group_event(group_id: int, event_type: str, data: dict[str, Any]) -> None:
    """Публикует live-событие группы для SSE-ленты в админке."""
    r = get_redis()
    payload = {"type": event_type, "group_id": group_id, "data": data}
    await r.publish(f"{GROUP_EVENTS_CHANNEL_PREFIX}{group_id}", json.dumps(payload, ensure_ascii=False, default=str))

