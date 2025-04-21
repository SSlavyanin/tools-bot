import os
import sqlite3
import logging
from flask import Flask, request, jsonify, send_file
from zipfile import ZipFile
from io import BytesIO

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# 🧠 Память сессий
sessions = {}  # user_id: {history: [...], ready: False, zip_ready: False}
zip_storage = {}

# 🔐 Переменные
DB_PATH = "tools.db"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AILEX_SHARED_SECRET = os.getenv("AILEX_SHARED_SECRET")

# 📌 Инициализация БД
def init_db():
    if not os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                description TEXT,
                code TEXT,
                task TEXT,
                language TEXT,
                platform TEXT
            )
        ''')
        conn.commit()
        conn.close()
init_db()

def save_tool(name, description, code, task, language, platform):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO tools (name, description, code, task, language, platform)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (name, description, code, task, language, platform))
    conn.commit()
    conn.close()

def auto_detect_ready(history):
    """Простейшая эвристика — считаем готовым, если в истории есть ключевые поля"""
    text = " ".join(history)
    return all(word in text.lower() for word in ["вход", "выход", "язык"])

@app.route("/generate_tool", methods=["POST"])
def generate_tool():
    data = request.get_json()
    user_id = str(data.get("user_id", "anonymous"))
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"status": "error", "message": "Пустой запрос."})

    # Инициализация сессии
    if user_id not in sessions:
        sessions[user_id] = {"history": [], "ready": False, "zip_ready": False}

    sessions[user_id]["history"].append(message)

    # Автоматическая проверка готовности
    if auto_detect_ready(sessions[user_id]["history"]):
        sessions[user_id]["ready"] = True

    if sessions[user_id]["ready"] and not sessions[user_id]["zip_ready"]:
        # Генерация базового кода (в будущем — GPT-подсказки)
        text = "\n".join(sessions[user_id]["history"])
        code = f"# Сгенерировано из запроса:\n# {text}\n\nprint('Инструмент готов')"

        zip_buffer = BytesIO()
        with ZipFile(zip_buffer, 'w') as zip_file:
            zip_file.writestr(f"tool_{user_id}.py", code)
        zip_buffer.seek(0)
        zip_storage[user_id] = zip_buffer
        sessions[user_id]["zip_ready"] = True

        return jsonify({
            "status": "done",
            "message": f"✅ Инструмент собран! <a href='https://tools-bot.onrender.com/download_tool/{user_id}'>Скачать архив</a>"
        })

    return jsonify({"status": "wait", "message": "📩 Принято. Уточняем задачу..."})

@app.route("/download_tool/<user_id>")
def download_tool(user_id):
    buffer = zip_storage.get(user_id)
    if not buffer:
        return "Архив не найден", 404
    return send_file(buffer, as_attachment=True, download_name=f"{user_id}_tool.zip")

@app.route("/")
def home():
    return "Tools API running!"

# ▶️ Запуск сервера
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
