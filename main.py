import os
import logging
import json
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
from aiogram.dispatcher.filters import CommandStart
from io import BytesIO
from zipfile import ZipFile
import httpx

# 🔐 Токены и ключи
BOT_TOKEN = os.getenv("TOOLBOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)
logging.basicConfig(level=logging.INFO)

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
                "Ты — ИИ-конструктор инструментов. Получаешь описание задачи, уточняешь детали. "
                "Когда всё понятно — возвращаешь JSON с:\n"
                "- status: 'ready' или 'need_more_info'\n"
                "- reply: что сказать пользователю\n"
                "- task: краткое описание\n"
                "- params: параметры задачи (dict)"
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
    sessions[user_id] = []  # начинаем с чистой истории
    await bot.send_message(user_id, "Привет! Опиши, какой инструмент тебе нужен 🧠")
    await callback_query.answer()

# 📩 Обработка всех сообщений после нажатия кнопки
@dp.message_handler()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()

    # добавляем сообщение в историю
    history = sessions.setdefault(user_id, [])
    history.append(text)

    # анализируем всю историю диалога
    result = await analyze_message("\n".join(history))
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

        # очищаем сессию
        sessions.pop(user_id, None)
    else:
        await message.answer("⚠️ Что-то пошло не так. Попробуй ещё раз.")

# 🚀 Старт бота
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
