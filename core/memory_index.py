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


def _dimensions() -> int | None:
    """Размерность эмбеддингов из конфига (None = не задана)."""
    return config.LLM_EMBED_DIMENSIONS


def _signature() -> str:
    """Подпись кэша: модель + размерность. При её смене кэш сбрасывается."""
    dim = _dimensions()
    return f"{_model()}|{dim if dim is not None else ''}"


def _key(text: str, signature: str) -> str:
    """Ключ кэша: sha1(подпись модели + текст)."""
    raw = f"{signature}:{text}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _round_vec(vec: list[float]) -> list[float]:
    """Округлить компоненты вектора до 5 знаков."""
    return [round(float(x), 5) for x in vec]


def _round_namespace(namespace: dict) -> dict:
    return {k: _round_vec(v) for k, v in namespace.items()}


def _load_cache(user_id: int, signature: str) -> dict:
    """Прочитать кэш. Если модель/размерность сменились — кэш сбрасываем."""
    data = storage.load_json(_cache_path(user_id), default={})
    if not isinstance(data, dict):
        data = {}

    if data.get("signature") != signature:
        return {"signature": signature, "namespaces": {}}

    namespaces = data.get("namespaces")
    if not isinstance(namespaces, dict):
        namespaces = {}

    return {"signature": signature, "namespaces": namespaces}


async def ensure_embeddings(user_id: int, namespace: str, texts: list[str]) -> list[list[float]]:
    """
    Вернуть эмбеддинги для texts, лениво досчитав только отсутствующие.
    Недостающие тексты считаются чанками по 32, кэш сохраняется после каждого
    чанка (прогресс не теряется при ошибке). Устаревшие записи чистятся.
    Кэш пишется только если были добавлены или удалены ключи.
    """
    model = _model()
    if not model:
        raise RuntimeError("LLM_EMBED_MODEL не задан")

    signature = _signature()
    keys = [_key(t, signature) for t in texts]

    cache = _load_cache(user_id, signature)
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

    added = 0
    chunk_size = 32

    for start in range(0, len(missing_texts), chunk_size):
        chunk_texts = missing_texts[start:start + chunk_size]
        chunk_keys = missing_keys[start:start + chunk_size]

        vectors = await llm.embed(chunk_texts)
        if len(vectors) != len(chunk_texts):
            raise RuntimeError("LLM вернул неверное число эмбеддингов")

        for key, vec in zip(chunk_keys, vectors):
            namespace_cache[key] = _round_vec(vec)

        added += len(chunk_texts)

        # Сохраняем прогресс после каждого чанка.
        cache["namespaces"][namespace] = _round_namespace(namespace_cache)
        storage.atomic_write_json(_cache_path(user_id), cache)

    # Оставляем только актуальные записи этого namespace (устаревшие чистим).
    current_keys = set(keys)
    cleaned = {
        k: _round_vec(v) for k, v in namespace_cache.items() if k in current_keys
    }
    removed = len(namespace_cache) - len(cleaned)

    cache["namespaces"][namespace] = cleaned

    if added or removed:
        storage.atomic_write_json(_cache_path(user_id), cache)

    total = sum(
        len(ns) for ns in cache["namespaces"].values() if isinstance(ns, dict)
    )
    logger.log(
        f"Эмбеддинги [{namespace}]: добавлено {added}, удалено {removed}, "
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
         min_similarity: float = MIN_SIMILARITY, query_text: str = "",
         source: str = "memory_index") -> list[dict]:
    """
    Отобрать top-limit элементов по гибридному скору:
    cosine + бонус за слова + бонус за свежесть.

    item — словарь с полями: vec (обязательно), text (для бонуса слов),
    date (опционально, для бонуса свежести). Возвращаются копии с полем score.
    Для подбора MIN_SIMILARITY в лог пишется топ-3 скора до отсечения порогом.
    """
    if not query_vec or not items:
        return []

    query_tokens = _tokenize(query_text)
    all_scored: list[tuple[float, float, dict]] = []

    for item in items:
        cos = cosine(query_vec, item.get("vec") or [])
        score = cos
        score += _word_bonus(query_tokens, item.get("text", ""))
        score += _fresh_bonus(item.get("date"))
        all_scored.append((score, cos, item))

    all_scored.sort(key=lambda x: x[0], reverse=True)

    preview = []
    for score, cos, item in all_scored[:3]:
        text = str(item.get("text", "")).replace("\n", " ")[:40]
        preview.append(f"{score:.3f} (cos={cos:.3f}) | {text}")
    logger.log(
        "Топ-3 скора: " + ("; ".join(preview) if preview else "нет элементов"),
        source=source,
    )

    result = []
    for score, cos, item in all_scored:
        if cos < min_similarity:
            continue

        copy = dict(item)
        copy["score"] = score
        result.append(copy)

        if len(result) >= limit:
            break

    return result


# -------------------------------------------------------------------
# 5. Сообщаем в лог, в каком режиме работаем
# -------------------------------------------------------------------

logger.log(
    "Семантический поиск "
    + ("включён" if is_enabled() else "выключен")
    + f" (модель: {_model() or 'не задана'}, "
    + f"размерность: {_dimensions() if _dimensions() is not None else 'по умолчанию'})",
    source="memory_index",
)
