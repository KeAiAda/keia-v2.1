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


def get_system_prompt() -> str:
    """
    Собрать системный промпт из трёх частей.
    Кэшируется: файлы читаются один раз за запуск.
    """
    global _cached_prompt

    # Если уже собирали — вернуть готовое.
    if _cached_prompt is not None:
        return _cached_prompt

    # Читаем три части.
    persona = _read("persona")
    style = _read("style")
    examples = _read("examples")

    # Склеиваем с пустыми строками между частями.
    # Пустая строка помогает модели видеть границу между секциями.
    parts = [persona, style, examples]

    # Фильтруем пустые (если какой-то файл не создан).
    parts = [p for p in parts if p]

    # "\n\n" — две строки между секциями.
    _cached_prompt = "\n\n".join(parts)

    return _cached_prompt


def reload() -> None:
    """
    Сбросить кэш. Полезно при отладке:
    поменял persona.txt — вызвал reload() — следующий запрос увидит новое.
    """
    global _cached_prompt
    _cached_prompt = None