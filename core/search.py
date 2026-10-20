# core/search.py
# -------------------------------------------------------------------
# Модуль поиска в интернете.
# Использует старый добрый парсинг DuckDuckGo, который работал в v1.
# -------------------------------------------------------------------

import json      # Для парсинга JSON-ответов от модели (решение о поиске)
import re        # Регулярные выражения (для очистки HTML и markdown)
import asyncio   # Для асинхронности (запуска блокирующего поиска в фоне)
import httpx     # Библиотека для HTTP-запросов (пришла на смену requests)

from core import config   # Наши настройки (пути, модели)
from core import llm      # Наш клиент для общения с ИИ
from core import logger   # Логгер (чтобы писать в консоль и файл)
from core import storage  # Для чтения промптов с диска

# -------------------------------------------------------------------
# 1. Быстрый фильтр (чтобы не тратить токены зря)
# -------------------------------------------------------------------

# Список слов-триггеров. Если в сообщении есть хотя бы одно из них,
# мы допускаем, что пользователю может понадобиться поиск.
_TRIGGERS = {
    "погода", "новости", "курс", "цена", "сколько стоит",
    "сегодня", "сейчас", "вчера", "завтра", "кто такой", "что такое",
    "найди", "поищи", "погугли", "проверь"
}

def _need_search(text: str) -> bool:
    """
    Быстрая проверка: стоит ли вообще беспокоить модель?
    Если пользователь написал "Привет, как дела?" — поиск не нужен.
    """
    # Приводим текст к нижнему регистру, чтобы искать без учета регистра
    lower = text.lower()
    
    # Проходимся по всем триггерам. Если хоть один найден — возвращаем True.
    return any(trigger in lower for trigger in _TRIGGERS)


# -------------------------------------------------------------------
# 2. Реальный поиск (старый метод из v1)
# -------------------------------------------------------------------

def _duckduckgo_search_sync(query: str, max_results: int = 3) -> list[dict]:
    """
    Синхронный поиск через DuckDuckGo HTML.
    Притворяется браузером Chrome, чтобы DDG не банил нас.
    """
    try:
        # Создаем HTTP-клиент.
        # trust_env=False отключает системные прокси (спасает от ошибки ascii с ВПН).
        # timeout=10.0 — если DDG тупит, мы не ждем вечно, а прерываем через 10 сек.
        with httpx.Client(trust_env=False, timeout=10.0) as client:
            
            # Делаем GET-запрос к HTML-версии поиска.
            resp = client.get(
                "https://html.duckduckgo.com/html/", 
                params={"q": query}, # Передаем поисковый запрос
                # ВАЖНО: Подставляем User-Agent реального браузера Chrome.
                # Если написать "KeiaBot", DDG выдаст капчу.
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
                follow_redirects=True, # Если DDG редиректит, идем по ссылке
            )
            
            # Если сервер вернул ошибку (404, 500), код выбросит исключение
            resp.raise_for_status()
            
            # Получаем HTML-код страницы
            html = resp.text

            # Регулярное выражение для вытаскивания результатов.
            # Ищет блоки <a class="result__a" href="...">Заголовок</a> и <a class="result__snippet">Описание</a>
            blocks = re.findall(
                r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
                r'class="result__snippet"[^>]*>(.*?)</a>',
                html,
                flags=re.DOTALL, # Флаг DOTALL позволяет искать в многострочном тексте
            )

            # Вспомогательная функция для очистки текста от HTML-тегов
            def _strip_tags(s):
                return re.sub(r"<[^>]+>", "", s).strip()

            results = []
            # Берем только первые max_results блоков (например, 3)
            for url, title_html, snippet_html in blocks[:max_results]:
                results.append({
                    "title": _strip_tags(title_html),   # Очищенный заголовок
                    "snippet": _strip_tags(snippet_html), # Очищенное описание
                    "url": url,                         # Ссылка
                })
            return results
            
    except Exception as e:
        # Если сеть упала или DDG изменил верстку — логируем ошибку и возвращаем пустоту
        logger.log(f"Ошибка поиска DDG: {e}", source="search")
        return []


async def search(query: str) -> str:
    """
    Асинхронная обертка.
    Запускает блокирующий поиск в отдельном потоке, чтобы бот не зависал.
    """
    # asyncio.to_thread берет функцию _duckduckgo_search_sync и выполняет ее
    # в фоновом потоке (как отдельный процесс), не блокируя основного бота.
    results = await asyncio.to_thread(_duckduckgo_search_sync, query, 3)
    
    # Если ничего не нашли — возвращаем пустую строку
    if not results:
        return ""

    # Собираем результаты в красивый текстовый список для промпта
    lines = [f"Результаты поиска по запросу '{query}':"]
    for r in results:
        lines.append(f"- {r['title']}: {r['snippet']}")
    
    # Склеиваем строки через перенос
    return "\n".join(lines)


# -------------------------------------------------------------------
# 3. Главная функция (мозг поиска)
# -------------------------------------------------------------------

async def maybe_search(text: str) -> str | None:
    """
    Проверяет, нужен ли поиск, и если да — ищет.
    """
    # 1. Сначала быстрый фильтр по ключевым словам
    if not _need_search(text):
        return None

    # 2. Если слова есть, загружаем промпт для модели (search_decision.txt)
    prompt = storage.load_text(config.PROMPTS_DIR / "search_decision.txt").strip()
    
    try:
        # 3. Спрашиваем дешевую модель: "Надо искать? Если да, то что?"
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text}
            ],
            model=config.LLM_UTILITY_MODEL, # Используем дешевую модель (Flash Lite)
            max_tokens=100,                 # Нам нужен только короткий JSON
            temperature=0.1,                # Температура 0.1 — чтобы модель не фантазировала
            response_format={"type": "json_object"} # Форсируем JSON
        )
        
        # 4. Чистим ответ от возможных markdown-тегов (```json ... ```)
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        
        # 5. Превращаем строку в словарь
        data = json.loads(raw)
        
        # 6. Если модель решила, что поиск не нужен — выходим
        if not data.get("search"):
            return None
            
        # 7. Если нужен — забираем поисковый запрос
        query = data.get("query")
        if not query:
            return None
            
        # 8. Пишем в лог и запускаем реальный поиск
        logger.log(f"Ищу в сети: {query}", source="search")
        return await search(query)
        
    except Exception as e:
        logger.log(f"Ошибка решения о поиске: {e}", source="search")
        return None