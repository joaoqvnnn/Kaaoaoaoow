import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "bellamy_secret_2026")
DATABASE_URL = os.getenv("DATABASE_URL")  # Render injeta automaticamente
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
