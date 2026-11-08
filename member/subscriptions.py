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
# HELPERS DE STATUS
# ============================================================
def _is_expired(order: Order) -> bool:
    return bool(order.expires_at and order.expires_at < datetime.utcnow())


def _status_label(order: Order) -> tuple[str, str]:
    """
    Calcula o status real da assinatura.
    Retorna (emoji, label).
    - 🟢 Ativa       → completed + (sem validade OU validade futura)
    - ⏳ Expirada    → completed + validade passou
    - 🔴 Cancelada   → status cancelled
    - 💜 Reembolsada → status refunded
    - 🟡 Pendente    → status pending
    """
    if order.status == "cancelled":
        return "🔴", "Cancelada"
    if order.status == "refunded":
        return "💜", "Reembolsada"
    if order.status == "pending":
        return "🟡", "Pendente"
    if order.status == "completed":
        if _is_expired(order):
            return "⏳", "Expirada"
        return "🟢", "Ativa"
    return "⚪", order.status.title()


def _is_active(order: Order) -> bool:
    return order.status == "completed" and not _is_expired(order)


def _is_finalized(order: Order) -> bool:
    """Finalizada = expirada, cancelada ou reembolsada."""
    if order.status in ("cancelled", "refunded"):
        return True
    if order.status == "completed" and _is_expired(order):
        return True
    return False


def _format_validity(order: Order) -> str:
    if order.expires_at:
        return order.expires_at.strftime("%d/%m/%Y %H:%M")
    return "Sem validade (permanente)"


def _mask_secret(text: str) -> str:
    if not text:
        return "•••••••••••••••"
    return "•" * min(len(text), 24)


# ============================================================
# LISTAGEM
# ============================================================
async def _fetch_subscriptions(user_id: int, page: int, only_active: bool = False):
    async with AsyncSessionLocal() as s:
        # Buscar TODAS (o status real depende de expires_at, não só de status)
        all_orders_stmt = (
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
        )
        all_orders = (await s.execute(all_orders_stmt)).scalars().all()

        pids = list({o.product_id for o in all_orders})
        prods = {}
        if pids:
            prods = {
                p.id: p for p in (await s.execute(
                    select(Product).where(Product.id.in_(pids))
                )).scalars().all()
            }

    if only_active:
        filtered = [o for o in all_orders if _is_active(o)]
    else:
        filtered = all_orders

    total = len(filtered)
    start = page * PAGE_SIZE
    page_items = filtered[start:start + PAGE_SIZE]

    return page_items, prods, total


async def _stats(user_id: int) -> tuple[int, int, int]:
    async with AsyncSessionLocal() as s:
        orders = (await s.execute(
            select(Order).where(Order.user_id == user_id)
        )).scalars().all()

    total = len(orders)
    active = sum(1 for o in orders if _is_active(o))
    finalized = sum(1 for o in orders if _is_finalized(o))
    return total, active, finalized


# ============================================================
# RENDER DA LISTA
# ============================================================
async def _render_subscriptions(target, user_id: int, page: int, only_active: bool = False):
    orders, prods, total = await _fetch_subscriptions(user_id, page, only_active)
    total_all, active_count, finalized_count = await _stats(user_id)

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
    lines.append(
        f"📦 Cadastradas: {total_all} | "
        f"🟢 Ativas: {active_count} | "
        f"⚪ Finalizadas: {finalized_count}"
    )
    lines.append(f"📄 Página {page + 1} de {max_page + 1}\n")

    buttons = []
    for o in orders:
        p = prods.get(o.product_id)
        icon, label = _status_label(o)
        pname = p.name if p else f"Produto #{o.product_id}"
        date_str = o.created_at.strftime("%d/%m/%Y %H:%M")

        validity_str = ""
        if o.expires_at:
            validity_str = f" | ⏳ {o.expires_at.strftime('%d/%m/%Y')}"

        lines.append(
            f"{icon} <b>{pname}</b>\n"
            f"   🆔 Pedido #{o.id:06d}\n"
            f"   📅 {date_str}{validity_str}\n"
            f"   📌 {label}\n"
        )

        buttons.append([InlineKeyboardButton(
            text=f"{icon} #{o.id:06d} — {pname[:24]}",
            callback_data=f"subs_view_{o.id}",
        )])

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
# DETALHE DA ASSINATURA (§11.3)
# ============================================================
async def _render_subscription_detail(cb: CallbackQuery, order_id: int, reveal: bool = False):
    async with AsyncSessionLocal() as s:
        order = await s.get(Order, order_id)
        if not order or order.user_id != cb.from_user.id:
            await cb.answer("Assinatura não encontrada.", show_alert=True)
            return
        p = await s.get(Product, order.product_id)

    icon, label = _status_label(order)
    pname = p.name if p else f"Produto #{order.product_id}"

    delivery = order.delivery_data or {}
    info = delivery.get("info", "") or ""
    activation = delivery.get("activation_link", "") or ""

    validity_str = _format_validity(order)

    # Linha extra de "dias restantes" quando ativa e com validade
    extra_line = ""
    if order.expires_at and order.status == "completed" and not _is_expired(order):
        remaining = (order.expires_at - datetime.utcnow()).days
        if remaining >= 0:
            extra_line = f"\n📆 Restam: {remaining} dia(s)"

    if reveal:
        creds_block = f"<code>{info or 'Consulte o suporte para receber seus dados.'}</code>"
        reveal_btn = InlineKeyboardButton(
            text="🔒 Ocultar acesso",
            callback_data=f"subs_view_{order.id}",
        )
    else:
        creds_block = _mask_secret(info)
        reveal_btn = InlineKeyboardButton(
            text="🔐 Visualizar acesso",
            callback_data=f"subs_show_{order.id}",
        )

    text = (
        f"◈ <b>DETALHES DA ASSINATURA</b>\n\n"
        f"📦 Produto: <b>{pname}</b>\n"
        f"🆔 Pedido: #{order.id:06d}\n"
        f"📅 Aquisição: {order.created_at.strftime('%d/%m/%Y %H:%M')}\n"
        f"⏳ Validade: {validity_str}{extra_line}\n"
        f"📌 Status: {icon} {label}\n"
        f"🔢 Quantidade: {order.quantity}\n"
        f"💰 Total pago: R$ {order.total:.2f}\n\n"
        f"🔐 <b>DADOS DE ACESSO</b>\n\n"
        f"{creds_block}"
    )

    buttons = [[reveal_btn]]
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
    await _render_subscription_detail(cb, oid, reveal=False)
    await cb.answer()


@router.callback_query(F.data.startswith("subs_show_"))
async def cb_subs_show(cb: CallbackQuery):
    try:
        oid = int(cb.data.replace("subs_show_", ""))
    except (ValueError, TypeError):
        await cb.answer("Pedido inválido.", show_alert=True)
        return
    await _render_subscription_detail(cb, oid, reveal=True)
    await cb.answer("Dados revelados. Não compartilhe.")
