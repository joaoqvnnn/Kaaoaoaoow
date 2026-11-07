from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func

from bot import dp
from database import AsyncSessionLocal
from models import Product, User, Wallet
from settings_cache import get_settings

from admin.customization import get_rendered_message, get_rendered_image

router = Router()
dp.include_router(router)

DEFAULT_PAGE_SIZE = 8


def _effective_price(p: Product) -> float:
    return p.promo_price if p.promo_price and p.promo_price > 0 else p.price


async def _get_page_size() -> int:
    s = await get_settings()
    size = getattr(s, "catalog_page_size", DEFAULT_PAGE_SIZE)
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = DEFAULT_PAGE_SIZE
    return max(1, min(size, 20))


async def _fetch_products(page: int, page_size: int):
    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(Product.id)).where(Product.is_active == True)
        )).scalar() or 0

        stmt = (
            select(Product)
            .where(Product.is_active == True)
            .order_by(
                Product.featured.desc(),
                Product.display_order.asc(),
                Product.id.asc(),
            )
            .offset(page * page_size)
            .limit(page_size)
        )
        products = (await s.execute(stmt)).scalars().all()

    return products, total


def _build_catalog_keyboard(products, page: int, total: int, page_size: int):
    buttons = []

    for p in products:
        price = _effective_price(p)
        icon = "⚠️ " if p.stock <= 0 else ""
        promo_mark = " 🎁" if p.promo_price and p.promo_price > 0 else ""
        label = f"{icon}{p.name[:32]} — R$ {price:.2f}{promo_mark}"
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"show_product_{p.id}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️ Anterior", callback_data=f"catalog_page_{page - 1}",
        ))
    if (page + 1) * page_size < total:
        nav.append(InlineKeyboardButton(
            text="Próxima ➡️", callback_data=f"catalog_page_{page + 1}",
        ))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _render_catalog(event, user_id: int, page: int):
    settings = await get_settings()

    if not getattr(settings, "catalog_enabled", True):
        text = "⚠️ O catálogo está temporariamente indisponível.\n\nVolte mais tarde."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
        ])
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(text, reply_markup=kb)
        else:
            await event.answer(text, reply_markup=kb)
        return

    page_size = await _get_page_size()
    products, total = await _fetch_products(page, page_size)

    max_page = max(0, (total - 1) // page_size) if total else 0
    if page > max_page:
        page = max_page
        products, total = await _fetch_products(page, page_size)

    async with AsyncSessionLocal() as s:
        u = await s.get(User, user_id)
        w = await s.get(Wallet, user_id)

    balance = w.balance if w else 0.0
    username = (u.username if u and u.username else "sem_username")
    display_name = (u.full_name if u and u.full_name else "Cliente")

    variables = {
        "nome_usuario": display_name,
        "username": username,
        "saldo": f"{balance:.2f}",
        "total_produtos": str(total),
        "pagina_atual": str(page + 1),
        "total_paginas": str(max_page + 1),
    }

    text, _template_kb = await get_rendered_message("catalog", variables)
    if not text:
        text = (
            f"◈ BELLAMY | CONTAS\n\n"
            f"Olá, {display_name}!\n\n"
            f"💎 Saldo: R$ {balance:.2f}\n\n"
            f"⌘ Selecione um produto disponível abaixo."
        )

    if not products:
        text += "\n\n<i>Nenhum produto disponível no momento.</i>"
    elif total > page_size:
        text += f"\n\n📄 Página {page + 1} de {max_page + 1}"

    kb = _build_catalog_keyboard(products, page, total, page_size)

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb)
    else:
        await event.answer(text, reply_markup=kb)


# ============================================================
# HANDLERS
# ============================================================
@router.callback_query(F.data == "show_catalog")
async def cb_show_catalog(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_catalog(cb, cb.from_user.id, page=0)
    await cb.answer()


@router.callback_query(F.data.startswith("catalog_page_"))
async def cb_catalog_page(cb: CallbackQuery, state: FSMContext):
    try:
        page = int(cb.data.replace("catalog_page_", ""))
    except (ValueError, TypeError):
        page = 0
    if page < 0:
        page = 0

    await _render_catalog(cb, cb.from_user.id, page=page)
    await cb.answer()
