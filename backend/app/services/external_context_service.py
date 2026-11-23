import asyncio
import html
import json
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemConfig
from app.redis_client import get_redis
from app.services.llm_service import complete_text

logger = logging.getLogger(__name__)

WEATHER_CACHE_KEY = "bot_talker:ext:weather:v1"
NEWS_CACHE_KEY = "bot_talker:ext:news:v1"
WEATHER_TTL_SEC = 1800
NEWS_TTL_SEC = 2700
WEATHER_REFRESH_AGE_SEC = 1500
NEWS_REFRESH_AGE_SEC = 2400

RU_WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

WMO_WEATHER_RU = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь/туман",
    51: "слабая морось",
    53: "морось",
    55: "сильная морось",
    56: "ледяная морось",
    57: "сильная ледяная морось",
    61: "небольшой дождь",
    63: "дождь",
    65: "сильный дождь",
    66: "ледяной дождь",
    67: "сильный ледяной дождь",
    71: "небольшой снег",
    73: "снег",
    75: "сильный снег",
    77: "снежная крупа",
    80: "кратковременный дождь",
    81: "ливень",
    82: "сильный ливень",
    85: "снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с градом",
    99: "сильная гроза с градом",
}

_VOID_TAGS = frozenset({"br", "img", "hr", "meta", "link", "input"})


def normalize_tg_channel(raw: str) -> str:
    """Извлекает username публичного Telegram-канала из @chan, t.me/chan или t.me/s/chan."""
    val = (raw or "").strip()
    if not val:
        return ""
    val = re.sub(r"^https?://", "", val, flags=re.IGNORECASE)
    val = re.sub(r"^(?:www\.)?(?:t\.me|telegram\.me)/", "", val, flags=re.IGNORECASE)
    if val.lower().startswith("s/"):
        val = val[2:]
    val = val.split("?", 1)[0].split("#", 1)[0].strip("/")
    if "/" in val:
        val = val.split("/", 1)[0]
    val = val.lstrip("@").strip()
    if not re.match(r"^[A-Za-z0-9_]{3,64}$", val):
        return ""
    return val


def normalize_tg_channels_list(raw_channels: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_channels or []:
        for part in re.split(r"[\s,;]+", str(item or "")):
            ch = normalize_tg_channel(part)
            if ch and ch.lower() not in seen:
                cleaned.append(ch)
                seen.add(ch.lower())
    return cleaned[:15]


class _TgChannelHTMLParser(HTMLParser):
    """Парсер публичной веб-ленты Telegram-канала https://t.me/s/<channel>."""

    def __init__(self, channel: str) -> None:
        super().__init__(convert_charrefs=True)
        self.channel = channel
        self.posts: list[dict[str, str]] = []
        self._stack: list[tuple[str, set[str]]] = []
        self._msg_depth: int = 0
        self._in_text_depth: int = 0
        self._cur_text: list[str] = []
        self._cur_date: str = ""
        self._cur_post_id: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_low = tag.lower()
        attr_dict = dict(attrs)
        classes = set((attr_dict.get("class") or "").split())

        if tag_low in _VOID_TAGS:
            if tag_low == "br" and self._in_text_depth > 0:
                self._cur_text.append(" ")
            return

        self._stack.append((tag_low, classes))
        depth = len(self._stack)

        if self._msg_depth == 0 and tag_low == "div" and "tgme_widget_message" in classes:
            self._msg_depth = depth
            self._cur_text = []
            self._cur_date = ""
            self._cur_post_id = str(attr_dict.get("data-post") or "")
            return

        if self._msg_depth == 0:
            return

        if tag_low == "time" and not self._cur_date:
            dt_val = attr_dict.get("datetime")
            if dt_val:
                self._cur_date = dt_val

        if (
            self._in_text_depth == 0
            and "tgme_widget_message_text" in classes
            and "js-message_reply_text" not in classes
        ):
            self._in_text_depth = depth

    def handle_data(self, data: str) -> None:
        if self._in_text_depth > 0:
            self._cur_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag_low = tag.lower()
        if tag_low in _VOID_TAGS or not self._stack:
            return

        match_idx: Optional[int] = None
        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx][0] == tag_low:
                match_idx = idx
                break
        if match_idx is None:
            return

        while len(self._stack) > match_idx:
            depth = len(self._stack)
            self._stack.pop()
            if self._in_text_depth > 0 and depth == self._in_text_depth:
                self._in_text_depth = 0
            if self._msg_depth > 0 and depth == self._msg_depth:
                txt = re.sub(r"\s+", " ", html.unescape("".join(self._cur_text))).strip()
                if len(txt) >= 20:
                    self.posts.append(
                        {
                            "channel": self.channel,
                            "post_id": self._cur_post_id,
                            "date": self._cur_date,
                            "text": txt[:600],
                        }
                    )
                self._msg_depth = 0
                self._in_text_depth = 0


async def _fetch_single_city_weather(client: httpx.AsyncClient, city_name: str) -> str:
    geo_resp = await client.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city_name, "count": 1, "language": "ru", "format": "json"},
    )
    geo_resp.raise_for_status()
    geo_data = geo_resp.json()
    results = geo_data.get("results") or []
    if not results:
        raise ValueError(f"Город «{city_name}» не найден в Open-Meteo")

    place = results[0]
    lat = place["latitude"]
    lon = place["longitude"]
    resolved_name = place.get("name") or city_name
    country = place.get("country") or ""
    title = f"{resolved_name} ({country})" if country else resolved_name

    fc_resp = await client.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
            "wind_speed_unit": "ms",
            "timezone": "auto",
            "forecast_days": 10,
        },
    )
    fc_resp.raise_for_status()
    fc = fc_resp.json()

    cur = fc.get("current") or {}
    cur_t = cur.get("temperature_2m")
    cur_app = cur.get("apparent_temperature")
    cur_code = cur.get("weather_code")
    cur_wind = cur.get("wind_speed_10m")
    cur_desc = WMO_WEATHER_RU.get(int(cur_code), "облачно") if cur_code is not None else ""

    lines = [
        f"Город {title}: сейчас {cur_t:+.0f}°C (ощущается {cur_app:+.0f}°C), {cur_desc}, ветер {cur_wind:.0f} м/с"
        if cur_t is not None and cur_app is not None and cur_wind is not None
        else f"Город {title}:"
    ]

    daily = fc.get("daily") or {}
    dates = daily.get("time") or []
    t_min_list = daily.get("temperature_2m_min") or []
    t_max_list = daily.get("temperature_2m_max") or []
    codes = daily.get("weather_code") or []
    precip_list = daily.get("precipitation_sum") or []

    lines.append("Прогноз на 10 дней:")
    for idx, d_str in enumerate(dates[:10]):
        try:
            d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
            wd = RU_WEEKDAYS_SHORT[d_obj.weekday()]
            if idx == 0:
                tag = f"{wd}, сегодня"
            elif idx == 1:
                tag = f"{wd}, завтра"
            else:
                tag = wd
            date_label = f"{d_obj.strftime('%d.%m.%Y')} ({tag})"
        except Exception:
            date_label = d_str

        t_min = t_min_list[idx] if idx < len(t_min_list) and t_min_list[idx] is not None else None
        t_max = t_max_list[idx] if idx < len(t_max_list) and t_max_list[idx] is not None else None
        code = codes[idx] if idx < len(codes) and codes[idx] is not None else None
        precip = precip_list[idx] if idx < len(precip_list) and precip_list[idx] is not None else 0.0
        desc = WMO_WEATHER_RU.get(int(code), "") if code is not None else ""
        t_part = f"{t_min:+.0f}..{t_max:+.0f}°C" if t_min is not None and t_max is not None else ""
        p_part = f", осадки {precip:.1f} мм" if precip and float(precip) >= 0.3 else ""
        lines.append(f"- {date_label}: {t_part}, {desc}{p_part}".rstrip(", "))

    return "\n".join(lines)


async def fetch_city_weather_10d(city_query: str) -> tuple[str, list[str]]:
    """Получает текущую погоду и прогноз на 10 дней с датами для указанного города (или списка городов через запятую)."""
    cities = [c.strip() for c in re.split(r"[,;]+", city_query or "") if c.strip()][:3]
    if not cities:
        return "", []

    blocks: list[str] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(_fetch_single_city_weather(client, c) for c in cities),
            return_exceptions=True,
        )
        for city, res in zip(cities, results):
            if isinstance(res, Exception):
                errors.append(f"Погода ({city}): {res}")
            elif res:
                blocks.append(res)

    return "\n\n".join(blocks), errors


async def fetch_tg_channel_posts(channel: str, max_posts: int = 8) -> list[dict[str, str]]:
    """Загружает последние посты из публичного Telegram-канала через https://t.me/s/<channel>."""
    clean = normalize_tg_channel(channel)
    if not clean:
        return []

    url = f"https://t.me/s/{clean}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    parser = _TgChannelHTMLParser(clean)
    parser.feed(resp.text)
    parser.close()
    return parser.posts[-max_posts:]


def _format_post_date_ru(iso_date: str) -> str:
    if not iso_date:
        return ""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return ""


async def build_news_digest(
    session: AsyncSession,
    channels: list[str],
    topics: str,
) -> tuple[str, int, list[str]]:
    """Собирает посты из публичных TG-каналов и сжимает их через Fast LLM по интересующим темам."""
    norm_channels = normalize_tg_channels_list(channels)
    if not norm_channels:
        return "", 0, []

    results = await asyncio.gather(
        *(fetch_tg_channel_posts(ch, max_posts=7) for ch in norm_channels),
        return_exceptions=True,
    )

    all_posts: list[dict[str, str]] = []
    errors: list[str] = []
    for ch, res in zip(norm_channels, results):
        if isinstance(res, Exception):
            errors.append(f"Канал @{ch}: {res}")
        elif not res:
            errors.append(f"Канал @{ch}: посты не найдены (возможно, канал закрытый)")
        else:
            all_posts.extend(res)

    if not all_posts:
        return "", 0, errors

    all_posts.sort(key=lambda p: p.get("date") or "", reverse=True)
    selected_posts = all_posts[:32]

    raw_lines = []
    for p in selected_posts:
        d_lbl = _format_post_date_ru(p.get("date") or "")
        meta = f"@{p['channel']}, {d_lbl}" if d_lbl else f"@{p['channel']}"
        raw_lines.append(f"[{meta}] {p['text']}")

    topics_clean = (topics or "").strip()
    sys_prompt = (
        "Ты — редактор новостной ленты для участника дружеского группового чата. "
        "Отобери из сырых постов Telegram-каналов до 8 самых заметных, реальных и обсуждаемых новостей. "
        "Убери рекламу, промокоды, розыгрыши, спам и дубликаты одной и той же новости из разных каналов. "
        "Формат каждой строки: `- [@канал, ДД.ММ] Краткая суть в 1 предложении на русском языке.` "
        "Если ни одна новость не подходит под заданные темы, верни строго пустую строку."
    )
    topics_instruction = (
        f"ИНТЕРЕСУЮЩИЕ ТЕМЫ (отбирай в первую очередь под них): {topics_clean}"
        if topics_clean
        else "ТЕМЫ: общие значимые новости, происшествия, городская жизнь, технологии, авто, спорт."
    )
    user_prompt = f"{topics_instruction}\n\nПОСТЫ ИЗ КАНАЛОВ:\n" + "\n".join(raw_lines)

    try:
        digest, _ = await complete_text(
            session=session,
            tier="fast",
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=900,
        )
        digest = (digest or "").strip()
        if not digest and not topics_clean:
            digest = "\n".join(f"- {line[:180]}" for line in raw_lines[:5])
        return digest, len(all_posts), errors
    except Exception as exc:
        errors.append(f"Фильтрация новостей Fast LLM: {exc}")
        fallback = "\n".join(f"- {line[:180]}" for line in raw_lines[:5])
        return fallback, len(all_posts), errors


def _weather_sig(cfg: SystemConfig | None) -> str:
    return (cfg.weather_city or "").strip().lower() if cfg else ""


def _news_sig(cfg: SystemConfig | None) -> str:
    if not cfg:
        return ""
    chans = normalize_tg_channels_list(cfg.news_tg_channels)
    topics = (cfg.news_topics or "").strip().lower()
    return json.dumps({"channels": chans, "topics": topics}, ensure_ascii=False, sort_keys=True)


async def _read_cache(key: str, expected_sig: str) -> Optional[dict[str, Any]]:
    try:
        raw = await get_redis().get(key)
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("sig") != expected_sig:
            return None
        return data
    except Exception:
        return None


async def _write_cache(key: str, payload: dict[str, Any], ttl: int) -> None:
    try:
        await get_redis().set(key, json.dumps(payload, ensure_ascii=False), ex=ttl)
    except Exception as exc:
        logger.debug("Redis cache write failed for %s: %s", key, exc)


async def invalidate_external_context_cache() -> None:
    try:
        await get_redis().delete(WEATHER_CACHE_KEY, NEWS_CACHE_KEY)
    except Exception:
        pass


async def refresh_external_context(
    session: AsyncSession,
    force_weather: bool = True,
    force_news: bool = True,
) -> dict[str, Any]:
    """Принудительно или при устаревании обновляет кэш погоды и новостей из TG-каналов."""
    cfg = await session.get(SystemConfig, 1)
    weather_city = (cfg.weather_city or "").strip() if cfg else ""
    channels = normalize_tg_channels_list(cfg.news_tg_channels) if cfg else []
    topics = (cfg.news_topics or "").strip() if cfg else ""

    w_sig = _weather_sig(cfg)
    n_sig = _news_sig(cfg)
    now_iso = datetime.now(timezone.utc).isoformat()
    errors: list[str] = []

    w_data = None if force_weather else await _read_cache(WEATHER_CACHE_KEY, w_sig)
    if weather_city and w_data is None:
        w_block, w_errs = await fetch_city_weather_10d(weather_city)
        errors.extend(w_errs)
        w_data = {"sig": w_sig, "value": w_block, "updated_at": now_iso, "errors": w_errs}
        if w_block:
            await _write_cache(WEATHER_CACHE_KEY, w_data, WEATHER_TTL_SEC)
    elif not weather_city:
        await get_redis().delete(WEATHER_CACHE_KEY)
        w_data = {"sig": "", "value": "", "updated_at": None, "errors": []}

    n_data = None if force_news else await _read_cache(NEWS_CACHE_KEY, n_sig)
    if channels and n_data is None:
        n_block, raw_cnt, n_errs = await build_news_digest(session, channels, topics)
        errors.extend(n_errs)
        n_data = {
            "sig": n_sig,
            "value": n_block,
            "updated_at": now_iso,
            "raw_posts_fetched": raw_cnt,
            "errors": n_errs,
        }
        await _write_cache(NEWS_CACHE_KEY, n_data, NEWS_TTL_SEC)
    elif not channels:
        await get_redis().delete(NEWS_CACHE_KEY)
        n_data = {"sig": "", "value": "", "updated_at": None, "raw_posts_fetched": 0, "errors": []}

    return {
        "weather_city": weather_city,
        "weather_block": (w_data or {}).get("value", ""),
        "weather_updated_at": (w_data or {}).get("updated_at"),
        "news_tg_channels": channels,
        "news_topics": topics,
        "news_block": (n_data or {}).get("value", ""),
        "news_updated_at": (n_data or {}).get("updated_at"),
        "raw_posts_fetched": int((n_data or {}).get("raw_posts_fetched") or 0),
        "errors": errors,
    }


async def get_external_context_status(session: AsyncSession) -> dict[str, Any]:
    """Возвращает текущие настройки и закэшированное состояние погоды и новостей для админки."""
    cfg = await session.get(SystemConfig, 1)
    weather_city = (cfg.weather_city or "").strip() if cfg else ""
    channels = normalize_tg_channels_list(cfg.news_tg_channels) if cfg else []
    topics = (cfg.news_topics or "").strip() if cfg else ""

    w_data = await _read_cache(WEATHER_CACHE_KEY, _weather_sig(cfg)) if weather_city else None
    n_data = await _read_cache(NEWS_CACHE_KEY, _news_sig(cfg)) if channels else None
    errors: list[str] = []
    if w_data and w_data.get("errors"):
        errors.extend(w_data["errors"])
    if n_data and n_data.get("errors"):
        errors.extend(n_data["errors"])

    return {
        "weather_city": weather_city,
        "weather_block": (w_data or {}).get("value", ""),
        "weather_updated_at": (w_data or {}).get("updated_at"),
        "news_tg_channels": channels,
        "news_topics": topics,
        "news_block": (n_data or {}).get("value", ""),
        "news_updated_at": (n_data or {}).get("updated_at"),
        "raw_posts_fetched": int((n_data or {}).get("raw_posts_fetched") or 0),
        "errors": errors,
    }


async def get_external_context_blocks(session: AsyncSession) -> tuple[str, str]:
    """Быстро возвращает `(weather_block, news_block)` из Redis (или догружает при холодном кэше)."""
    cfg = await session.get(SystemConfig, 1)
    if not cfg:
        return "", ""

    weather_city = (cfg.weather_city or "").strip()
    channels = normalize_tg_channels_list(cfg.news_tg_channels)

    w_data = await _read_cache(WEATHER_CACHE_KEY, _weather_sig(cfg)) if weather_city else None
    n_data = await _read_cache(NEWS_CACHE_KEY, _news_sig(cfg)) if channels else None

    need_weather = bool(weather_city and w_data is None)
    need_news = bool(channels and n_data is None)
    if need_weather or need_news:
        lock_key = "bot_talker:lock:ext_ctx_refresh"
        acquired = False
        try:
            acquired = bool(await get_redis().set(lock_key, "1", ex=30, nx=True))
            if acquired:
                refreshed = await refresh_external_context(
                    session,
                    force_weather=need_weather,
                    force_news=need_news,
                )
                return refreshed["weather_block"], refreshed["news_block"]
        except Exception as exc:
            logger.debug("Lazy external context fetch skipped: %s", exc)
        finally:
            if acquired:
                try:
                    await get_redis().delete(lock_key)
                except Exception:
                    pass

    return (w_data or {}).get("value", ""), (n_data or {}).get("value", "")


async def ensure_external_context_fresh(session: AsyncSession) -> None:
    """Фоновое упреждающее обновление погоды и новостей (вызывается кроном раз в 5 минут)."""
    cfg = await session.get(SystemConfig, 1)
    if not cfg:
        return

    now_utc = datetime.now(timezone.utc)
    weather_city = (cfg.weather_city or "").strip()
    channels = normalize_tg_channels_list(cfg.news_tg_channels)

    def _is_stale(data: Optional[dict[str, Any]], max_age_sec: int) -> bool:
        if data is None or not data.get("updated_at"):
            return True
        try:
            dt = datetime.fromisoformat(str(data["updated_at"]))
            return (now_utc - dt).total_seconds() >= max_age_sec
        except Exception:
            return True

    w_data = await _read_cache(WEATHER_CACHE_KEY, _weather_sig(cfg)) if weather_city else None
    n_data = await _read_cache(NEWS_CACHE_KEY, _news_sig(cfg)) if channels else None

    need_w = bool(weather_city and _is_stale(w_data, WEATHER_REFRESH_AGE_SEC))
    need_n = bool(channels and _is_stale(n_data, NEWS_REFRESH_AGE_SEC))
    if need_w or need_n:
        await refresh_external_context(session, force_weather=need_w, force_news=need_n)
