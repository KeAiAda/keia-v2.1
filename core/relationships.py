# core/relationships.py
# -------------------------------------------------------------------
# Система отношений Кеи с пользователями.
# Хранит уровни симпатии и статусы в JSON-файле.
# -------------------------------------------------------------------

from core import config
from core import storage
from core import logger
import json

# Путь к файлу с отношениями
RELATIONSHIPS_FILE = config.MEMORY_DIR / "relationships.json"

def _load() -> dict:
    """Читает файл отношений. Если нет — создает пустой словарь."""
    return storage.load_json(RELATIONSHIPS_FILE, default={})

def _save(data: dict) -> None:
    """Сохраняет отношения на диск."""
    storage.atomic_write_json(RELATIONSHIPS_FILE, data)

def get_relation(user_id: int, name: str = None) -> dict:
    """
    Возвращает отношение к пользователю.
    Если пользователя нет в базе — создает дефолтное отношение.
    """
    data = _load()
    user_key = str(user_id)
    
    # Если пользователя еще нет — создаем базовую запись
    if user_key not in data:
        # Если это владелец (сравниваем с OWNER_ID из конфига)
        if user_id == config.OWNER_ID:
            data[user_key] = {
                "name": "KeAiAda", 
                "affection": 100, 
                "status": "любимый хозяин"
            }
        else:
            # Для всех остальных — нейтральное отношение
            data[user_key] = {
                "name": name or f"User_{user_id}", 
                "affection": 0, 
                "status": "незнакомец"
            }
        _save(data) # Сохраняем, чтобы в следующий раз уже было
        
    return data[user_key]

def update_affection(user_id: int, delta: int) -> None:
    """
    Изменяет уровень симпатии (например, +5 за хороший комплимент, -10 за грубость).
    """
    from core import config # Импортируем внутри функции, чтобы избежать циклов
    
    data = _load()
    user_key = str(user_id)
    
    if user_key in data:
        old_affection = data[user_key]["affection"]
        
        # ПРИВИЛЕГИЯ СОЗДАТЕЛЯ: Твой уровень не может упасть ниже 80.
        # Даже если ты её назовешь дурой, она внутри всё равно тебя любит.
        if user_id == config.OWNER_ID:
            new_affection = max(80, min(100, old_affection + delta))
        else:
            # Для всех остальных — стандартные рамки от -100 до 100
            new_affection = max(-100, min(100, old_affection + delta))
            
        data[user_key]["affection"] = new_affection
        
        # Автоматически обновляем статус в зависимости от симпатии
        if new_affection >= 80:
            data[user_key]["status"] = "любимый хозяин" if user_id == config.OWNER_ID else "обожаемый"
        elif new_affection >= 30:
            data[user_key]["status"] = "друг"
        elif new_affection <= -50:
            data[user_key]["status"] = "враг"
        elif new_affection <= -10:
            data[user_key]["status"] = "бесячий"
            
        _save(data)