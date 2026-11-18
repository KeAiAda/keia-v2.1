# core/llm.py
# -------------------------------------------------------------------
# Клиент к LLM (OpenAI-совместимый API).
# -------------------------------------------------------------------

import httpx
from openai import AsyncOpenAI
from core import config

# -------------------------------------------------------------------
# 1. Инициализация клиента
# -------------------------------------------------------------------

# Очищаем API-ключ и URL от случайных пробелов и кириллицы (частая проблема при копировании).
clean_api_key = "".join(c for c in config.LLM_API_KEY if c.isascii()).strip()
clean_base_url = "".join(c for c in config.LLM_BASE_URL if c.isascii()).strip()

# Создаем HTTP-клиент.
# trust_env=False отключает системные прокси (спасает от конфликтов с ВПН).
# timeout=40.0 — если API тупит, мы прервем запрос через 40 секунд.
http_client = httpx.AsyncClient(trust_env=False, timeout=40.0)

# Создаём асинхронный клиент OpenAI, указывая ему наш URL и ключ.
client = AsyncOpenAI(
    base_url=clean_base_url,
    api_key=clean_api_key,
    http_client=http_client,
)

# -------------------------------------------------------------------
# 2. Основная функция для общения с моделью
# -------------------------------------------------------------------

async def chat(messages: list[dict], model: str | None = None, **options) -> str:
    """
    Отправить список сообщений (messages) модели и получить ответ.
    """
    # Если модель не передана явно, берем основную из конфига.
    target_model = model or config.LLM_MODEL
    
    # Собираем параметры запроса.
    params = {
        "model": target_model,
        "messages": messages,
        "max_tokens": options.get("max_tokens", config.LLM_MAX_TOKENS),
        "temperature": options.get("temperature", config.LLM_TEMPERATURE),
    }
    
    # Если нужен строгий JSON (для служебных задач), добавляем параметр.
    if "response_format" in options:
        params["response_format"] = options["response_format"]

    # Отправляем запрос (await — ждем ответа, не блокируя бота).
    response = await client.chat.completions.create(**params)
    
    # Возвращаем текст ответа. Если вдруг content пустой, вернем пустую строку.
    return response.choices[0].message.content or ""


# -------------------------------------------------------------------
# 3. Эмбеддинги для семантического поиска
# -------------------------------------------------------------------

async def embed(texts: list[str]) -> list[list[float]]:
    """
    Получить эмбеддинги для списка текстов ОДНИМ батч-запросом.
    Короткий таймаут (~10 с), чтобы бот не завис, если API эмбеддингов лежит.
    """
    if not texts:
        return []

    model = config.LLM_EMBED_MODEL.strip()
    if not model:
        raise RuntimeError("LLM_EMBED_MODEL не задан")

    response = await client.with_options(timeout=10.0).embeddings.create(
        model=model,
        input=texts,
    )

    return [item.embedding for item in response.data]