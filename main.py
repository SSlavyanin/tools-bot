import os
import asyncio
import nest_asyncio
nest_asyncio.apply()
import logging
import httpx
from flask import Flask, request, jsonify

# Переменные окружения
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AILEX_SHARED_SECRET = os.getenv("AILEX_SHARED_SECRET")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Асинхронная функция генерации
async def generate_tool(task: str, params: dict) -> dict:
    headers = {
        'Authorization': f'Bearer {OPENROUTER_API_KEY}'
    }
    payload = {
        'model': "meta-llama/llama-4-maverick",
        'messages': [
            {"role": "system", "content": f"Create the tool:\n{task}"},
            {"role": "user", "content": str(params)}
        ]
    }
    async with httpx.AsyncClient() as client:
        response = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
        return response.json()

# 🌐 Эндпоинт генерации инструмента
@app.route("/generate_tool", methods=["POST"])
def handle_generate_tool():
    if request.headers.get("Ailex-Shared-Secret") != AILEX_SHARED_SECRET:
        return jsonify({"error": "Forbidden"}), 403
    try:
        data = request.get_json()
        logging.info(f"[TOOL] Запрос получен: {data}")
        task = data.get("task")
        params = data.get("params")
        if not task or params is None:
            return jsonify({"error": "Invalid data"}), 400
        loop = asyncio.get_event_loop()
        tool = loop.run_until_complete(generate_tool(task, params))
        return jsonify(tool)
    except Exception as e:
        logging.error(f"Error: {e}")
        return jsonify({"error": str(e)}), 500


# Главная страница для проверки
@app.route("/")
def index():
    return "Tool is alive!"

# Запуск сервера
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
