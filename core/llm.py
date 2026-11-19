# core/llm.py
# -------------------------------------------------------------------
# Клиент к LLM (OpenAI-совместимый API).
# -------------------------------------------------------------------

import httpx
from openai import AsyncOpenAI
from core import config
from core import logger

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

    Если модель вернула пустой content (например, reasoning-модель
    израсходовала весь max_tokens на размышления), автоматически делаем
    повторный запрос с увеличенным лимитом токенов.
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

    # Некоторые reasoning-модели поддерживают явное управление "размышлениями".
    if "reasoning_effort" in options:
        params["reasoning_effort"] = options["reasoning_effort"]

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        response = await client.chat.completions.create(**params)

        choice = response.choices[0] if response.choices else None
        content = (choice.message.content or "") if choice else ""

        if content.strip():
            return content

        finish_reason = getattr(choice, "finish_reason", None) if choice else None
        usage = getattr(response, "usage", None)

        # Если это последняя попытка — возвращаем пустую строку и пишем причину.
        if attempt == max_attempts:
            logger.log(
                f"LLM вернул пустой ответ после {max_attempts} попыток "
                f"(model={target_model}, finish_reason={finish_reason}, usage={usage})",
                source="llm",
            )
            return ""

        # Увеличиваем бюджет токенов: reasoning-модель могла упереться в лимит.
        current = int(params.get("max_tokens") or 0)
        new_max = min(max(current * 2, 4000), 16000)
        logger.log(
            f"Пустой ответ LLM (model={target_model}, finish_reason={finish_reason}, "
            f"usage={usage}), повтор с max_tokens={new_max}",
            source="llm",
        )
        params["max_tokens"] = new_max

    return ""


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

    params = {"model": model, "input": texts}
    if config.LLM_EMBED_DIMENSIONS is not None:
        params["dimensions"] = int(config.LLM_EMBED_DIMENSIONS)

    response = await client.with_options(timeout=10.0).embeddings.create(**params)

    return [item.embedding for item in response.data]