# discord_bot.py
# -------------------------------------------------------------------
# Бот для Discord.
# Умеет общаться, помнить факты, держать статус, видеть картинки и писать первым.
# -------------------------------------------------------------------

import asyncio      # Нужен для асинхронности: чтобы бот мог думать и одновременно слушать чат
import os           # Для работы с путями к файлам (получение пути к .env)
import re           # Регулярные выражения (пригодятся в проактивности для очистки JSON)
import sys          # Системные функции (используем для принудительной настройки кодировки UTF-8)
import json         # Для парсинга JSON-ответов от модели (в проактивности)
from datetime import datetime, timezone # Для работы со временем (отслеживание тишины)

import discord      # Главная библиотека для создания бота
from dotenv import load_dotenv # Чтобы загрузить токен из файла .env

# Импортируем наши внутренние модули (мозг, память, настройки)
from core import agent, history, config, facts, llm, storage
from core import logger
from core import journal # Для команды !дневник

# Добавляем корневую папку в пути, чтобы импорты core работали
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# -------------------------------------------------------------------
# 1. Вспомогательные функции для работы с картинками
# -------------------------------------------------------------------

# Расширения картинок, которые мы умеем читать
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

def looks_image_attachment(att):
    """
    Проверяет, является ли вложение картинкой.
    Discord присылает content_type (например, image/png), но иногда его нет,
    поэтому мы дополнительно проверяем расширение файла в ссылке.
    """
    ct = (att.content_type or "").lower()
    if ct.startswith("image/"):
        return True
    # Убираем параметры запроса из URL (все, что после ?)
    url_lower = att.url.lower().split("?")[0]
    return any(url_lower.endswith(ext) for ext in IMAGE_EXTS)

async def gather_image_refs_from_message(message):
    """
    Собирает все URL картинок из сообщения.
    Discord может прикреплять картинки двумя способами:
    1. Как файл (attachments) - обычное вложение.
    2. Как эмбед (embeds) - когда кидают ссылку на Tenor/Giphy, Discord разворачивает её в красивую карточку.
    """
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
            
    # Убираем дубликаты ссылок, если они есть
    return list(dict.fromkeys(refs))

# -------------------------------------------------------------------
# 2. Инициализация и настройки
# -------------------------------------------------------------------

# Жесткий фикс кодировки (чтобы консоль не падала с ошибкой ascii, если в системе включен ВПН)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Загружаем переменные окружения из файла .env
load_dotenv(os.path.join(config.PROJECT_ROOT, ".env"))
# Достаем токен бота из конфига
TOKEN = config.DISCORD_TOKEN

# Интенты — это "разрешения" для бота. 
# Нам нужно разрешение читать текст сообщений (message_content), иначе бот их не увидит.
intents = discord.Intents.default()
intents.message_content = True

# -------------------------------------------------------------------
# 3. Глобальные переменные (Буферизация)
# -------------------------------------------------------------------

# Если пользователь пишет несколько сообщений подряд, мы склеиваем их в одно.
message_buffers = {}  # Здесь храним сами тексты {user_id: "склеенный текст"}
image_buffers = {}    # Здесь храним ссылки на картинки {user_id: ["url1", "url2"]}
buffer_tasks = {}     # А здесь храним таймеры {user_id: asyncio.Task}
DISCORD_MESSAGE_LIMIT = 1900 # Лимит длины одного сообщения в Discord (2000 символов)
BUFFER_DEBOUNCE_SEC = 2.5    # Ждем 2.5 секунды тишины, прежде чем отвечать

# Путь к файлу, где храним время последней активности пользователей (для проактивности)
STATE_FILE = config.MEMORY_DIR / "discord_state.json"

# -------------------------------------------------------------------
# 4. Функции для работы с активностью (нужны для проактивности)
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
    # Записываем текущее время в формате ISO
    state[str(user_id)] = {
        "last_active": datetime.now(timezone.utc).isoformat(),
        # Сохраняем старое время проактивности, если оно было
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
    # Идем по строкам и собираем их, пока не упремся в лимит
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
    # Склеиваем через перенос строки
    message_buffers[user_id] = existing + "\n" + clean_text if existing else clean_text

# -------------------------------------------------------------------
# 6. Основной класс бота
# -------------------------------------------------------------------

class KeiaBot(discord.Client):
    async def setup_hook(self):
        """Вызывается один раз при запуске бота (до подключения к серверам)."""
        history.init() # Инициализируем память (загружаем chat_histories.json)
        # Запускаем фоновую задачу проактивности (чтобы она могла писать сама)
        self.loop.create_task(self.proactive_loop())

    async def on_ready(self):
        """Вызывается, когда бот успешно подключился к серверам Discord."""
        logger.log(f"Бот запущен как: {self.user}", source="discord")
        
        # Выводим список серверов, на которых находится бот (для отладки)
        print("Я нахожусь на серверах:")
        for guild in self.guilds:
            print(f"  - {guild.name} (ID: {guild.id})")
            
        # Ставим красивый статус (Rich Presence)
        activity = discord.Activity(
            type=discord.ActivityType.listening, 
            name="KeAiAda (:"  # Текст в профиле бота
        )
        # Устанавливаем статус "В сети" (зеленый кружок)
        await self.change_presence(status=discord.Status.online, activity=activity)

    async def send_reply_chunks(self, message, text):
        """Отправляет ответ пользователю, разбивая на куски и имитируя печать."""
        parts = split_message(text)
        if not parts: return

        try:
            # Пытаемся отправить как реплай (с цитатой)
            await message.reply(parts[0], mention_author=False)
        except Exception:
            # Если сообщение удалили или реплай не прошел, отправляем просто в чат
            await message.channel.send(parts[0])
            
        # Отправляем остальные части с задержкой
        for part in parts[1:]:
            # Считаем время "печати" в зависимости от длины текста (но не больше 1.5 сек)
            typing_time = min(len(part) * 0.02, 1.5) 
            # Показываем в чате "Кея печатает..."
            async with message.channel.typing():
                await asyncio.sleep(typing_time + 0.5) # Ждем
                await message.channel.send(part)       # Отправляем

    async def on_message(self, message):
        """Вызывается КАЖДЫЙ раз, когда в чате появляется новое сообщение."""
        
        # Игнорируем сообщения от самого бота (чтобы он не отвечал сам себе)
        if message.author == self.user:
            return
            
        # Проверяем: это личка (DM) или нас упомянули через @?
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = self.user.mentioned_in(message)

        # Если это не ЛС и не упоминание — ничего не делаем
        if is_mentioned or is_dm:
            try:
                user_id = message.author.id
                
                # Очищаем текст от самого упоминания <@123456>
                clean_text = (
                    message.content.replace(f"<@!{self.user.id}>", "")
                    .replace(f"<@{self.user.id}>", "")
                    .strip()
                )
                
                # Собираем картинки из сообщения
                image_urls = await gather_image_refs_from_message(message)

                # Если нет ни текста, ни картинок — выходим
                if not clean_text and not image_urls: 
                    return
                
                # Отмечаем активность (для проактивности)
                mark_active(user_id)
                
                # Если есть текст, добавляем в буфер
                if clean_text:
                    _append_buffer_text(user_id, clean_text)
                    
                # Сохраняем картинки в буфер для этого юзера
                if image_urls:
                    image_buffers.setdefault(user_id, []).extend(image_urls)

                # Если написали "!дневник" — запускаем суммаризацию за сегодня
                if clean_text == "!дневник":
                    await message.channel.send("*зевает* Так, дай мне минутку, я перечитаю нашу переписку...")
                    summary = await journal.summarize_day(user_id)
                    if summary:
                        await message.channel.send(summary)
                    else:
                        await message.channel.send("Мы сегодня слишком мало говорили, мне нечего записать (:")
                    return # Выходим, чтобы не отправлять это в ИИ
                
                # Если для этого юзера уже запущен таймер (он пишет подряд) — отменяем его
                if user_id in buffer_tasks:
                    buffer_tasks[user_id].cancel()
                
                # Создаем новый таймер (задачу). Он подождет 2.5 сек и вызовет process_buffer
                buffer_tasks[user_id] = asyncio.create_task(self.process_buffer(message, user_id))
                
            except Exception as e:
                logger.log(f"Ошибка в on_message: {e}", source="error")

    async def process_buffer(self, message, user_id):
        """Вызывается после того, как пользователь перестал писать."""
        try:
            # Ждем 2.5 секунды, чтобы точно убедиться, что он дописал
            await asyncio.sleep(BUFFER_DEBOUNCE_SEC)
            
            # Забираем текст и картинки из буфера
            full_request = message_buffers.pop(user_id, "").strip()
            urls = image_buffers.pop(user_id, [])
            
            # Если пусто — выходим
            if not full_request and not urls:
                return

            user_name = message.author.display_name
            
            # Формируем контекст, если это не ЛС
            if isinstance(message.channel, discord.DMChannel):
                final_prompt = full_request
            else:
                # Читаем последние 2 сообщения в канале для контекста
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

            # Показываем статус "Печатает..." и отправляем текст в наш "мозг"
            async with message.channel.typing():
                # Теперь agent.ask возвращает кортеж: (текст, реакция)
                response_text, reaction_emoji = await agent.ask(
                    user_text=final_prompt, 
                    user_id=user_id, 
                    user_name=user_name,
                    memory_source_text=full_request,
                    image_urls=urls
                )
                
            # Логируем то, что мы собираемся отправить в чат (чтобы видеть, если там пустота)
            logger.log(f"Готовлюсь отправить текст: '{response_text[:50]}...'", source="discord")

            # Если есть текст — отправляем
            if response_text:
                await self.send_reply_chunks(message, response_text)
            else:
                logger.log("Модель вернула пустой ответ!", source="error")

                # Если модель захотела поставить реакцию — ставим её на сообщение пользователя
                if reaction_emoji:
                    try:
                        await message.add_reaction(reaction_emoji)
                    except Exception as e:
                        logger.log(f"Не удалось поставить реакцию {reaction_emoji}: {e}", source="error")
                    
        except asyncio.CancelledError:
            # Если пришло новое сообщение, старый таймер отменяется.
            # Мы просто игнорируем отмену, новый таймер сделает всю работу.
            raise
        except Exception as e:
            # Если что-то пошло не так — логируем ошибку
            logger.log(f"Ошибка в process_buffer: {e}", source="error")

    async def proactive_loop(self):
        """Фоновый цикл: проверяет время и решает, написать ли первой."""
        logger.log("Цикл проактивности запущен.", source="proactive")
        await self.wait_until_ready() # Ждем, пока бот подключится к Discord
        
        while not self.is_closed(): # Бесконечный цикл
            # Спим 15 минут (в тестах можно поставить 10 секунд)
            await asyncio.sleep(60 * 15) 

            state = load_state() # Загружаем время активности всех юзеров
            now = datetime.now(timezone.utc) # Текущее время

            for user_id_str, data in state.items():
                user_id = int(user_id_str)
                
                # Парсим время из текста обратно в объект datetime
                last_active = datetime.fromisoformat(data.get("last_active", now.isoformat()))
                last_proactive_str = data.get("last_proactive")
                last_proactive = datetime.fromisoformat(last_proactive_str) if last_proactive_str else last_active

                # Считаем, сколько минут назад он писал / мы писали
                quiet_minutes = (now - last_active).total_seconds() / 60
                since_proactive_minutes = (now - last_proactive).total_seconds() / 60

                # Условия: он молчит больше 2 часов (120 мин), и мы не писали последние 4 часа (240 мин)
                if quiet_minutes > 120 and since_proactive_minutes > 240:
                    
                    # Не пишем ночью (с 0 до 8 утра по МСК)
                    current_hour = (now.hour + 3) % 24 
                    if 0 <= current_hour < 8:
                        continue

                    # Собираем контекст: что мы о нем знаем
                    facts_text = facts.get_relevant(user_id, "интересы дела учеба", limit=5)
                    prompt = storage.load_text(config.PROMPTS_DIR / "proactive.txt")
                    
                    # Формируем контекст для модели
                    user_context = f"Пользователь молчит уже {int(quiet_minutes/60)} часов.\nЧто ты о нём знаешь:\n{facts_text}"

                    try:
                        # Просим модель решить: писать или нет, и придумать текст
                        raw = await llm.chat(
                            messages=[
                                {"role": "system", "content": prompt},
                                {"role": "user", "content": user_context}
                            ],
                            model=config.LLM_UTILITY_MODEL,
                            max_tokens=150,
                            temperature=0.9,
                            response_format={"type": "json_object"}
                        )
                        
                        # Чистим ответ от markdown (```json)
                        raw = re.sub(r"^```(?:json)?\s*", "", raw)
                        raw = re.sub(r"\s*```$", "", raw).strip()
                        data = json.loads(raw) # Превращаем текст в словарь

                        # Если модель решила написать — отправляем
                        if data.get("decision") == "MESSAGE":
                            text = data.get("text", "").strip()
                            if text:
                                # Получаем объект пользователя по ID (чтобы отправить ЛС)
                                user = await self.fetch_user(user_id)
                                await user.send(text) # Отправляем в личку
                                mark_proactive(user_id) # Отмечаем, что написали
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
        
    # Запускаем бота (log_handler=None отключает лишний шум в консоли)
    client.run(TOKEN, log_handler=None)