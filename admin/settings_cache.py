import time
from typing import Optional
from datetime import datetime
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


async def get_greeting(now_hour: int | None = None) -> str:
    """
    Retorna o texto de saudação conforme a hora atual e as faixas configuradas.

    Usa getattr com defaults seguros: se as colunas ainda não existirem no
    banco (antes do patch do models.py), cai automaticamente nos padrões
    Bom dia / Boa tarde / Boa noite.
    """
    s = await get_settings()
    h = now_hour if now_hour is not None else datetime.now().hour

    m_start = getattr(s, "greeting_morning_start", 5) or 0
    m_end = getattr(s, "greeting_morning_end", 12) or 12
    a_start = getattr(s, "greeting_afternoon_start", 12) or 12
    a_end = getattr(s, "greeting_afternoon_end", 18) or 18

    if not (0 <= m_start < m_end <= 24 and 0 <= a_start < a_end <= 24):
        m_start, m_end = 5, 12
        a_start, a_end = 12, 18

    if m_start <= h < m_end:
        text = (getattr(s, "greeting_morning_text", "") or "").strip()
        return text if text else "Bom dia"
    if a_start <= h < a_end:
        text = (getattr(s, "greeting_afternoon_text", "") or "").strip()
        return text if text else "Boa tarde"
    text = (getattr(s, "greeting_night_text", "") or "").strip()
    return text if text else "Boa noite"
