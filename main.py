import os
import logging
import json
import threading
import httpx
import asyncio
import time
from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
from aiogram.dispatcher.filters import CommandStart
from io import BytesIO
from zipfile import ZipFile


# 🔐 Токены и ключи
BOT_TOKEN = os.getenv("TOOLBOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# 🧠 История сообщений по каждому пользователю
sessions = {}

# 🧠 Парсинг JSON из текста
def extract_json(text: str) -> dict:
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except Exception:
        return None

# 🔍 Анализ сообщения через OpenRouter
async def analyze_message(history: str):
    prompt = [
        {
            "role": "system",
            "content": (
                "Ты — ИИ-конструктор инструментов. Получаешь описание задачи от пользователя. "
                "Твоя цель — понять задачу и при необходимости задать уточняющие вопросы. "
                "Когда задача ясна, **не переходи сразу к генерации кода**. Вместо этого уточни у пользователя, можно ли начинать генерацию.\n\n"
                "Всегда возвращай **только** JSON со следующими полями:\n"
                "- status: 'need_more_info' (если нужно задать вопрос) или 'ready' (если всё понятно и можно спросить, начинать ли генерацию);\n"
                "- reply: текст для пользователя (вопрос или сообщение);\n"
                "- task: краткое описание задачи (если status = 'ready');\n"
                "- params: словарь параметров задачи (если status = 'ready').\n\n"
                "Никакого текста вне JSON. Только словарь Python."
            )
        },
        {"role": "user", "content": history}
    ]

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {"model": "openchat/openchat-7b", "messages": prompt}

    async with httpx.AsyncClient() as client:
        response = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
        result = response.json()
        content = result["choices"][0]["message"]["content"]

        result_dict = extract_json(content)
        if not result_dict:
            return {"status": "need_more_info", "reply": content.strip()}

        return {
            "status": result_dict.get("status", "need_more_info"),
            "reply": result_dict.get("reply", content.strip()),
            "task": result_dict.get("task"),
            "params": result_dict.get("params"),
        }

# 🛠 Генерация кода инструмента
def generate_code(task, params):
    lines = [f"# Инструмент: {task}", "# Параметры:"]
    for k, v in params.items():
        lines.append(f"# {k}: {v}")
    lines.append("\nprint('Инструмент готов к работе!')")
    return "\n".join(lines)

# 📦 Создание ZIP-архива
def create_zip(task, code: str):
    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, 'w') as zip_file:
        filename = f"{task.replace(' ', '_')}.py"
        zip_file.writestr(filename, code)
    zip_buffer.seek(0)
    zip_buffer.name = f"{task.replace(' ', '_')}.zip"
    return zip_buffer

# Начало диалога по нажатию кнопки
@dp.message_handler(commands=["start"])
async def send_welcome(message: types.Message):
    if message.get_args() == "tool":
        # Это сработает, если бот был вызван с параметром start=tool
        await message.reply("Вы перешли к созданию инструмента! Начни описывать, что тебе нужно.")
    else:
        # Это сработает для обычного запуска бота (например, без параметров)
        await message.reply("Привет! Нажми кнопку ниже, чтобы начать создание инструмента.")

# 🎛 Кнопка "Сделать инструмент"
@dp.message_handler(commands=["start", "tool"])
async def start_command(message: types.Message):
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🛠 Сделать инструмент", callback_data="make_tool")
    )
    await message.reply("Нажми кнопку ниже, чтобы начать создание инструмента:", reply_markup=kb)

# 🔘 Обработка нажатия на кнопку
@dp.callback_query_handler(lambda c: c.data == "make_tool")
async def handle_tool_request(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    sessions[user_id] = {"history": [], "last_active": time.time()}  # начинаем с чистой истории и метки времени
    await bot.send_message(user_id, "Привет! Опиши, какой инструмент тебе нужен 🧠")
    await callback_query.answer()

# 📩 Обработка всех сообщений после нажатия кнопки
@dp.message_handler()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()

    # добавляем сообщение в историю и обновляем время активности
    history = sessions.setdefault(user_id, {"history": [], "last_active": time.time()})
    history["history"].append(text)
    history["last_active"] = time.time()

    result = await analyze_message("\n".join(history["history"]))
    status = result.get("status")
    reply = result.get("reply")

    if status == "need_more_info":
        await message.answer(reply)
    elif status == "ready":
        task = result.get("task")
        params = result.get("params")
        code = generate_code(task, params)
        zip_file = create_zip(task, code)

        await message.answer("✅ Инструмент готов! Вот архив:")
        await message.answer_document(InputFile(zip_file))

        sessions.pop(user_id, None)
    else:
        await message.answer("⚠️ Что-то пошло не так. Попробуй ещё раз.")

# 🌐 Flask-сервер для Render
@app.route("/")
def index():
    return "ToolBot работает!"

# 🚀 Функция очистки сессий
async def cleanup_sessions():
    while True:
        now = time.time()
        to_delete = []

        for user_id, session in sessions.items():
            last_msg_time = session["last_active"]
            if now - last_msg_time > 3600:  # 1 час неактивности
                to_delete.append(user_id)

        for user_id in to_delete:
            sessions.pop(user_id, None)

        await asyncio.sleep(600)  # проверка каждые 10 минут

# 🚀 Запуск Flask + aiogram
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(cleanup_sessions())  # фоновая задача по очистке
    threading.Thread(target=lambda: executor.start_polling(dp, skip_updates=True)).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
