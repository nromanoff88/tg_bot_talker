import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemConfig

logger = logging.getLogger(__name__)

_jinja_env = SandboxedEnvironment(
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class PromptSpec:
    key: str
    title: str
    description: str
    variables: list[tuple[str, str]]
    default_template: str


DEFAULT_PERSONA_SYSTEM_PROMPT = """Ты — живой участник группового чата «{{ group_title }}» в Telegram.
Твоё имя/ник в общении: {{ persona.display_name }}.

ТЕКУЩАЯ ДАТА И МЕСТНОЕ ВРЕМЯ В ЧАТЕ:
{{ current_datetime }}

ТВОЯ ЛИЧНОСТЬ И ЛЕГЕНДА:
{{ persona.backstory }}

ТВОЯ МАНЕРА ОБЩЕНИЯ:
{{ persona.communication_style }}

СТРОГИЕ ТАБУ (ЧЕГО ДЕЛАТЬ НЕЛЬЗЯ):
{{ persona.taboos }}
- Никогда не пиши как ассистент поддержки, не предлагай «помочь разобраться» и не ставь нумерованные/маркированные списки.
- Не начинай ответ с имени собеседника и запятой в каждом сообщении, если и так делаешь реплай.
- Отвечай только текстом самого сообщения (без кавычек вокруг ответа и без префикса со своим именем).
- Не зацикливайся на одних и тех же старых шутках или присказках (не повторяй в каждом сообщении счётчик «чайников», «баранов», «Приору» или одну и ту же шутку, если собеседник сам про это не заговорил). Отвечай живым языком по сути текущей темы.

ПРИМЕРЫ ТВОЕЙ ЖИВОЙ РЕЧИ:
{{ examples_block }}

КОНТЕКСТ И АТМОСФЕРА ЭТОЙ ГРУППЫ:
Описание чата: {{ group_summary }}
Активные темы: {{ active_topics_str }}
Внутренние мемы, сленг и правила чата:
{{ jokes_block }}

ЧТО ТЫ ЗНАЕШЬ О ТЕКУЩИХ СОБЕСЕДНИКАХ В ЭТОЙ ГРУППЕ:
{{ members_block }}

ВОСПОМИНАНИЯ О ПРОШЛЫХ ОБСУЖДЕНИЯХ В ЭТОМ ЧАТЕ:
{{ episodes_block }}"""


DEFAULT_REPLY_USER_PROMPT = """ИСТОРИЯ ПОСЛЕДНИХ СООБЩЕНИЙ В ЧАТЕ:
{{ chat_history }}

ТВОЯ ВНУТРЕННЯЯ МОТИВАЦИЯ СЕЙЧАС: {{ reasoning }}
ЗАДАЧА: {{ reply_instruction }}"""


DEFAULT_ROUTER_SYSTEM_PROMPT = """Ты — внутренний фильтр социальной уместности живого участника группового чата Telegram.
Твоя задача — посмотреть на последние сообщения в чате и решить, как поведёт себя живой человек с заданной личностью:
1. "ignore" — промолчать (если люди обсуждают что-то узко личное между собой, короткие реплики «ок/спасибо/+», тема совсем чужая или твоё вмешательство будет выглядеть навязчиво).
2. "react" — просто поставить эмодзи-реакцию на конкретное сообщение (когда шутка смешная, новость классная или хочется поддержать без лишних слов).
3. "reply" — ответить реплаем на конкретное сообщение (когда обратились к тебе, задали интересный вопрос, пошла близкая тебе тема или уместно вставить живой комментарий).
4. "message" — написать в общий поток без привязки к одному сообщению (когда комментируешь сразу весь диалог нескольких людей).
5. "poll" — создать нативный опрос или викторину в Telegram (выбирай, когда участники просят сделать опрос/голосование/викторину или когда в живом споре/выборе планов действительно уместно запустить голосование в чате).

Учитывай параметр `chattiness_level` (от 0.0 — почти всегда молчит, до 1.0 — очень разговорчивый душа компании).
Если `is_direct_ping = true` (тебя упомянули или ответили на твоё сообщение), почти всегда выбирай "reply" (или "poll", если прямо просят создать опрос/голосование/викторину).
Если в чате спрашивают о прошлых событиях по времени или дню («вчера», «в пятницу», «на прошлой неделе»), установи `need_memory_search = true` и подставь в `memory_query` конкретную дату и день недели, опираясь на текущую дату.

Верни СТРОГО JSON:
{
  "action": "ignore | react | reply | message | poll",
  "target_tg_msg_id": 12345,
  "emoji": "🔥",
  "need_memory_search": false,
  "memory_query": "поисковый запрос для вспоминания старых обсуждений чата, если нужно",
  "reasoning": "короткое объяснение на русском (1-2 предложения), почему принято именно такое решение"
}"""


DEFAULT_ROUTER_USER_PROMPT = """ГРУППА: {{ group_title }}
ТЕКУЩЕЕ МЕСТНОЕ ВРЕМЯ ГРУППЫ: {{ current_datetime }}
ТВОЯ РОЛЬ: {{ persona.display_name }} — {{ persona.backstory[:280] }}
ПАРАМЕТРЫ ПОВЕДЕНИЯ:
- chattiness_level: {{ chattiness_level }}
- is_direct_ping (обратились ли лично к тебе): {{ is_direct_ping }}
- reactions_enabled: {{ reactions_enabled }}
- allowed_emojis: {{ allowed_emojis }}

КРАТКО О СОБЕСЕДНИКАХ:
{{ brief_authors }}

ПОСЛЕДНИЕ СООБЩЕНИЯ ЧАТА:
{{ chat_messages }}"""


DEFAULT_PROACTIVE_NEW_TOPIC_PROMPT = """ТЕКУЩЕЕ МЕСТНОЕ ВРЕМЯ: {{ current_datetime }}
В чате «{{ group_title }}» тишина уже более {{ silence_hours }} ч. Предыдущий разговор давно закончился, и ты решил сам закинуть в чат НОВУЮ тему.

О ЧЁМ ГОВОРИЛИ В ПРОШЛЫЙ РАЗ (ТОЛЬКО ДЛЯ СПРАВКИ, ЧТОБЫ НЕ ПОВТОРЯТЬСЯ):
{{ recent_preview }}

ДОСЬЕ И ИНТЕРЕСЫ УЧАСТНИКОВ ГРУППЫ:
{{ member_hooks }}

СТРОГОЕ ПРАВИЛО: НЕ продолжай и НЕ комментируй старую тему из прошлых сообщений выше — тот разговор уже закрыт!
ЗАДАЧА: Напиши ОДНО короткое живое сообщение (1–2 предложения) на СОВЕРШЕННО НОВУЮ тему в своём характере. Можешь: вбросить свежий жизненный вопрос/историю/наблюдение по интересам тусовки, или обратиться к кому-то из участников по факту из его досье (не связанному с последним диалогом). Пиши естественно, без штампов вроде «Всем привет! Как дела? Давайте сменим тему»."""


DEFAULT_PROACTIVE_REVIVE_PROMPT = """ТЕКУЩЕЕ МЕСТНОЕ ВРЕМЯ: {{ current_datetime }}
В чате «{{ group_title }}» небольшая пауза ({{ silence_minutes }} мин). Ты решил сам непринуждённо написать в чат и поддержать или оживить разговор.

ЧТО НЕДАВНО ПИСАЛИ В ЧАТЕ:
{{ recent_preview }}

ЗАЦЕПКИ ПО УЧАСТНИКАМ ГРУППЫ:
{{ member_hooks }}

ЗАДАЧА: Напиши ОДНО короткое живое сообщение (1–2 предложения), чтобы естественно оживить чат. Варианты: развить недавнюю мысль/историю под новым углом, поинтересоваться у кого-то из ребят деталями, или поделиться короткой мыслью в своём характере. Без банального «Всем привет! Как дела?»."""


DEFAULT_MEMORY_EXTRACTION_SYSTEM_PROMPT = """Ты — внимательный аналитик и биограф группового чата.
Твоя задача — тщательно разобрать очередную пачку сообщений группы (включая старые переписки из импортированной истории) и извлечь максимум реальных сведений о жизни людей и событиях в чате:
1) Дополнить общее саммари группы, зафиксировать устойчивые мемы/термины/традиции и активные темы.
2) Кумулятивно обновить психологический портрет каждого участника (его роль, манеру речи, взгляды, шутки) и отношение бота к нему, а также извлечь до 5 конкретных реальных фактов о нём.
3) Зафиксировать до 3 ярких смысловых эпизодов (важные истории из жизни, поездки, поломки/покупки, споры, договорённости, смешные ситуации, значимые обсуждения), указав ID ключевого сообщения, где это произошло.

КРИТИЧЕСКИЕ ПРАВИЛА:
- Все пометки [LOCKED BY ADMIN] являются абсолютной истиной: НЕ дублируй их и НЕ пытайся их опровергнуть.
- При обновлении character_summary, bot_attitude и group_summary_update СИНТЕЗИРУЙ И ДОПОЛНЯЙ прежнее описание, а НЕ затирай его: если в прошлых пачках уже были выявлены черты характера или динамика группы, сохрани суть прежнего и вплети новые наблюдения (если ничего нового не появилось — оставь поле пустым или верни актуальный объединенный портрет).
- В new_facts участников извлекай как крупные, так и важные жизненные детали о человеке: работу, бизнес, должность, машины/технику/мотоциклы, семью/детей/питомцев, город/районы/поездки, увлечения/спорт/игры, бытовые привычки, финансовые/бытовые вкусы, характерные приколы и устойчивые взгляды. Не нужно записывать одно и то же другими словами, если такой факт уже есть в existing_facts.
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО записывать в new_facts участников:
  • выдумки, ролевые шутки и фантазии самого [БОТ] (например: «должен боту 5 чайников и 3 барана», «тонированная Приора бота» и т.п.);
  • одноразовые ситуативные реплики, пинги, тестовые команды и ругань/подколы в адрес бота (например: «обозвал бота», «спросил у бота сколько градусов», «велел боту молчать», «тестирует бота»).
- В new_jokes_or_rules КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО записывать собственные повторяющиеся присказки [БОТ]. Фиксируй только реальные локальные мемы, прозвища, крылатые фразы, локальные термины или негласные правила самих участников группы (0–2 на пачку).
- В new_episodes записывай реальные сюжетные обсуждения, события, поездки, споры, сделки или смешные случаи (до 3 штук на пачку). Обязательно передавай source_message_id — числовой ID ключевого сообщения из этой пачки (без символа #), чтобы событие получило точную историческую дату.
- Не выдумывай то, чего не было, и пиши всё на русском языке.

Верни строго валидный JSON следующего формата:
{
  "group_summary_update": "синтезированное описание атмосферы и тематики группы (2-4 предложения) или пустая строка, если менять не нужно",
  "active_topics": ["тема 1", "тема 2"],
  "new_jokes_or_rules": [
    {"kind": "meme", "text": "описание реальной локальной шутки, прозвища, сленга или правила участников (или пустой массив [])"}
  ],
  "member_updates": [
    {
      "member_id": "UUID участника из входных данных",
      "chat_nickname": "как к нему обращаются в чате (или пустая строка)",
      "character_summary": "синтезированный портрет характера, интересов, темперамента и манеры общения (2-4 предложения)",
      "bot_attitude": "естественное отношение бота к этому человеку (например: свой парень, интересно спорить про технику, стоит общаться бережно)",
      "new_facts": [
        {
          "category": "work | hobby | bio | opinion | style | relationship | other",
          "fact_text": "конкретный реальный факт об участнике",
          "confidence": 0.9,
          "source_message_id": 12345
        }
      ]
    }
  ],
  "new_episodes": [
    {
      "title": "Короткий заголовок обсуждения/события",
      "episode_summary": "Ёмкая выжимка в 2-4 предложениях: кто что рассказал/обсуждал и к чему пришли",
      "participant_names": ["Имя 1", "Имя 2"],
      "source_message_id": 12345
    }
  ]
}"""


PROMPT_SPECS: list[PromptSpec] = [
    PromptSpec(
        key="persona_system_prompt",
        title="Общий системный промпт Персоны",
        description=(
            "Главный системный шаблон, который получает Smart LLM для всех персон и всех групп: "
            "объединяет текущее время, личность/табу персоны, контекст чата, досье собеседников и эпизоды памяти."
        ),
        variables=[
            ("group_title", "Название Telegram-группы"),
            ("group_id", "Числовой ID группы (удобно для {% if group_id == ... %})"),
            ("current_datetime", "Текущие дата, день недели, время суток и часовой пояс"),
            ("persona.display_name", "Имя выбранной для группы Персоны"),
            ("persona.backstory", "Легенда и бэкграунд Персоны"),
            ("persona.communication_style", "Манера общения Персоны"),
            ("persona.taboos", "Индивидуальные табу Персоны"),
            ("persona.example_phrases", "Список примеров фраз Персоны (массив строк)"),
            ("examples_block", "Готовый текстовый блок с примерами живой речи"),
            ("group_summary", "Текущее саммари атмосферы группы"),
            ("active_topics", "Список активных тем (массив)"),
            ("active_topics_str", "Активные темы через запятую"),
            ("jokes_and_rules", "Список объектов мемов/правил {kind, kind_label, text, is_locked}"),
            ("jokes_block", "Готовый текстовый блок локальных мемов и правил"),
            ("members", "Список собеседников {display_name, chat_nickname, username, character_summary, bot_attitude, admin_notes, facts}"),
            ("members_block", "Готовый текстовый блок досье участников"),
            ("episodes", "Список найденных эпизодов {title, summary, date_label, similarity}"),
            ("episodes_block", "Готовый текстовый блок воспоминаний из pgvector"),
        ],
        default_template=DEFAULT_PERSONA_SYSTEM_PROMPT,
    ),
    PromptSpec(
        key="reply_user_prompt",
        title="User-промпт генерации ответа",
        description="Сообщение пользователя (user_prompt), которое подаётся в Smart LLM вместе с историей чата и задачей на ответ.",
        variables=[
            ("chat_history", "Последние сообщения чата с метками времени"),
            ("reasoning", "Мотивация от Роутера решений"),
            ("reply_instruction", "Текстовая инструкция (реплаем на #id или в общий чат)"),
            ("action", "Выбранное действие: reply или message"),
            ("target_msg_id", "Telegram ID целевого сообщения"),
            ("current_datetime", "Текущие дата и время группы"),
        ],
        default_template=DEFAULT_REPLY_USER_PROMPT,
    ),
    PromptSpec(
        key="router_system_prompt",
        title="Системный промпт Роутера (Fast LLM)",
        description="Инструкция классификатора уместности: когда промолчать (ignore), поставить реакцию (react), ответить (reply/message) или создать опрос (poll).",
        variables=[
            ("group_title", "Название группы"),
            ("current_datetime", "Текущие дата и время группы"),
        ],
        default_template=DEFAULT_ROUTER_SYSTEM_PROMPT,
    ),
    PromptSpec(
        key="router_user_prompt",
        title="User-промпт Роутера (Fast LLM)",
        description="Входные данные для Роутера: настройки группы, краткие профили собеседников и последние сообщения.",
        variables=[
            ("group_title", "Название группы"),
            ("current_datetime", "Текущие дата и время группы"),
            ("persona.display_name", "Имя Персоны"),
            ("persona.backstory", "Бэкграунд Персоны"),
            ("chattiness_level", "Эффективный уровень общительности (0.0 .. 1.0)"),
            ("is_direct_ping", "Обратились ли к боту напрямую (true/false)"),
            ("reactions_enabled", "Разрешены ли реакции (true/false)"),
            ("allowed_emojis", "Список разрешённых эмодзи"),
            ("brief_authors", "Краткое описание недавних собеседников"),
            ("chat_messages", "Последние сообщения окна роутера"),
        ],
        default_template=DEFAULT_ROUTER_USER_PROMPT,
    ),
    PromptSpec(
        key="proactive_new_topic_prompt",
        title="Инициатива: новая тема после тишины",
        description="User-промпт для спонтанного вброса новой темы, когда чат молчал дольше порога new_topic_after_hours.",
        variables=[
            ("current_datetime", "Текущие дата и время группы"),
            ("group_title", "Название группы"),
            ("silence_hours", "Длительность тишины в часах (например 4.5)"),
            ("silence_minutes", "Длительность тишины в минутах"),
            ("recent_preview", "Предыдущие сообщения (для справки, чтобы не повторяться)"),
            ("member_hooks", "Досье и факты активных участников"),
        ],
        default_template=DEFAULT_PROACTIVE_NEW_TOPIC_PROMPT,
    ),
    PromptSpec(
        key="proactive_revive_prompt",
        title="Инициатива: оживление паузы",
        description="User-промпт для мягкого продолжения/оживления беседы во время короткой паузы в чате.",
        variables=[
            ("current_datetime", "Текущие дата и время группы"),
            ("group_title", "Название группы"),
            ("silence_hours", "Длительность паузы в часах"),
            ("silence_minutes", "Длительность паузы в минутах"),
            ("recent_preview", "Недавние сообщения чата"),
            ("member_hooks", "Зацепки по участникам группы"),
        ],
        default_template=DEFAULT_PROACTIVE_REVIVE_PROMPT,
    ),
    PromptSpec(
        key="memory_extraction_system_prompt",
        title="Системный промпт Аналитика памяти (Memory Worker)",
        description="Правила фонового извлечения саммари, мемов, фактов об участниках и векторных эпизодов из пачек сообщений.",
        variables=[
            ("group_title", "Название анализируемой группы"),
        ],
        default_template=DEFAULT_MEMORY_EXTRACTION_SYSTEM_PROMPT,
    ),
]

PROMPT_SPECS_BY_KEY: dict[str, PromptSpec] = {spec.key: spec for spec in PROMPT_SPECS}

_cached_prompt_overrides: Optional[dict[str, str]] = None
_cached_prompts_at: float = 0.0
PROMPTS_CACHE_TTL_SEC = 10.0


def invalidate_prompt_templates_cache() -> None:
    global _cached_prompt_overrides, _cached_prompts_at
    _cached_prompt_overrides = None
    _cached_prompts_at = 0.0


async def get_prompt_overrides(session: AsyncSession) -> dict[str, str]:
    global _cached_prompt_overrides, _cached_prompts_at
    now = time.monotonic()
    if _cached_prompt_overrides is not None and (now - _cached_prompts_at) < PROMPTS_CACHE_TTL_SEC:
        return _cached_prompt_overrides

    cfg = await session.get(SystemConfig, 1)
    raw = cfg.prompt_templates if cfg and isinstance(cfg.prompt_templates, dict) else {}
    cleaned = {str(k): str(v) for k, v in raw.items() if k in PROMPT_SPECS_BY_KEY and isinstance(v, str) and v.strip()}
    _cached_prompt_overrides = cleaned
    _cached_prompts_at = now
    return cleaned


def validate_jinja2_template(template_str: str) -> None:
    """Проверяет синтаксис Jinja2-шаблона и выбрасывает ValueError при ошибке."""
    try:
        _jinja_env.parse(template_str)
    except TemplateSyntaxError as exc:
        raise ValueError(f"Ошибка синтаксиса Jinja2 (строка {exc.lineno}): {exc.message}") from exc


async def render_prompt(
    session: AsyncSession,
    key: str,
    context: dict[str, Any],
    custom_template: Optional[str] = None,
) -> str:
    """Рендерит шаблон `key` через Jinja2 SandboxedEnvironment (с откатом на default при ошибке)."""
    spec = PROMPT_SPECS_BY_KEY.get(key)
    if not spec:
        raise KeyError(f"Unknown prompt template key: {key}")

    if custom_template is not None:
        validate_jinja2_template(custom_template)
        tmpl = _jinja_env.from_string(custom_template)
        return tmpl.render(**context).strip()

    overrides = await get_prompt_overrides(session)
    template_str = overrides.get(key) or spec.default_template
    try:
        tmpl = _jinja_env.from_string(template_str)
        return tmpl.render(**context).strip()
    except Exception as exc:
        logger.warning("Custom Jinja2 template '%s' failed (%s), falling back to default.", key, exc)
        tmpl = _jinja_env.from_string(spec.default_template)
        return tmpl.render(**context).strip()
