# core/storage.py
# -------------------------------------------------------------------
# Модуль для безопасной работы с файлами (чтение/запись).
# -------------------------------------------------------------------

import json
# Стандартный модуль для работы с JSON-форматом.

import os
# Нужен для os.replace (атомарная подмена файла).

import tempfile
# Нужен для создания временных файлов.

from pathlib import Path
# Удобная работа с путями.

# -------------------------------------------------------------------
# 1. Атомарная запись текста (Защита от повреждения файлов)
# -------------------------------------------------------------------

def atomic_write_text(path: Path, text: str) -> None:
    """
    Записать текст в файл атомарно.
    Либо запись проходит полностью, либо файл остаётся прежним (не ломается).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Создаём временный файл в той же папке, где и целевой.
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".tmp_",
        suffix=".json",
    )

    try:
        # Записываем текст во временный файл.
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)

        # Атомарно заменяем старый файл новым.
        os.replace(tmp_path, path)

    except Exception:
        # Если произошла ошибка, удаляем временный файл.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

# -------------------------------------------------------------------
# 2. Запись JSON
# -------------------------------------------------------------------

def atomic_write_json(path: Path, data) -> None:
    """Сохранить Python-объект (dict/list) в файл как JSON."""
    # ensure_ascii=False — чтобы русские буквы не превращались в \uXXXX.
    # indent=4 — для красивого форматирования (чтобы можно было читать глазами).
    text = json.dumps(data, ensure_ascii=False, indent=4)
    atomic_write_text(path, text)

# -------------------------------------------------------------------
# 3. Чтение файлов
# -------------------------------------------------------------------

def load_json(path: Path, default=None):
    """Прочитать JSON из файла. Если файла нет или он битый — вернуть default."""
    path = Path(path)
    if not path.exists():
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Если JSON сломан или нет доступа — возвращаем дефолтное значение.
        return default

def load_text(path: Path) -> str:
    """Прочитать текстовый файл. Если нет — вернуть пустую строку."""
    path = Path(path)
    if not path.exists():
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

# -------------------------------------------------------------------
# 4. Дописывание в конец файла (для логов)
# -------------------------------------------------------------------

def append_line(path: Path, text: str) -> None:
    """Дописать строку в конец файла (не стирая старое)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Режим "a" (append) открывает файл для дописывания в конец.
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")