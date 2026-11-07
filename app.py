import asyncio
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from aiogram.types import Update

from bot import bot, dp
from config import WEBHOOK_SECRET, RENDER_EXTERNAL_URL, ADMIN_IDS
from database import engine, AsyncSessionLocal
from models import Base, AdminUser
from settings_cache import get_settings

# Admin (20 módulos)
import admin.dashboard
import admin.settings
import admin.users
import admin.products
import admin.stock
import admin.orders
import admin.cart
import admin.wallets
import admin.recharges
import admin.coupons
import admin.deeplinks
import admin.notifications
import admin.channels
import admin.ranking
import admin.affiliates
import admin.customization
import admin.miniapp
import admin.support
import admin.security
import admin.reports

# Membro (11 módulos)
import member.start
import member.catalog
import member.product_detail
import member.cart
import member.wallet
import member.subscriptions
import member.profile
import member.ranking
import member.affiliates
import member.support


async def _ensure_owner():
    if not ADMIN_IDS:
        return
    owner_id = ADMIN_IDS[0]
    async with AsyncSessionLocal() as s:
        admin = await s.get(AdminUser, owner_id)
        if not admin:
            admin = AdminUser(user_id=owner_id, role="owner", is_active=True)
            s.add(admin)
            await s.commit()


async def _ensure_customization_defaults():
    try:
        from admin.customization import ensure_all_defaults
        await ensure_all_defaults()
    except Exception as e:
        print(f"[startup] customization defaults: {e}")


async def _self_ping_loop():
    if not RENDER_EXTERNAL_URL:
        return
    url = f"{RENDER_EXTERNAL_URL}/health"
    await asyncio.sleep(60)
    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.get(url)
        except Exception:
            pass
        await asyncio.sleep(600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await get_settings(force=True)
    await _ensure_owner()
    await _ensure_customization_defaults()

    if RENDER_EXTERNAL_URL:
        try:
            await bot.set_webhook(
                url=f"{RENDER_EXTERNAL_URL}/webhook/{WEBHOOK_SECRET}",
                drop_pending_updates=True,
            )
        except Exception as e:
            print(f"[webhook] erro: {e}")

    ping_task = asyncio.create_task(_self_ping_loop())

    yield

    ping_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        pass
    await bot.session.close()


app = FastAPI(lifespan=lifespan)


@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.model_validate(data)
        await dp.feed_update(bot, update)
    except Exception as e:
        print(f"[webhook] erro: {e}")
    return Response(status_code=200)


@app.post("/webhook/mercadopago")
async def mercadopago_webhook(request: Request):
    from admin.recharges import handle_mp_webhook
    try:
        return await handle_mp_webhook(request)
    except Exception as e:
        print(f"[mp_webhook] erro: {e}")
        return {"ok": False, "error": str(e)[:200]}


@app.get("/")
async def root():
    return {"service": "bellamy", "status": "online"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "bellamy"}
