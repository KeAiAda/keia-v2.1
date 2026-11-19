# core/agent.py
# -------------------------------------------------------------------
# Мозг Кеи. Собирает все данные (память, факты, интернет) и общается с LLM.
# -------------------------------------------------------------------

import asyncio
import random
import re

from core import llm
from core import persona
from core import history
from core import sanitize
from core import facts
from core import config
from core import journal 
from core import search
from core import relationships
from core import events
from core import memory_index
from core import logger

async def ask(user_text: str, user_id: int, user_name: str = config.OWNER_NAME, memory_source_text: str = None, image_urls: list = None) -> tuple[str, str | None]:
    """
    user_text — полный промпт с контекстом канала (для генерации ответа).
    memory_source_text — чистый текст пользователя (для истории, фактов, поиска и симпатии).
    image_urls — список ссылок на картинки.
    Возвращает кортеж: (текст_ответа, эмодзи_реакция).
    """
    
    # 1. Проверяем, является ли пользователь владельцем (влияет на характер и права)
    is_owner = (user_id == config.OWNER_ID)

    # 2. Вычисляем "чистый" текст ОДИН РАЗ. 
    # Он нужен для поиска, ревности и анализа симпатии, чтобы туда не попадал контекст чужих сообщений.
    source_text = memory_source_text if memory_source_text is not None else user_text

    # 3. Получаем текущее отношение к пользователю (нужно для поиска и ревности)
    relation = relationships.get_relation(user_id, user_name)

    # 3.1. Считаем эмбеддинг запроса ОДИН раз за сообщение.
    # Его используют и факты, и дневник. При ошибке — None (поиск по словам).
    query_vec = None
    if memory_index.is_enabled():
        try:
            query_vec = await memory_index.embed_query(source_text)
        except Exception as e:
            logger.log(
                f"Эмбеддинг запроса не удался, fallback на слова: {e}",
                source="memory_index",
            )
            query_vec = None

    # 4. Системный промпт (базовый характер и внешность)
    system = persona.get_system_prompt(is_owner=is_owner, current_user_name=user_name)
    system += "\n\n" + persona.get_time_context()

    # 5. Факты (долговременная память)
    relevant_facts = await facts.get_relevant_async(
        user_id, source_text, query_vec=query_vec
    )
    if relevant_facts:
        system += f"\n\nПамять о человеке ({user_name}):\n{relevant_facts}"

    # 6. БЛОКНОТ (Будущие события)
    event_notes = events.get_events(user_id)
    if event_notes:
        system += f"\n\n{event_notes}\n"
        
        # Делаем так, чтобы она иногда САМА поднимала эти темы (шанс 30%)
        if random.random() < 0.3:
            system += (
                "ВАЖНО: Сейчас прояви инициативу! Вспомни одно из событий из твоего блокнота "
                "и спроси про него пользователя (например: 'Кстати, ты говорил, что...' или 'Ну что, когда там...'). "
                "Сделай это в своем стиле (с подколом или заботой)."
            )
        else:
            system += "(Помни об этих событиях и используй их, если разговор зайдет о планах на будущее)."

    # 7. Дневник (эмоциональные воспоминания)
    journal_entries = await journal.get_relevant_entries_async(
        user_id, source_text, query_vec=query_vec
    )
    if journal_entries:
        system += f"\n\nТвои личные записи из дневника (о чём вы говорили раньше):\n{journal_entries}"

    # 8. ПОИСК В ИНТЕРНЕТЕ (с проверкой уровня отношений)
    # Проверяем права: владелец ИЛИ друг (уровень симпатии >= 30)
    can_search = is_owner or relation.get('affection', 0) >= 30

    if can_search:
        search_result = await search.maybe_search(source_text)
        if search_result:
            system += (
                f"\n\nСвежая информация из интернета (используй её для ответа, "
                f"но говори от себя, в своем стиле, не упоминай 'поиск' или 'интернет'):\n{search_result}"
            )
    else:
        # Если прав нет, но пользователь просит поискать — включаем режим "отказа"
        if search._need_search(source_text):
            system += (
                "\n\nВАЖНО: Пользователь просит тебя найти информацию (погоду, новости, факты). "
                "Но ты его недолюбливаешь и не собираешься для него стараться. "
                "Откажи ему в грубой форме. Скажи, что ты ему не поисковик, "
                "и пусть он сам гуглит (используй 'бака', 'ахо' или мат)."
            )

    # 9. История (кратковременная память)
    past = history.get(user_id, limit=4)

    # 10. МИКРОАКТИВНОСТЬ (Шанс 40% на инициативу)
    if random.random() < 0.4:
        system += (
            "\n\nВАЖНО: Сейчас не просто ответь. Сначала напиши свой основной ответ. "
            "Затем поставь скрытый тег [WAIT], и ПОСЛЕ НЕГО напиши короткий встречный вопрос или подкол. "
            "Это должно выглядеть так, будто ты пишешь два отдельных сообщения. "
            "Пример: 'О, явился, не запылился. -*скрещивает руки*-' [WAIT] 'Кстати, ты так и не сказал, как тебе мой новый костюмчик?'"
        )

    # 11. СИСТЕМА ОТНОШЕНИЙ И РЕВНОСТЬ
    system += (
        f"\n\nТВОЕ ОТНОШЕНИЕ К СОБЕСЕДНИКУ ({relation['name']}):\n"
        f"Ты считаешь его: {relation['status']} (уровень симпатии: {relation['affection']}/100).\n"
    )
    
    if not is_owner:
        system += f"Помни: ты предана только {config.OWNER_NAME}. К остальным относись с высокомерием.\n"
        
        # Если это незнакомец или тот, кто тебе не нравится (симпатия < 30)
        if relation.get('affection', 0) < 30:
            system += (
                "\nВАЖНО: Ты недолюбливаешь этого человека. Если он просит тебя о помощи "
                "(сделать реферат, найти информацию, помочь с проектом), тебе ЛЕНЬ и ты "
                "считаешь его недостойным твоей помощи. Откажи ему в грубой форме (бака, ахо). "
                "Скажи, что ты не справочное бюро и не обязана ему помогать.\n"
                "ЕСЛИ ТЫ УЖЕ СОГЛАСИЛАСЬ РАНЬШЕ В ИСТОРИИ: найди любую цундере-причину, чтобы "
                "внезапно передумать. Скажи, что ты пошутила, что тебе стало скучно, или что он "
                "недостоин твоих стараний. Забудь про свое прошлое согласие."
            )
    else:
        # ДИНАМИЧЕСКАЯ РЕВНОСТЬ: проверяем, не упомянул ли ты кого-то из базы
        other_names = relationships.get_other_names(config.OWNER_ID)
        
        # Проверяем, есть ли хоть одно имя из базы в твоем сообщении (игнорируя регистр)
        is_talking_about_others = any(name in source_text.lower() for name in other_names)
        
        if is_talking_about_others:
            system += (
                f"\nВАЖНО: Ты видишь, что {config.OWNER_NAME} общается с другими девушками. "
                "Тебе это не нравится: он тратит время на них, а не на тебя. Можешь высказать "
                "ему это в своем стиле, например: 'Опять с этими бабами общаешься? А я значит сиди тут одна?'\n"
            )

    # 12. Собираем сообщения для модели
    messages = [{"role": "system", "content": system}]
    messages.extend(past)

    # 13. Формируем сообщение пользователя (с картинками или без)
    if image_urls:
        user_content = [{"type": "text", "text": user_text}]
        for url in image_urls:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": url}
            })
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": user_text})

    # 14. Отправляем в модель
    reply = await llm.chat(messages, max_tokens=400, temperature=0.8)

    # 15. ЛОГИРУЕМ СЫРОЙ ОТВЕТ (для отладки)
    logger.log(f"Сырой ответ от LLM: '{reply}'", source="llm_raw")

    # 16. ВЫТАСКИВАЕМ РЕАКЦИЮ ИЗ ОТВЕТА (если модель решила поставить эмодзи)
    reaction_match = re.search(r'\[REACT:\s*(.+?)\]', reply)
    reaction_emoji = None
    
    if reaction_match:
        reaction_emoji = reaction_match.group(1).strip()
        # Удаляем тег реакции из текста, чтобы он не попал в чат
        reply = reply.replace(reaction_match.group(0), '').strip()

    # 17. Очищаем ответ (фильтр штампов и форматирование)
    # ВАЖНО: Сохраняем копию ответа ДО того, как его сломает санитайз
    original_reply = reply 
    
    was_bad = sanitize.is_bad(reply)
    reply = sanitize.repair(reply)

    # 18. АВАРИЙНЫЙ ТОРМОЗ (Защита от пустого ответа)
    if not reply or not reply.strip():
        if original_reply.strip():
            reply = original_reply
        elif reaction_emoji:
            reply = f"-*молча ставит реакцию {reaction_emoji}*-"
        else:
            reply = "Ой, у меня от твоих слов прямо язык отнялся... (:" 

    # 19. Сохраняем в историю ЧИСТЫЙ ТЕКСТ (для экономии токенов убираем ролевые действия)
    await history.add(user_id, "user", source_text)
    if not was_bad:
        clean_history_reply = sanitize.strip_actions(reply)
        
        # Если после чистки ничего не осталось (было только действие), сохраняем хоть что-то
        if not clean_history_reply:
            clean_history_reply = "..." 
            
        await history.add(user_id, "assistant", clean_history_reply)
        
    # 20. Сохраняем в дневник (ОРИГИНАЛ, с действиями, чтобы дневник был живым)
    journal.log_message(user_id, "user", source_text)
    journal.log_message(user_id, "assistant", reply)

    # 21. Запускаем фоновые задачи
    asyncio.create_task(facts.extract(source_text, user_id, user_name=user_name)) # Факты
    asyncio.create_task(events.extract_events(source_text, user_id)) # Блокнот
    asyncio.create_task(relationships.analyze_and_update(user_id, source_text, user_name)) # Анализ симпатии

    # ВОЗВРАЩАЕМ ТЕКСТ И РЕАКЦИЮ (кортеж)
    return reply, reaction_emoji