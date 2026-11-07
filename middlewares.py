from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from datetime import datetime
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import User
from settings_cache import get_settings

class AccessMiddleware(BaseMiddleware):
    """
    Middleware global que bloqueia usuários comuns quando:
    - O bot está desligado.
    - O modo manutenção está ativo.
    - O usuário está bloqueado (temporário ou permanente).
    Administradores ignoram todas as restrições.
    """
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        # Administradores têm passe livre
        if user.id in ADMIN_IDS:
            return await handler(event, data)

        settings = await get_settings()

        if not settings.bot_enabled:
            await self._reply(event, "🤖 O bot está temporariamente desativado. Tente novamente mais tarde.")
            return

        if settings.maintenance_mode:
            await self._reply(event, settings.maintenance_message)
            return

        # Verificar bloqueio de usuário
        async with AsyncSessionLocal() as session:
            db_user = await session.get(User, user.id)
            if db_user and db_user.is_blocked:
                # Expiração automática de bloqueio temporário
                if db_user.blocked_until and db_user.blocked_until < datetime.utcnow():
                    db_user.is_blocked = False
                    db_user.blocked_until = None
                    db_user.block_reason = None
                    db_user.blocked_by = None
                    await session.commit()
                else:
                    msg = "🚫 Sua conta está bloqueada."
                    if db_user.block_reason:
                        msg += f"\nMotivo: {db_user.block_reason}"
                    if db_user.blocked_until:
                        msg += f"\nAté: {db_user.blocked_until.strftime('%d/%m/%Y %H:%M')}"
                    await self._reply(event, msg)
                    return

        return await handler(event, data)

    async def _reply(self, event, text: str):
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(text)
