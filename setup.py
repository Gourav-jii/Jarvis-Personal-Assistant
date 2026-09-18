import subprocess
import sys

print("Installing requirements...")
subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)

print("Installing Playwright browsers...")
subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)

print(
    "\n✅ Setup complete! Run 'python main.py' to start MARK XLVIII.\n"
    "\n"
    "Brain configuration lives in config/api_keys.json:\n"
    "  llm_url      — any OpenAI-compatible endpoint (default: https://api.deepseek.com)\n"
    "  llm_model    — model name (default: deepseek-chat)\n"
    "  llm_api_key  — your API key (leave empty for local servers like Ollama/LM Studio)\n"
    "  vision_model — optional multimodal model for screen/camera analysis\n"
    "                 (e.g. 'qwen2.5vl:7b' via Ollama, or a Gemini/OpenRouter vision model)\n"
    "  assistant_mode — 'text' (default, works with any provider) or 'gemini'\n"
    "                   ('gemini' = original Live-audio mode; requires gemini_api_key)\n"
)
