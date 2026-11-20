import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

import httpx
from google import genai
from google.genai import types as genai_types
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import SystemConfig

logger = logging.getLogger(__name__)
env_settings = get_settings()


@dataclass
class LLMUsage:
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            model=other.model or self.model,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cost_usd=round(self.cost_usd + other.cost_usd, 6),
        )


@dataclass
class RuntimeLLMConfig:
    llm_base_url: str
    llm_api_key: str
    llm_fast_model: str
    llm_smart_model: str
    embedding_base_url: str
    embedding_api_key: str
    embedding_model: str


_cached_cfg: Optional[RuntimeLLMConfig] = None
_cached_at: float = 0.0
CACHE_TTL_SEC = 10.0

# Кэш метаданных Yandex AI Studio (folder_id и список доступных моделей) на 5 минут
_yandex_models_cache: dict[tuple[str, str], tuple[float, Optional[str], list[str]]] = {}
YANDEX_CACHE_TTL_SEC = 300.0


def invalidate_llm_config_cache() -> None:
    global _cached_cfg, _cached_at
    _cached_cfg = None
    _cached_at = 0.0
    _yandex_models_cache.clear()


async def get_runtime_llm_config(session: AsyncSession) -> RuntimeLLMConfig:
    global _cached_cfg, _cached_at
    now = time.monotonic()
    if _cached_cfg is not None and (now - _cached_at) < CACHE_TTL_SEC:
        return _cached_cfg

    db_cfg = await session.get(SystemConfig, 1)
    if db_cfg:
        cfg = RuntimeLLMConfig(
            llm_base_url=db_cfg.llm_base_url or env_settings.llm_base_url,
            llm_api_key=db_cfg.llm_api_key or env_settings.llm_api_key,
            llm_fast_model=db_cfg.llm_fast_model or env_settings.llm_fast_model,
            llm_smart_model=db_cfg.llm_smart_model or env_settings.llm_smart_model,
            embedding_base_url=db_cfg.embedding_base_url or db_cfg.llm_base_url or env_settings.embedding_base_url,
            embedding_api_key=db_cfg.embedding_api_key or db_cfg.llm_api_key or env_settings.embedding_api_key,
            embedding_model=db_cfg.embedding_model or env_settings.embedding_model,
        )
    else:
        cfg = RuntimeLLMConfig(
            llm_base_url=env_settings.llm_base_url,
            llm_api_key=env_settings.llm_api_key,
            llm_fast_model=env_settings.llm_fast_model,
            llm_smart_model=env_settings.llm_smart_model,
            embedding_base_url=env_settings.embedding_base_url,
            embedding_api_key=env_settings.embedding_api_key or env_settings.llm_api_key,
            embedding_model=env_settings.embedding_model,
        )

    _cached_cfg = cfg
    _cached_at = now
    return cfg


def _detect_google_mode(base_url: str, api_key: str) -> tuple[bool, bool]:
    """
    Определяет, нужно ли использовать нативный SDK google-genai вместо OpenAI SDK.
    Возвращает (use_google_sdk, use_vertex_express):
    - Ключи `AQ.` — это Google Cloud Vertex AI Express Mode (требуют vertexai=True).
    - Ключи `AIza` или URL `googleapis.com` — Google AI Studio / Gemini API.
    """
    key = (api_key or "").strip()
    url = (base_url or "").strip().lower()

    if key.startswith("AQ."):
        return True, True
    if key.startswith("AIza"):
        return True, ("aiplatform.googleapis.com" in url or "vertex" in url)
    if "generativelanguage.googleapis.com" in url:
        return True, False
    if "aiplatform.googleapis.com" in url or url in ("vertex", "google", "gemini"):
        return True, True
    return False, False


def _is_yandex_provider(base_url: str) -> bool:
    url = (base_url or "").strip().lower()
    return "yandex.net" in url or "yandex.ru" in url


async def _fetch_yandex_catalog(base_url: str, api_key: str) -> tuple[Optional[str], list[str]]:
    """Получает folder_id и список доступных model URI из /v1/models Yandex AI Studio."""
    cache_key = (base_url.rstrip("/"), api_key)
    now = time.monotonic()
    cached = _yandex_models_cache.get(cache_key)
    if cached and (now - cached[0]) < YANDEX_CACHE_TTL_SEC:
        return cached[1], cached[2]

    url = f"{base_url.rstrip('/')}/models"
    folder_id: Optional[str] = None
    model_ids: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Api-Key {api_key}"})
            if resp.status_code == 200:
                data = resp.json().get("data") or []
                for item in data:
                    mid = str(item.get("id") or "").strip()
                    if mid:
                        model_ids.append(mid)
                        if not folder_id:
                            m = re.match(r"^(?:gpt|emb)://([^/]+)/", mid)
                            if m:
                                folder_id = m.group(1)
    except Exception as exc:
        logger.debug("Failed to fetch Yandex models catalog: %s", exc)

    _yandex_models_cache[cache_key] = (now, folder_id, model_ids)
    return folder_id, model_ids


async def _resolve_yandex_model_and_project(
    base_url: str,
    api_key: str,
    model: str,
    kind: Literal["gpt", "emb"],
) -> tuple[str, Optional[str]]:
    """
    Нормализует имя модели и извлекает folder_id (для заголовка OpenAI-Project) в Yandex AI Studio.
    Позволяет указывать как полные URI (`gpt://<folder_id>/aliceai-llm/latest`),
    так и короткие имена (`aliceai-llm`, `aliceai-llm-flash`, `text-embeddings-v2-doc`).
    """
    clean = (model or "").strip()
    uri_match = re.match(r"^(?:gpt|emb)://([^/]+)/(.+)$", clean)
    if uri_match:
        folder_id = uri_match.group(1)
        if kind == "emb" and clean.startswith("gpt://"):
            # Если в эмбеддинги по ошибке передали чат-модель, попробуем заменить на модель эмбеддингов каталога
            _, available = await _fetch_yandex_catalog(base_url, api_key)
            for pref in ("text-embeddings-v2-doc/latest", "text-search-doc/latest"):
                cand = f"emb://{folder_id}/{pref}"
                if cand in available:
                    return cand, folder_id
            for mid in available:
                if mid.startswith("emb://") and "query" not in mid:
                    return mid, folder_id
        return clean, folder_id

    folder_id, available = await _fetch_yandex_catalog(base_url, api_key)
    if not folder_id:
        return clean, None

    if kind == "emb":
        if clean and ("embed" in clean.lower() or "search" in clean.lower()):
            candidates = [
                f"emb://{folder_id}/{clean}",
                f"emb://{folder_id}/{clean}/latest",
            ]
            for c in candidates:
                if c in available:
                    return c, folder_id
            return (candidates[0] if "/" in clean else candidates[1]), folder_id

        # Автовыбор лучшей embedding-модели из каталога, если указано нерелевантное имя (например, gemini-2.5-flash)
        for pref in ("text-embeddings-v2-doc/latest", "text-search-doc/latest", "text-embeddings/latest"):
            cand = f"emb://{folder_id}/{pref}"
            if cand in available:
                return cand, folder_id
        for mid in available:
            if mid.startswith("emb://"):
                return mid, folder_id
        return f"emb://{folder_id}/text-embeddings-v2-doc/latest", folder_id

    # kind == "gpt"
    short = clean or "aliceai-llm-flash"
    candidates = [
        f"gpt://{folder_id}/{short}",
        f"gpt://{folder_id}/{short}/latest",
    ]
    for c in candidates:
        if c in available:
            return c, folder_id

    return (candidates[0] if "/" in short else candidates[1]), folder_id


def _clean_google_model_name(model: str) -> str:
    m = (model or "").strip()
    if m.startswith("google/"):
        m = m.removeprefix("google/")
    if m.startswith("models/"):
        m = m.removeprefix("models/")
    return m


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int, raw_usage: Any = None) -> float:
    if raw_usage is not None:
        direct_cost = getattr(raw_usage, "cost", None)
        if isinstance(direct_cost, (int, float)) and direct_cost > 0:
            return round(float(direct_cost), 6)
        extra = getattr(raw_usage, "model_extra", None) or {}
        if isinstance(extra.get("cost"), (int, float)):
            return round(float(extra["cost"]), 6)

    m = model.lower()
    if any(k in m for k in ("flash", "mini", "haiku", "nano", "lite")):
        in_rate, out_rate = 0.20, 0.80
    elif any(k in m for k in ("embedding", "embed", "text-search")):
        in_rate, out_rate = 0.02, 0.0
    else:
        in_rate, out_rate = 3.00, 12.00

    return round((prompt_tokens * in_rate + completion_tokens * out_rate) / 1_000_000, 6)


def _strip_thinking_tags(raw_text: str) -> str:
    """Удаляет блоки рассуждений <think>...</think>, которые возвращают reasoning-модели (DeepSeek, Qwen и др.)."""
    text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL | re.IGNORECASE)
    # Если блок <think> остался незакрытым из-за лимита токенов — обрезаем его
    if "<think>" in text.lower():
        idx = text.lower().find("<think>")
        after = text[idx + 7 :]
        brace_idx = after.find("{")
        text = (text[:idx] + (after[brace_idx:] if brace_idx != -1 else "")).strip()
    return text.strip()


def _repair_truncated_json(raw_json: str) -> str:
    """Восстанавливает обрезанный на полуслове JSON (закрывает незакрытую строку и открытые скобки)."""
    s = raw_json.strip()
    if not s.startswith("{"):
        start = s.find("{")
        if start != -1:
            s = s[start:]
        else:
            return "{}"

    in_str = False
    escape = False
    stack: list[str] = []
    out_chars: list[str] = []

    for ch in s:
        if escape:
            out_chars.append(ch)
            escape = False
            continue
        if ch == "\\":
            out_chars.append(ch)
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            out_chars.append(ch)
            continue
        if in_str:
            if ch == "\n":
                out_chars.append("\\n")
            elif ch == "\r":
                continue
            elif ch == "\t":
                out_chars.append("\\t")
            else:
                out_chars.append(ch)
            continue

        out_chars.append(ch)
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]") and stack and stack[-1] == ch:
            stack.pop()

    repaired = "".join(out_chars).rstrip()
    if escape and repaired.endswith("\\"):
        repaired = repaired[:-1]
    if in_str:
        repaired += '"'

    # Убираем висящие запятые или незаконченные ключи перед закрытием скобок
    repaired = re.sub(r",\s*$", "", repaired)
    repaired = re.sub(r',\s*"[^"]*"\s*:\s*$', "", repaired)
    repaired = re.sub(r'\{\s*"[^"]*"\s*:\s*$', "{", repaired)
    repaired = re.sub(r',\s*"[^"]*"\s*$', "", repaired)

    while stack:
        repaired = re.sub(r",\s*$", "", repaired)
        repaired += stack.pop()

    return repaired


def _extract_json_dict(raw_text: str) -> dict[str, Any]:
    text = _strip_thinking_tags(raw_text)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE).strip()

    start = text.find("{")
    if start != -1:
        end = text.rfind("}")
        if end != -1 and end > start:
            candidate = text[start : end + 1]
        else:
            candidate = text[start:]
    else:
        candidate = text

    for attempt_str in (candidate, _repair_truncated_json(candidate)):
        try:
            parsed = json.loads(attempt_str, strict=False)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    # Regex fallback для классификатора решений, если модель выдала совсем нестандартно экранированный JSON
    fallback: dict[str, Any] = {}
    m_act = re.search(r'"action"\s*:\s*"(ignore|react|reply|message|poll)"', text, flags=re.IGNORECASE)
    if m_act:
        fallback["action"] = m_act.group(1).lower()
    m_tid = re.search(r'"target_tg_msg_id"\s*:\s*(\d+)', text)
    if m_tid:
        fallback["target_tg_msg_id"] = int(m_tid.group(1))
    m_emj = re.search(r'"emoji"\s*:\s*"([^"]+)"', text)
    if m_emj:
        fallback["emoji"] = m_emj.group(1)
    m_rsn = re.search(r'"reasoning"\s*:\s*"([^"\n]*)', text)
    if m_rsn:
        fallback["reasoning"] = m_rsn.group(1).strip()
    if fallback:
        return fallback

    raise ValueError(f"Expected JSON object from LLM, got: {text[:180]}")


def _normalize_vector_dim(vec: list[float], target_dim: int = 1536) -> list[float]:
    if len(vec) > target_dim:
        vec = vec[:target_dim]
    elif len(vec) < target_dim:
        vec = vec + [0.0] * (target_dim - len(vec))

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 1e-12:
        vec = [x / norm for x in vec]
    return vec


async def _google_generate(
    api_key: str,
    use_vertex: bool,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
) -> tuple[str, LLMUsage]:
    clean_model = _clean_google_model_name(model)
    gen_cfg = genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=temperature,
        max_output_tokens=max_tokens,
        response_mime_type="application/json" if json_mode else None,
    )

    try:
        client = genai.Client(vertexai=use_vertex, api_key=api_key)
        resp = await client.aio.models.generate_content(
            model=clean_model,
            contents=user_prompt,
            config=gen_cfg,
        )
    except Exception as primary_exc:
        try:
            alt_client = genai.Client(vertexai=not use_vertex, api_key=api_key)
            resp = await alt_client.aio.models.generate_content(
                model=clean_model,
                contents=user_prompt,
                config=gen_cfg,
            )
        except Exception as secondary_exc:
            raise RuntimeError(f"Vertex: {primary_exc} | AI Studio: {secondary_exc}") from primary_exc

    content = (resp.text or "").strip()
    um = getattr(resp, "usage_metadata", None)
    p_tok = int(getattr(um, "prompt_token_count", 0) or 0) if um else 0
    c_tok = int(getattr(um, "candidates_token_count", 0) or 0) if um else 0
    usage = LLMUsage(
        model=clean_model,
        prompt_tokens=p_tok,
        completion_tokens=c_tok,
        cost_usd=_estimate_cost_usd(clean_model, p_tok, c_tok),
    )
    return content, usage


async def _build_openai_client_and_model(
    base_url: str,
    api_key: str,
    raw_model: str,
    kind: Literal["gpt", "emb"],
    timeout: float = 60.0,
) -> tuple[AsyncOpenAI, str]:
    project_id: Optional[str] = None
    effective_model = raw_model

    if _is_yandex_provider(base_url):
        effective_model, project_id = await _resolve_yandex_model_and_project(base_url, api_key, raw_model, kind)

    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        project=project_id,
        timeout=timeout,
    )
    return client, effective_model


async def complete_json(
    session: AsyncSession,
    tier: Literal["fast", "smart"],
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.25,
    max_tokens: int = 2500,
) -> tuple[dict[str, Any], LLMUsage]:
    cfg = await get_runtime_llm_config(session)
    if not cfg.llm_api_key:
        raise RuntimeError("LLM API ключ не настроен. Укажите его в Настройках админки или в .env.")

    # Reasoning-модели (DeepSeek, Qwen3, Gemini Thinking) расходуют max_tokens на внутренние рассуждения
    effective_max_tokens = max(max_tokens, 2500)
    raw_model = cfg.llm_fast_model if tier == "fast" else cfg.llm_smart_model
    is_google, is_vertex = _detect_google_mode(cfg.llm_base_url, cfg.llm_api_key)

    if is_google:
        content, usage = await _google_generate(
            api_key=cfg.llm_api_key,
            use_vertex=is_vertex,
            model=raw_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=effective_max_tokens,
            json_mode=True,
        )
        parsed = _extract_json_dict(content or "{}")
        return parsed, usage

    client, model = await _build_openai_client_and_model(
        cfg.llm_base_url, cfg.llm_api_key, raw_model, kind="gpt", timeout=60.0
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=effective_max_tokens,
            response_format={"type": "json_object"},
        )
    except Exception:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=effective_max_tokens,
        )

    content = (resp.choices[0].message.content or "{}") if resp.choices else "{}"
    usage_obj = resp.usage
    p_tok = getattr(usage_obj, "prompt_tokens", 0) or 0
    c_tok = getattr(usage_obj, "completion_tokens", 0) or 0
    usage = LLMUsage(
        model=model,
        prompt_tokens=p_tok,
        completion_tokens=c_tok,
        cost_usd=_estimate_cost_usd(model, p_tok, c_tok, usage_obj),
    )

    parsed = _extract_json_dict(content)
    return parsed, usage


async def complete_text(
    session: AsyncSession,
    tier: Literal["fast", "smart"],
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.82,
    max_tokens: int = 600,
) -> tuple[str, LLMUsage]:
    cfg = await get_runtime_llm_config(session)
    if not cfg.llm_api_key:
        raise RuntimeError("LLM API ключ не настроен. Укажите его в Настройках админки или в .env.")

    effective_max_tokens = max(max_tokens, 1600)
    raw_model = cfg.llm_fast_model if tier == "fast" else cfg.llm_smart_model
    is_google, is_vertex = _detect_google_mode(cfg.llm_base_url, cfg.llm_api_key)

    if is_google:
        txt, usage = await _google_generate(
            api_key=cfg.llm_api_key,
            use_vertex=is_vertex,
            model=raw_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=effective_max_tokens,
            json_mode=False,
        )
        return _strip_thinking_tags(txt), usage

    client, model = await _build_openai_client_and_model(
        cfg.llm_base_url, cfg.llm_api_key, raw_model, kind="gpt", timeout=60.0
    )
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=effective_max_tokens,
    )

    content = _strip_thinking_tags((resp.choices[0].message.content or "").strip()) if resp.choices else ""
    usage_obj = resp.usage
    p_tok = getattr(usage_obj, "prompt_tokens", 0) or 0
    c_tok = getattr(usage_obj, "completion_tokens", 0) or 0
    usage = LLMUsage(
        model=model,
        prompt_tokens=p_tok,
        completion_tokens=c_tok,
        cost_usd=_estimate_cost_usd(model, p_tok, c_tok, usage_obj),
    )
    return content, usage


async def create_embedding(session: AsyncSession, text_input: str) -> Optional[list[float]]:
    cfg = await get_runtime_llm_config(session)
    if not cfg.embedding_api_key or not text_input.strip():
        return None

    target_dim = env_settings.embedding_dimensions
    cleaned = text_input.strip()[:6000]
    is_google, is_vertex = _detect_google_mode(cfg.embedding_base_url, cfg.embedding_api_key)

    if is_google:
        raw_emb_model = _clean_google_model_name(cfg.embedding_model)
        candidate_models = (
            [raw_emb_model, "gemini-embedding-001", "text-embedding-004"]
            if "embed" in raw_emb_model.lower()
            else ["gemini-embedding-001", "text-embedding-004"]
        )
        for cand in dict.fromkeys(candidate_models):
            for v_flag in (is_vertex, not is_vertex):
                try:
                    g_client = genai.Client(vertexai=v_flag, api_key=cfg.embedding_api_key)
                    res = await g_client.aio.models.embed_content(
                        model=cand,
                        contents=cleaned,
                        config=genai_types.EmbedContentConfig(output_dimensionality=target_dim),
                    )
                    if res.embeddings and res.embeddings[0].values:
                        return _normalize_vector_dim(list(res.embeddings[0].values), target_dim)
                except Exception:
                    try:
                        g_client = genai.Client(vertexai=v_flag, api_key=cfg.embedding_api_key)
                        res = await g_client.aio.models.embed_content(model=cand, contents=cleaned)
                        if res.embeddings and res.embeddings[0].values:
                            return _normalize_vector_dim(list(res.embeddings[0].values), target_dim)
                    except Exception:
                        continue

        logger.warning("Google Embedding generation failed (fallback to text search).")
        return None

    if not cfg.embedding_model and not _is_yandex_provider(cfg.embedding_base_url):
        return None

    client, emb_model = await _build_openai_client_and_model(
        cfg.embedding_base_url, cfg.embedding_api_key, cfg.embedding_model, kind="emb", timeout=30.0
    )
    try:
        # encoding_format="float" обязателен для Yandex AI Studio (иначе openai-sdk шлёт base64 и получает 400)
        try:
            resp = await client.embeddings.create(
                model=emb_model,
                input=cleaned,
                encoding_format="float",
                dimensions=target_dim,
            )
        except Exception:
            try:
                resp = await client.embeddings.create(
                    model=emb_model,
                    input=cleaned,
                    encoding_format="float",
                )
            except Exception:
                resp = await client.embeddings.create(
                    model=emb_model,
                    input=cleaned,
                )
        raw_vec = list(resp.data[0].embedding)
        return _normalize_vector_dim(raw_vec, target_dim)
    except Exception as exc:
        logger.warning("Embedding generation failed (fallback to text search): %s", exc)
        return None


async def test_llm_connectivity(session: AsyncSession) -> dict[str, Any]:
    cfg = await get_runtime_llm_config(session)
    is_google, is_vertex = _detect_google_mode(cfg.llm_base_url, cfg.llm_api_key)
    is_yandex = _is_yandex_provider(cfg.llm_base_url)

    provider_label = (
        "Google Vertex AI Express Mode (google-genai)"
        if (is_google and is_vertex)
        else "Google Gemini API (google-genai)"
        if is_google
        else "Yandex AI Studio (OpenAI-compatible)"
        if is_yandex
        else f"OpenAI-compatible ({cfg.llm_base_url})"
    )

    results: dict[str, Any] = {
        "provider": provider_label,
        "fast_ok": False,
        "smart_ok": False,
        "embedding_ok": False,
        "errors": [],
    }

    try:
        res_fast, u_fast = await complete_json(
            session,
            tier="fast",
            system_prompt='Return JSON {"ok": true}',
            user_prompt="ping",
            max_tokens=60,
        )
        results["fast_ok"] = bool(res_fast.get("ok", True))
        results["fast_model"] = u_fast.model
    except Exception as exc:
        results["errors"].append(f"Fast LLM ({cfg.llm_fast_model}) [{provider_label}]: {exc}")

    try:
        txt_smart, u_smart = await complete_text(
            session,
            tier="smart",
            system_prompt="Ответь коротко: на связи",
            user_prompt="Проверка связи",
            max_tokens=40,
        )
        results["smart_ok"] = bool(txt_smart)
        results["smart_model"] = u_smart.model
        results["smart_sample"] = txt_smart
    except Exception as exc:
        results["errors"].append(f"Smart LLM ({cfg.llm_smart_model}) [{provider_label}]: {exc}")

    if is_google and (not results["fast_ok"] or not results["smart_ok"]) and cfg.llm_api_key:
        try:
            g_client = genai.Client(vertexai=is_vertex, api_key=cfg.llm_api_key)
            avail = []
            async for m in await g_client.aio.models.list():
                name = getattr(m, "name", "") or ""
                if "gemini" in name.lower():
                    avail.append(name.split("/")[-1])
                if len(avail) >= 12:
                    break
            if avail:
                results["errors"].append(f"Доступные модели для вашего ключа: {', '.join(avail)}")
        except Exception:
            pass

    if is_yandex and (not results["fast_ok"] or not results["smart_ok"]) and cfg.llm_api_key:
        _, y_models = await _fetch_yandex_catalog(cfg.llm_base_url, cfg.llm_api_key)
        if y_models:
            results["errors"].append(f"Доступные модели Yandex AI Studio: {', '.join(y_models[:12])}")

    vec = await create_embedding(session, "Тестовый эпизод памяти группы")
    results["embedding_ok"] = vec is not None and len(vec) == env_settings.embedding_dimensions
    if not results["embedding_ok"]:
        hint = (
            "emb://<folder_id>/text-embeddings-v2-doc/latest или text-embeddings-v2-doc"
            if is_yandex
            else "gemini-embedding-001 или text-embedding-3-small"
        )
        results["errors"].append(
            f"Embeddings: не удалось получить вектор (используйте {hint}; пока активен безопасный текстовый поиск)"
        )

    return results
