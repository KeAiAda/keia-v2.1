# test_facts.py
# Проверяем факты: извлечение, сохранение, поиск.

import asyncio
from core import facts


async def main():
    USER_ID = 999  # Тестовый пользователь, чтобы не мешать основному.

    # 1. Ручное добавление факта.
    facts._add_or_replace(USER_ID, "имя", "TestUser")
    facts._add_or_replace(USER_ID, "любит_пить", "кофе")
    facts._add_or_replace(USER_ID, "живёт", "один")
    print("1. Добавили 3 факта вручную.")
    print(facts.get_relevant(USER_ID, "как меня зовут?"))
    print()

    # 2. Извлечение из текста через LLM.
    print("2. Отправляю сообщение в LLM...")
    await facts.extract("Кстати, у меня есть кот по имени Барсик", USER_ID)
    print("Готово. Все факты:")
    print(facts.get_relevant(USER_ID, "расскажи что знаешь"))
    print()

    # 3. Замена факта.
    print("3. Меняю напиток с кофе на чай...")
    facts._add_or_replace(USER_ID, "любит_пить", "чай")
    print(facts.get_relevant(USER_ID, "что я люблю пить?"))
    print()

    # 4. Проверка keyword-поиска (когда фактов много).
    print("4. Добавляю много фактов для теста поиска...")
    for i in range(20):
        facts._add_or_replace(USER_ID, f"факт_{i}", f"значение номер {i}")
    print("Поиск по запросу 'как меня зовут':")
    print(facts.get_relevant(USER_ID, "как меня зовут?"))


if __name__ == "__main__":
    asyncio.run(main())