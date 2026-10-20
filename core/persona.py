# core/persona.py
# -------------------------------------------------------------------
# Собирает системный промпт из трёх файлов: persona, style, examples.
# Загружает их один раз при первом вызове, потом держит в памяти.
# -------------------------------------------------------------------

from core import config
from core import storage

# Кэш — сюда положим собранный промпт, чтобы не читать файлы каждый раз.
_cached_prompt: str | None = None


def _read(name: str) -> str:
    """
    Прочитать файл промпта по имени (без .txt).
    Например, _read("persona") → содержимое core/prompts/persona.txt
    """
    # config.PROMPTS_DIR — папка с промптами
    # / f"{name}.txt" — приклеиваем имя файла
    path = config.PROMPTS_DIR / f"{name}.txt"

    # storage.load_text вернёт "" если файла нет — не упадёт.
    return storage.load_text(path).strip()


def get_system_prompt(is_owner: bool = True, current_user_name: str = "KeAiAda") -> str:
    """
    Собрать системный промпт из трёх частей.
    В зависимости от того, кто пишет, добавляем правила.
    """
    global _cached_prompt
    
    # Читаем базу (характер, стиль, примеры)
    persona = _read("persona")
    style = _read("style")
    examples = _read("examples")
    
    parts = [persona, style, examples]
    parts = [p for p in parts if p]
    
    base_prompt = "\n\n".join(parts)
    
    # Если пишет НЕ владелец (например, сестра или гость)
    if not is_owner:
        guest_addon = (
            f"\n\nВАЖНО: Сейчас с тобой говорит не KeAiAda, а другой человек по имени {current_user_name}. "
            "Веди себя вежливо, но помни, что это не твой хозяин. "
            "Не используй факты из памяти KeAiAda для этого человека, если он сам о них не скажет. "
            "Можешь быть немного сдержаннее в эмоциях."
        )
        return base_prompt + guest_addon
        
    # Если пишет сам KeAiAda
    return base_prompt

def reload() -> None:
    """
    Сбросить кэш. Полезно при отладке:
    поменял persona.txt — вызвал reload() — следующий запрос увидит новое.
    """
    global _cached_prompt
    _cached_prompt = None

def get_time_context() -> str:
    """
    Строка с текущей датой и временем для промпта.
    Формат: "Сейчас: 13 октября 2026, вторник, 14:30"
    """
    from datetime import datetime

    now = datetime.now()

    # Русские названия месяцев. strftime их не даёт — пишем вручную.
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    # Дни недели. weekday() возвращает 0 = понедельник.
    weekdays = [
        "понедельник", "вторник", "среда", "четверг",
        "пятница", "суббота", "воскресенье",
    ]

    day = now.day
    month = months[now.month - 1]
    year = now.year
    weekday = weekdays[now.weekday()]
    time_str = now.strftime("%H:%M")

    return f"Сейчас: {day} {month} {year}, {weekday}, {time_str}"