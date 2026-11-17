# ui/discord_bot.py
# -------------------------------------------------------------------
# Бот для Discord.
# Умеет общаться, помнить факты, держать статус, видеть картинки и писать первым.
# -------------------------------------------------------------------

import sys
import os

# ВАЖНО: Добавляем корень проекта в пути ДО импорта внутренних модулей.
# Иначе Python не сможет найти папку "core".
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio      # Для асинхронности (одновременное чтение чата и работа с API)
import re           # Регулярные выражения (для очистки JSON)
import json         # Парсинг JSON-ответов (в проактивности)
from datetime import datetime, timezone # Для работы со временем (таймеры проактивности)

import discord      # Главная библиотека для Discord

# Импортируем наши внутренние модули (мозг, память, настройки).
# Загружать .env здесь не нужно, так как config.py делает это при импорте.
from core import agent, history, config, facts, llm, storage
from core import logger
from core import journal

# -------------------------------------------------------------------
# 1. Вспомогательные функции для работы с картинками
# -------------------------------------------------------------------

# Расширения картинок, которые мы умеем читать
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

def looks_image_attachment(att):
    """Проверяет, является ли вложение картинкой."""
    ct = (att.content_type or "").lower()
    if ct.startswith("image/"):
        return True
    # Проверяем расширение в ссылке (на случай, если content_type не указан)
    url_lower = att.url.lower().split("?")[0]
    return any(url_lower.endswith(ext) for ext in IMAGE_EXTS)

async def gather_image_refs_from_message(message):
    """Собирает все URL картинок из сообщения (вложения и эмбеды)."""
    refs = []
    
    # 1. Обычные вложения (когда файл прикрепляют напрямую)
    for a in message.attachments:
        if looks_image_attachment(a):
            refs.append(a.url)
            
    # 2. Эмбеды (например, когда кидают ссылку на Tenor/Giphy)
    for e in message.embeds:
        if e.type in ("gifv", "image") and e.url:
            refs.append(e.url)
        elif e.image and e.image.url:
            refs.append(e.image.url)
            
    # Убираем дубликаты ссылок
    return list(dict.fromkeys(refs))

# -------------------------------------------------------------------
# 2. Инициализация и настройки
# -------------------------------------------------------------------

# Жесткий фикс кодировки (чтобы консоль не падала с ошибкой ascii из-за ВПН)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Достаем токен бота из конфига (он уже загружен из .env в config.py)
TOKEN = config.DISCORD_TOKEN

# Интенты — "разрешения" для бота.
# Нам нужно разрешение читать текст сообщений (message_content).
intents = discord.Intents.default()
intents.message_content = True

# -------------------------------------------------------------------
# 3. Глобальные переменные (Буферизация сообщений)
# -------------------------------------------------------------------

# Если пользователь пишет несколько сообщений подряд, мы склеиваем их в одно.
message_buffers = {}  # {user_id: "склеенный текст"}
image_buffers = {}    # {user_id: ["url1", "url2"]}
buffer_tasks = {}     # {user_id: asyncio.Task (таймер)}
DISCORD_MESSAGE_LIMIT = 1900 # Лимит длины одного сообщения в Discord (2000 символов)
BUFFER_DEBOUNCE_SEC = 2.5    # Ждем 2.5 секунды тишины, прежде чем отвечать

# Путь к файлу, где храним время последней активности (для проактивности)
STATE_FILE = config.MEMORY_DIR / "discord_state.json"

# -------------------------------------------------------------------
# 4. Функции для работы с активностью (Проактивность)
# -------------------------------------------------------------------

def load_state():
    """Читает файл состояния (когда юзер последний раз писал)."""
    return storage.load_json(STATE_FILE, default={})

def save_state(state):
    """Сохраняет файл состояния на диск (атомарно)."""
    storage.atomic_write_json(STATE_FILE, state)

def mark_active(user_id: int):
    """Отмечает, что пользователь только что написал сообщение."""
    state = load_state()
    state[str(user_id)] = {
        "last_active": datetime.now(timezone.utc).isoformat(),
        "last_proactive": state.get(str(user_id), {}).get("last_proactive")
    }
    save_state(state)

def mark_proactive(user_id: int):
    """Отмечает, что бот только что написал пользователю сам."""
    state = load_state()
    if str(user_id) in state:
        state[str(user_id)]["last_proactive"] = datetime.now(timezone.utc).isoformat()
        save_state(state)

# -------------------------------------------------------------------
# 5. Утилиты
# -------------------------------------------------------------------

def split_message(text, limit=DISCORD_MESSAGE_LIMIT):
    """Разбивает длинный текст на куски, чтобы Discord не ругался на длину."""
    if not text: return []
    if len(text) <= limit: return [text]
    
    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) <= limit:
            current += line
        else:
            chunks.append(current.rstrip())
            current = line
    if current: 
        chunks.append(current.rstrip())
    return [c for c in chunks if c]

def _append_buffer_text(user_id, clean_text):
    """Добавляет текст в буфер к уже имеющемуся (если юзер пишет быстро)."""
    if not clean_text: return
    existing = message_buffers.get(user_id, "")
    message_buffers[user_id] = existing + "\n" + clean_text if existing else clean_text

# -------------------------------------------------------------------
# 6. Основной класс бота
# -------------------------------------------------------------------

class KeiaBot(discord.Client):
    async def setup_hook(self):
        """Вызывается один раз при запуске (до подключения к серверам)."""
        history.init() # Загружаем историю из файла
        self.loop.create_task(self.proactive_loop()) # Запускаем цикл проактивности

    async def on_ready(self):
        """Вызывается, когда бот успешно подключился."""
        logger.log(f"Бот запущен как: {self.user}", source="discord")
        
        # Выводим список серверов (для отладки)
        print("Я нахожусь на серверах:")
        for guild in self.guilds:
            print(f"  - {guild.name} (ID: {guild.id})")
            
        # Ставим статус "Слушает KeAiAda (:"
        activity = discord.Activity(
            type=discord.ActivityType.listening, 
            name="KeAiAda (:"
        )
        # "В сети" (зеленый кружок)
        await self.change_presence(status=discord.Status.online, activity=activity)

    async def send_reply_chunks(self, message, text):
        """Отправляет ответ пользователю, разбивая на куски и имитируя печать."""
        parts = split_message(text)
        if not parts: return

        try:
            # Пытаемся отправить как реплай (с цитатой)
            await message.reply(parts[0], mention_author=False)
        except Exception:
            # Если реплай не прошел, шлем просто в чат
            await message.channel.send(parts[0])
            
        # Отправляем остальные части с задержкой
        for part in parts[1:]:
            typing_time = min(len(part) * 0.02, 1.5) 
            async with message.channel.typing():
                await asyncio.sleep(typing_time + 0.5)
                await message.channel.send(part)

    async def on_message(self, message):
        """Вызывается КАЖДЫЙ раз, когда в чате появляется новое сообщение."""
        
        # Игнорируем сообщения от самого бота
        if message.author == self.user:
            return
            
        # Проверяем: это личка (DM) или нас упомянули через @?
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = self.user.mentioned_in(message)

        # Если это не ЛС и не упоминание — игнорируем
        if is_mentioned or is_dm:
            try:
                user_id = message.author.id
                
                # Очищаем текст от упоминания бота <@123456>
                clean_text = (
                    message.content.replace(f"<@!{self.user.id}>", "")
                    .replace(f"<@{self.user.id}>", "")
                    .strip()
                )
                
                # Ищем картинки в сообщении
                image_urls = await gather_image_refs_from_message(message)

                # Если нет ни текста, ни картинок — выходим
                if not clean_text and not image_urls: 
                    return
                
                mark_active(user_id) # Отмечаем активность
                
                if clean_text:
                    _append_buffer_text(user_id, clean_text)
                    
                if image_urls:
                    image_buffers.setdefault(user_id, []).extend(image_urls)

                # Специальная команда для дневника
                if clean_text == "!дневник":
                    await message.channel.send("*зевает* Так, дай мне минутку, я перечитаю нашу переписку...")
                    summary = await journal.summarize_day(user_id)
                    if summary:
                        await message.channel.send(summary)
                    else:
                        await message.channel.send("Мы сегодня слишком мало говорили, мне нечего записать (:")
                    return # Выходим, чтобы не отправлять это в ИИ
                
                # Отменяем прошлый таймер, если он был
                if user_id in buffer_tasks:
                    buffer_tasks[user_id].cancel()
                
                # Создаем новый таймер (2.5 сек тишины)
                buffer_tasks[user_id] = asyncio.create_task(self.process_buffer(message, user_id))
                
            except Exception as e:
                logger.log(f"Ошибка в on_message: {e}", source="error")

    async def process_buffer(self, message, user_id):
        """Вызывается после того, как пользователь перестал писать."""
        try:
            # Ждем 2.5 секунды
            await asyncio.sleep(BUFFER_DEBOUNCE_SEC)
            
            # Забираем накопленные данные из буфера
            full_request = message_buffers.pop(user_id, "").strip()
            urls = image_buffers.pop(user_id, [])
            
            if not full_request and not urls:
                return

            user_name = message.author.display_name
            
            # Формируем контекст для промпта (если это не ЛС)
            if isinstance(message.channel, discord.DMChannel):
                final_prompt = full_request
            else:
                # Читаем последние 2 сообщения в канале (для понимания контекста)
                chat_context = []
                async for msg in message.channel.history(limit=2, before=message):
                    author = "Ты (Кея)" if msg.author == self.user else msg.author.display_name
                    chat_context.append(f"{author}: {msg.content}")
                chat_context.reverse()
                
                context_str = "\n".join(chat_context)
                final_prompt = (
                    f"КОНТЕКСТ ЧАТА (последние сообщения):\n{context_str}\n\n"
                    f"СЕЙЧАС ПИШЕТ ({user_name}): {full_request}"
                )

            # Показываем статус "Печатает..." и отправляем в мозг
            async with message.channel.typing():
                response_text, reaction_emoji = await agent.ask(
                    user_text=final_prompt, 
                    user_id=user_id, 
                    user_name=user_name,
                    memory_source_text=full_request,
                    image_urls=urls
                )
                
            # Логируем ответ
            logger.log(f"Готовлюсь отправить текст: '{response_text[:50]}...'", source="discord")

            # Отправляем текст (если он есть)
            if response_text:
                # Проверяем, есть ли скрытый тег [WAIT] (наша микроактивность)
                if "[WAIT]" in response_text:
                    # Разделяем на две части
                    parts = response_text.split("[WAIT]", 1)
                    main_reply = parts[0].strip()
                    follow_up = parts[1].strip()
                    
                    # 1. Отправляем основной ответ
                    if main_reply:
                        await self.send_reply_chunks(message, main_reply)
                    
                    # 2. Делаем паузу и показываем "Печатает..."
                    await asyncio.sleep(1.5)
                    async with message.channel.typing():
                        await asyncio.sleep(1.0) # Еще секунда на подумать
                        if follow_up:
                            # Отправляем вторую часть как новое сообщение в канал (без реплая)
                            await message.channel.send(follow_up)
                else:
                    # Если тега нет — просто отправляем все как обычно
                    await self.send_reply_chunks(message, response_text)
            else:
                logger.log("Модель вернула пустой ответ!", source="error")

            # ВАЖНО: Ставим реакцию (ВНЕ блока else, чтобы работало всегда)
            if reaction_emoji:
                try:
                    await message.add_reaction(reaction_emoji)
                except Exception as e:
                    logger.log(f"Не удалось поставить реакцию {reaction_emoji}: {e}", source="error")
                    
        except asyncio.CancelledError:
            # Если пришло новое сообщение, таймер отменяется. Просто выходим.
            raise
        except Exception as e:
            logger.log(f"Ошибка в process_buffer: {e}", source="error")

    async def proactive_loop(self):
        """Фоновый цикл: проверяет время и решает, написать ли первой."""
        logger.log("Цикл проактивности запущен.", source="proactive")
        await self.wait_until_ready() # Ждем подключения
        
        while not self.is_closed():
            # Спим 15 минут
            await asyncio.sleep(60 * 15) 

            state = load_state()
            now = datetime.now(timezone.utc)

            for user_id_str, data in state.items():
                user_id = int(user_id_str)
                
                # Парсим время активности
                last_active = datetime.fromisoformat(data.get("last_active", now.isoformat()))
                last_proactive_str = data.get("last_proactive")
                last_proactive = datetime.fromisoformat(last_proactive_str) if last_proactive_str else last_active

                # Считаем минуты тишины
                quiet_minutes = (now - last_active).total_seconds() / 60
                since_proactive_minutes = (now - last_proactive).total_seconds() / 60

                # Если молчит > 2 часов и мы не писали > 4 часов
                if quiet_minutes > 120 and since_proactive_minutes > 240:
                    
                    # Не пишем ночью (с 0 до 8 утра)
                    current_hour = (now.hour + 3) % 24 
                    if 0 <= current_hour < 8:
                        continue

                    # Собираем контекст (факты)
                    facts_text = facts.get_relevant(user_id, "интересы дела учеба", limit=5)
                    prompt = storage.load_text(config.PROMPTS_DIR / "proactive.txt")
                    user_context = f"Пользователь молчит уже {int(quiet_minutes/60)} часов.\nЧто ты о нём знаешь:\n{facts_text}"

                    try:
                        # Запрос к модели
                        raw = await llm.chat(
                            messages=[
                                {"role": "system", "content": prompt},
                                {"role": "user", "content": user_context}
                            ],
                            model=config.LLM_MODEL, # Умная модель для творчества
                            max_tokens=150,
                            temperature=0.9,
                            response_format={"type": "json_object"}
                        )
                        
                        # ЗАЩИТА: Если пусто — пропускаем
                        if not raw or not raw.strip():
                            continue

                        # Чистим от markdown
                        raw = re.sub(r"^```(?:json)?\s*", "", raw)
                        raw = re.sub(r"\s*```$", "", raw).strip()
                        
                        # ЗАЩИТА: Вырезаем JSON
                        start_idx = raw.find('{')
                        end_idx = raw.rfind('}')
                        
                        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                            clean_json = raw[start_idx:end_idx+1]
                        else:
                            continue # Если нет скобок, пропускаем

                        try:
                            data = json.loads(clean_json)
                        except json.JSONDecodeError:
                            continue # Если JSON битый, пропускаем

                        # Если решила написать — отправляем
                        if data.get("decision") == "MESSAGE":
                            text = data.get("text", "").strip()
                            if text:
                                user = await self.fetch_user(user_id)
                                await user.send(text)
                                mark_proactive(user_id)
                                logger.log(f"Написала сама пользователю {user_id}", source="proactive")
                                break # Выходим, чтобы не спамить всем сразу
                                
                    except Exception as e:
                        logger.log(f"Ошибка проактивности: {e}", source="proactive")

# -------------------------------------------------------------------
# 7. Точка входа
# -------------------------------------------------------------------

# Создаем экземпляр бота на уровне модуля, чтобы его можно было импортировать
client = KeiaBot(intents=intents)

def run_bot():
    """Функция для запуска бота из main.py."""
    if not TOKEN:
        print("✗ DISCORD_TOKEN не задан в .env")
        sys.exit(1)
        
    # Запускаем бота
    client.run(TOKEN, log_handler=None)