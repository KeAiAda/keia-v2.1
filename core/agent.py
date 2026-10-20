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
from core import logger

async def ask(user_text: str, user_id: int, user_name: str = "KeAiAda", memory_source_text: str = None, image_urls: list = None) -> str:
    """
    user_text — полный промпт с контекстом (для генерации ответа)
    memory_source_text — чистый текст пользователя (для истории, фактов, поиска и симпатии)
    image_urls — список ссылок на картинки
    """
    
    # Проверяем, владелец ли это
    is_owner = (user_id == config.OWNER_ID)

    # ВЫЧИСЛЯЕМ ЧИСТЫЙ ТЕКСТ СРАЗУ (он нужен для поиска, ревности и симпатии)
    source_text = memory_source_text if memory_source_text is not None else user_text

    # 1. Системный промпт (базовый характер и внешность)
    system = persona.get_system_prompt(is_owner=is_owner, current_user_name=user_name)
    system += "\n\n" + persona.get_time_context()

    # 2. Факты (долговременная память)
    relevant_facts = facts.get_relevant(user_id, source_text)
    if relevant_facts:
        system += f"\n\nПамять о человеке ({user_name}):\n{relevant_facts}"

    # 2.1. БЛОКНОТ (Будущие события)
    event_notes = events.get_events(user_id)
    if event_notes:
        system += f"\n\n{event_notes}\n(Помни об этих событиях и можешь ссылаться на них)."

    # 3. Дневник (эмоциональные воспоминания)
    journal_entries = journal.get_relevant_entries(user_id, source_text)
    if journal_entries:
        system += f"\n\nТвои личные записи из дневника (о чём вы говорили раньше):\n{journal_entries}"

    # 4. ПОИСК В ИНТЕРНЕТЕ (с проверкой уровня отношений)
    # Сначала получаем отношение к пользователю (переносим получение сюда, чтобы использовать для поиска)
    relation = relationships.get_relation(user_id, user_name)
    
    # Проверяем права: владелец ИЛИ друг (уровень симпатии >= 30)
    can_search = is_owner or relation.get('affection', 0) >= 30

    if can_search:
        # Если права есть — ищем
        search_result = await search.maybe_search(source_text)
        if search_result:
            system += (
                f"\n\nСвежая информация из интернета (используй её для ответа, "
                f"но говори от себя, в своем стиле, не упоминай 'поиск' или 'интернет'):\n{search_result}"
            )
    else:
        # Если прав нет, но пользователь просит поискать — включаем режим "отказа"
        # Используем функцию из search.py, чтобы понять, пытается ли он что-то найти
        if search._need_search(source_text):
            system += (
                "\n\nВАЖНО: Пользователь просит тебя найти информацию (погоду, новости, факты). "
                "Но ты его недолюбливаешь и не собираешься для него стараться. "
                "Откажи ему в грубой форме. Скажи, что ты ему не поисковик, "
                "и пусть он сам гуглит (используй 'бака', 'ахо' или мат)."
            )

    # 5. История (кратковременная память)
    past = history.get(user_id, limit=4)

    # 6. МИКРОАКТИВНОСТЬ (Шанс 40%)
    if random.random() < 0.4:
        system += (
            "\n\nВАЖНО: Сейчас не просто ответь. Прояви инициативу! "
            "В конце своего сообщения задай встречный вопрос, подколи его, "
            "или переведи тему на что-то дерзкое/флиртующее."
        )

    # 6.1. СИСТЕМА ОТНОШЕНИЙ И РЕВНОСТЬ
    system += (
        f"\n\nТВОЕ ОТНОШЕНИЕ К СОБЕСЕДНИКУ ({relation['name']}):\n"
        f"Ты считаешь его: {relation['status']} (уровень симпатии: {relation['affection']}/100).\n"
    )
    
    if not is_owner:
        system += (
            f"Помни: ты любишь только KeAiAda. К остальным относись с высокомерием, "
            f"особенно если они пытаются с тобой заигрывать. Защищай свою территорию.\n"
        )
    else:
        # Ищем имена других девушек в ЧИСТОМ тексте (а не во всем контексте канала)
        if "Veevif" in source_text or "Weiko" in source_text:
            system += (
                "\nВАЖНО: Ты видишь, что KeAiAda общается с другими девушками. "
                "Ты чувствуешь легкую ревность. Можешь высказать ему это в своем стиле, "
                "например: 'Опять с этими бабами общаешься? А я значит сиди тут одна?'\n"
            )

    # 7. Собираем сообщения для модели
    messages = [{"role": "system", "content": system}]
    messages.extend(past)

    # 8. Формируем сообщение пользователя (с картинками или без)
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

    # 8.1. Меняем симпатию в зависимости от ЧИСТОГО текста
    if "люблю" in source_text.lower() or "классная" in source_text.lower():
        relationships.update_affection(user_id, +2)
    elif "дура" in source_text.lower() or "тупая" in source_text.lower():
        relationships.update_affection(user_id, -2)

    # 9. Отправляем в модель
    reply = await llm.chat(messages, max_tokens=200, temperature=0.8)

    # 9.1. ЛОГИРУЕМ СЫРОЙ ОТВЕТ
    logger.log(f"Сырой ответ от LLM: '{reply}'", source="llm_raw")

    # 9.2. ВЫТАСКИВАЕМ РЕАКЦИЮ
    reaction_match = re.search(r'\[REACT:\s*(.+?)\]', reply)
    reaction_emoji = None
    
    if reaction_match:
        reaction_emoji = reaction_match.group(1).strip()
        reply = reply.replace(reaction_match.group(0), '').strip()

    # 10. Очищаем ответ
    # ВАЖНО: Сохраняем копию ответа ДО того, как его сломает санитайз
    original_reply = reply 
    
    was_bad = sanitize.is_bad(reply)
    reply = sanitize.repair(reply)

    # 10.1. АВАРИЙНЫЙ ТОРМОЗ
    if not reply or not reply.strip():
        # Если санитайз всё удалил, но изначально текст был — возвращаем оригинал!
        if original_reply.strip():
            reply = original_reply
        elif reaction_emoji:
            reply = f"-*молча ставит реакцию {reaction_emoji}*-"
        else:
            reply = "Ой, у меня от твоих слов прямо язык отнялся... (:" 

    # 11. Вычисляем чистый текст пользователя для сохранения в память
    source_text = memory_source_text if memory_source_text is not None else user_text

    # 12. Сохраняем в историю ЧИСТЫЙ ТЕКСТ
    await history.add(user_id, "user", source_text)
    if not was_bad:
        # ВОТ ТУТ ЦЕНЗУРА: Сохраняем в историю только сухой смысл, без действий
        clean_history_reply = sanitize.strip_actions(reply)
        
        # Если после чистки ничего не осталось (было только действие), сохраняем хоть что-то
        if not clean_history_reply:
            clean_history_reply = "..." 
            
        await history.add(user_id, "assistant", clean_history_reply)
        
    # 13. Сохраняем в дневник (ОРИГИНАЛ, с действиями)
    journal.log_message(user_id, "user", source_text)
    journal.log_message(user_id, "assistant", reply) # <--- Тут пишем полный reply

    # 14. Извлекаем факты в фоне
    asyncio.create_task(facts.extract(source_text, user_id, user_name=user_name))

    # 14.1. Извлекаем будущие события в фоне
    asyncio.create_task(events.extract_events(source_text, user_id))

    # 15. Изменение симпатии
    if "люблю" in source_text.lower() or "классная" in source_text.lower():
        relationships.update_affection(user_id, +2)
    elif "дура" in source_text.lower() or "тупая" in source_text.lower():
        relationships.update_affection(user_id, -2)

    # ВОЗВРАЩАЕМ ТЕКСТ И РЕАКЦИЮ (кортеж)
    return reply, reaction_emoji