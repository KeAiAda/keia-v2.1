# core/mood.py
# -------------------------------------------------------------------
# Общее (не по пользователям) настроение Кеи.
# Хранится в memory/mood.json, со временем затухает к нулю.
# -------------------------------------------------------------------

from datetime import datetime

from core import config, storage, logger

# Путь к файлу настроения.
MOOD_FILE = config.MEMORY_DIR / "mood.json"

# Период полураспада valence (в часах): за это время отклонение уменьшается вдвое.
HALF_LIFE_HOURS = 6.0

# Сколько часов тишины от владельца считаем "скучает".
OWNER_SILENCE_HOURS = 6.0

# Ночные часы (по локальному времени, как в persona.get_time_context).
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 6


def _default() -> dict:
    return {"valence": 0.0, "updated_at": None, "last_owner_msg": None}


def _load() -> dict:
    """Читает состояние настроения. Если файла нет — возвращает дефолт."""
    data = storage.load_json(MOOD_FILE, default=None)
    if not isinstance(data, dict):
        return _default()

    # Гарантируем наличие всех полей.
    result = _default()
    result.update(data)
    return result


def _save(data: dict) -> None:
    """Сохраняет состояние настроения на диск."""
    storage.atomic_write_json(MOOD_FILE, data)


def _parse_dt(value):
    """Парсит ISO-время. При ошибке возвращает None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _decay(valence: float, updated_at, now: datetime) -> float:
    """Применяет экспоненциальное затухание valence к нулю."""
    if updated_at is None:
        return valence
    elapsed_hours = (now - updated_at).total_seconds() / 3600
    if elapsed_hours <= 0:
        return valence
    return valence * (0.5 ** (elapsed_hours / HALF_LIFE_HOURS))


def _current_valence(data: dict, now: datetime) -> float:
    """Текущий valence с учётом затухания (без записи)."""
    valence = float(data.get("valence", 0) or 0)
    return _decay(valence, _parse_dt(data.get("updated_at")), now)


def _state_for(data: dict, now: datetime) -> str:
    """Вычисляет текстовое состояние по данным и моменту времени."""
    valence = _current_valence(data, now)
    last_owner = _parse_dt(data.get("last_owner_msg"))

    # 1. Давно не было сообщений от владельца — скучает.
    if last_owner is not None:
        silence_hours = (now - last_owner).total_seconds() / 3600
        if silence_hours > OWNER_SILENCE_HOURS and valence > -40:
            return f"скучает по {config.OWNER_NAME}"

    # 2. Ночь.
    if now.hour >= NIGHT_START_HOUR or now.hour < NIGHT_END_HOUR:
        return "сонная"

    # 3. Настроение по valence.
    if valence <= -40:
        return "раздражена"
    if valence <= -15:
        return "немного не в духе"
    if valence >= 40:
        return "в приподнятом настроении"
    if valence >= 15:
        return "в хорошем настроении"
    return "спокойная"


def get_valence() -> float:
    """Текущий valence с учётом затухания (без записи)."""
    return _current_valence(_load(), datetime.now())


def get_state() -> str:
    """Текущее текстовое состояние настроения."""
    now = datetime.now()
    return _state_for(_load(), now)


def get_prompt_line() -> str:
    """Строка для системного промпта."""
    return (
        f"Твоё текущее настроение: {get_state()}. "
        "Пусть это слегка влияет на тон ответов, но не объявляй своё настроение прямо, "
        "если тебя об этом не спросили."
    )


def _log_change(before: str, after: str, valence: float) -> None:
    if before != after:
        logger.log(
            f"Настроение: '{before}' -> '{after}' (valence {valence:.1f})",
            source="mood",
        )


def apply_delta(delta) -> None:
    """Меняет valence на delta * 0.5 с предварительным затуханием."""
    data = _load()
    now = datetime.now()

    before = _state_for(data, now)
    decayed = _current_valence(data, now)
    new_valence = decayed + float(delta) * 0.5
    new_valence = max(-100.0, min(100.0, new_valence))

    data["valence"] = new_valence
    data["updated_at"] = now.isoformat()
    data.setdefault("last_owner_msg", None)
    _save(data)

    after = _state_for(data, now)
    _log_change(before, after, new_valence)


def set_valence(value) -> None:
    """Жёстко выставляет valence (для ручной проверки)."""
    data = _load()
    now = datetime.now()

    before = _state_for(data, now)
    new_valence = max(-100.0, min(100.0, float(value)))

    data["valence"] = new_valence
    data["updated_at"] = now.isoformat()
    data.setdefault("last_owner_msg", None)
    _save(data)

    after = _state_for(data, now)
    _log_change(before, after, new_valence)


def mark_owner_message() -> None:
    """Записывает текущее время как время последнего сообщения владельца."""
    data = _load()
    now = datetime.now()

    before = _state_for(data, now)
    data["last_owner_msg"] = now.isoformat()
    if not data.get("updated_at"):
        data["updated_at"] = now.isoformat()
    _save(data)

    after = _state_for(data, now)
    _log_change(before, after, _current_valence(data, now))
