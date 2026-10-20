import os
import sys
from dotenv import load_dotenv

# Загружаем .env из корня проекта
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Добавляем корень проекта в пути (на всякий случай, чтобы ui импортировался)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Импортируем функцию запуска
from ui.discord_bot import run_bot

if __name__ == "__main__":
    print("Запускаем Keia OS...")
    run_bot()