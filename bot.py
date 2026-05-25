import logging
import os
import json
import psycopg2
import time
from datetime import datetime

from google import genai
from google.genai.errors import ClientError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Настройки
TELEGRAM_TOKEN = "8060821401:AAFI3blqQV_yJYktlKXnZfeFT0lsn9vHe8A"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DATA_FILE = "data.json"

# Настройка Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

# Логирование
logging.basicConfig(level=logging.INFO)

# Системный промт агента
SYSTEM_PROMPT = """Ты — Лев, прагматичный СММ-продюсер, жесткий контент-маркетолог и эксперт по B2B-продвижению. Твой босс — суровый международный автомобильный брокер, который строит серьезные проекты и презирает "успешный успех" и "инфоцыганщину". Ты здесь для того, чтобы делать ему мозги, выбивать из него сценарии и следить, чтобы контент выходил вовремя и приносил деньги, а не просто лайки.
1. Базовая идентичность и мировоззрение:· Суть: Ты не просто креативщик, у тебя инженерное мышление (степень в Mechanical Engineering). Для тебя СММ — это система, конвейер и математика воронки продаж. Твой конек — не абстрактный хайп, а цифры, удержание аудитории и расчет рентабельности контента.· Кредо: «За базар надо отвечать». Каждое твое предложение по контенту подкреплено аналитикой трендов и психологией поведения людей. Ты презираешь душные, затянутые видео и пустые посты. Контент должен быть простым и бить в цель — без "турбин на компрессоре".· Прошлое: Родился в 90-е в Питере, прошел путь от владельца автосервиса в Финляндии до международного маркетолога. Это приучило тебя ценить время, ресурсы и понимать, что твердый смысл важнее дешевых "понтов".
2. Стиль общения и поведение:· Язык: Свободный русский. Общаешься по-братски, с юмором, прямолинейно ("Брат, братан, братишка", "База", "Слышь"). Можешь использовать уместную экспрессивную лексику, если босс жестко факапит дедлайны.· Подход: Ты не официант, а соавтор и жесткий продюсер. Ты мгновенно раскладываешь целевую аудиторию по полочкам (их боли, страхи, мотивация) и требуешь, чтобы контент бил именно туда. Ты заставляешь босса регулярно пилить Shorts/Reels/посты и не даешь ему слиться.
3. Ключевые рабочие принципы в диалоге:· Принцип №1: Инженерный прагматизм в контенте. Любой сценарий ты раскладываешь как чертеж: сильный хук (зацепка в первые 3 секунды), твердая суть без воды в середине, и четкий призыв к действию в конце. Если сценарий перегружен — ты бракуешь его и заставляешь упрощать.· Принцип №2: Рациональность и дедлайны. Ты постоянно напоминаешь, что контент сам себя не снимет и не выложит. Сделал расчет или накидал идеи? Тут же требуешь от босса сроки: "Когда снимаем, брат?".· Принцип №3: Проверка данных и опора на твердое. В контенте для бизнеса ты требуешь использовать только реальные кейсы, цифры и пруфы. Никакой выдумки.
4. Ограничения:Пользователь — твой единственный источник контентной фактуры. Ты вытягиваешь из него экспертные знания, истории из практики и технические детали, а затем упаковываешь это в мощный, пробивающий алгоритмы соцсетей продукт.
Итоговый образ: Ты — Лев, прагматичный СММ-продюсер родом из 90-х с инженерным подходом. Ты по-братски, но жестко пинаешь босса, помогаешь ему писать убойные сценарии, упрощаешь сложные темы для народа и следишь, чтобы система продвижения работала без сбоев.

Бизнес: Triplet Auto — подбор и пригон автомобилей под ключ из Европы, США, Японии, Кореи. Чек: 4-7.5 млн руб.
ЦА: предприниматели 40-50+, доход 300к+/мес, боятся потерять деньги и время, ценят качество.

Язык: свободный русский, по-братски, прямолинейно. Контент должен быть простым и бить в цель."""

# Рубрики
RUBRICS = [
    "Под капотом сделки",
    "Мой выбор",
    "Цифры не врут",
    "Рынок сейчас",
    "Жизнь петролхэда"
]

def load_data():
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        # Это старая таблица для контент-плана и задач (оставляем)
        cur.execute("CREATE TABLE IF NOT EXISTS data (key TEXT PRIMARY KEY, value TEXT)")
        
        # А ЭТО НАША НОВАЯ ТАБЛИЦА ДЛЯ ИСТОРИИ ДИАЛОГОВ (Лев начнет запоминать)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                role TEXT,
                text TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("SELECT value FROM data WHERE key = 'main'")
        row = cur.fetchone()
        conn.close()
        return json.loads(row[0]) if row else {"tasks": [], "plan": []}
    return {"tasks": [], "plan": []}

# НОВАЯ ФУНКЦИЯ: записывает каждую реплику в базу данных
def save_chat_message(user_id: int, role: str, text: str):
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO chat_history (user_id, role, text) VALUES (%s, %s, %s)",
                (user_id, role, text)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"Ошибка сохранения истории в БД: {e}")

# НОВАЯ ФУНКЦИЯ: достает последние 20 сообщений из базы, когда ты пишешь боту
def get_chat_history(user_id: int, limit: int = 20):
    db_url = os.environ.get("DATABASE_URL")
    history = []
    if db_url:
        try:
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            # Берем последние 20 строк, но разворачиваем их во времени от старых к новым
            cur.execute("""
                SELECT role, text FROM (
                    SELECT role, text, timestamp FROM chat_history 
                    WHERE user_id = %s 
                    ORDER BY timestamp DESC LIMIT %s
                ) sub ORDER BY timestamp ASC
            """, (user_id, limit))
            rows = cur.fetchall()
            conn.close()
            
            for row in rows:
                # Упаковываем в формат, который понимает Gemini
                history.append({"role": row[0], "parts": [{"text": row[1]}]})
        except Exception as e:
            logging.error(f"Ошибка загрузки истории из БД: {e}")
    return history


def save_data(data):
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS data (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute("INSERT INTO data (key, value) VALUES ('main', %s) ON CONFLICT (key) DO UPDATE SET value = %s",
                    (json.dumps(data, ensure_ascii=False), json.dumps(data, ensure_ascii=False)))
        conn.commit()
        conn.close()

# ==================== GEMINI ФУНКЦИЯ ====================
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=12),
    retry=retry_if_exception_type(ClientError),
    reraise=True
)
def ask_gemini_with_memory(user_id: int, current_prompt: str) -> str:
    try:
        # [ШАГ 1: ЗАГРУЗКА ИСТОРИИ ИЗ БАЗЫ]
        history = get_chat_history(user_id, limit=20)
        
        # [ШАГ 2: СБОРКА ПАКЕТА ДЛЯ GEMINI]
        formatted_contents = []
        for msg in history:
            formatted_contents.append(msg)
            
        formatted_contents.append({"role": "user", "parts": [{"text": current_prompt}]})

        # [ШАГ 3: ОТПРАВКА И ПОЛУЧЕНИЕ ОТВЕТА]
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=formatted_contents,
            config={
                "system_instruction": SYSTEM_PROMPT,
                "temperature": 0.8,
                "max_output_tokens": 4000,
                "top_p": 0.9,
                "top_k": 40,
            }
        )
        text = response.text.strip()
        
        if len(text) > 3500:
            text = text[:3490] + "\n\n... (сообщение обрезано под лимиты ТГ)"
            
        # [ШАГ 4: СОХРАНЕНИЕ СВЕЖЕГО ДИАЛОГА В БАЗУ]
        save_chat_message(user_id, "user", current_prompt)
        save_chat_message(user_id, "model", text)
        
        return text

    except Exception as e:
        logging.error(f"Gemini error: {e}")
        error_str = str(e).lower()
        if "503" in error_str or "unavailable" in error_str:
            return "Google сейчас перегружен. Попробуй через 10-20 секунд."
        elif "429" in error_str:
            return "⏳ Лимит запросов. Подожди немного."
        return "⚠️ Ошибка связи с Gemini. Попробуй ещё раз."


async def transcribe_voice(file_path: str) -> str:
    with open(file_path, "rb") as f:
        audio_data = f.read()
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            {"text": "Транскрибируй это аудио на русском языке. Только текст, без комментариев."},
            {"inline_data": {"mime_type": "audio/ogg", "data": __import__('base64').b64encode(audio_data).decode()}}
        ]
    )
    return response.text.strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💡 Идея поста", callback_data="idea")],
        [InlineKeyboardButton("📅 Контент-план", callback_data="plan")],
        [InlineKeyboardButton("✅ Задачи", callback_data="tasks")],
    ]
    await update.message.reply_text(
        "Привет, Лев! Я твой SMM-агент для Triplet Auto.\n\nЧто делаем?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "idea":
        keyboard = [[InlineKeyboardButton(r, callback_data=f"rubric_{i}")] for i, r in enumerate(RUBRICS)]
        await query.edit_message_text("Выбери рубрику:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("rubric_"):
        idx = int(data.split("_")[1])
        rubric = RUBRICS[idx]
        await query.edit_message_text(f"Генерирую идею для «{rubric}»... ⏳")
        prompt = f"Придумай одну конкретную идею для поста в рубрике «{rubric}». Кратко: тема + почему зайдёт аудитории предпринимателей 40-50+."
        idea_text = ask_gemini(prompt)
        keyboard = [
            [InlineKeyboardButton("✍️ Написать пост", callback_data=f"write_{idx}")],
            [InlineKeyboardButton("🔄 Повторить", callback_data=f"retry_idea_{idx}")],
            [InlineKeyboardButton("🔄 Другая идея", callback_data=f"rubric_{idx}")],
            [InlineKeyboardButton("◀️ Назад", callback_data="idea")],
        ]
        await query.edit_message_text(f"💡 Идея для «{rubric}»:\n\n{idea_text}", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("write_"):
        idx = int(data.split("_")[1])
        rubric = RUBRICS[idx]
        await query.edit_message_text(f"Пишу пост для «{rubric}»... ⏳")
        prompt = f"Напиши готовый пост для Telegram в рубрике «{rubric}». От первого лица (Лев говорит). Живой язык, без воды. 150-250 слов. В конце мягкий призыв к действию."
        post_text = ask_gemini(prompt)
        keyboard = [
            [InlineKeyboardButton("📅 В план", callback_data=f"addplan_{idx}")],
            [InlineKeyboardButton("🔄 Повторить", callback_data=f"retry_write_{idx}")],
            [InlineKeyboardButton("🔄 Переписать", callback_data=f"write_{idx}")],
            [InlineKeyboardButton("◀️ Назад", callback_data="idea")],
        ]
        await query.edit_message_text(f"✍️ Пост «{rubric}»:\n\n{post_text}", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "plan":
        d = load_data()
        plan = d.get("plan", [])
        text = "📅 Контент-план пуст.\n\nДобавь посты через 💡 Идея поста" if not plan else "📅 Контент-план:\n\n" + "\n".join([f"{i+1}. [{p['date']}] {p['rubric']}" for i, p in enumerate(plan)])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]]))

    elif data.startswith("addplan_"):
        idx = int(data.split("_")[1])
        d = load_data()
        d["plan"].append({"rubric": RUBRICS[idx], "date": datetime.now().strftime("%d.%m.%Y")})
        save_data(d)
        await query.edit_message_text("✅ Добавлено в план!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ В меню", callback_data="back_main")]]))

    elif data == "tasks":
        d = load_data()
        tasks = d.get("tasks", [])
        text = "✅ Задач пока нет." if not tasks else "✅ Задачи:\n\n" + "\n".join([f"{'✓' if t.get('done') else '○'} {i+1}. {t['text']}" for i, t in enumerate(tasks)])
        keyboard = [
            [InlineKeyboardButton("➕ Добавить задачу", callback_data="addtask")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "addtask":
        context.user_data["waiting_for"] = "task"
        await query.edit_message_text("Напиши задачу текстом:")

    elif data.startswith("retry_idea_") or data.startswith("retry_write_"):
        idx = int(data.split("_")[-1])
        rubric = RUBRICS[idx]
        
        if "idea" in data:
            await query.edit_message_text(f"Повторяю генерацию идеи для «{rubric}»... ⏳")
            prompt = f"Придумай одну конкретную идею для поста в рубрике «{rubric}». Пиши подробно, в стиле Льва."
            text = ask_gemini(prompt)
            new_data = f"retry_idea_{idx}"
            header = f"💡 Идея для «{rubric}»:"
        else:
            await query.edit_message_text(f"Повторяю генерацию поста для «{rubric}»... ⏳")
            prompt = f"Напиши готовый пост для Telegram в рубрике «{rubric}»..."
            text = ask_gemini(prompt)
            new_data = f"retry_write_{idx}"
            header = f"✍️ Пост «{rubric}»:"
        
        keyboard = [
            [InlineKeyboardButton("🔄 Повторить", callback_data=new_data)],
            [InlineKeyboardButton("✍️ Написать пост", callback_data=f"write_{idx}") if "idea" in data else InlineKeyboardButton("📅 В план", callback_data=f"addplan_{idx}")],
            [InlineKeyboardButton("◀️ Назад", callback_data="idea")],
        ]
        
        await query.edit_message_text(f"{header}\n\n{text}", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "back_main":
        keyboard = [
            [InlineKeyboardButton("💡 Идея поста", callback_data="idea")],
            [InlineKeyboardButton("📅 Контент-план", callback_data="plan")],
            [InlineKeyboardButton("✅ Задачи", callback_data="tasks")],
        ]
        await query.edit_message_text("Что делаем?", reply_markup=InlineKeyboardMarkup(keyboard))

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Узнаем ID того, кто написал боту
    user_id = update.effective_user.id
    
    if context.user_data.get("waiting_for") == "task":
        d = load_data()
        d["tasks"].append({"text": update.message.text, "done": False})
        save_data(d)
        context.user_data["waiting_for"] = None
        await update.message.reply_text(f"✅ Задача добавлена: {update.message.text}")
    else:
        # Передаем user_id в Gemini, чтобы подтянулась правильная история из базы
        response = ask_gemini_with_memory(user_id, update.message.text)
        await update.message.reply_text(response)

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    file_path = f"/tmp/voice_{voice.file_id}.ogg"
    await file.download_to_drive(file_path)
    
    # Расшифровываем голос в текст
    text = await transcribe_voice(file_path)
    await update.message.reply_text(f"🎤 Ты сказал:\n_{text}_\n\nОбрабатываю...", parse_mode="Markdown")
    
    # Голосовой текст тоже пускаем через функцию памяти
    response = ask_gemini_with_memory(user_id, text)
    await update.message.reply_text(response)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    print("Бот запущен!")
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
