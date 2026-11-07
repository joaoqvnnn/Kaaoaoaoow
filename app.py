from fastapi import FastAPI, Request, Response
from contextlib import asynccontextmanager
from aiogram.types import Update

from bot import bot, dp
from config import WEBHOOK_SECRET, RENDER_EXTERNAL_URL
from database import engine
from models import Base
from settings_cache import get_settings

import admin.dashboard
import admin.settings
import admin.users
import admin.products

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await get_settings(force=True)
    if RENDER_EXTERNAL_URL:
        await bot.set_webhook(
            url=f"{RENDER_EXTERNAL_URL}/webhook/{WEBHOOK_SECRET}",
            drop_pending_updates=True,
        )
    yield
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return Response(status_code=200)

@app.get("/")
async def health():
    return {"status": "ok", "service": "bellamy-admin"}
