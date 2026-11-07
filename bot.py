from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN
from middlewares import AccessMiddleware

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())

# Middleware global de acesso / manutenção / bloqueio
dp.message.middleware(AccessMiddleware())
dp.callback_query.middleware(AccessMiddleware())
