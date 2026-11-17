# core/search.py
# -------------------------------------------------------------------
# Модуль поиска в интернете через DuckDuckGo.
# -------------------------------------------------------------------

import json
import re
import asyncio
import httpx

from core import config, llm, logger, storage

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
    """Быстрая проверка: стоит ли вообще беспокоить модель?"""
    lower = text.lower()
    # Если хотя бы один триггер найден — возвращаем True
    return any(trigger in lower for trigger in _TRIGGERS)

# -------------------------------------------------------------------
# 2. Реальный поиск (парсинг DuckDuckGo)
# -------------------------------------------------------------------

def _duckduckgo_search_sync(query: str, max_results: int = 3) -> list[dict]:
    """Синхронный поиск через DuckDuckGo HTML."""
    try:
        # Создаем HTTP-клиент (trust_env=False отключает ВПН-прокси)
        with httpx.Client(trust_env=False, timeout=10.0) as client:
            
            # Делаем GET-запрос к HTML-версии поиска
            resp = client.get(
                "https://html.duckduckgo.com/html/", 
                params={"q": query},
                # Притворяемся браузером Chrome, чтобы DDG не выдал капчу
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
                follow_redirects=True,
            )
            
            # Если сервер вернул ошибку — код выбросит исключение
            resp.raise_for_status()
            html = resp.text

            # Регулярка для вытаскивания заголовков и описаний
            blocks = re.findall(
                r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
                r'class="result__snippet"[^>]*>(.*?)</a>',
                html,
                flags=re.DOTALL,
            )

            # Функция для очистки текста от HTML-тегов
            def _strip_tags(s):
                return re.sub(r"<[^>]+>", "", s).strip()

            results = []
            # Берем только первые max_results блоков
            for url, title_html, snippet_html in blocks[:max_results]:
                results.append({
                    "title": _strip_tags(title_html),
                    "snippet": _strip_tags(snippet_html),
                    "url": url,
                })
            return results
            
    except Exception as e:
        logger.log(f"Ошибка поиска DDG: {e}", source="search")
        return []

async def search(query: str) -> str:
    """Асинхронная обертка для поиска."""
    # Запускаем блокирующий поиск в отдельном потоке, чтобы не вешать бота
    results = await asyncio.to_thread(_duckduckgo_search_sync, query, 3)
    
    if not results:
        return ""

    # Собираем результаты в текстовый список для промпта
    lines = [f"Результаты поиска по запросу '{query}':"]
    for r in results:
        lines.append(f"- {r['title']}: {r['snippet']}")
    
    return "\n".join(lines)

# -------------------------------------------------------------------
# 3. Главная функция (мозг поиска)
# -------------------------------------------------------------------

async def maybe_search(text: str) -> str | None:
    """Проверяет, нужен ли поиск, и если да — ищет."""
    # 1. Быстрый фильтр по ключевым словам
    if not _need_search(text):
        return None

    # 2. Загружаем промпт для модели
    prompt = storage.load_text(config.PROMPTS_DIR / "search_decision.txt").strip()
    
    try:
        # 3. Спрашиваем дешевую модель: "Надо искать? Если да, то что?"
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text}
            ],
            model=config.LLM_UTILITY_MODEL,
            max_tokens=100,
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        
        # ЗАЩИТА: Если пусто — выходим
        if not raw or not raw.strip():
            return None

        # 4. Чистим от markdown-тегов
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        
        # 5. ЗАЩИТА: Вырезаем JSON из любого текста
        start_idx = raw.find('{')
        end_idx = raw.rfind('}')
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            clean_json = raw[start_idx:end_idx+1]
        else:
            return None
            
        # 6. Превращаем строку в словарь
        data = json.loads(clean_json)
        
        # 7. Если модель решила, что поиск не нужен — выходим
        if not data.get("search"):
            return None
            
        # 8. Если нужен — забираем поисковый запрос
        query = data.get("query")
        if not query:
            return None
            
        # 9. Пишем в лог и запускаем реальный поиск
        logger.log(f"Ищу в сети: {query}", source="search")
        return await search(query)
        
    except Exception as e:
        logger.log(f"Ошибка решения о поиске: {e}", source="search")
        return None