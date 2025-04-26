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
from collections import defaultdict, deque

# Режимы работы пользователей: 'chat' или 'code'
user_modes = defaultdict(lambda: 'chat')

# ⬆️ Сессии
sessions = defaultdict(lambda: {"history": [], "last_active": time.time()})


# Настройка логирования
# logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("aiogram.event").setLevel(logging.WARNING)

# ключевые слова для переключения из чата в код-режим
CONFIRM_WORDS = ["да", "готово", "подтверждаю", "всё верно"]

# 🔐 Токены и ключи
BOT_TOKEN = os.getenv("TOOLBOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)


# обновления сессии
def update_user_session(user_id, user_message):
    user_sessions[user_id].append({"role": "user", "content": user_message})
    logging.info(f"📚 Обновлена сессия пользователя {user_id}: {user_message[:50]}...")


# Функция для чтения system prompt из файла
def load_system_prompts():
    try:
        with open("chat_system_prompt.txt", "r", encoding="utf-8") as f:
            chat_system_prompt = f.read().strip()
        with open("code_system_prompt.txt", "r", encoding="utf-8") as f:
            code_system_prompt = f.read().strip()
        return chat_system_prompt, code_system_prompt
    except Exception as e:
        logging.error(f"❌ Ошибка при загрузке system prompt'ов: {e}")
        return "", ""



# Пример использования system_prompt
prompt_chat, prompt_code = load_system_prompts()

# system_prompt_chat = chat_system_prompt()
# system_prompt_code = code_system_prompt()


# 🔁 ФУНКЦИЯ ДЛЯ ПИНГОВАНИЯ RENDER
async def ping_render():
    while True:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("https://tools-bot.onrender.com")
                logging.info(f"🔄 Пинг на Render: {response.status_code}")
        except Exception as e:
            logging.warning(f"⚠️ Ошибка при пинге Render: {e}")
        await asyncio.sleep(300)  # каждые 14 минут


# 🧠 Парсинг JSON из текста
def extract_json(text: str) -> dict:
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except Exception:
        return None
        

async def analyze_message(history: str, prompt, mode="chat"):
    if mode == 'code':
        prompt = [
            {"role": "system", "content": prompt_code},
            {"role": "user", "content": history}
        ]
    else:
        prompt = [
            {"role": "system", "content": prompt_chat},
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



# 🧠 Функция анализа требований в режиме чата
async def summarize_requirements(messages_text, system_prompt):
    try:
        response = await analyze_message(messages_text, system_prompt, mode="chat")
        logging.info(f"[summarize_requirements] Получен ответ:\n{response}")

        # Пытаемся вырезать JSON из ответа
        json_start = response.find('{')
        json_end = response.rfind('}') + 1
        json_part = response[json_start:json_end]

        data = json.loads(json_part)
        logging.info(f"[summarize_requirements] Распарсенный JSON:\n{data}")

        return data

    except Exception as e:
        logging.error(f"[summarize_requirements] Ошибка при обработке ответа: {e}")
        return {"status": "need_more_info", "reply": "Извини, я не смог обработать твой ответ. Попробуй переформулировать задачу."}



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
    

async def send_generated_tool(message, result):
    """Формирует скрипт, упаковывает в архив и отправляет пользователю"""

    user_id = message.from_user.id

    # 🛠 Генерация кода на основе результата
    task = "Скрипт инструмента"  # Можно сделать умнее — брать тему из задачи
    params = {"Описание": result[:100]}  # Просто краткий отрывок в параметры
    code = generate_code(task, params)

    # 📦 Создание ZIP-архива
    zip_file = create_zip(task, code)

    # ⬇️ Отправляем архив пользователю
    await message.answer_document(zip_file, caption="✅ Инструмент успешно создан!")

    # 🧹 Сброс режима и истории после отправки
    user_modes[user_id] = 'chat'
    sessions.pop(user_id, None)



# Начало диалога по нажатию кнопки
@dp.message_handler(commands=["start"])
async def send_welcome(message: types.Message):
    user_id = message.from_user.id
    user_modes[user_id] = 'chat'
    sessions.pop(user_id, None)
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


@dp.message_handler()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()

    # Получаем текущий режим пользователя
    mode = user_modes.get(user_id, 'chat')

    if mode == 'chat':
        # Работа в чате: уточняем требования
        history = sessions.setdefault(user_id, {"history": [], "last_active": time.time()})
        history["history"].append(text)
        history["last_active"] = time.time()

        combined_history = "\n".join(history["history"])
        result = await summarize_requirements(combined_history, prompt_chat)

        if result.get('status') == 'ready_to_generate':
            await message.answer("Отлично! Генерирую инструмент...")

            # Генерация и отправка инструмента
            await send_generated_tool(message, combined_history)

            # Сброс сессии
            user_modes[user_id] = 'chat'
            sessions.pop(user_id, None)
        else:
            reply = result.get('reply', "Не совсем понял. Можешь переформулировать?")
            await message.answer(reply)

    else:
        await message.answer("Пожалуйста, опиши, какой инструмент ты хочешь создать.")




# 🧹 АВТООЧИСТКА СЕССИЙ
async def cleanup_sessions():
    while True:
        now = time.time()
        to_delete = []

        for user_id, session in list(sessions.items()):
            if now - session["last_active"] > 3600:  # 1 час неактивности
                to_delete.append(user_id)

        for user_id in to_delete:
            sessions.pop(user_id, None)
            logging.info(f"🗑️ Удалена сессия пользователя {user_id} из-за неактивности.")
        await asyncio.sleep(600)  # каждые 10 мин



# 🌐 Flask-сервер для Render
@app.route("/")
def index():
    return "ToolBot работает!"


# 🚀 Главная точка входа
async def main():
    asyncio.create_task(cleanup_sessions())  # автоочистка
    asyncio.create_task(ping_render())
    await dp.start_polling()

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

if __name__ == "__main__":
    Thread(target=run_flask).start()  # запуск Flask в фоне
    asyncio.run(main())  # запуск aiogram как основной задачи


