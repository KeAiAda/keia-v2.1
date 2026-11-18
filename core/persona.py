# core/persona.py
# -------------------------------------------------------------------
# Собирает системный промпт из трёх файлов: persona, style, examples.
# -------------------------------------------------------------------

from core import config
from core import storage
from datetime import datetime # Перенесли импорт наверх

def _read(name: str) -> str:
    """
    Прочитать файл промпта по имени (без .txt).
    Например, _read("persona") → содержимое core/prompts/persona.txt
    """
    path = config.PROMPTS_DIR / f"{name}.txt"
    # storage.load_text вернёт "" если файла нет — не упадёт.
    text = storage.load_text(path).strip()
    # Подставляем имя владельца в плейсхолдер {owner_name}.
    return text.replace("{owner_name}", config.OWNER_NAME)


def get_system_prompt(is_owner: bool = True, current_user_name: str = config.OWNER_NAME) -> str:
    """
    Собрать системный промпт из трёх частей.
    В зависимости от того, кто пишет, добавляем правила.
    """
    # Читаем базу (характер, стиль, примеры)
    persona = _read("persona")
    style = _read("style")
    examples = _read("examples")
    
    # Склеиваем только непустые файлы
    parts = [persona, style, examples]
    parts = [p for p in parts if p]
    base_prompt = "\n\n".join(parts)
    
    # Если пишет НЕ владелец (например, сестра или гость)
    if not is_owner:
        guest_addon = (
            f"\n\nВАЖНО: Сейчас с тобой говорит не {config.OWNER_NAME}, а другой человек по имени {current_user_name}. "
            "Веди себя вежливо, но помни, что это не твой хозяин. "
            f"Не используй факты из памяти {config.OWNER_NAME} для этого человека, если он сам о них не скажет. "
            "Можешь быть немного сдержаннее в эмоциях."
        )
        return base_prompt + guest_addon
        
    # Если пишет сам владелец
    return base_prompt

def get_time_context() -> str:
    """
    Строка с текущей датой и временем для промпта.
    Формат: "Сейчас: 13 октября 2026, вторник, 14:30"
    """
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