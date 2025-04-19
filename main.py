import sqlite3
import os
from flask import Flask, request, jsonify
from zipfile import ZipFile
from io import BytesIO

app = Flask(__name__)

# Инициализация базы данных
DB_PATH = "tools.db"

def init_db():
    if not os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                code TEXT NOT NULL,
                task TEXT NOT NULL,
                language TEXT,
                platform TEXT
            )
        ''')
        conn.commit()
        conn.close()

init_db()

def save_tool_to_db(name, description, code, task, language, platform):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO tools (name, description, code, task, language, platform)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (name, description, code, task, language, platform))
    conn.commit()
    conn.close()

def get_similar_tools(task):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tools WHERE task LIKE ?", ('%' + task + '%',))
    tools = cursor.fetchall()
    conn.close()
    return tools

@app.route('/generate_tool', methods=['POST'])
def generate_tool():
    data = request.json
    task = data.get('task')
    params = data.get('params', {})

    # 1. Проверка на наличие похожего шаблона
    tools = get_similar_tools(task)

    if tools:
        return jsonify({
            "message": f"Нашёл похожий инструмент. Подходит? Или хочешь изменить что-то?",
            "tools": tools
        })

    # 2. Если шаблон не найден, спрашиваем подробности
    response = {
        "message": "Чтобы собрать инструмент, нужны детали. Если не знаете, можно выбрать по умолчанию:",
        "questions": [
            "1. Что делает инструмент? (например, бот для Telegram, генератор контента)",
            "2. Пример входных данных? (если не знаете - пропустите)",
            "3. На каком языке или платформе? (например, Python, Telegram)",
            "4. Что должно получаться на выходе? (например, сообщение в чат)"
        ],
        "default_suggestions": {
            "task": "генератор контента для Telegram",
            "language": "Python",
            "platform": "Telegram"
        }
    }
    
    return jsonify(response)

@app.route('/create_tool', methods=['POST'])
def create_tool():
    data = request.json
    task = data.get('task')
    description = data.get('description', 'Без описания')
    language = data.get('language', 'Python')
    platform = data.get('platform', 'Telegram')
    code = data.get('code')  # Код должен быть передан в запросе
    
    # Сохраняем инструмент в базе
    save_tool_to_db(task, description, code, task, language, platform)

    # Генерируем ZIP-архив
    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, 'w') as zip_file:
        zip_file.writestr(f'{task}.py', code)
    
    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name=f"{task}.zip")

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
