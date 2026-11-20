# core/relationships.py
# -------------------------------------------------------------------
# Система отношений Кеи с пользователями (уровень симпатии).
# -------------------------------------------------------------------

import json
import re
from core import config, storage, llm, logger, mood

# Путь к файлу с отношениями.
RELATIONSHIPS_FILE = config.MEMORY_DIR / "relationships.json"

def _load() -> dict:
    """Читает файл отношений. Если нет — создает пустой словарь."""
    return storage.load_json(RELATIONSHIPS_FILE, default={})

def _save(data: dict) -> None:
    """Сохраняет отношения на диск."""
    storage.atomic_write_json(RELATIONSHIPS_FILE, data)

def get_relation(user_id: int, name: str = None) -> dict:
    """Возвращает отношение к пользователю (создает дефолтное, если нет)."""
    data = _load()
    user_key = str(user_id)
    
    if user_key not in data:
        if user_id == config.OWNER_ID:
            data[user_key] = {"name": config.OWNER_NAME, "affection": 100, "status": "любимый хозяин"}
        else:
            data[user_key] = {"name": name or f"User_{user_id}", "affection": 0, "status": "незнакомец"}
        _save(data)
        
    return data[user_key]

def _status_for(user_id: int, affection: int) -> str:
    """Вычислить статус по уровню симпатии (покрывает ВЕСЬ диапазон)."""
    if affection >= 80:
        return "любимый хозяин" if user_id == config.OWNER_ID else "обожаемый"
    if affection >= 50:
        return "очень близкий"
    if affection >= 30:
        return "друг"
    if affection > 0:
        return "приятель"
    if affection == 0:
        return "незнакомец"
    if affection <= -50:
        return "враг"
    # от -49 до -1
    return "бесячий"

def update_affection(user_id: int, delta: int) -> None:
    """Изменяет уровень симпатии (например, +5 за комплимент, -10 за грубость)."""
    data = _load()
    user_key = str(user_id)

    # Если пользователя ещё нет в базе — создаём дефолтную запись,
    # иначе изменение симпатии молча терялось бы.
    if user_key not in data:
        if user_id == config.OWNER_ID:
            data[user_key] = {"name": config.OWNER_NAME, "affection": 100, "status": "любимый хозяин"}
        else:
            data[user_key] = {"name": f"User_{user_id}", "affection": 0, "status": "незнакомец"}

    old_affection = data[user_key].get("affection", 0)

    # Привилегия создателя: не может упасть ниже 80.
    if user_id == config.OWNER_ID:
        new_affection = max(80, min(100, old_affection + delta))
    else:
        new_affection = max(-100, min(100, old_affection + delta))

    data[user_key]["affection"] = new_affection
    data[user_key]["status"] = _status_for(user_id, new_affection)

    _save(data)

async def analyze_and_update(user_id: int, text: str, user_name: str):
    """Анализирует текст через LLM и меняет уровень симпатии."""
    prompt = (
        "Оцени, как этот пользователь относится к тебе (Кее).\n"
        "Верни строго JSON: {\"delta\": число_от_минус_10_до_плюс_10}\n"
        "Примеры:\n"
        "- Комплимент, забота, флирт -> +5\n"
        "- Обычное общение (привет, как дела) -> +1\n"
        "- Игнорирование, сухость -> 0\n"
        "- Грубость, оскорбление -> -10"
    )

    try:
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            model=config.LLM_UTILITY_MODEL,
            # ВАЖНО: gpt-5-nano тратит токены на reasoning, и они входят
            # в max_tokens. При лимите 30 бюджет съедался полностью и content
            # приходил пустым — симпатия никогда не менялась.
            max_tokens=2000,
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        if not raw or not raw.strip():
            logger.log("Пустой ответ LLM при анализе симпатии", source="relationship")
            return

        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()

        start_idx = raw.find('{')
        end_idx = raw.rfind('}')
        if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
            logger.log(f"Не найден JSON в ответе LLM: {raw[:120]!r}", source="relationship")
            return

        data = json.loads(raw[start_idx:end_idx+1])
        delta = int(data.get("delta", 0))
        # Ограничиваем разумными рамками на случай, если модель выдаст чушь.
        delta = max(-10, min(10, delta))
        # Настроение меняется всегда (в том числе при delta == 0), чтобы шло затухание.
        mood.apply_delta(delta)
        if delta != 0:
            update_affection(user_id, delta)
            logger.log(f"Симпатия к {user_name} изменилась на {delta}", source="relationship")
    except Exception as e:
        # Не глотаем ошибки молча — иначе проблемы не видно в логах.
        logger.log(f"Ошибка анализа симпатии: {type(e).__name__}: {e}", source="relationship")

def get_other_names(owner_id: int) -> list[str]:
    """Возвращает список имен всех пользователей (кроме владельца) для ревности."""
    data = _load()
    other_names = []
    for uid_str, info in data.items():
        if int(uid_str) != owner_id and info.get("name"):
            other_names.append(info["name"].lower())
    return other_names