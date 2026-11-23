import asyncio
import time
from typing import Optional

from app.redis_client import get_redis

DUE_ZSET_KEY = "bot_talker:debounce:due_groups"
MSGS_LIST_PREFIX = "bot_talker:debounce:msgs:"
FIRST_SEEN_PREFIX = "bot_talker:debounce:first_seen:"
INFLIGHT_PREFIX = "bot_talker:debounce:inflight:"
INFLIGHT_TTL_SEC = 120


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
    """
    Возвращает список (group_id, [tg_message_ids]), для которых пауза ожидания завершилась.
    Если по группе уже идёт генерация/отправка ответа (in-flight lock), оставляет сообщения в очереди.
    """
    r = get_redis()
    now = time.time()

    due_members: list[str] = await r.zrangebyscore(DUE_ZSET_KEY, "-inf", now)
    if not due_members:
        return []

    ready: list[tuple[int, list[int]]] = []
    for gid_str in due_members:
        group_id = int(gid_str)
        inflight_key = f"{INFLIGHT_PREFIX}{group_id}"
        acquired = await r.set(inflight_key, "1", ex=INFLIGHT_TTL_SEC, nx=True)
        if not acquired:
            continue

        removed = await r.zrem(DUE_ZSET_KEY, gid_str)
        if not removed:
            await r.delete(inflight_key)
            continue

        msgs_key = f"{MSGS_LIST_PREFIX}{group_id}"
        first_key = f"{FIRST_SEEN_PREFIX}{group_id}"

        pipe = r.pipeline(transaction=True)
        pipe.lrange(msgs_key, 0, -1)
        pipe.delete(msgs_key, first_key)
        raw_ids, _ = await pipe.execute()

        msg_ids = [int(x) for x in raw_ids if str(x).lstrip("-").isdigit()]
        if msg_ids:
            ready.append((group_id, msg_ids))
        else:
            await r.delete(inflight_key)

    return ready


async def drain_newly_arrived_messages(group_id: int, max_wait_extra: float = 2.5) -> list[int]:
    """
    Проверяет, не появились ли новые сообщения в буфере группы за время генерации ответа LLM.
    Если появились — кратко дожидается хвоста дебаунса и атомарно забирает их для объединения.
    """
    r = get_redis()
    msgs_key = f"{MSGS_LIST_PREFIX}{group_id}"
    first_key = f"{FIRST_SEEN_PREFIX}{group_id}"

    if await r.llen(msgs_key) == 0:
        return []

    due_score = await r.zscore(DUE_ZSET_KEY, str(group_id))
    if due_score is not None:
        remaining = float(due_score) - time.time()
        if 0 < remaining <= max_wait_extra:
            await asyncio.sleep(remaining)

    pipe = r.pipeline(transaction=True)
    pipe.zrem(DUE_ZSET_KEY, str(group_id))
    pipe.lrange(msgs_key, 0, -1)
    pipe.delete(msgs_key, first_key)
    _, raw_ids, _ = await pipe.execute()

    return [int(x) for x in raw_ids if str(x).lstrip("-").isdigit()]


async def release_group_inflight(group_id: int) -> None:
    """Снимает блокировку активной обработки по группе после завершения генерации и отправки."""
    await get_redis().delete(f"{INFLIGHT_PREFIX}{group_id}")

