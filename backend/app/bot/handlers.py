import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import ChatMemberUpdated, Message as TgMessage, MessageReactionUpdated, ReactionTypeEmoji
from sqlalchemy import func, select

from app.bot.debouncer import schedule_group_debounce
from app.database import AsyncSessionLocal
from app.models import GroupMember, Message, SystemConfig
from app.redis_client import get_arq_pool, publish_group_event
from app.services.import_service import SYNTHETIC_TG_ID_BASE
from app.services.memory_service import ensure_group_initialized

logger = logging.getLogger(__name__)
router = Router(name="group_observer")


@router.message(F.chat.type == "private")
async def handle_private_message(message: TgMessage) -> None:
    await message.answer(
        "Привет! Я работаю как живой участник в групповых чатах.\n"
        "Добавь меня в группу (убедись, что Privacy Mode отключён в @BotFather) "
        "и настраивай мою память и характер в веб-админке."
    )


@router.my_chat_member()
async def handle_my_chat_member(event: ChatMemberUpdated) -> None:
    if event.chat.type not in ("group", "supergroup"):
        return

    new_status = event.new_chat_member.status
    async with AsyncSessionLocal() as session:
        group = await ensure_group_initialized(
            session=session,
            group_id=event.chat.id,
            title=event.chat.title or f"Group {event.chat.id}",
            chat_type=event.chat.type,
        )
        if new_status in ("member", "administrator", "creator"):
            group.is_active = True
            logger.info("Bot joined/active in group %s (%s)", event.chat.id, event.chat.title)
        elif new_status in ("left", "kicked"):
            group.is_active = False
            logger.info("Bot removed from group %s (%s)", event.chat.id, event.chat.title)
        await session.commit()


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_message(message: TgMessage) -> None:
    text_content = (message.text or message.caption or "").strip()
    if not text_content:
        return

    from_user = message.from_user
    if not from_user or from_user.is_bot:
        return

    group_id = message.chat.id
    msg_dt = message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)
    full_name = " ".join(p for p in (from_user.first_name, from_user.last_name) if p).strip() or "Участник"

    should_enqueue_memory = False
    debounce_delay = 4.5

    async with AsyncSessionLocal() as session:
        group = await ensure_group_initialized(
            session=session,
            group_id=group_id,
            title=message.chat.title or f"Group {group_id}",
            chat_type=message.chat.type,
        )

        # Находим или создаём изолированный профиль участника в этой группе
        mem_stmt = select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.telegram_user_id == from_user.id,
        )
        member = (await session.execute(mem_stmt)).scalars().first()
        if not member:
            # Проверяем, нет ли карточки этого участника, созданной при импорте HTML-истории (по имени)
            synth_stmt = select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.telegram_user_id >= SYNTHETIC_TG_ID_BASE,
                func.lower(GroupMember.display_name) == full_name[:255].lower(),
            )
            member = (await session.execute(synth_stmt)).scalars().first()
            if member:
                member.telegram_user_id = from_user.id
                member.username = from_user.username
                member.message_count = (member.message_count or 0) + 1
                member.last_seen_at = msg_dt
            else:
                member = GroupMember(
                    group_id=group_id,
                    telegram_user_id=from_user.id,
                    username=from_user.username,
                    display_name=full_name[:255],
                    message_count=1,
                    last_seen_at=msg_dt,
                )
                session.add(member)
                await session.flush()
        else:
            member.username = from_user.username
            member.display_name = full_name[:255]
            member.message_count = (member.message_count or 0) + 1
            member.last_seen_at = msg_dt

        # Проверяем дедупликацию по (group_id, telegram_message_id)
        dup_stmt = select(Message.id).where(
            Message.group_id == group_id,
            Message.telegram_message_id == message.message_id,
        )
        if (await session.execute(dup_stmt)).scalar_one_or_none() is not None:
            return

        reply_to_id = message.reply_to_message.message_id if message.reply_to_message else None

        db_msg = Message(
            group_id=group_id,
            telegram_message_id=message.message_id,
            member_id=member.id,
            sender_name=full_name[:255],
            sender_username=from_user.username,
            is_from_bot=False,
            reply_to_tg_msg_id=reply_to_id,
            text_content=text_content,
            reactions={},
            is_imported=False,
            is_processed_for_memory=False,
            created_at=msg_dt,
        )
        session.add(db_msg)

        # Сбрасываем счётчик безответных инициатив, так как живой участник написал в чат
        group.unanswered_initiatives = 0
        group.last_message_at = datetime.now(timezone.utc)
        group.unanalyzed_msg_count = (group.unanalyzed_msg_count or 0) + 1

        if group.unanalyzed_msg_count >= group.settings.memory_batch_size:
            should_enqueue_memory = True

        # Если реплай боту или прямой тег @bot — ждём чуть меньше обычного debounce
        sys_cfg = await session.get(SystemConfig, 1)
        bot_uname = (sys_cfg.bot_username or "").lower().lstrip("@") if sys_cfg else ""
        is_reply_to_bot = bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and sys_cfg
            and message.reply_to_message.from_user.id == sys_cfg.bot_telegram_id
        )
        is_mention = bool(bot_uname and f"@{bot_uname}" in text_content.lower())

        base_debounce = group.settings.debounce_seconds
        debounce_delay = max(1.8, base_debounce * 0.55) if (is_reply_to_bot or is_mention) else base_debounce

        is_group_active = group.is_active
        await session.commit()

        await publish_group_event(
            group_id,
            "new_message",
            {
                "id": db_msg.id,
                "telegram_message_id": db_msg.telegram_message_id,
                "sender_name": db_msg.sender_name,
                "sender_username": db_msg.sender_username,
                "is_from_bot": False,
                "reply_to_tg_msg_id": db_msg.reply_to_tg_msg_id,
                "text_content": db_msg.text_content,
                "reactions": db_msg.reactions,
                "created_at": db_msg.created_at.isoformat(),
            },
        )

    if should_enqueue_memory:
        try:
            arq = await get_arq_pool()
            await arq.enqueue_job("analyze_group_memory_job", group_id, False)
        except Exception as exc:
            logger.warning("Failed to enqueue memory extraction job: %s", exc)

    if is_group_active:
        await schedule_group_debounce(group_id, message.message_id, debounce_delay)


@router.edited_message(F.chat.type.in_({"group", "supergroup"}))
async def handle_edited_group_message(message: TgMessage) -> None:
    new_text = (message.text or message.caption or "").strip()
    if not new_text:
        return
    async with AsyncSessionLocal() as session:
        stmt = select(Message).where(
            Message.group_id == message.chat.id,
            Message.telegram_message_id == message.message_id,
        )
        db_msg = (await session.execute(stmt)).scalars().first()
        if db_msg:
            db_msg.text_content = new_text
            await session.commit()


@router.message_reaction()
async def handle_message_reaction(event: MessageReactionUpdated) -> None:
    if event.chat.type not in ("group", "supergroup"):
        return

    async with AsyncSessionLocal() as session:
        stmt = select(Message).where(
            Message.group_id == event.chat.id,
            Message.telegram_message_id == event.message_id,
        )
        db_msg = (await session.execute(stmt)).scalars().first()
        if not db_msg:
            return

        counts = dict(db_msg.reactions or {})
        for old_r in event.old_reaction:
            if isinstance(old_r, ReactionTypeEmoji):
                cur = counts.get(old_r.emoji, 0) - 1
                if cur <= 0:
                    counts.pop(old_r.emoji, None)
                else:
                    counts[old_r.emoji] = cur

        for new_r in event.new_reaction:
            if isinstance(new_r, ReactionTypeEmoji):
                counts[new_r.emoji] = counts.get(new_r.emoji, 0) + 1

        db_msg.reactions = counts
        await session.commit()

