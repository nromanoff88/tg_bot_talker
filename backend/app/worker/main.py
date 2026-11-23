import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from arq import cron
from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal, init_db
from app.models import Group
from app.redis_client import parse_arq_redis_settings
from app.services.external_context_service import ensure_external_context_fresh
from app.services.memory_service import analyze_group_memory_batch
from app.services.proactive_service import check_all_groups_for_proactive_initiative

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("memory_worker")
settings = get_settings()


async def startup(ctx: dict[str, Any]) -> None:
    await init_db()
    logger.info("Memory worker initialized and ready.")


async def analyze_group_memory_job(ctx: dict[str, Any], group_id: int, force: bool = False) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        result = await analyze_group_memory_batch(session, group_id=group_id, force=force)
        logger.info("Memory batch result for group %s: %s", group_id, result)
        return result


async def process_all_pending_memory_job(
    ctx: dict[str, Any],
    group_id: int,
    max_batches: int = 25,
) -> dict[str, Any]:
    """Последовательно разбирает большие очереди непроанализированных сообщений (например, после импорта истории)."""
    total_processed = 0
    batches_run = 0
    last_remaining = 0
    consecutive_errors = 0

    for _ in range(max_batches):
        try:
            async with AsyncSessionLocal() as session:
                res = await analyze_group_memory_batch(session, group_id=group_id, force=True, batch_limit=35)
        except Exception as exc:
            consecutive_errors += 1
            logger.exception("Error in memory batch for group %s (attempt %s): %s", group_id, consecutive_errors, exc)
            if consecutive_errors >= 3:
                break
            await asyncio.sleep(3.0)
            continue

        status = res.get("status")
        if status == "already_running":
            consecutive_errors += 1
            if consecutive_errors >= 3:
                break
            await asyncio.sleep(3.0)
            continue

        consecutive_errors = 0
        if status != "ok" or int(res.get("processed") or 0) == 0:
            last_remaining = int(res.get("remaining") or 0)
            break

        total_processed += int(res["processed"])
        batches_run += 1
        last_remaining = int(res.get("remaining") or 0)
        if last_remaining == 0:
            break
        await asyncio.sleep(1.0)

    if last_remaining > 0 and ctx.get("redis") is not None and batches_run > 0:
        try:
            await ctx["redis"].enqueue_job("process_all_pending_memory_job", group_id, max_batches=max_batches)
            logger.info(
                "Enqueued continuation memory job for group %s (%s messages remaining)",
                group_id,
                last_remaining,
            )
        except Exception as exc:
            logger.warning("Failed to enqueue continuation memory job for group %s: %s", group_id, exc)

    logger.info(
        "Bulk memory processing finished for group %s: %s messages in %s batches (remaining=%s)",
        group_id,
        total_processed,
        batches_run,
        last_remaining,
    )
    return {
        "group_id": group_id,
        "total_processed": total_processed,
        "batches_run": batches_run,
        "remaining": last_remaining,
    }


async def periodic_maintenance_cron(ctx: dict[str, Any]) -> None:
    """
    Каждые 5 минут:
    1. Запускает анализ памяти в затихших чатах (где есть непрочитанные сообщения и тишина >= 30 мин).
    2. Проверяет условия для спонтанной инициативы бота в группах.
    """
    now_utc = datetime.now(timezone.utc)
    idle_cutoff = now_utc - timedelta(minutes=30)

    async with AsyncSessionLocal() as session:
        # 1. Дочитываем хвосты сообщений в затихших группах
        idle_groups_stmt = select(Group).where(
            Group.is_active.is_(True),
            Group.unanalyzed_msg_count > 0,
            Group.last_message_at.is_not(None),
            Group.last_message_at <= idle_cutoff,
        )
        idle_groups = list((await session.execute(idle_groups_stmt)).scalars().all())
        for g in idle_groups:
            try:
                await analyze_group_memory_batch(session, group_id=g.id, force=True)
            except Exception as exc:
                logger.warning("Idle memory flush failed for group %s: %s", g.id, exc)

        # 2. Проверяем спонтанную инициативу бота
        triggered = await check_all_groups_for_proactive_initiative(session)
        if triggered:
            logger.info("Proactive initiative triggered in %s group(s).", triggered)

        # 3. Упреждающе обновляем кэш погоды и новостей из Telegram-каналов
        try:
            await ensure_external_context_fresh(session)
        except Exception as exc:
            logger.warning("Periodic external context refresh failed: %s", exc)


class WorkerSettings:
    redis_settings = parse_arq_redis_settings(settings.redis_url)
    on_startup = startup
    functions = [analyze_group_memory_job, process_all_pending_memory_job]
    cron_jobs = [
        cron(periodic_maintenance_cron, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55})
    ]
    max_jobs = 4
    job_timeout = 600

