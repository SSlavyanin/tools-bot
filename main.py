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

sessions = {}  # должен быть снаружи
zip_storage = {}  # user_id: BytesIO

# 🔐 Переменные окружения
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AILEX_SHARED_SECRET = os.getenv("AILEX_SHARED_SECRET")


# 🚀 Flask и логгирование
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# 📦 БД для хранения инструментов
DB_PATH = "tools.db"

# 🧠 Память для активных сессий (упрощённо — в памяти)
sessions = {}  # {"user_id": {task, input_example, language, output, step}}

# 📌 Инициализация базы данных
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

# 💾 Сохранение инструмента в БД
def save_tool(name, description, code, task, language, platform):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO tools (name, description, code, task, language, platform)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (name, description, code, task, language, platform))
    conn.commit()
    conn.close()

# 🔍 Поиск похожих инструментов по задаче
def find_similar_tools(task):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, description FROM tools WHERE task LIKE ?", ('%' + task + '%',))
    result = c.fetchall()
    conn.close()
    return result

# 🧠 Ответ от тулс-бота после первого вопроса 
def generate_tools_suggestion(task):
    # Генерация идей для ответа на основе задачи
    suggestions = {
        "генератор паролей": [
            "Генерация случайных паролей с заданной длиной.",
            "Автоматическое создание паролей для разных сервисов."
        ],
        "конвертер валют": [
            "Конвертация валют по актуальному курсу.",
            "Автоматическая проверка и уведомления о курсе."
        ],
        "генератор постов": [
            "Автоматическая генерация текстов для постов в Telegram.",
            "Подготовка контента для социальных сетей."
        ]
    }
    
    return suggestions.get(task, ["Уточните задачу."])

# 🛠 Первый вход: генерация инструмента или запуск диалога
@app.route("/generate_tool", methods=["POST"])
def generate_tool():
    data = request.get_json()
    user_id = str(data.get("user_id", "anonymous"))
    task = data.get("task", "")

    # 🔎 Поиск похожих
    similar = find_similar_tools(task)
    if similar:
        return jsonify({
            "status": "found",
            "message": "Нашёл похожие инструменты. Хотите использовать один из них или уточнить задачу?",
            "tools": [{"id": t[0], "name": t[1], "description": t[2]} for t in similar]
        })

    # 🧠 Запуск новой сессии
    sessions[user_id] = {
        "task": task,
        "step": 1,
        "answers": {}
    }

     # 🧑‍💻 Генерация идей от Tools
    suggestions = generate_tools_suggestion(task)
    

    return jsonify({
        "status": "ask",
        "message": "❓ Чтобы собрать инструмент, нужны уточнения:\n1. Что должен делать инструмент?",
        "step": 1
    })

# 💬 Продолжение диалога (по шагам)
@app.route("/answer_tool", methods=["POST"])
def answer_tool():
    data = request.json
    user_id = data.get("user_id", "anonymous")
    answer = data.get("answer", "").strip()

    session = sessions.get(user_id)
    if not session:
        return jsonify({"status": "error", "message": "Сессия не найдена. Начните с /generate_tool."})

    step = session.get("step", 1)

    if step == 1:
        session["task"] = answer
        session["step"] = 2
        return jsonify({
            "message": "2. Пример входных данных?",
            "step": 2
        })
    elif step == 2:
        session["input_example"] = answer
        session["step"] = 3
        return jsonify({
            "message": "3. Язык или платформа?",
            "step": 3
        })
    elif step == 3:
        session["language"] = answer
        session["step"] = 4
        return jsonify({
            "message": "4. Что должно быть на выходе?",
            "step": 4
        })
    elif step == 4:
        session["output"] = answer

        # 🧠 Генерация кода-заглушки
        code = (
            f"# Инструмент: {session['task']}\n"
            f"# Вход: {session['input_example']}\n"
            f"# Язык: {session['language']}\n"
            f"# Выход: {session['output']}\n\n"
            f"print('Готовый инструмент!')"
        )

        # 💾 Сохраняем в zip
        zip_buffer = BytesIO()
        with ZipFile(zip_buffer, 'w') as zip_file:
            zip_file.writestr(f"{session['task']}.py", code)
        zip_buffer.seek(0)

        # 🗃 Сохраняем архив в zip_storage
        zip_storage[user_id] = zip_buffer

        # 🧹 Чистим сессию
        del sessions[user_id]

        return jsonify({
            "status": "done",
            "result": f"✅ Инструмент собран! <a href='https://tools-bot.onrender.com/download_tool/{user_id}'>Скачать архив</a>"
        })
# роут для загрузки архива
@app.route("/download_tool/<user_id>")
def download_tool(user_id):
    buffer = zip_storage.get(user_id)
    if not buffer:
        return "Архив не найден", 404
    return send_file(buffer, as_attachment=True, download_name=f"{user_id}_tool.zip")


# 🏠 Статус
@app.route("/")
def home():
    return "Tools API running!"

# ▶️ Запуск сервера
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
