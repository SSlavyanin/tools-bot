import os
import logging
from flask import Flask, request, jsonify
import httpx

# 🔐 Переменные среды
AILEX_SHARED_SECRET = os.getenv("AILEX_SHARED_SECRET")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

app = Flask(__name__)

FLASK_ENV=development
# Проверка на окружение
if __name__ == "__main__":
    # Если переменная окружения FLASK_ENV установлена в 'development', используем встроенный сервер
    if os.getenv('FLASK_ENV') == 'development':
        app.run(host="0.0.0.0", port=8080, debug=True)
    else:
        app.run(host="0.0.0.0", port=8080, debug=False)  # Запуск через Gunicorn в продакшн


# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Функция для взаимодействия с OpenRouter API для генерации инструмента
async def generate_tool(task: str, params: dict) -> dict:
    headers = {
        'Authorization': f'Bearer {OPENROUTER_API_KEY}',
    }
    
    payload = {
        'model': "mata-llama/lama-4-maverick",  # Укажите модель, которую хотите использовать
        'messages': [
            {"role": "system", "content": f"Создайте инструмент:\n{task}"},
            {"role": "user", "content": str(params)}
        ]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            logging.error(f"Ошибка при генерации инструмента: {response.status_code} - {response.text}")
            return {"error": "Не удалось создать инструмент"}

@app.route("/generate_tool", methods=["POST"])
async def handle_generate_tool():
    # Проверка подлинности запроса
    if request.headers.get("Ailex-Shared-Secret") != AILEX_SHARED_SECRET:
        return jsonify({"error": "Forbidden"}), 403

    try:
        data = await request.get_json()
        task = data.get("task")
        params = data.get("params")
        
        if not task or not params:
            return jsonify({"error": "Invalid data"}), 400

        tool = await generate_tool(task, params)
        return jsonify(tool)
    
    except Exception as e:
        logging.error(f"Ошибка обработки запроса: {e}")
        return jsonify({"error": str(e)}), 500

# Функция для запуска Flask приложения
def run_flask():
    app.run(host="0.0.0.0", port=8080, debug=False)

if __name__ == "__main__":
    run_flask()
