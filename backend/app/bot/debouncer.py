import time
from typing import Optional

from app.redis_client import get_redis

DUE_ZSET_KEY = "bot_talker:debounce:due_groups"
MSGS_LIST_PREFIX = "bot_talker:debounce:msgs:"
FIRST_SEEN_PREFIX = "bot_talker:debounce:first_seen:"


async def schedule_group_debounce(group_id: int, tg_message_id: int, delay_seconds: float) -> None:
    """
    Добавляет сообщение в буфер ожидания конца мысли группы и сдвигает таймер ответа.
    Защита от бесконечного сдвига в очень активном чате: не ждём дольше max_wait = delay * 3.5.
    """
    r = get_redis()
    now = time.time()
    delay = max(1.0, min(30.0, float(delay_seconds)))

    msgs_key = f"{MSGS_LIST_PREFIX}{group_id}"
    first_key = f"{FIRST_SEEN_PREFIX}{group_id}"

    await r.rpush(msgs_key, str(tg_message_id))
    await r.expire(msgs_key, 3600)

    first_seen_str: Optional[str] = await r.get(first_key)
    if first_seen_str is None:
        first_seen = now
        await r.set(first_key, str(first_seen), ex=3600)
    else:
        first_seen = float(first_seen_str)

    max_due = first_seen + max(delay * 3.5, 12.0)
    target_due = min(now + delay, max_due)

    await r.zadd(DUE_ZSET_KEY, {str(group_id): target_due})


async def pop_due_debounced_groups() -> list[tuple[int, list[int]]]:
    """Возвращает список (group_id, [tg_message_ids]), для которых пауза ожидания завершилась."""
    r = get_redis()
    now = time.time()

    due_members: list[str] = await r.zrangebyscore(DUE_ZSET_KEY, "-inf", now)
    if not due_members:
        return []

    ready: list[tuple[int, list[int]]] = []
    for gid_str in due_members:
        removed = await r.zrem(DUE_ZSET_KEY, gid_str)
        if not removed:
            continue
        group_id = int(gid_str)
        msgs_key = f"{MSGS_LIST_PREFIX}{group_id}"
        first_key = f"{FIRST_SEEN_PREFIX}{group_id}"

        raw_ids = await r.lrange(msgs_key, 0, -1)
        await r.delete(msgs_key, first_key)

        msg_ids = [int(x) for x in raw_ids if str(x).lstrip("-").isdigit()]
        if msg_ids:
            ready.append((group_id, msg_ids))

    return ready

