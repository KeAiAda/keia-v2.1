# core/journal.py
# -------------------------------------------------------------------
# Дневник Кеи. Сохраняет логи за день и делает из них эмоциональную выжимку.
# -------------------------------------------------------------------

import os
import re
from datetime import datetime, timedelta

from core import config
from core import storage
from core import llm
from core import logger

# -------------------------------------------------------------------
# 1. Сохранение сырого лога дня
# -------------------------------------------------------------------

def log_message(user_id: int, role: str, text: str) -> None:
    """
    Записывает каждое сообщение в лог текущего дня.
    role: "user" или "assistant".
    """
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = config.LOGS_DIR / f"{user_id}_{today}.log"
    
    # Форматируем строку: [14:30:05] user: Привет!
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {role}: {text}"
    
    # Дописываем в файл (append_line не перезаписывает, а добавляет в конец)
    storage.append_line(log_path, entry)


# -------------------------------------------------------------------
# 2. Суммаризация (создание записи в дневнике)
# -------------------------------------------------------------------

async def summarize_day(user_id: int, date_str: str = None) -> str | None:
    """
    Берет лог за день, просит модель сделать выжимку и сохраняет в .md файл.
    """
    if not date_str:
        # Если дата не передана, берем сегодняшнюю
        date_str = datetime.now().strftime("%Y-%m-%d")
        
    log_path = config.LOGS_DIR / f"{user_id}_{date_str}.log"
    
    # Если лога за этот день нет — делать нечего
    if not log_path.exists():
        return None
        
    raw_log = storage.load_text(log_path)
    
    # Если сообщений было слишком мало (меньше 100 символов), дневник не пишем
    if len(raw_log) < 100:
        return None

    # Читаем промпт для дневника
    prompt = storage.load_text(config.PROMPTS_DIR / "journal.txt").strip()
    
    # Формируем запрос к модели
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Дата: {date_str}\n\nЛог чата:\n{raw_log}"}
    ]
    
    try:
        # Используем основную (умную) модель, так как нужно творчество
        summary = await llm.chat(messages, temperature=0.7, max_tokens=300)
    except Exception as e:
        logger.log(f"Ошибка при создании дневника: {e}", source="journal")
        return None

    # Сохраняем результат в файл .md
    journal_path = config.JOURNAL_DIR / f"{user_id}_{date_str}.md"
    storage.atomic_write_text(journal_path, summary)
    
    logger.log(f"Записала дневник за {date_str}", source="journal")
    return summary


# -------------------------------------------------------------------
# 3. Поиск по дневнику (чтобы Кея вспоминала прошлое)
# -------------------------------------------------------------------

def get_relevant_entries(user_id: int, query: str, limit: int = 2) -> str:
    """
    Ищет последние записи в дневнике. 
    Пока используем простой поиск по ключевым словам (как в facts).
    """
    if not config.JOURNAL_DIR.exists():
        return ""

    # Ищем все файлы дневника этого пользователя
    all_files = [f for f in os.listdir(config.JOURNAL_DIR) if f.startswith(f"{user_id}_") and f.endswith(".md")]
    
    if not all_files:
        return ""

    # Сортируем по дате (сначала новые)
    files = sorted(all_files, reverse=True)[:10] # Берем последние 10 дней для анализа
    
    # Простейший токенизатор (убираем знаки препинания и короткие слова)
    def tokenize(text):
        cleaned = re.sub(r"[^\w\s]", " ", text.lower())
        return [w for w in cleaned.split() if len(w) > 2]
        
    query_words = set(tokenize(query))
    
    scored_entries = []
    
    for filename in files:
        path = config.JOURNAL_DIR / filename
        content = storage.load_text(path)
        
        # Считаем, сколько слов из запроса есть в тексте дневника
        score = 0
        for word in query_words:
            if word in content.lower():
                score += 1
                
        # Извлекаем дату из имени файла (user_id_2026-10-18.md -> 2026-10-18)
        date_str = filename.replace(f"{user_id}_", "").replace(".md", "")
        scored_entries.append((score, date_str, content))

    # Сортируем по количеству совпадений
    scored_entries.sort(key=lambda x: x[0], reverse=True)
    
    # Берем top N записей
    top_entries = scored_entries[:limit]
    
    if not top_entries:
        return ""
        
    # Форматируем для вставки в промпт
    result = []
    for score, date_str, content in top_entries:
        if score > 0 or len(result) == 0:
            # Берем только первые 600 символов записи, чтобы не раздувать промпт
            short_content = content[:600] + "..." if len(content) > 600 else content
            result.append(f"### Запись от {date_str}:\n{short_content}")
            
    return "\n\n".join(result)