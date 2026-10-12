# test_storage.py
# Проверяем, что storage.py работает: пишем, читаем, дописываем.

from pathlib import Path
from core import storage
from core import config

# Создадим временную папку для теста
test_dir = config.MEMORY_DIR / "_test"
test_dir.mkdir(exist_ok=True)

# 1. Тест: записать и прочитать JSON
json_path = test_dir / "test.json"
data = {"user_id": 123, "history": ["привет", "как дела?"], "nested": {"a": 1}}
storage.atomic_write_json(json_path, data)

loaded = storage.load_json(json_path)
print("1. JSON записан и прочитан:", loaded)
assert loaded == data, "JSON не совпадает!"

# 2. Тест: прочитать несуществующий файл — вернёт default
missing = storage.load_json(test_dir / "nope.json", default="EMPTY")
print("2. Отсутствующий файл:", missing)
assert missing == "EMPTY", "Default не сработал!"

# 3. Тест: append_line
log_path = test_dir / "test.log"
storage.append_line(log_path, "первая строка")
storage.append_line(log_path, "вторая строка")
text = storage.load_text(log_path)
print("3. Файл после двух append_line:")
print(repr(text))
assert "первая строка" in text
assert "вторая строка" in text

# 4. Тест: битый JSON — не падаем
bad_path = test_dir / "bad.json"
storage.atomic_write_text(bad_path, "{это не JSON")
recovered = storage.load_json(bad_path, default={"fallback": True})
print("4. Битый JSON восстановлен:", recovered)
assert recovered == {"fallback": True}, "Fallback не сработал!"

print("\n✅ Все тесты прошли!")

# Уберём за собой
import shutil
shutil.rmtree(test_dir)