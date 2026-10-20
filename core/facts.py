# core/facts.py
# -------------------------------------------------------------------
# Долговременная память: факты о пользователе.
# Формат файла: memory/facts/{user_id}.txt
# Строки: [дата] ключ: значение
# -------------------------------------------------------------------

import json
import re
from datetime import datetime

from core import config
from core import storage
from core import llm
from core import logger


# -------------------------------------------------------------------
# 1. Работа с файлом фактов
# -------------------------------------------------------------------

def _facts_path(user_id: int):
    """Путь к файлу фактов пользователя."""
    return config.FACTS_DIR / f"{user_id}.txt"


def _load(user_id: int) -> dict[str, tuple[str, str]]:
    """
    Прочитать файл фактов.
    Возвращает dict {ключ: (значение, дата)}.
    Если ключ встречается несколько раз — побеждает последний.
    """
    path = _facts_path(user_id)
    if not path.exists():
        return {}

    raw = storage.load_text(path)
    result = {}

    # Регулярка: [дата] ключ: значение
    # \d{2} — две цифры, \. — точка, .+? — не жадный захват.
    pattern = re.compile(r"^\[(\d{2}\.\d{2}\.\d{4})\]\s+(.+?):\s+(.+)$")

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            date, key, value = m.groups()
            # Перезаписываем — последний факт по ключу побеждает.
            result[key.strip()] = (value.strip(), date)

    return result


def _save(user_id: int, facts: dict[str, tuple[str, str]]) -> None:
    """Записать факты обратно в файл."""
    lines = []
    # Сортируем по ключу — стабильный порядок в файле.
    for key in sorted(facts.keys()):
        value, date = facts[key]
        lines.append(f"[{date}] {key}: {value}")

    text = "\n".join(lines)
    if text:
        text += "\n"

    storage.atomic_write_text(_facts_path(user_id), text)


def _add_or_replace(user_id: int, key: str, value: str) -> bool:
    """
    Добавить факт. Если ключ уже есть — заменить значение.
    Возвращает True, если что-то изменилось.
    """
    facts = _load(user_id)
    today = datetime.now().strftime("%d.%m.%Y")

    old = facts.get(key)
    if old and old[0] == value:
        # Такой же факт уже есть — не трогаем.
        return False

    facts[key] = (value, today)
    _save(user_id, facts)
    return True


# -------------------------------------------------------------------
# 2. Извлечение фактов через LLM
# -------------------------------------------------------------------

def _extract_prompt() -> str:
    """Загрузить промпт для извлечения фактов."""
    return storage.load_text(config.PROMPTS_DIR / "extract_facts.txt").strip()


async def extract(user_text: str, user_id: int, user_name: str = "Пользователь") -> None:
    """
    Извлечь факт из сообщения и сохранить, если он есть.
    """
    # 1. Отсеиваем слишком короткие сообщения
    if len(user_text.strip()) < 5:
        return

    # 2. Загружаем промпт и подставляем имя пользователя
    prompt_template = _extract_prompt()
    if not prompt_template:
        return
    prompt = prompt_template.replace("{user_name}", user_name)

    try:
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_text}, # Отправляем ТОЛЬКО текст пользователя
            ],
            model=config.LLM_UTILITY_MODEL,
            max_tokens=100,
            temperature=0.1, # Снизили температуру для максимальной точности
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.log(f"Ошибка запроса: {e}", source="facts")
        return

    try:
        # 3. Отдельный запрос к модели (используем дешевую модель)
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_text},
            ],
            model=config.LLM_UTILITY_MODEL,  # ← Передаем дешевую модель
            max_tokens=100,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.log(f"Ошибка запроса: {e}", source="facts")
        return

    # 4. Парсим ответ. Модель может добавить ```json ... ``` — чистим.
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.log(f"Не смог распарсить JSON: {raw[:80]}", source="facts")
        return

    if data.get("type") != "FACT":
        return

    key = str(data.get("key") or "").strip().lower()
    value = str(data.get("value") or "").strip()

    if not key or not value:
        return

    # 5. Сохраняем.
    changed = _add_or_replace(user_id, key, value)
    if changed:
        logger.log(f"Запомнила: {key} = {value}", source="facts")


# -------------------------------------------------------------------
# 3. Получение релевантных фактов
# -------------------------------------------------------------------

# Стоп-слова: не помогают при keyword-поиске, только шумят.
_STOP_WORDS = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как",
    "а", "то", "все", "всё", "она", "так", "его", "но", "да", "ты",
    "к", "у", "же", "вы", "за", "бы", "по", "только", "ее", "мне",
    "было", "вот", "от", "меня", "еще", "ещё", "нет", "о", "из",
    "ему", "теперь", "когда", "даже", "ну", "ли", "если", "уже",
    "или", "ни", "быть", "был", "него", "до", "вас", "вам", "там",
    "потом", "себя", "ей", "может", "они", "тут", "где", "есть",
    "надо", "ней", "для", "мы", "тебя", "их", "чем", "была", "сам",
    "чтоб", "без", "чего", "раз", "тоже", "себе", "под", "будет",
    "тогда", "кто", "этот", "того", "потому", "этого", "какой",
    "здесь", "этом", "один", "почти", "мой", "тем", "чтобы", "нее",
    "сейчас", "были", "куда", "зачем", "всех", "можно", "при",
    "другой", "хоть", "после", "над", "больше", "тот", "через",
    "эти", "нас", "про", "всего", "них", "какая", "много", "эту",
    "моя", "хорошо", "свою", "этой", "перед", "иногда", "лучше",
    "том", "нельзя", "такой", "им", "более", "всегда", "конечно",
}


def _tokenize(text: str) -> list[str]:
    """Разбить текст на значимые слова (без стоп-слов и знаков)."""
    # Убираем всё, кроме букв и пробелов.
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    # Разбиваем на слова.
    words = cleaned.split()
    # Убираем стоп-слова и слишком короткие.
    return [w for w in words if w not in _STOP_WORDS and len(w) > 2]


def get_relevant(user_id: int, query: str, limit: int = 8) -> str:
    """
    Вернуть релевантные факты в виде текста для промпта.
    Если фактов мало — возвращает все.
    Если много — top-N по keyword-совпадениям.
    """
    facts = _load(user_id)
    if not facts:
        return ""

    # Мало фактов — отдаём все, поиск не нужен.
    if len(facts) <= limit:
        lines = [f"- {key}: {value}" for key, (value, _) in facts.items()]
        return "\n".join(lines)

    # Много фактов — keyword-поиск.
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        # Если в запросе нет значимых слов — вернуть последние limit по дате.
        sorted_facts = sorted(facts.items(), key=lambda x: x[1][1], reverse=True)
        lines = [f"- {key}: {value}" for key, (value, _) in sorted_facts[:limit]]
        return "\n".join(lines)

    # Считаем score: сколько токенов запроса встречается в key или value.
    scored = []
    for key, (value, date) in facts.items():
        haystack = f"{key} {value}".lower()
        score = sum(1 for token in query_tokens if token in haystack)
        scored.append((score, key, value, date))

    # Сортируем: сначала по score (убывание), потом по дате (свежие сверху).
    scored.sort(key=lambda x: (-x[0], x[3]), reverse=False)

    # Берём top-limit.
    top = scored[:limit]
    lines = [f"- {key}: {value}" for _, key, value, _ in top]
    return "\n".join(lines)