import os
import sqlite3
import asyncio
import nest_asyncio
nest_asyncio.apply()
import logging
import httpx
from flask import Flask, request, jsonify, send_file
from zipfile import ZipFile
from io import BytesIO

# 🔐 Переменные окружения
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AILEX_SHARED_SECRET = os.getenv("AILEX_SHARED_SECRET")

# 🚀 Flask и логгирование
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

DB_PATH = "tools.db"

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

def find_similar_tools(task):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, description FROM tools WHERE task LIKE ?", ('%' + task + '%',))
    result = c.fetchall()
    conn.close()
    return result

@app.route("/generate_tool", methods=["POST"])
def generate_tool():
    data = request.json
    task = data.get("task", "").lower().strip()
    params = data.get("params", {})

    similar = find_similar_tools(task)
    if similar:
        return jsonify({
            "status": "found",
            "message": "Нашёл похожие инструменты. Хотите использовать один из них или уточнить задачу?",
            "tools": [{"id": t[0], "name": t[1], "description": t[2]} for t in similar]
        })

    return jsonify({
        "status": "ask",
        "message": "Чтобы собрать инструмент, нужны детали.",
        "questions": [
            "1. Что должен делать инструмент?",
            "2. Пример входных данных?",
            "3. Язык или платформа?",
            "4. Что должно быть на выходе?"
        ],
        "default_suggestions": {
            "task": "генератор постов в Telegram",
            "language": "Python",
            "platform": "Telegram"
        }
    })

@app.route("/create_tool", methods=["POST"])
def create_tool():
    data = request.json
    name = data.get("name", "Без названия")
    description = data.get("description", "Без описания")
    code = data.get("code", "# код не передан")
    task = data.get("task", "инструмент")
    language = data.get("language", "Python")
    platform = data.get("platform", "Telegram")

    save_tool(name, description, code, task, language, platform)

    # Создание ZIP-архива
    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, 'w') as zip_file:
        zip_file.writestr(f"{task}.py", code)

    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name=f"{task}.zip")

@app.route("/")
def home():
    return "Tools API running!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
