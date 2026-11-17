# core/events.py
# -------------------------------------------------------------------
# Блокнот Кеи для будущих событий.
# Хранит список событий в JSON (до MAX_EVENTS штук на пользователя).
# Новые события кладём в НАЧАЛО списка, поэтому старые вытесняются первыми.
# -------------------------------------------------------------------

import json
import re
from core import config, storage, llm, logger

# Сколько событий максимум держим в блокноте на одного пользователя.
MAX_EVENTS = 10

# Сколько токенов даём модели на ответ.
# ВАЖНО: gpt-5-nano (и подобные reasoning-модели) тратят токены на "размышления",
# и они входят в max_tokens. При маленьком лимите (100) весь бюджет съедается
# на reasoning, а content возвращается пустым. Раньше из-за этого блокнот
# молча не пополнялся. Поэтому даём запас.
MAX_TOKENS = 2000


def _events_path(user_id: int):
    """Путь к файлу блокнота пользователя."""
    return config.MEMORY_DIR / f"notes_{user_id}.json"


def get_events(user_id: int) -> str:
    """Возвращает список событий в виде текста для системного промпта."""
    path = _events_path(user_id)
    if not path.exists():
        return ""

    events = storage.load_json(path, default=[])
    if not events:
        return ""

    # Новые события лежат в начале списка — так они и попадут в промпт первыми.
    lines = ["Твой блокнот (важные события):"]
    for ev in events:
        lines.append(f"- {ev}")

    return "\n".join(lines)


def _save_event(user_id: int, event_text: str) -> None:
    """Сохраняет новое событие в начало блокнота, избегая дублей."""
    event_text = event_text.strip()
    if not event_text:
        return

    path = _events_path(user_id)
    events = storage.load_json(path, default=[])

    # storage.load_json мог вернуть не список — защищаемся.
    if not isinstance(events, list):
        events = []

    # Убираем старое вхождение того же события (без учёта регистра),
    # чтобы оно не дублировалось и переехало в начало как свежее.
    lowered = event_text.lower()
    events = [e for e in events if str(e).strip().lower() != lowered]

    # Новое событие — в начало списка.
    events.insert(0, event_text)

    # Ограничиваем блокнот последними MAX_EVENTS событиями.
    events = events[:MAX_EVENTS]

    storage.atomic_write_json(path, events)
    logger.log(f"Записала в блокнот: {event_text}", source="events")


def _clean_json(raw: str) -> str:
    """Убирает markdown-обёртку и мусор вокруг JSON."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw).strip()

    start_idx = raw.find("{")
    end_idx = raw.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        return raw[start_idx:end_idx + 1]
    return ""


async def extract_events(user_text: str, user_id: int) -> None:
    """Извлекает события из текста пользователя и пишет их в блокнот."""
    if len(user_text.strip()) < 5:
        return

    prompt = storage.load_text(config.PROMPTS_DIR / "extract_events.txt").strip()
    if not prompt:
        logger.log("Промпт extract_events.txt пуст — пропускаю", source="events")
        return

    try:
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_text},
            ],
            model=config.LLM_UTILITY_MODEL,
            max_tokens=MAX_TOKENS,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.log(f"Ошибка извлечения события: {type(e).__name__}: {e}", source="events")
        return

    # Пустой ответ — не глотаем молча, пишем в лог для отладки.
    if not raw or not raw.strip():
        logger.log("Пустой ответ LLM при извлечении события", source="events")
        return

    clean_json = _clean_json(raw)
    if not clean_json:
        logger.log(f"Не найден JSON в ответе LLM: {raw[:120]!r}", source="events")
        return

    try:
        data = json.loads(clean_json)
    except json.JSONDecodeError as e:
        logger.log(f"JSON ошибка (events): {e}. Сырой ответ: '{raw[:100]}...'", source="events")
        return

    if not isinstance(data, dict):
        return

    if data.get("type") == "EVENT":
        text = str(data.get("text") or "").strip()
        if text:
            _save_event(user_id, text)
