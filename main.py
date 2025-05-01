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

# Отслеживание подтверждения генерации
user_states = {}

user_goals = {}  # user_id -> goal (назначение будущего инструмента)

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


# Обновления сессии
def update_user_session(user_id, user_message):
    sessions[user_id]["history"].append({"role": "user", "content": user_message})
    sessions[user_id]["last_active"] = time.time()
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
    logging.debug(f"[extract_json] 🔍 Пытаемся извлечь JSON из текста:\n{text}")
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        json_str = text[start:end]
        result = json.loads(json_str)
        logging.debug(f"[extract_json] ✅ Успешно извлечён JSON:\n{result}")
        return result
    except ValueError as ve:
        logging.error(f"[extract_json] ❌ Ошибка при поиске фигурных скобок: {ve}")
    except json.JSONDecodeError as je:
        logging.error(f"[extract_json] ❌ Ошибка при разборе JSON: {je}")
        logging.debug(f"[extract_json] 🚫 Проблемный фрагмент:\n{text[start:end]}")
    except Exception as e:
        logging.error(f"[extract_json] ❌ Неизвестная ошибка: {e}")

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
            logging.debug(f"[analyze_message] 📥 Ответ от OpenRouter:\n{response.text}")
            response.raise_for_status()

            try:
                result = response.json()
            except Exception as json_error:
                logging.error(f"[analyze_message] ❌ Ошибка парсинга JSON: {json_error}")
                return {
                    "status": "need_more_info",
                    "reply": "⚠️ Не удалось распознать ответ от модели. Попробуй переформулировать."
                }

            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                logging.warning("[analyze_message] ❗️Пустой content в ответе.")
                return {
                    "status": "need_more_info",
                    "reply": "Ответ от модели был пуст. Попробуй ещё раз описать задачу."
                }

            logging.debug(f"[analyze_message] 🧠 Содержимое content:\n{content}")
            result_dict = extract_json(content)

            if not result_dict:
                logging.error("[analyze_message] ❌ Не удалось извлечь JSON из содержимого.")
                return {
                    "status": "need_more_info",
                    "reply": "Извини, я не понял твою задачу. Можешь объяснить чуть подробнее?"
                }

            logging.info(f"[analyze_message] ✅ Успешный разбор результата: {result_dict}")
            return {
                "status": result_dict.get("status", "need_more_info"),
                "reply": result_dict.get("reply"),
                "task": result_dict.get("task"),
                "params": result_dict.get("params"),
            }

    except httpx.RequestError as e:
        logging.error(f"[analyze_message] 🔌 Ошибка при запросе к OpenRouter: {e}")
        return {"status": "need_more_info", "reply": "Ошибка при соединении с OpenRouter."}

    except Exception as e:
        logging.error(f"[analyze_message] ❗️ Произошла непредвиденная ошибка: {e}")
        return {"status": "need_more_info", "reply": "Произошла непредвиденная ошибка."}



# 🧠 Функция анализа требований в режиме чата
async def summarize_requirements(messages_text, system_prompt):
    try:
        logging.info("[summarize_requirements] Отправка текста в analyze_message()")
        response = await analyze_message(messages_text, system_prompt, mode="chat")
        logging.info(f"[summarize_requirements] Получен исходный ответ:\n{response}")

        # Если ответ уже в виде словаря — отлично
        if isinstance(response, dict):
            logging.info("[summarize_requirements] Ответ уже является словарём, возвращаем напрямую.")
            return response

        # Попытка восстановить JSON из текста
        logging.warning("[summarize_requirements] Ответ не словарь. Пробуем извлечь JSON вручную.")
        start = response.find('{')
        end = response.rfind('}') + 1
        maybe_json = response[start:end]

        logging.info(f"[summarize_requirements] Извлекаемый JSON:\n{maybe_json}")

        parsed = json.loads(maybe_json)
        logging.info(f"[summarize_requirements] Успешно распарсили JSON: {parsed}")
        return parsed

    except json.JSONDecodeError as json_err:
        logging.error(f"[summarize_requirements] Ошибка JSON-декодирования: {json_err}")
        logging.debug(f"[summarize_requirements] Невозможно распарсить это как JSON:\n{response}")
        return {
            "status": "need_more_info",
            "reply": "Ответ получен, но не удалось его обработать. Попробуй переформулировать или уточни, что ты хочешь.",
        }

    except Exception as e:
        logging.error(f"[summarize_requirements] Общая ошибка: {e}", exc_info=True)
        return {
            "status": "need_more_info",
            "reply": "Извини, возникла ошибка при обработке. Попробуй ещё раз.",
        }



async def summarize_code_details(user_input, system_prompt):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input}
    ]

    try:
        response = await call_openrouter(messages)
        return {
            "status": "ok",
            "reply": response
        }
    except Exception as e:
        logging.error(f"[summarize_code_details] Ошибка при генерации: {e}")
        return {
            "status": "error",
            "reply": "Ошибка при генерации технического задания. Попробуй ещё раз."
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
    text = message.text.strip().lower()
    logging.info(f"[handle_message] 📩 Сообщение от {user_id}: {text}")

    # Получаем режим пользователя
    mode = user_modes.get(user_id, 'chat')
    logging.info(f"[handle_message] 🔄 Текущий режим: {mode}")

    # === Подтверждение перехода на генерацию ===
    if mode == 'waiting_confirmation':
        if text in ['готов', 'го', 'давай', 'поехали']:
            user_modes[user_id] = 'code'
            logging.info(f"[handle_message] ✅ Пользователь подтвердил — переходим в режим code.")
            await message.answer("🚀 Отлично! Теперь переходим к сбору параметров и созданию инструмента.")
        else:
            logging.info(f"[handle_message] ⏳ Ожидаем подтверждения от {user_id}.")
            await message.answer("✋ Напиши 'Готов', если хочешь перейти к следующему этапу.")
        return

   # === Обновление истории сессии с использованием функции update_user_session ===
    session = sessions.setdefault(user_id, {"history": [], "last_active": time.time()})
    update_user_session(user_id, text)
    session["last_active"] = time.time()
    combined_history = "\n".join([msg["content"] for msg in session["history"]])
    logging.info(f"[handle_message] 💬 История пользователя {user_id}:\n{combined_history}")


    # === Анализ идеи ===
    logging.info(f"[handle_message] ⏳ Отправка в summarize_requirements...")
    result = await summarize_requirements(combined_history, prompt_chat)

    reply = result.get('reply', "Не совсем понял. Можешь переформулировать?")
    params = result.get('params', {})
    
    ideas = result.get('params', {}).get('вопросы', [])
    ideas_text = "\n".join([f"📌 *{i['название']}*\n{i['описание']}" for i in ideas]) if ideas else ""
    reply_text = f"{reply}\n\n{ideas_text}" if ideas_text else reply

    logging.info(f"[handle_message] 📥 Ответ анализа идеи: {result}")

    status = result.get('status')

    # === Предложение перейти к следующему этапу ===
    if status == 'ready_to_start_code_phase':
        user_modes[user_id] = 'waiting_confirmation'
        await message.answer(f"✅ {reply} Напиши 'Готов', если хочешь перейти к сбору параметров.")
        return

    # === Полная готовность (альтернатива, если используешь ready_to_generate) ===
    if status == 'ready_to_generate':
        user_modes[user_id] = 'waiting_confirmation'
        await message.answer(f"✅ {reply} Напиши 'Готов', чтобы начать генерацию инструмента.")
        return

    # === Юзер просит идеи ===
    if status == 'need_more_info':
        if any(kw in text for kw in ['предложи', 'идею', 'идеи', 'варианты', 'подкинь', 'не знаю']) and not result.get("params"):
            logging.info(f"[handle_message] 🔍 Обнаружен запрос на генерацию идей от {user_id}")

            suggestion_prompt = (
                "Пользователь пока не определился с инструментом. "
                "Предложи 3-5 идей полезных инструментов или скриптов на основе нейросетей или Python. "
                "Кратко опиши назначение каждого, чтобы пользователь мог выбрать."
            )
            suggestions = await analyze_message(suggestion_prompt, prompt_chat, mode="chat")
            logging.info(f"[handle_message] 💡 Идеи, предложенные пользователю:\n{suggestions}")

            # 🧠 Поддержка формата JSON с полем params
            if isinstance(suggestions, dict):
                ideas = suggestions.get("params", {}).get("вопросы")
                if ideas:
                    text_response = "🧠 Вот несколько идей:\n"
                    for idea in ideas:
                        title = idea.get("название", "Без названия")
                        description = idea.get("описание", "Без описания")
                        text_response += f"\n📌 *{title}*\n{description}\n"
                    await message.answer(text_response, parse_mode="Markdown")
                else:
                    await message.answer(suggestions.get("reply", "Готов обсудить идеи!"))
            else:
                await message.answer(reply_text, parse_mode="Markdown")
            return

        # Просто уточнение
        await message.answer(reply_text, parse_mode="Markdown")
        return

    # Неизвестный статус
    logging.warning(f"[handle_message] ⚠️ Неизвестный статус: {status}")
    await message.answer("⚠️ Что-то пошло не так. Попробуй переформулировать запрос.")




async def process_code_mode(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()
    logging.info(f"[process_code_mode] Обработка сообщения в режиме code от {user_id}: {text}")

    # Получаем цель, сформулированную ранее
    session = sessions.get(user_id, {})
    goal = session.get("goal", "неизвестный инструмент")

    prompt = prompt_code.replace("<<GOAL>>", goal)

    result = await summarize_code_details(text, prompt)
    reply = result.get("reply", "Что-то пошло не так при составлении ТЗ.")

    await message.answer(reply)
    logging.info(f"[process_code_mode] Ответ отправлен пользователю {user_id}")

        

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


