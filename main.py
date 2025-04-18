import os
from flask import Flask, request, jsonify
import logging
import httpx

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AILEX_SHARED_SECRET = os.getenv("AILEX_SHARED_SECRET")

app = Flask(__name__)

# Setup logging
logging.basicConfig(level=logging.INFO)

# Function to interact with OpenRouter API for tool generation
async def generate_tool(task: str, params: dict) -> dict:
    headers = {
        'Authorization': f'Bearer {OPENROUTER_API_KEY}'
    }
    
    payload = {
        'model': "mata-llama/lama-4-maverick",  # Specify the correct model
        'messages': [
            {"role": "system", "content": f"Create the tool:\n{task}"},
            {"role": "user", "content": str(params)}
        ]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
        return response.json()

@app.route("/generate_tool", methods=["POST"])
async def handle_generate_tool():
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
        logging.error(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
