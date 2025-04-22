import os
import logging
from flask import Flask, request, jsonify, send_file
from zipfile import ZipFile
from io import BytesIO
import httpx

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AILEX_SHARED_SECRET = os.getenv("AILEX_SHARED_SECRET")

zip_storage = {}
sessions = {}  # 🆕 Хранилище сессий

# 🔍 OpenRouter-запрос
async def analyze_message(message: str):
    prompt = [
        {"role": "system", "content": "Ты ИИ-помощник. Получи текст задачи и определи, какой инструмент нужно создать. Верни JSON с полями task и params."},
        {"role": "user", "content": message}
    ]
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openchat/openchat-7b",  # Можно заменить на другой
        "messages": prompt
    }

    async with httpx.AsyncClient() as client:
        response = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        logging.info(f"[OpenRouter] Ответ: {content}")
        return eval(content)  # Простой парсинг словаря из строки

# 🆕 Проверка готовности к генерации
def ready_to_generate(history):
    text = " ".join(history).lower()
    return len(history) > 2 and any(kw in text for kw in ["сделай", "нужно", "бот", "код", "инструмент"])

@app.route("/generate_tool", methods=["POST"])
def generate_tool():
    from asyncio import run

    data = request.get_json()
    if request.headers.get("Ailex-Shared-Secret") != AILEX_SHARED_SECRET:
        return jsonify({"status": "error", "message": "⛔ Неверный секрет. 🤖 (tулс-бот)"}), 403

    user_id = str(data.get("user_id", "anonymous"))
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"status": "error", "message": "Пустой запрос. 🤖 (tулс-бот)"})

    # 🆕 Обработка истории пользователя
    history = sessions.setdefault(user_id, {"history": []})["history"]
    history.append(message)

    if not ready_to_generate(history):
        if len(history) == 1:
            return jsonify({"status": "ok", "message": "👋 Привет! Что хочешь, чтобы я сделал?"})
        else:
            return jsonify({"status": "ok", "message": "🛠 Уточни, что именно нужно. Я пока слушаю."})

    try:
        # 🧠 AI-анализ сообщения
        result = run(analyze_message(" ".join(history)))
        task = result.get("task", "инструмент")
        params = result.get("params", {})

        # 🛠 Генерация кода
        code = f"# Задача: {task}\n# Параметры: {params}\n\nprint('Инструмент готов')"

        zip_buffer = BytesIO()
        with ZipFile(zip_buffer, 'w') as zip_file:
            zip_file.writestr(f"{task.replace(' ', '_')}.py", code)
        zip_buffer.seek(0)
        zip_storage[user_id] = zip_buffer

        return jsonify({
            "status": "done",
            "message": f"🤖 (tулс-бот) ✅ Инструмент готов! <a href='https://tools-bot.onrender.com/download_tool/{user_id}'>Скачать архив</a>"
        })

    except Exception as e:
        logging.exception("Ошибка генерации")
        return jsonify({"status": "error", "message": f"🤖 (tулс-бот) Ошибка: {str(e)}"})

@app.route("/download_tool/<user_id>")
def download_tool(user_id):
    buffer = zip_storage.get(user_id)
    if not buffer:
        return "Архив не найден", 404
    return send_file(buffer, as_attachment=True, download_name=f"{user_id}_tool.zip")

@app.route("/")
def home():
    return "🤖 Tools API (с ИИ) работает!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
