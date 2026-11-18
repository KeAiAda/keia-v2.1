# core/memory_index.py
# -------------------------------------------------------------------
# Семантический индекс памяти (эмбеддинги фактов и дневника).
# Работает без numpy: косинус считается на чистом Python.
# Кэш лежит рядом с остальной памятью пользователя: memory/embeddings_{id}.json
# -------------------------------------------------------------------

import hashlib
import math
import re
from datetime import datetime

from core import config
from core import storage
from core import llm
from core import logger

# Минимальная косинусная схожесть, ниже которой элемент не попадает в промпт.
MIN_SIMILARITY = 0.20

# Максимальные бонусы (маленькие, чтобы не перебивать косинус).
WORD_BONUS_MAX = 0.10
FRESH_BONUS_MAX = 0.05

# Сколько дней считать "свежим" (затухание бонуса).
FRESH_DAYS = 30.0


# -------------------------------------------------------------------
# 1. Включён ли семантический режим
# -------------------------------------------------------------------

def is_enabled() -> bool:
    """True, если в конфиге задана модель эмбеддингов."""
    return bool(config.LLM_EMBED_MODEL.strip())


def _model() -> str:
    return config.LLM_EMBED_MODEL.strip()


# -------------------------------------------------------------------
# 2. Косинусная близость и вспомогательные функции
# -------------------------------------------------------------------

def cosine(a: list[float], b: list[float]) -> float:
    """Косинус между двумя векторами (без numpy)."""
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _tokenize(text: str) -> set[str]:
    """Разбить текст на значимые слова для бонуса за совпадение."""
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return {w for w in cleaned.split() if len(w) > 2}


def _parse_date(value) -> datetime | None:
    """Понять дату в форматах DD.MM.YYYY и YYYY-MM-DD."""
    if isinstance(value, datetime):
        return value
    if not value or not isinstance(value, str):
        return None

    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except (ValueError, TypeError):
            continue

    return None


def _word_bonus(query_tokens: set[str], text: str) -> float:
    """Небольшой бонус за совпадение слов запроса и текста."""
    if not query_tokens:
        return 0.0

    tokens = _tokenize(text)
    if not tokens:
        return 0.0

    overlap = len(query_tokens & tokens) / len(query_tokens)
    return WORD_BONUS_MAX * overlap


def _fresh_bonus(date_value) -> float:
    """Небольшой бонус за свежесть (последние дни весят чуть больше)."""
    dt = _parse_date(date_value)
    if dt is None:
        return 0.0

    age_days = (datetime.now() - dt).days
    if age_days < 0:
        age_days = 0

    return FRESH_BONUS_MAX * math.exp(-age_days / FRESH_DAYS)


# -------------------------------------------------------------------
# 3. Кэш эмбеддингов
# -------------------------------------------------------------------

def _cache_path(user_id: int):
    """Путь к кэшу эмбеддингов пользователя."""
    return config.MEMORY_DIR / f"embeddings_{user_id}.json"


def _key(text: str, model: str) -> str:
    """Ключ кэша: sha1(модель + текст)."""
    raw = f"{model}:{text}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _load_cache(user_id: int, model: str) -> dict:
    """Прочитать кэш. Если модель сменилась — старый кэш сбрасываем."""
    data = storage.load_json(_cache_path(user_id), default={})
    if not isinstance(data, dict):
        data = {}

    if data.get("model") != model:
        return {"model": model, "namespaces": {}}

    namespaces = data.get("namespaces")
    if not isinstance(namespaces, dict):
        namespaces = {}

    return {"model": model, "namespaces": namespaces}


async def ensure_embeddings(user_id: int, namespace: str, texts: list[str]) -> list[list[float]]:
    """
    Вернуть эмбеддинги для texts, лениво досчитав только отсутствующие.
    Кэш чистится: записи, которых больше нет в texts, удаляются из namespace.
    """
    model = _model()
    if not model:
        raise RuntimeError("LLM_EMBED_MODEL не задан")

    keys = [_key(t, model) for t in texts]

    cache = _load_cache(user_id, model)
    namespace_cache = cache["namespaces"].get(namespace)
    if not isinstance(namespace_cache, dict):
        namespace_cache = {}

    missing_texts: list[str] = []
    missing_keys: list[str] = []
    seen: set[str] = set()

    for key, text in zip(keys, texts):
        if key in seen:
            continue
        seen.add(key)
        if key not in namespace_cache:
            missing_texts.append(text)
            missing_keys.append(key)

    if missing_texts:
        vectors = await llm.embed(missing_texts)
        if len(vectors) != len(missing_texts):
            raise RuntimeError("LLM вернул неверное число эмбеддингов")

        for key, vec in zip(missing_keys, vectors):
            namespace_cache[key] = vec

    # Оставляем только актуальные записи этого namespace (устаревшие чистим).
    current_keys = set(keys)
    cleaned = {k: v for k, v in namespace_cache.items() if k in current_keys}

    cache["namespaces"][namespace] = cleaned
    storage.atomic_write_json(_cache_path(user_id), cache)

    total = sum(
        len(ns) for ns in cache["namespaces"].values() if isinstance(ns, dict)
    )
    logger.log(
        f"Эмбеддинги [{namespace}]: добавлено {len(missing_texts)}, "
        f"в кэше {total}",
        source="memory_index",
    )

    return [cleaned.get(key) for key in keys]


async def embed_query(text: str) -> list[float] | None:
    """Посчитать эмбеддинг поискового запроса (один раз за сообщение)."""
    if not is_enabled():
        return None

    vectors = await llm.embed([text])
    if not vectors:
        return None
    return vectors[0]


# -------------------------------------------------------------------
# 4. Гибридный ранжировщик
# -------------------------------------------------------------------

def rank(query_vec: list[float], items: list[dict], limit: int,
         min_similarity: float = MIN_SIMILARITY, query_text: str = "") -> list[dict]:
    """
    Отобрать top-limit элементов по гибридному скору:
    cosine + бонус за слова + бонус за свежесть.

    item — словарь с полями: vec (обязательно), text (для бонуса слов),
    date (опционально, для бонуса свежести). Возвращаются копии с полем score.
    """
    if not query_vec or not items:
        return []

    query_tokens = _tokenize(query_text)
    scored: list[tuple[float, dict]] = []

    for item in items:
        cos = cosine(query_vec, item.get("vec") or [])
        if cos < min_similarity:
            continue

        score = cos
        score += _word_bonus(query_tokens, item.get("text", ""))
        score += _fresh_bonus(item.get("date"))

        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)

    result = []
    for score, item in scored[:limit]:
        copy = dict(item)
        copy["score"] = score
        result.append(copy)

    return result


# -------------------------------------------------------------------
# 5. Сообщаем в лог, в каком режиме работаем
# -------------------------------------------------------------------

logger.log(
    "Семантический поиск "
    + ("включён" if is_enabled() else "выключен")
    + f" (модель: {_model() or 'не задана'})",
    source="memory_index",
)
