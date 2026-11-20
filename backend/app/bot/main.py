import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatAction
from aiogram.types import ReactionTypeEmoji, ReplyParameters

from app.bot.debouncer import pop_due_debounced_groups
from app.bot.handlers import router as group_router
from app.config import get_settings
from app.database import AsyncSessionLocal, init_db
from app.models import DecisionLog, Message, SystemConfig
from app.redis_client import BOT_COMMANDS_CHANNEL, get_redis, publish_group_event
from app.services.decision_service import evaluate_group_batch_and_decide, sanitize_poll_params
from app.services.memory_service import ensure_group_initialized, resolve_effective_persona

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot_gateway")
settings = get_settings()


async def send_and_record_bot_message(
    bot: Bot,
    group_id: int,
    text: str,
    reply_to_tg_msg_id: Optional[int] = None,
    simulate_typing: bool = True,
) -> Optional[Message]:
    """Имитирует живой набор текста, отправляет сообщение в Telegram и сохраняет его в БД."""
    if simulate_typing:
        typing_duration = min(max(len(text) * 0.04, 1.2), 6.5)
        try:
            await bot.send_chat_action(chat_id=group_id, action=ChatAction.TYPING)
        except Exception as exc:
            logger.debug("send_chat_action failed: %s", exc)
        await asyncio.sleep(typing_duration)

    try:
        reply_params = (
            ReplyParameters(message_id=reply_to_tg_msg_id, allow_sending_without_reply=True)
            if reply_to_tg_msg_id is not None
            else None
        )
        sent = await bot.send_message(
            chat_id=group_id,
            text=text,
            reply_parameters=reply_params,
        )
    except Exception as exc:
        logger.error("Failed to send message to group %s: %s", group_id, exc)
        return None

    now_utc = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        group = await ensure_group_initialized(session, group_id)
        persona = await resolve_effective_persona(session, group)
        sys_cfg = await session.get(SystemConfig, 1)

        db_msg = Message(
            group_id=group_id,
            telegram_message_id=sent.message_id,
            member_id=None,
            sender_name=persona.get("display_name") or (sys_cfg.bot_display_name if sys_cfg else "Бот") or "Бот",
            sender_username=sys_cfg.bot_username if sys_cfg else None,
            is_from_bot=True,
            reply_to_tg_msg_id=reply_to_tg_msg_id,
            text_content=text,
            reactions={},
            is_imported=False,
            is_processed_for_memory=False,
            created_at=now_utc,
        )
        session.add(db_msg)
        group.last_bot_message_at = now_utc
        group.last_message_at = now_utc
        group.unanalyzed_msg_count = (group.unanalyzed_msg_count or 0) + 1
        await session.commit()

        await publish_group_event(
            group_id,
            "new_message",
            {
                "id": db_msg.id,
                "telegram_message_id": db_msg.telegram_message_id,
                "sender_name": db_msg.sender_name,
                "sender_username": db_msg.sender_username,
                "is_from_bot": True,
                "reply_to_tg_msg_id": db_msg.reply_to_tg_msg_id,
                "text_content": db_msg.text_content,
                "reactions": {},
                "created_at": db_msg.created_at.isoformat(),
            },
        )
        return db_msg


async def send_and_record_bot_poll(
    bot: Bot,
    group_id: int,
    poll_data: dict[str, Any],
    reply_to_tg_msg_id: Optional[int] = None,
    simulate_typing: bool = True,
) -> Optional[Message]:
    """Отправляет опрос/викторину в Telegram со всеми параметрами и сохраняет запись в БД."""
    clean = sanitize_poll_params(poll_data)
    effective_reply_id = reply_to_tg_msg_id if reply_to_tg_msg_id is not None else clean.get("reply_to_tg_msg_id")

    if simulate_typing:
        try:
            await bot.send_chat_action(chat_id=group_id, action=ChatAction.TYPING)
        except Exception as exc:
            logger.debug("send_chat_action failed: %s", exc)
        await asyncio.sleep(1.5)

    try:
        reply_params = (
            ReplyParameters(message_id=effective_reply_id, allow_sending_without_reply=True)
            if effective_reply_id is not None
            else None
        )
        send_kwargs: dict[str, Any] = {
            "chat_id": group_id,
            "question": clean["question"],
            "options": clean["options"],
            "is_anonymous": clean["is_anonymous"],
            "type": clean["type"],
            "allows_multiple_answers": clean["allows_multiple_answers"],
            "is_closed": clean["is_closed"],
            "protect_content": clean["protect_content"],
            "reply_parameters": reply_params,
        }
        if clean["type"] == "quiz":
            send_kwargs["correct_option_id"] = clean["correct_option_id"]
            if clean["explanation"]:
                send_kwargs["explanation"] = clean["explanation"]
        if clean["open_period"] is not None:
            send_kwargs["open_period"] = clean["open_period"]

        sent = await bot.send_poll(**send_kwargs)
    except Exception as exc:
        logger.error("Failed to send poll to group %s: %s", group_id, exc)
        return None

    poll_tag = "ВИКТОРИНА" if clean["type"] == "quiz" else "ОПРОС"
    text_repr = f"[{poll_tag}: {clean['question']} | Варианты: {', '.join(clean['options'])}]"
    now_utc = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        group = await ensure_group_initialized(session, group_id)
        persona = await resolve_effective_persona(session, group)
        sys_cfg = await session.get(SystemConfig, 1)

        db_msg = Message(
            group_id=group_id,
            telegram_message_id=sent.message_id,
            member_id=None,
            sender_name=persona.get("display_name") or (sys_cfg.bot_display_name if sys_cfg else "Бот") or "Бот",
            sender_username=sys_cfg.bot_username if sys_cfg else None,
            is_from_bot=True,
            reply_to_tg_msg_id=effective_reply_id,
            text_content=text_repr,
            reactions={},
            is_imported=False,
            is_processed_for_memory=False,
            created_at=now_utc,
        )
        session.add(db_msg)
        group.last_bot_message_at = now_utc
        group.last_message_at = now_utc
        group.unanalyzed_msg_count = (group.unanalyzed_msg_count or 0) + 1
        await session.commit()

        await publish_group_event(
            group_id,
            "new_message",
            {
                "id": db_msg.id,
                "telegram_message_id": db_msg.telegram_message_id,
                "sender_name": db_msg.sender_name,
                "sender_username": db_msg.sender_username,
                "is_from_bot": True,
                "reply_to_tg_msg_id": db_msg.reply_to_tg_msg_id,
                "text_content": db_msg.text_content,
                "reactions": {},
                "created_at": db_msg.created_at.isoformat(),
            },
        )
        return db_msg


async def _process_single_debounced_group(bot: Bot, group_id: int, trigger_ids: list[int]) -> None:
    try:
        async with AsyncSessionLocal() as session:
            decision = await evaluate_group_batch_and_decide(session, group_id, trigger_ids)

        if decision.action == "react" and decision.emoji and decision.target_tg_msg_id:
            await asyncio.sleep(0.8)
            try:
                await bot.set_message_reaction(
                    chat_id=group_id,
                    message_id=decision.target_tg_msg_id,
                    reaction=[ReactionTypeEmoji(emoji=decision.emoji)],
                )
            except Exception as exc:
                logger.warning("Failed to set emoji reaction in group %s: %s", group_id, exc)

        elif decision.action in ("reply", "message") and decision.text:
            await send_and_record_bot_message(
                bot=bot,
                group_id=group_id,
                text=decision.text,
                reply_to_tg_msg_id=decision.target_tg_msg_id if decision.action == "reply" else None,
                simulate_typing=decision.simulate_typing,
            )
        elif decision.action == "poll" and decision.poll_data:
            await send_and_record_bot_poll(
                bot=bot,
                group_id=group_id,
                poll_data=decision.poll_data,
                reply_to_tg_msg_id=decision.target_tg_msg_id,
                simulate_typing=decision.simulate_typing,
            )
    except Exception as exc:
        logger.exception("Error processing debounced group %s: %s", group_id, exc)
        try:
            async with AsyncSessionLocal() as err_session:
                err_log = DecisionLog(
                    group_id=group_id,
                    trigger_tg_msg_ids=trigger_ids,
                    decision_type="error",
                    router_reasoning=f"Ошибка при обращении к ИИ: {exc}",
                    action_payload={},
                    retrieved_context_meta={},
                    model_used="error",
                )
                err_session.add(err_log)
                await err_session.commit()
                await publish_group_event(
                    group_id,
                    "decision_logged",
                    {
                        "id": str(err_log.id),
                        "decision_type": "error",
                        "router_reasoning": err_log.router_reasoning,
                        "action_payload": {},
                        "model_used": "error",
                        "cost_usd": 0.0,
                        "created_at": err_log.created_at.isoformat(),
                    },
                )
        except Exception:
            pass


async def debounce_watcher_loop(bot: Bot) -> None:
    while True:
        try:
            due_items = await pop_due_debounced_groups()
            for group_id, msg_ids in due_items:
                asyncio.create_task(_process_single_debounced_group(bot, group_id, msg_ids))
        except Exception as exc:
            logger.error("Debounce watcher error: %s", exc)
        await asyncio.sleep(0.45)


async def redis_commands_listener_loop(bot: Bot) -> None:
    """Слушает команды из backend-api (ручная отправка сообщений админом) и memory-worker (инициатива)."""
    while True:
        try:
            r = get_redis()
            pubsub = r.pubsub()
            await pubsub.subscribe(BOT_COMMANDS_CHANNEL)
            logger.info("Subscribed to Redis command channel: %s", BOT_COMMANDS_CHANNEL)

            async for raw in pubsub.listen():
                if raw.get("type") != "message":
                    continue
                data: dict[str, Any] = json.loads(raw["data"])
                cmd = data.get("command")
                if cmd == "send_message":
                    gid = int(data["group_id"])
                    txt = str(data["text"])
                    reply_id = data.get("reply_to_tg_msg_id")
                    sim_typing = bool(data.get("simulate_typing", False))
                    source = str(data.get("source") or "manual")

                    await send_and_record_bot_message(
                        bot=bot,
                        group_id=gid,
                        text=txt,
                        reply_to_tg_msg_id=int(reply_id) if reply_id else None,
                        simulate_typing=sim_typing,
                    )
                    if source == "manual":
                        async with AsyncSessionLocal() as session:
                            log_entry = DecisionLog(
                                group_id=gid,
                                trigger_tg_msg_ids=[int(reply_id)] if reply_id else [],
                                decision_type="manual",
                                router_reasoning="Сообщение отправлено вручную администратором из веб-панели.",
                                action_payload={"text": txt, "target_tg_msg_id": reply_id},
                                retrieved_context_meta={},
                                model_used="admin-manual",
                            )
                            session.add(log_entry)
                            await session.commit()
                elif cmd == "send_poll":
                    gid = int(data["group_id"])
                    poll_payload = data.get("poll") or {}
                    reply_id = data.get("reply_to_tg_msg_id")
                    sim_typing = bool(data.get("simulate_typing", False))
                    source = str(data.get("source") or "manual")

                    sent_msg = await send_and_record_bot_poll(
                        bot=bot,
                        group_id=gid,
                        poll_data=poll_payload,
                        reply_to_tg_msg_id=int(reply_id) if reply_id else None,
                        simulate_typing=sim_typing,
                    )
                    if source == "manual" and sent_msg is not None:
                        async with AsyncSessionLocal() as session:
                            log_entry = DecisionLog(
                                group_id=gid,
                                trigger_tg_msg_ids=[int(reply_id)] if reply_id else [],
                                decision_type="poll",
                                router_reasoning="Опрос создан вручную администратором из веб-панели.",
                                action_payload={"poll": poll_payload, "target_tg_msg_id": reply_id},
                                retrieved_context_meta={},
                                model_used="admin-manual",
                            )
                            session.add(log_entry)
                            await session.commit()
                            await publish_group_event(
                                gid,
                                "decision_logged",
                                {
                                    "id": str(log_entry.id),
                                    "decision_type": "poll",
                                    "router_reasoning": log_entry.router_reasoning,
                                    "action_payload": log_entry.action_payload,
                                    "model_used": "admin-manual",
                                    "cost_usd": 0.0,
                                    "created_at": log_entry.created_at.isoformat(),
                                },
                            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Redis command listener reconnecting after error: %s", exc)
            await asyncio.sleep(3.0)


async def heartbeat_loop(bot: Bot) -> None:
    while True:
        try:
            me = await bot.get_me()
            async with AsyncSessionLocal() as session:
                cfg = await session.get(SystemConfig, 1)
                if cfg:
                    cfg.bot_telegram_id = me.id
                    cfg.bot_username = me.username
                    cfg.bot_display_name = me.first_name
                    cfg.bot_last_heartbeat = datetime.now(timezone.utc)
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Bot heartbeat failed: %s", exc)
        await asyncio.sleep(20.0)


async def main() -> None:
    await init_db()

    while True:
        token = (settings.telegram_bot_token or "").strip()
        if not token or "replace_with_your_bot_token" in token or ":" not in token:
            logger.warning(
                "TELEGRAM_BOT_TOKEN не задан или содержит шаблонное значение. "
                "Укажите реальный токен в .env и перезапустите bot-gateway."
            )
            await asyncio.sleep(15.0)
            continue

        bot = Bot(token=token)
        try:
            me = await bot.get_me()
            logger.info("Connected to Telegram as @%s (id=%s)", me.username, me.id)
            break
        except Exception as exc:
            logger.error("Не удалось авторизоваться в Telegram API (%s). Повтор через 15 сек...", exc)
            await bot.session.close()
            await asyncio.sleep(15.0)

    dp = Dispatcher()
    dp.include_router(group_router)

    bg_tasks = [
        asyncio.create_task(heartbeat_loop(bot)),
        asyncio.create_task(debounce_watcher_loop(bot)),
        asyncio.create_task(redis_commands_listener_loop(bot)),
    ]

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(
            bot,
            allowed_updates=["message", "edited_message", "message_reaction", "my_chat_member"],
        )
    finally:
        for t in bg_tasks:
            t.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
