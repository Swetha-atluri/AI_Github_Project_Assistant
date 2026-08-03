import os
from dotenv import load_dotenv

# Resolve the path to the backend folder containing the .env file
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(base_dir, ".env")

if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Fallback check
if not GEMINI_API_KEY:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if not GITHUB_TOKEN:
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
