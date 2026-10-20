# core/logger.py
# -------------------------------------------------------------------
# Единый логгер Keia.
# Модули (facts, search, sanitize) пишут сюда, а не в print.
# Кто подписан (TUI, CLI) — тот и показывает.
# -------------------------------------------------------------------

from datetime import datetime


# Подписчики — функции вида callback(message: str).
_subscribers: list = []

# Последние N сообщений — для отладки и «показать историю логов».
_recent: list[str] = []
MAX_RECENT = 50


def subscribe(callback) -> None:
    """Зарегистрировать подписчика. Он будет вызываться на каждое сообщение."""
    _subscribers.append(callback)


def get_recent(limit: int = 10) -> list[str]:
    """Последние N сообщений."""
    return _recent[-limit:]


def log(message: str, source: str = "") -> None:
    text = f"[{source}] {message}" if source else message
    
    _recent.append(text)
    if len(_recent) > MAX_RECENT:
        del _recent[:-MAX_RECENT]

    try:
        with open("/tmp/keia_all_logs.log", "a", encoding="utf-8") as f:
            timestamp = datetime.now().strftime("%H:%M:%S")
            f.write(f"{timestamp} {text}\n")
    except Exception:
        pass

    if not _subscribers:
        timestamp = datetime.now().strftime("%H:%M:%S")
        try:
            # Пытаемся напечатать в консоль
            print(f"{timestamp} {text}")
        except UnicodeEncodeError:
            # Если консоль не понимает кириллицу — просто пропускаем
            pass

    for cb in _subscribers:
        try:
            cb(text)
        except Exception:
            pass