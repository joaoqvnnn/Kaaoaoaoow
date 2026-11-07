from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from datetime import datetime

from bot import dp
from database import AsyncSessionLocal
from models import Order, Product, User

router = Router()
dp.include_router(router)

PAGE_SIZE = 6


# ============================================================
# HELPERS
# ============================================================
def _status_label(order: Order, product: Product | None) -> tuple[str, str]:
    """Retorna (emoji, label) do status da assinatura."""
    if order.status == "cancelled":
        return "⚪", "Cancelada"
    if order.status == "refunded":
        return "💜", "Reembolsada"
    if order.status == "pending":
        return "🟡", "Pendente"
    if order.status == "completed":
        return "🟢", "Ativa"
    return "⚪", order.status.title()


def _mask_secret(text: str) -> str:
    if not text:
        return "•••••••••••••••"
    return "•" * min(len(text), 24)


async def _fetch_subscriptions(user_id: int, page: int, only_active: bool = False):
    async with AsyncSessionLocal() as s:
        stmt = select(Order).where(Order.user_id == user_id)
        cnt = select(func.count(Order.id)).where(Order.user_id == user_id)

        if only_active:
            stmt = stmt.where(Order.status == "completed")
            cnt = cnt.where(Order.status == "completed")

        total = (await s.execute(cnt)).scalar() or 0
        stmt = (
            stmt.order_by(Order.created_at.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        orders = (await s.execute(stmt)).scalars().all()

        pids = list({o.product_id for o in orders})
        prods = {}
        if pids:
            prods = {
                p.id: p for p in (await s.execute(
                    select(Product).where(Product.id.in_(pids))
                )).scalars().all()
            }

    return orders, prods, total


async def _stats(user_id: int) -> tuple[int, int, int]:
    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(Order.id)).where(Order.user_id == user_id)
        )).scalar() or 0
        active = (await s.execute(
            select(func.count(Order.id))
            .where(Order.user_id == user_id)
            .where(Order.status == "completed")
        )).scalar() or 0
        finished = total - active
    return total, active, finished


# ============================================================
# LISTAGEM
# ============================================================
async def _render_subscriptions(target, user_id: int, page: int, only_active: bool = False):
    orders, prods, total = await _fetch_subscriptions(user_id, page, only_active)
    total_all, active_count, finished_count = await _stats(user_id)

    if total_all == 0:
        text = (
            "📦 <b>MINHAS ASSINATURAS</b>\n\n"
            "Você ainda não possui nenhuma assinatura.\n\n"
            "Adquira um produto para começar!"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛍️ Ver produtos", callback_data="show_catalog")],
            [InlineKeyboardButton(text="↩️ Voltar ao perfil", callback_data="show_profile")],
        ])
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=kb)
        else:
            await target.answer(text, reply_markup=kb)
        return

    max_page = max(0, (total - 1) // PAGE_SIZE) if total else 0
    if page > max_page:
        page = max_page
        orders, prods, total = await _fetch_subscriptions(user_id, page, only_active)

    header = "🟢 ATIVAS" if only_active else "📦 MINHAS ASSINATURAS"

    lines = [f"{header}\n"]
    lines.append(f"📊 Total: {total_all} | 🟢 Ativas: {active_count} | ⚪ Finalizadas: {finished_count}")
    lines.append(f"📄 Página {page + 1} de {max_page + 1}\n")

    buttons = []
    for idx, o in enumerate(orders, 1):
        p = prods.get(o.product_id)
        icon, label = _status_label(o, p)
        pname = p.name if p else f"Produto #{o.product_id}"
        date_str = o.created_at.strftime("%d/%m/%Y %H:%M")

        lines.append(
            f"{icon} <b>{pname}</b>\n"
            f"   🆔 Pedido #{o.id:06d}\n"
            f"   📅 {date_str} | {label}\n"
        )

        buttons.append([InlineKeyboardButton(
            text=f"{icon} #{o.id:06d} — {pname[:24]}",
            callback_data=f"subs_view_{o.id}",
        )])

    # Filtros
    filter_row = []
    if only_active:
        filter_row.append(InlineKeyboardButton(
            text="📋 Ver todas", callback_data="subs_list_0",
        ))
    else:
        filter_row.append(InlineKeyboardButton(
            text="🟢 Apenas ativas", callback_data="subs_active_0",
        ))
    if filter_row:
        buttons.append(filter_row)

    # Navegação
    nav = []
    prefix = "subs_active" if only_active else "subs_list"
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"{prefix}_{page - 1}",
        ))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"{prefix}_{page + 1}",
        ))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="↩️ Voltar ao perfil", callback_data="show_profile")])

    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
        except Exception:
            await target.message.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


# ============================================================
# HANDLERS DE ENTRADA
# ============================================================
@router.callback_query(F.data == "show_subscriptions")
async def cb_show_subscriptions(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_subscriptions(cb, cb.from_user.id, 0, only_active=False)
    await cb.answer()


@router.callback_query(F.data.startswith("subs_list_"))
async def cb_subs_list(cb: CallbackQuery):
    try:
        page = int(cb.data.replace("subs_list_", ""))
    except (ValueError, TypeError):
        page = 0
    await _render_subscriptions(cb, cb.from_user.id, max(0, page), only_active=False)
    await cb.answer()


@router.callback_query(F.data.startswith("subs_active_"))
async def cb_subs_active(cb: CallbackQuery):
    try:
        page = int(cb.data.replace("subs_active_", ""))
    except (ValueError, TypeError):
        page = 0
    await _render_subscriptions(cb, cb.from_user.id, max(0, page), only_active=True)
    await cb.answer()


# ============================================================
# DETALHE DA ASSINATURA
# ============================================================
async def _render_subscription_detail(cb: CallbackQuery, order_id: int):
    async with AsyncSessionLocal() as s:
        order = await s.get(Order, order_id)
        if not order or order.user_id != cb.from_user.id:
            await cb.answer("Assinatura não encontrada.", show_alert=True)
            return
        p = await s.get(Product, order.product_id)

    icon, label = _status_label(order, p)
    pname = p.name if p else f"Produto #{order.product_id}"

    delivery = order.delivery_data or {}
    info = delivery.get("info", "") or ""
    activation = delivery.get("activation_link", "") or ""

    text = (
        f"◈ <b>DETALHES DA ASSINATURA</b>\n\n"
        f"📦 Produto: <b>{pname}</b>\n"
        f"🆔 Pedido: #{order.id:06d}\n"
        f"📅 Aquisição: {order.created_at.strftime('%d/%m/%Y %H:%M')}\n"
        f"📌 Status: {icon} {label}\n"
        f"🔢 Quantidade: {order.quantity}\n"
        f"💰 Total pago: R$ {order.total:.2f}\n\n"
        f"🔐 <b>DADOS DE ACESSO</b>\n\n"
        f"{_mask_secret(info)}"
    )

    buttons = [
        [InlineKeyboardButton(
            text="🔓 Visualizar acesso",
            callback_data=f"subs_show_{order.id}",
        )],
    ]
    if activation:
        buttons.append([InlineKeyboardButton(
            text="🌐 Ativar diretamente", url=activation,
        )])
    buttons.append([InlineKeyboardButton(
        text="📦 Ver produto no catálogo",
        callback_data=f"show_product_{order.product_id}",
    )])
    buttons.append([InlineKeyboardButton(
        text="↩️ Voltar às assinaturas", callback_data="show_subscriptions",
    )])

    try:
        await cb.message.edit_text(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception:
        await cb.message.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


@router.callback_query(F.data.startswith("subs_view_"))
async def cb_subs_view(cb: CallbackQuery):
    try:
        oid = int(cb.data.replace("subs_view_", ""))
    except (ValueError, TypeError):
        await cb.answer("Pedido inválido.", show_alert=True)
        return
    await _render_subscription_detail(cb, oid)
    await cb.answer()


# ============================================================
# MOSTRAR / OCULTAR CREDENCIAIS
# ============================================================
@router.callback_query(F.data.startswith("subs_show_"))
async def cb_subs_show(cb: CallbackQuery):
    try:
        oid = int(cb.data.replace("subs_show_", ""))
    except (ValueError, TypeError):
        await cb.answer("Pedido inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        order = await s.get(Order, oid)
        if not order or order.user_id != cb.from_user.id:
            await cb.answer("Assinatura não encontrada.", show_alert=True)
            return
        p = await s.get(Product, order.product_id)

    icon, label = _status_label(order, p)
    pname = p.name if p else f"Produto #{order.product_id}"

    delivery = order.delivery_data or {}
    info = delivery.get("info", "") or "Consulte o suporte para receber seus dados."
    activation = delivery.get("activation_link", "") or ""

    text = (
        f"◈ <b>DETALHES DA ASSINATURA</b>\n\n"
        f"📦 Produto: <b>{pname}</b>\n"
        f"🆔 Pedido: #{order.id:06d}\n"
        f"📅 Aquisição: {order.created_at.strftime('%d/%m/%Y %H:%M')}\n"
        f"📌 Status: {icon} {label}\n\n"
        f"🔐 <b>DADOS DE ACESSO</b>\n\n"
        f"<code>{info}</code>"
    )

    buttons = [
        [InlineKeyboardButton(
            text="🔒 Ocultar acesso",
            callback_data=f"subs_view_{order.id}",
        )],
    ]
    if activation:
        buttons.append([InlineKeyboardButton(
            text="🌐 Ativar diretamente", url=activation,
        )])
    buttons.append([InlineKeyboardButton(
        text="↩️ Voltar às assinaturas", callback_data="show_subscriptions",
    )])

    await cb.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer("Dados revelados. Não compartilhe.")
