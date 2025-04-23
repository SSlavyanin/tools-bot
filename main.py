import os
import logging
import json
import httpx
import asyncio
import time
from threading import Thread
from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
from aiogram.dispatcher.filters import CommandStart
from io import BytesIO
from zipfile import ZipFile


# Настройка логирования
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# 🔐 Токены и ключи
BOT_TOKEN = os.getenv("TOOLBOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# 🧠 История сообщений по каждому пользователю
sessions = {}

# Функция для чтения system prompt из файла
def load_system_prompt(filename="system_prompt.txt"):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(base_dir, filename)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()

# Пример использования
system_prompt = load_system_prompt()

# 🧠 Парсинг JSON из текста
def extract_json(text: str) -> dict:
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except Exception:
        return None

async def analyze_message(history: str):
    prompt = [
        {
            "role": "system",
            "content": system_prompt
        },
        {"role": "user", "content": history}
    ]

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {"model": "meta-llama/llama-4-maverick", "messages": prompt}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
            logging.debug(f"Ответ от OpenRouter: {response.text}")  # Логируем ответ
            response.raise_for_status()  # Проверка статуса ответа
            result = response.json()
            content = result["choices"][0]["message"]["content"]

            result_dict = extract_json(content)
            if not result_dict:
                logging.error("Не удалось извлечь JSON из ответа.")
                return {
                    "status": "need_more_info",
                    "reply": "Извини, я не понял твою задачу. Можешь объяснить чуть подробнее?"
                }

            return {
                "status": result_dict.get("status", "need_more_info"),
                "reply": result_dict.get("reply"),
                "task": result_dict.get("task"),
                "params": result_dict.get("params"),
            }

    except httpx.RequestError as e:
        logging.error(f"Ошибка при запросе к OpenRouter: {e}")
        return {"status": "need_more_info", "reply": "Ошибка при запросе к OpenRouter."}

    except Exception as e:
        logging.error(f"Произошла ошибка: {e}")
        return {"status": "need_more_info", "reply": "Произошла непредвиденная ошибка."}



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

    # обновляем историю
    history = sessions.setdefault(user_id, {"history": [], "last_active": time.time()})
    history["history"].append(text)
    history["last_active"] = time.time()

    # получаем результат анализа
    result = await analyze_message("\n".join(history["history"]))
    status = result.get("status")
    reply = result.get("reply")

    if status == "need_more_info":
        # выводим только reply — текст вопроса
        if reply:
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
            logging.info(f"Удалена сессия пользователя {user_id} из-за неактивности.")  # Логируем удаление

        await asyncio.sleep(600)  # проверка каждые 10 минут


# 🌐 Flask-сервер для Render
@app.route("/")
def index():
    return "ToolBot работает!"


# 🚀 Главная точка входа
async def main():
    asyncio.create_task(cleanup_sessions())  # автоочистка
    await dp.start_polling()

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

if __name__ == "__main__":
    Thread(target=run_flask).start()  # запуск Flask в фоне
    asyncio.run(main())  # запуск aiogram как основной задачи


