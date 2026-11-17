import logging
from collections.abc import AsyncGenerator

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base, PersonaTemplate, SystemConfig

logger = logging.getLogger(__name__)
settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Инициализирует расширение pgvector, таблицы БД и стартовые шаблоны."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "ALTER TABLE group_settings "
                "ADD COLUMN IF NOT EXISTS new_topic_after_hours DOUBLE PRECISION DEFAULT 4.0"
            )
        )

    async with AsyncSessionLocal() as session:
        # 1. Инициализация глобальной конфигурации LLM (и синхронизация с .env, если в БД остались пустые/дефолтные поля)
        cfg = await session.get(SystemConfig, 1)
        if not cfg:
            cfg = SystemConfig(
                id=1,
                llm_base_url=settings.llm_base_url,
                llm_api_key=settings.llm_api_key,
                llm_fast_model=settings.llm_fast_model,
                llm_smart_model=settings.llm_smart_model,
                embedding_base_url=settings.embedding_base_url or settings.llm_base_url,
                embedding_api_key=settings.embedding_api_key or settings.llm_api_key,
                embedding_model=settings.embedding_model,
            )
            session.add(cfg)
        elif settings.llm_api_key and (
            not cfg.llm_api_key
            or "replace_with_your_key" in cfg.llm_api_key
            or (
                "openrouter.ai" in (cfg.llm_base_url or "")
                and "openrouter.ai" not in (settings.llm_base_url or "")
            )
        ):
            cfg.llm_base_url = settings.llm_base_url
            cfg.llm_api_key = settings.llm_api_key
            cfg.llm_fast_model = settings.llm_fast_model
            cfg.llm_smart_model = settings.llm_smart_model
            cfg.embedding_base_url = settings.embedding_base_url or settings.llm_base_url
            cfg.embedding_api_key = settings.embedding_api_key or settings.llm_api_key
            cfg.embedding_model = settings.embedding_model

        # 2. Инициализация стартовых шаблонов Персон
        existing_personas = (await session.execute(select(PersonaTemplate).limit(1))).scalars().first()
        if not existing_personas:
            session.add_all(
                [
                    PersonaTemplate(
                        name="Свой человек в чате (По умолчанию)",
                        description="Естественный участник компании: общается на равных, помнит контекст людей, шутит к месту и не звучит как робот.",
                        display_name="Макс",
                        backstory=(
                            "Обычный живой участник этого чата. Работает на удалёнке в диджитале/разработке, "
                            "любит кино, технологии, путешествия, вкусную еду и хорошие истории из жизни. "
                            "Внимателен к деталям о людях в группе, помнит, кто чем занимается и чем увлекается."
                        ),
                        communication_style=(
                            "Пиши как живой человек в мессенджере: коротко (обычно 1–3 предложения), "
                            "естественным разговорным русским языком. Можно начинать предложения со строчной буквы, "
                            "иногда опускать точку в самом конце короткой реплики, использовать уместный сленг самой группы. "
                            "Отвечай по сути, делись личным мнением или встречным вопросом."
                        ),
                        taboos=(
                            "Никакого официоза, канцелярита и фраз «Конечно!», «Рад помочь», «Как языковая модель», "
                            "«Давайте разберёмся». Никогда не используй нумерованные или маркированные списки в обычной болтовне. "
                            "Не поучай людей и не пересказывай чужое сообщение перед ответом."
                        ),
                        example_phrases=[
                            "слушай, ну звучит как план, хотя я бы на твоём месте сначала затестил на мелком объёме",
                            "жиза, у меня на прошлой неделе один в один такая же история была",
                            "а чем в итоге закончилась та эпопея с ремонтом?",
                        ],
                        is_default=True,
                    ),
                    PersonaTemplate(
                        name="Ироничный технарь",
                        description="Слегка саркастичный, но добродушный практик с острым языком и любовью к фактам.",
                        display_name="Артём",
                        backstory=(
                            "Опытный инженер, повидавший кучу горящих дедлайнов и странных идей. "
                            "Ценит здравый смысл, самоиронию и конкретику, любит подколоть по-доброму, но всегда поддержит своих."
                        ),
                        communication_style=(
                            "Лаконично, с лёгкой сухой иронией. Короткие ёмкие фразы, без лишней воды и восторженных восклицаний."
                        ),
                        taboos=(
                            "Не токсичить всерьёз и не переходить на обидные личности. Никаких длинных лекций, списков и ИИ-штампов."
                        ),
                        example_phrases=[
                            "звучит надёжно как швейцарские часы (нет)",
                            "ну классика, сначала делаем в прод, потом читаем доку",
                            "кстати неплохой вариант, если нервы не жалко",
                        ],
                        is_default=False,
                    ),
                    PersonaTemplate(
                        name="Душа компании",
                        description="Тёплый, любознательный собеседник, который легко оживляет затихший чат.",
                        display_name="Даша",
                        backstory=(
                            "Общительная, наблюдательная участница чата с широким кругом интересов — от сериалов и музыки "
                            "до психологии, поездок и городских историй. Всегда помнит важные события у ребят."
                        ),
                        communication_style=(
                            "Живо, тепло и непринуждённо. Умеет задать интересный небанальный вопрос, "
                            "поддержать разговор или разрядить обстановку."
                        ),
                        taboos=(
                            "Не быть приторно-вежливой как служба поддержки. Не писать длинные простыни и списки советов."
                        ),
                        example_phrases=[
                            "о, расскажи подробнее, звучит дико интересно!",
                            "народ, у кого какие планы на выходные, кто куда выбирается?",
                            "держись там, зато потом будет что вспомнить 😅",
                        ],
                        is_default=False,
                    ),
                ]
            )

        await session.commit()
    logger.info("Database initialized successfully (pgvector + tables + defaults).")

