# core/logger.py
# -------------------------------------------------------------------
# Единый логгер.
# Модули пишут сюда, а бот выводит это в консоль и файл.
# -------------------------------------------------------------------

from datetime import datetime
from core import config

# Подписчики (например, Discord-бот), которые будут выводить логи в чат.
_subscribers: list = []

# Последние N сообщений в памяти (на случай, если нужно посмотреть историю).
_recent: list[str] = []
MAX_RECENT = 50


def subscribe(callback) -> None:
    """Зарегистрировать подписчика (функцию), которая будет вызвана при логе."""
    _subscribers.append(callback)


def log(message: str, source: str = "") -> None:
    """Записать сообщение в лог."""
    # Форматируем строку: если есть источник, пишем [source] text.
    text = f"[{source}] {message}" if source else message
    
    # Сохраняем в список последних сообщений.
    _recent.append(text)
    if len(_recent) > MAX_RECENT:
        del _recent[:-MAX_RECENT]

    # Пишем в файл в папке logs (чтобы не терять при перезагрузке).
    log_file = config.LOGS_DIR / "system.log"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            timestamp = datetime.now().strftime("%H:%M:%S")
            f.write(f"{timestamp} {text}\n")
    except Exception:
        pass

    # Если нет подписчиков (мы запустили просто скрипт), печатаем в консоль.
    if not _subscribers:
        timestamp = datetime.now().strftime("%H:%M:%S")
        try:
            print(f"{timestamp} {text}")
        except UnicodeEncodeError:
            pass

    # Вызываем всех подписчиков (например, бота в Дискорде).
    for cb in _subscribers:
        try:
            cb(text)
        except Exception:
            pass