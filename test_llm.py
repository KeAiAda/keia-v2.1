# test_llm.py
# Первый настоящий разговор с моделью через Python!

import asyncio
from core import llm


async def main():
    # Простой запрос
    print("Отправляю запрос...")

    messages = [
        {"role": "system", "content": "Ты — Кея. Отвечай кратко, по-русски."},
        {"role": "user", "content": "Привет, ты кто?"},
    ]

    # Вызываем нашу функцию chat
    reply = await llm.chat(messages, max_tokens=100, temperature=0.7)

    print("Ответ модели:")
    print(reply)


# asyncio.run — запускает асинхронную функцию main() как программу.
if __name__ == "__main__":
    asyncio.run(main())