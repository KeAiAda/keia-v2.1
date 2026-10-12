# core/agent.py
# -------------------------------------------------------------------
# Мозг Кеи. С памятью, фактами и фильтром штампов.
# -------------------------------------------------------------------

import asyncio

from core import llm
from core import persona
from core import history
from core import sanitize
from core import facts


async def ask(user_text: str, user_id: int) -> str:
    """
    Принять сообщение пользователя, вернуть ответ Кеи.
    """
    # 1. Системный промпт.
    system = persona.get_system_prompt()

    # 2. Релевантные факты из долговременной памяти.
    relevant_facts = facts.get_relevant(user_id, user_text)
    if relevant_facts:
        system += (
            f"\n\nПамять о человеке, с которым ты говоришь. Это НЕ ты. "
            f"Его имя и привычки:\n{relevant_facts}"
)

    # 3. История последних сообщений.
    past = history.get(user_id, limit=10)

    # 4. Собираем сообщения.
    messages = [{"role": "system", "content": system}]
    messages.extend(past)
    messages.append({"role": "user", "content": user_text})

    # 5. Отправляем в модель.
    reply = await llm.chat(messages, max_tokens=150, temperature=0.8)

    # 6. Чистим ответ.
    was_bad = sanitize.is_bad(reply)
    reply = sanitize.repair(reply)

    # 7. Сохраняем в историю.
    await history.add(user_id, "user", user_text)
    if not was_bad:
        await history.add(user_id, "assistant", reply)

    # 8. Извлекаем факт из сообщения — ПОСЛЕ ответа, чтобы не тормозить.
    #    create_task запускает в фоне; основной поток не ждёт.
    asyncio.create_task(facts.extract(user_text, user_id))

    return reply