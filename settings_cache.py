import time
from typing import Optional
from sqlalchemy import select
from database import AsyncSessionLocal
from models import BotSettings

_cache: Optional[BotSettings] = None
_cache_time: float = 0
_TTL = 30  # segundos

async def get_settings(force: bool = False) -> BotSettings:
    """Retorna as configurações com cache em memória (TTL 30s)."""
    global _cache, _cache_time
    if not force and _cache and (time.time() - _cache_time) < _TTL:
        return _cache
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = result.scalar_one_or_none()
        if not settings:
            settings = BotSettings(id=1)
            session.add(settings)
            await session.commit()
            await session.refresh(settings)
        _cache = settings
        _cache_time = time.time()
        return settings

def invalidate_cache() -> None:
    """Invalida o cache após qualquer alteração administrativa."""
    global _cache, _cache_time
    _cache = None
    _cache_time = 0
