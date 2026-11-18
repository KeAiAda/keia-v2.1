# core/facts.py
# -------------------------------------------------------------------
# Долговременная память: факты о пользователе.
# Формат файла: memory/facts/{user_id}.txt (Строки: [дата] ключ: значение)
# -------------------------------------------------------------------

import json
import re
from datetime import datetime

from core import config
from core import storage
from core import llm
from core import logger
from core import memory_index

# -------------------------------------------------------------------
# 1. Работа с файлом фактов (Чтение и Запись)
# -------------------------------------------------------------------

def _facts_path(user_id: int):
    """Путь к файлу фактов пользователя."""
    return config.FACTS_DIR / f"{user_id}.txt"

def _load(user_id: int) -> dict[str, tuple[str, str]]:
    """Прочитать файл фактов. Возвращает: {ключ: (значение, дата)}."""
    path = _facts_path(user_id)
    if not path.exists():
        return {}

    raw = storage.load_text(path)
    result = {}

    # Регулярка для парсинга строки вида: [20.10.2026] имя: значение
    pattern = re.compile(r"^\[(\d{2}\.\d{2}\.\d{4})\]\s+(.+?):\s+(.+)$")

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            date, key, value = m.groups()
            # Перезаписываем: если ключ уже был, последний факт важнее.
            result[key.strip()] = (value.strip(), date)

    return result

def _save(user_id: int, facts: dict[str, tuple[str, str]]) -> None:
    """Записать факты обратно в файл."""
    lines = []
    # Сортируем ключи по алфавиту для аккуратного вида.
    for key in sorted(facts.keys()):
        value, date = facts[key]
        lines.append(f"[{date}] {key}: {value}")

    text = "\n".join(lines)
    if text:
        text += "\n"

    storage.atomic_write_text(_facts_path(user_id), text)

def _add_or_replace(user_id: int, key: str, value: str) -> bool:
    """Добавить или обновить факт. Возвращает True, если данные изменились."""
    facts = _load(user_id)
    today = datetime.now().strftime("%d.%m.%Y")

    # Если такой же факт уже есть — ничего не делаем.
    old = facts.get(key)
    if old and old[0] == value:
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
    """Извлечь факт из сообщения и сохранить, если он есть."""
    
    # 1. Отсеиваем короткие сообщения ("Привет", "Ок").
    if len(user_text.strip()) < 5:
        return

    # 2. Загружаем промпт и подставляем имя.
    prompt_template = _extract_prompt()
    if not prompt_template:
        return 
    prompt = prompt_template.replace("{user_name}", user_name)

    # 3. Запрос к дешевой модели.
    try:
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_text},
            ],
            model=config.LLM_UTILITY_MODEL,
            # ВАЖНО: gpt-5-nano тратит токены на reasoning ("размышления"),
            # и они входят в max_tokens. При маленьком лимите (например, 100)
            # весь бюджет съедается на reasoning, а content возвращается пустым.
            # Поэтому даём запас.
            max_tokens=2000,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.log(f"Ошибка запроса к LLM: {e}", source="facts")
        return

    # 4. Защита: если пусто — выходим (но пишем в лог, чтобы не терять проблему).
    if not raw or not raw.strip():
        logger.log("Пустой ответ LLM при извлечении факта", source="facts")
        return

    # 5. Чистим от markdown-тегов.
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    # 6. Вырезаем JSON (ищем от первой { до последней }).
    start_idx = raw.find('{')
    end_idx = raw.rfind('}')
    
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        clean_json = raw[start_idx:end_idx+1]
    else:
        return

    # 7. Парсим JSON.
    try:
        data = json.loads(clean_json)
    except json.JSONDecodeError as e:
        short_raw = raw[:100].replace("\n", " ")
        logger.log(f"JSON ошибка: {e}. Сырой ответ: '{short_raw}...'", source="facts")
        return

    # 8. Проверяем тип и достаем данные.
    if data.get("type") != "FACT":
        return

    key = str(data.get("key") or "").strip().lower()
    value = str(data.get("value") or "").strip()

    if not key or not value:
        return

    # 9. Сохраняем и логируем.
    changed = _add_or_replace(user_id, key, value)
    if changed:
        logger.log(f"Запомнила: {key} = {value}", source="facts")


# -------------------------------------------------------------------
# 3. Получение релевантных фактов
# -------------------------------------------------------------------

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
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    words = cleaned.split()
    return [w for w in words if w not in _STOP_WORDS and len(w) > 2]

def get_relevant(user_id: int, query: str, limit: int = 8) -> str:
    """Вернуть релевантные факты в виде текста для системного промпта."""
    facts = _load(user_id)
    if not facts:
        return ""

    # Если фактов мало — отдаем все.
    if len(facts) <= limit:
        lines = [f"- {key}: {value}" for key, (value, _) in facts.items()]
        return "\n".join(lines)

    # Если фактов много — ищем по ключевым словам.
    query_tokens = set(_tokenize(query))
    
    def _parse_date(d: str):
        """DD.MM.YYYY -> datetime. При ошибке — минимальная дата (в самый конец)."""
        try:
            return datetime.strptime(d, "%d.%m.%Y")
        except (ValueError, TypeError):
            return datetime.min

    if not query_tokens:
        # Свежие факты — выше (дату парсим, а не сравниваем строкой).
        sorted_facts = sorted(
            facts.items(),
            key=lambda x: _parse_date(x[1][1]),
            reverse=True,
        )
        lines = [f"- {key}: {value}" for key, (value, _) in sorted_facts[:limit]]
        return "\n".join(lines)

    scored = []
    for key, (value, date) in facts.items():
        haystack = f"{key} {value}".lower()
        score = sum(1 for token in query_tokens if token in haystack)
        scored.append((score, key, value, date))

    # Сортировка: сначала по релевантности (больше — выше),
    # затем по дате (свежее — выше). Дату парсим, а не сравниваем строкой.
    scored.sort(key=lambda x: (x[0], _parse_date(x[3])), reverse=True)
    top = scored[:limit]
    lines = [f"- {key}: {value}" for _, key, value, _ in top]
    return "\n".join(lines)

async def get_relevant_async(user_id: int, query: str, limit: int = 8,
                             query_vec: list[float] | None = None) -> str:
    """
    Асинхронная версия поиска фактов с эмбеддингами.
    query_vec считается один раз в agent.py и передаётся готовым.
    При любой проблеме или выключенных эмбеддингах — старый поиск по словам.
    """
    facts = _load(user_id)
    if not facts:
        return ""

    # Если фактов мало — отдаем все, как и раньше.
    if len(facts) <= limit:
        lines = [f"- {key}: {value}" for key, (value, _) in facts.items()]
        return "\n".join(lines)

    if query_vec is None or not memory_index.is_enabled():
        return get_relevant(user_id, query, limit)

    items = []
    for key, (value, date) in facts.items():
        items.append({
            "text": f"{key}: {value}",
            "date": date,
            "key": key,
            "value": value,
        })

    try:
        vectors = await memory_index.ensure_embeddings(
            user_id, "facts", [item["text"] for item in items]
        )
        for item, vec in zip(items, vectors):
            item["vec"] = vec

        ranked = memory_index.rank(
            query_vec, items, limit, query_text=query
        )
        if not ranked:
            return get_relevant(user_id, query, limit)

        lines = [f"- {item['key']}: {item['value']}" for item in ranked]
        return "\n".join(lines)
    except Exception as e:
        logger.log(
            f"Семантический поиск фактов не удался, fallback на слова: {e}",
            source="facts",
        )
        return get_relevant(user_id, query, limit)
