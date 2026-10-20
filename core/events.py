# core/events.py
# -------------------------------------------------------------------
# Блокнот Кеи для будущих событий.
# -------------------------------------------------------------------

import json
import re
from datetime import datetime
from core import config, storage, llm, logger

def _events_path(user_id: int):
    """Путь к файлу блокнота пользователя."""
    return config.MEMORY_DIR / f"notes_{user_id}.json"

def get_events(user_id: int) -> str:
    """Возвращает список событий для промпта."""
    path = _events_path(user_id)
    if not path.exists():
        return ""
    
    events = storage.load_json(path, default=[])
    if not events:
        return ""
        
    # Форматируем в красивый список
    lines = ["Твой блокнот (важные события):"]
    for ev in events:
        lines.append(f"- {ev}")
    return "\n".join(lines)

def _save_event(user_id: int, event_text: str):
    """Сохраняет новое событие, избегая дублей."""
    path = _events_path(user_id)
    events = storage.load_json(path, default=[])
    
    # Если такого события еще нет — добавляем
    if event_text not in events:
        events.append(event_text)
        # Ограничиваем блокнот 10 последними событиями
        events = events[-10:] 
        storage.atomic_write_json(path, events)
        logger.log(f"Записала в блокнот: {event_text}", source="events")

async def extract_events(user_text: str, user_id: int):
    """Извлекает события из текста пользователя."""
    if len(user_text.strip()) < 5:
        return

    prompt = storage.load_text(config.PROMPTS_DIR / "extract_events.txt").strip()
    if not prompt:
        return

    try:
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_text},
            ],
            model=config.LLM_UTILITY_MODEL,
            max_tokens=100,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.log(f"Ошибка извлечения события: {e}", source="events")
        return

    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    if data.get("type") == "EVENT" and data.get("text"):
        _save_event(user_id, data["text"])