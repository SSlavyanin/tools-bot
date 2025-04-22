import os
import logging
from flask import Flask, request, jsonify, send_file
from zipfile import ZipFile
from io import BytesIO
import httpx
import json
import re

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AILEX_SHARED_SECRET = os.getenv("AILEX_SHARED_SECRET")

zip_storage = {}
sessions = {}  # 🧠 Храним историю общения по user_id

# 🧠 Парсер JSON из текста
def extract_json(text: str) -> dict:
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        json_str = text[start:end]
        return json.loads(json_str)
    except Exception:
        return None  # ⬅ вместо ValueError — возвращаем None

    
# 🔍 Обращение к OpenRouter с задачей
async def analyze_message(text: str):
    prompt = [
        {
            "role": "system",
            "content": (
                "Ты — ИИ-конструктор инструментов. Получаешь сообщение от пользователя и помогаешь сформулировать задание. "
                "Задавай уточняющие вопросы, если что-то неясно. Когда всё ясно — уточни у пользователя, можно ли начинать генерацию. "
                "Верни _только_ JSON в виде Python словаря (dict), без обрамляющего текста. "
                "Поля:\n"
                "- status: 'need_more_info' или 'ready';\n"
                "- reply: строка с ответом пользователю;\n"
                "- task: краткое описание задачи (если ready);\n"
                "- params: словарь параметров (если ready)."
            )
        },
        {"role": "user", "content": text}
    ]

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "openchat/openchat-7b",  # Можно заменить модель
        "messages": prompt
    }

    async with httpx.AsyncClient() as client:
        response = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        logging.info(f"[OpenRouter] Ответ: {content}")

        # Пытаемся распарсить ответ как JSON
        content = await call_openrouter(text)
        result_dict = extract_json(content)

        if not result_dict:
            return {
                "status": "need_more_info",
                "reply": content.strip(),  # Возвращаем текст ответа без изменений
                "task": None,
                "params": None
            }
    
        return {
            "status": result_dict.get("status", "need_more_info"),
            "reply": result_dict.get("reply", content.strip()),
            "task": result_dict.get("task"),
            "params": result_dict.get("params"),
        }

            

# 🛠 Генерация простого Python-скрипта
def generate_code(task, params):
    lines = [f"# Инструмент: {task}", "# Параметры:"]
    for k, v in params.items():
        lines.append(f"# {k}: {v}")
    lines.append("\nprint('Инструмент готов к работе!')")
    return "\n".join(lines)

# 📦 Основной обработчик генерации инструмента
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

    # 💬 Обновляем историю
    history = sessions.setdefault(user_id, {"history": []})["history"]
    history.append(message)

    try:
        result = run(analyze_message("\n".join(history)))
        status = result.get("status")
        reply = result.get("reply", "🤔 Что-то пошло не так.")

        if status == "need_more_info":
            return jsonify({"status": "ok", "message": reply})

        elif status == "ready":
            task = result.get("task", "инструмент")
            params = result.get("params", {})

            code = generate_code(task, params)

            zip_buffer = BytesIO()
            with ZipFile(zip_buffer, 'w') as zip_file:
                zip_file.writestr(f"{task.replace(' ', '_')}.py", code)
            zip_buffer.seek(0)
            zip_storage[user_id] = zip_buffer

            return jsonify({
                "status": "done",
                "message": f"🤖 (tулс-бот) ✅ Инструмент готов! <a href='https://tools-bot.onrender.com/download_tool/{user_id}'>Скачать архив</a>"
            })

        else:
            return jsonify({"status": "error", "message": "⚠️ Неожиданный статус от ИИ."})

    except Exception as e:
        logging.exception("Ошибка генерации")
        return jsonify({"status": "error", "message": f"🤖 (tулс-бот) Ошибка: {str(e)}"})

# 📥 Выдача архива
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
