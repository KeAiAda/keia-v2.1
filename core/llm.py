# core/llm.py
# -------------------------------------------------------------------
# Клиент к LLM.
# Сейчас — локальный llama-server, потом можно переключить на облако,
# просто поменяв .env (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL).
# -------------------------------------------------------------------

from openai import AsyncOpenAI
# AsyncOpenAI — асинхронный клиент. Мы используем асинхронность, потому что
# Кея будет ждать ответа модели, не блокируя другие задачи (напоминания,
# проактивность, чтение сообщений).
# Установится командой: pip install openai

from core import config

import httpx
from core import config

# -------------------------------------------------------------------
# 1. Клиент
# -------------------------------------------------------------------

# Создаём один клиент на весь проект — так правильнее, чем новый на каждый запрос.
# Внутри клиент держит пул HTTP-соединений, что экономит время и ресурсы.

# 1. Вычищаем API-ключ от любых случайных кириллических символов
clean_api_key = "".join(c for c in config.LLM_API_KEY if c.isascii()).strip()

# 2. Создаем свой http-клиент, который ИГНОРИРУЕТ системные прокси и VPN
# trust_env=False — спасает от ошибки ascii codec с ВПН
http_client = httpx.AsyncClient(trust_env=False, timeout=40.0)

client = AsyncOpenAI(
    # base_url — куда стучаться. Из config (читается из .env).
    base_url=config.LLM_BASE_URL,
    # api_key — для локального llama-server ключ не нужен, но клиент требует строку.
    api_key=config.LLM_API_KEY,
    http_client=http_client, # <--- вот это спасает от ошибки
)


# -------------------------------------------------------------------
# 2. Основная функция — поговорить с моделью
# -------------------------------------------------------------------

async def chat(messages: list[dict], model: str | None = None, **options) -> str:
    """
    Отправить messages модели и получить ответ (строкой).

    messages — список словарей вида:
        [{"role": "system", "content": "..."},
         {"role": "user", "content": "..."},
         {"role": "assistant", "content": "..."}]

    options — дополнительные настройки, например:
        max_tokens=300, temperature=0.8, response_format={"type": "json_object"}
    """
    # Склеиваем дефолтные настройки (из config) и то, что передали аргументом.
    # Если в options есть ключ — он перекрывает дефолт. Иначе берём дефолт.
    params = {
        "model": config.LLM_MODEL or config.LLM_MODEL,
        "messages": messages,
        "max_tokens": options.get("max_tokens", config.LLM_MAX_TOKENS),
        "temperature": options.get("temperature", config.LLM_TEMPERATURE),
    }

    # Если передан response_format — добавляем его (для JSON-режима).
    # llama-server поддерживает response_format={"type": "json_object"}.
    if "response_format" in options:
        params["response_format"] = options["response_format"]

    # Отправляем запрос. await — потому что операция асинхронная,
    # можем в это время делать что-то ещё.
    response = await client.chat.completions.create(**params)

    # response.choices — список вариантов ответа. Мы всегда берём [0] —
    # первый (и у нас всегда единственный).
    # .message.content — сам текст.
    # Если по какой-то причине пусто — вернём пустую строку.
    return response.choices[0].message.content or ""