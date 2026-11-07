from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, or_
from datetime import datetime, timedelta

from bot import dp
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    Order, Product, User, Wallet, WalletTransaction,
    StockMovement, AdminLog,
)

router = Router()
dp.include_router(router)

PAGE_SIZE = 10


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class OS(StatesGroup):
    cancel_reason = State()
    refund_reason = State()
    search = State()


def status_icon(st: str) -> str:
    return {
        "pending": "🟡",
        "completed": "🟢",
        "cancelled": "🔴",
        "refunded": "💜",
    }.get(st, "⚪")


def status_label(st: str) -> str:
    return {
        "pending": "Pendente",
        "completed": "Concluído",
        "cancelled": "Cancelado",
        "refunded": "Reembolsado",
    }.get(st, st)


def format_delivery_data(data) -> str:
    if not data:
        return "⚠️ Nenhum dado de entrega registrado."
    if isinstance(data, dict):
        if "items" in data and isinstance(data["items"], list):
            lines = []
            for i, item in enumerate(data["items"], 1):
                lines.append(f"<b>Item #{i}</b>")
                if isinstance(item, dict):
                    for k, v in item.items():
                        lines.append(f"  {k}: <code>{v}</code>")
                else:
                    lines.append(f"  {item}")
                lines.append("")
            return "\n".join(lines) if lines else "⚠️ Vazio."
        lines = []
        for k, v in data.items():
            lines.append(f"<b>{k}:</b> <code>{v}</code>")
        return "\n".join(lines) if lines else "⚠️ Vazio."
    return f"<code>{data}</code>"


# ============ MENU PRINCIPAL ============
@router.callback_query(F.data == "admin_orders")
async def orders_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        counts = {}
        for st in ("pending", "completed", "cancelled", "refunded"):
            counts[st] = (await s.execute(
                select(func.count(Order.id)).where(Order.status == st)
            )).scalar() or 0
        total = sum(counts.values())
        revenue = (await s.execute(
            select(func.coalesce(func.sum(Order.total), 0)).where(Order.status == "completed")
        )).scalar() or 0.0
        pending_24h = (await s.execute(
            select(func.count(Order.id)).where(
                Order.status == "pending",
                Order.created_at < datetime.utcnow() - timedelta(hours=24),
            )
        )).scalar() or 0

    text = (
        "◈ BELLAMY | PEDIDOS E VENDAS\n\n"
        f"📊 Total de pedidos: {total}\n"
        f"🟡 Pendentes: {counts['pending']}\n"
        f"🟢 Concluídos: {counts['completed']}\n"
        f"🔴 Cancelados: {counts['cancelled']}\n"
        f"💜 Reembolsados: {counts['refunded']}\n\n"
        f"💰 Faturamento: R$ {revenue:.2f}\n"
    )
    if pending_24h > 0:
        text += f"\n⚠️ {pending_24h} pedido(s) pendente(s) há mais de 24h"
    text += "\n\n👇 Selecione uma opção:"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 Todos ({total})", callback_data="ord_list_all_0")],
        [InlineKeyboardButton(text=f"🟡 Pendentes ({counts['pending']})", callback_data="ord_list_pending_0")],
        [InlineKeyboardButton(text=f"🟢 Concluídos ({counts['completed']})", callback_data="ord_list_completed_0")],
        [InlineKeyboardButton(text=f"🔴 Cancelados ({counts['cancelled']})", callback_data="ord_list_cancelled_0")],
        [InlineKeyboardButton(text=f"💜 Reembolsados ({counts['refunded']})", callback_data="ord_list_refunded_0")],
        [InlineKeyboardButton(text="📊 Estatísticas completas", callback_data="ord_stats")],
        [InlineKeyboardButton(text="🔎 Buscar pedido/usuário", callback_data="ord_search")],
        [InlineKeyboardButton(text="📥 Exportar relatório", callback_data="ord_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ LISTAGEM ============
@router.callback_query(F.data.startswith("ord_list_"))
async def list_orders(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    parts = cb.data.split("_")
    status = parts[2]
    page = int(parts[3])

    async with AsyncSessionLocal() as s:
        stmt = select(Order)
        cnt = select(func.count(Order.id))
        if status != "all":
            stmt = stmt.where(Order.status == status)
            cnt = cnt.where(Order.status == status)
        total = (await s.execute(cnt)).scalar() or 0
        stmt = stmt.order_by(desc(Order.created_at)).offset(page * PAGE_SIZE).limit(PAGE_SIZE)
        orders = (await s.execute(stmt)).scalars().all()

        users, prods = {}, {}
        if orders:
            uids = list({o.user_id for o in orders})
            pids = list({o.product_id for o in orders})
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}
            p = (await s.execute(select(Product).where(Product.id.in_(pids)))).scalars().all()
            prods = {x.id: x for x in p}

    labels = {
        "all": "TODOS OS PEDIDOS",
        "pending": "PEDIDOS PENDENTES",
        "completed": "PEDIDOS CONCLUÍDOS",
        "cancelled": "PEDIDOS CANCELADOS",
        "refunded": "PEDIDOS REEMBOLSADOS",
    }
    text = f"◈ BELLAMY | {labels.get(status, status.upper())}\n\n"
    text += f"Total: {total} | Página {page + 1}\n\n"

    if not orders:
        text += "Nenhum pedido encontrado."
    else:
        for o in orders:
            icon = status_icon(o.status)
            uname = (users[o.user_id].full_name if o.user_id in users else f"User {o.user_id}")[:20]
            pname = (prods[o.product_id].name if o.product_id in prods else f"Produto #{o.product_id}")[:28]
            text += (
                f"{icon} #{o.id:06d} — {uname}\n"
                f"   {pname} × {o.quantity} — R$ {o.total:.2f}\n"
                f"   {o.created_at.strftime('%d/%m %H:%M')}\n\n"
            )

    buttons = []
    for o in orders:
        icon = status_icon(o.status)
        buttons.append([InlineKeyboardButton(
            text=f"{icon} #{o.id:06d} — R$ {o.total:.2f}",
            callback_data=f"ord_view_{o.id}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"ord_list_{status}_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"ord_list_{status}_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_orders")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ DETALHE ============
async def render_order_detail(oid: int):
    async with AsyncSessionLocal() as s:
        order = await s.get(Order, oid)
        if not order:
            return (
                "❌ Pedido não encontrado.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_orders")]
                ]),
            )
        user = await s.get(User, order.user_id)
        product = await s.get(Product, order.product_id)

    icon = status_icon(order.status)
    label = status_label(order.status)
    uname = user.full_name if user else f"User {order.user_id}"
    uusername = f"@{user.username}" if user and user.username else "—"
    pname = product.name if product else f"Produto #{order.product_id}"

    text = (
        f"◈ BELLAMY | PEDIDO #{order.id:06d}\n\n"
        f"👤 Cliente: {uname}\n"
        f"💠 Username: {uusername}\n"
        f"🆔 User ID: <code>{order.user_id}</code>\n\n"
        f"📦 Produto: {pname}\n"
        f"🔢 Quantidade: {order.quantity}\n"
        f"💳 Total: R$ {order.total:.2f}\n\n"
        f"📌 Status: {icon} {label}\n"
        f"💳 Pagamento: {(order.payment_method or 'wallet').title()}\n"
        f"📅 Criado: {order.created_at.strftime('%d/%m/%Y %H:%M')}\n"
    )
    if order.updated_at:
        text += f"🔄 Atualizado: {order.updated_at.strftime('%d/%m/%Y %H:%M')}\n"
    if order.confirmed_by:
        text += f"✅ Confirmado por: Admin <code>{order.confirmed_by}</code>\n"
    if order.cancelled_by:
        text += f"❌ Cancelado por: Admin <code>{order.cancelled_by}</code>\n"
    if order.cancelled_reason:
        text += f"📝 Motivo: {order.cancelled_reason}\n"

    text += "\n🔐 DADOS DE ENTREGA\n" + format_delivery_data(order.delivery_data) + "\n"

    buttons = []
    if order.status == "pending":
        buttons.append([InlineKeyboardButton(text="✅ Confirmar pedido", callback_data=f"ord_confirm_{oid}")])
        buttons.append([InlineKeyboardButton(text="❌ Cancelar pedido", callback_data=f"ord_cancel_{oid}")])
    elif order.status == "completed":
        buttons.append([InlineKeyboardButton(text="💜 Reembolsar (mantém entrega)", callback_data=f"ord_refund_{oid}")])
        buttons.append([InlineKeyboardButton(text="❌ Cancelar (reembolso + estorno de estoque)", callback_data=f"ord_cancel_{oid}")])

    buttons.append([InlineKeyboardButton(text="👤 Ver usuário", callback_data=f"usr_view_{order.user_id}")])
    if product:
        buttons.append([InlineKeyboardButton(text="📦 Ver produto", callback_data=f"prd_view_{order.product_id}")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=f"ord_list_{order.status}_0")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("ord_view_"))
async def view_order(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    oid = int(cb.data.split("_")[-1])
    text, kb = await render_order_detail(oid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ CONFIRMAR ============
@router.callback_query(F.data.startswith("ord_confirm_"))
async def confirm_order(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    oid = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        order = await s.get(Order, oid)
        if not order or order.status != "pending":
            await cb.answer("Pedido não pode ser confirmado.", show_alert=True)
            return
        product = await s.get(Product, order.product_id)
        if not product:
            await cb.answer("Produto não encontrado.", show_alert=True)
            return
        if product.stock < order.quantity:
            await cb.answer(
                f"Estoque insuficiente (disponível: {product.stock}).",
                show_alert=True,
            )
            return

        before = product.stock
        product.stock -= order.quantity
        product.sold_count += order.quantity
        order.status = "completed"
        order.confirmed_by = cb.from_user.id
        order.updated_at = datetime.utcnow()

        s.add(StockMovement(
            product_id=order.product_id,
            admin_id=cb.from_user.id,
            movement_type="sale",
            quantity=order.quantity,
            stock_before=before,
            stock_after=product.stock,
            reason=f"Pedido #{order.id} confirmado por admin",
        ))
        await s.commit()

    await log_admin(cb.from_user.id, "order_confirm", {"order_id": oid})
    await cb.answer("✅ Pedido confirmado.")

    text, kb = await render_order_detail(oid)
    await cb.message.edit_text(text, reply_markup=kb)


# ============ CANCELAR ============
@router.callback_query(F.data.startswith("ord_cancel_"))
async def cancel_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    oid = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        order = await s.get(Order, oid)
        if not order or order.status not in ("pending", "completed"):
            await cb.answer("Pedido não pode ser cancelado.", show_alert=True)
            return
        method = (order.payment_method or "wallet").title()
        was_completed = order.status == "completed"

    note = ""
    if was_completed:
        note = (
            "\n\n⚠️ Este pedido já foi concluído. O cancelamento irá:\n"
            "• Devolver o valor à carteira do cliente (se pago via carteira)\n"
            "• Restaurar as unidades ao estoque\n"
        )
        if order.payment_method not in ("wallet", None):
            note += "• ⚠️ Pagamento via PIX — reembolso deve ser feito manualmente no Mercado Pago\n"

    await state.update_data(order_id=oid)
    await state.set_state(OS.cancel_reason)
    await cb.message.answer(
        f"❌ CANCELAR PEDIDO #{oid:06d}\n\n"
        f"Pagamento: {method}{note}\n\n"
        f"Informe o motivo do cancelamento (mínimo 3 caracteres):"
    )
    await cb.answer()


@router.message(OS.cancel_reason)
async def cancel_execute(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    reason = m.text.strip() if m.text else ""
    if len(reason) < 3:
        await m.answer("❌ Motivo muito curto.")
        return

    data = await state.get_data()
    oid = data.get("order_id")
    if not oid:
        await state.clear()
        await m.answer("Sessão expirada.")
        return

    async with AsyncSessionLocal() as s:
        order = await s.get(Order, oid)
        if not order or order.status not in ("pending", "completed"):
            await state.clear()
            await m.answer("❌ Pedido não pode mais ser cancelado.")
            return

        was_completed = order.status == "completed"
        total = order.total
        method = order.payment_method or "wallet"
        product = await s.get(Product, order.product_id) if was_completed else None

        order.status = "cancelled"
        order.cancelled_by = m.from_user.id
        order.cancelled_reason = reason
        order.updated_at = datetime.utcnow()

        if was_completed and product:
            before = product.stock
            product.stock += order.quantity
            s.add(StockMovement(
                product_id=order.product_id,
                admin_id=m.from_user.id,
                movement_type="adjustment",
                quantity=order.quantity,
                stock_before=before,
                stock_after=product.stock,
                reason=f"Cancelamento pedido #{oid}: {reason}",
            ))

        refunded = False
        if method == "wallet" and total > 0:
            wallet = await s.get(Wallet, order.user_id)
            if not wallet:
                wallet = Wallet(user_id=order.user_id, balance=0.0)
                s.add(wallet)
            before_bal = wallet.balance
            wallet.balance += total
            s.add(WalletTransaction(
                user_id=order.user_id,
                amount=total,
                type="refund",
                description=f"Cancelamento pedido #{oid}: {reason}",
                balance_before=before_bal,
                balance_after=wallet.balance,
            ))
            refunded = True

        await s.commit()

    await log_admin(m.from_user.id, "order_cancel", {
        "order_id": oid, "reason": reason,
        "was_completed": was_completed, "refunded": refunded,
    })
    await state.clear()

    msg = f"✅ Pedido #{oid:06d} cancelado."
    if refunded:
        msg += f"\n💰 R$ {total:.2f} reembolsado na carteira."
    if was_completed:
        msg += f"\n📦 {order.quantity} unidade(s) devolvida(s) ao estoque."
    if method != "wallet" and was_completed:
        msg += "\n⚠️ PIX: reembolso deve ser feito no painel do Mercado Pago."

    text, kb = await render_order_detail(oid)
    await m.answer(msg)
    await m.answer(text, reply_markup=kb)


# ============ REEMBOLSAR (SEM CANCELAR) ============
@router.callback_query(F.data.startswith("ord_refund_"))
async def refund_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    oid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        order = await s.get(Order, oid)
        if not order or order.status != "completed":
            await cb.answer("Apenas pedidos concluídos podem ser reembolsados.", show_alert=True)
            return
    await state.update_data(order_id=oid)
    await state.set_state(OS.refund_reason)
    await cb.message.answer(
        f"💜 REEMBOLSAR PEDIDO #{oid:06d}\n\n"
        f"⚠️ O valor será creditado na carteira do cliente.\n"
        f"A entrega já realizada NÃO será revertida e o estoque NÃO será alterado.\n\n"
        f"Informe o motivo do reembolso (mínimo 3 caracteres):"
    )
    await cb.answer()


@router.message(OS.refund_reason)
async def refund_execute(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    reason = m.text.strip() if m.text else ""
    if len(reason) < 3:
        await m.answer("❌ Motivo muito curto.")
        return

    data = await state.get_data()
    oid = data.get("order_id")
    if not oid:
        await state.clear()
        await m.answer("Sessão expirada.")
        return

    async with AsyncSessionLocal() as s:
        order = await s.get(Order, oid)
        if not order or order.status != "completed":
            await state.clear()
            await m.answer("❌ Pedido não pode mais ser reembolsado.")
            return

        total = order.total
        method = order.payment_method or "wallet"

        order.status = "refunded"
        order.cancelled_by = m.from_user.id
        order.cancelled_reason = f"Reembolso: {reason}"
        order.updated_at = datetime.utcnow()

        if method == "wallet":
            wallet = await s.get(Wallet, order.user_id)
            if not wallet:
                wallet = Wallet(user_id=order.user_id, balance=0.0)
                s.add(wallet)
            before_bal = wallet.balance
            wallet.balance += total
            s.add(WalletTransaction(
                user_id=order.user_id,
                amount=total,
                type="refund",
                description=f"Reembolso pedido #{oid}: {reason}",
                balance_before=before_bal,
                balance_after=wallet.balance,
            ))
        await s.commit()

    await log_admin(m.from_user.id, "order_refund", {
        "order_id": oid, "reason": reason, "method": method,
    })
    await state.clear()

    msg = f"💜 Pedido #{oid:06d} reembolsado."
    if method == "wallet":
        msg += f"\n💰 R$ {total:.2f} creditado na carteira do cliente."
    else:
        msg += "\n⚠️ PIX: faça o reembolso no painel do Mercado Pago."

    text, kb = await render_order_detail(oid)
    await m.answer(msg)
    await m.answer(text, reply_markup=kb)


# ============ ESTATÍSTICAS ============
@router.callback_query(F.data == "ord_stats")
async def stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    today = datetime.utcnow().date()
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        counts = {}
        for st in ("pending", "completed", "cancelled", "refunded"):
            counts[st] = (await s.execute(
                select(func.count(Order.id)).where(Order.status == st)
            )).scalar() or 0
        total = sum(counts.values())

        revenue = (await s.execute(
            select(func.coalesce(func.sum(Order.total), 0)).where(Order.status == "completed")
        )).scalar() or 0.0
        revenue_today = (await s.execute(
            select(func.coalesce(func.sum(Order.total), 0)).where(
                Order.status == "completed",
                Order.created_at >= datetime.combine(today, datetime.min.time()),
            )
        )).scalar() or 0.0
        revenue_month = (await s.execute(
            select(func.coalesce(func.sum(Order.total), 0)).where(
                Order.status == "completed",
                Order.created_at >= datetime.combine(month_start, datetime.min.time()),
            )
        )).scalar() or 0.0

        avg_ticket = revenue / counts["completed"] if counts["completed"] else 0.0

        top_products = (await s.execute(
            select(Product.name, func.count(Order.id).label("cnt"))
            .join(Order, Order.product_id == Product.id)
            .where(Order.status == "completed")
            .group_by(Product.id, Product.name)
            .order_by(desc("cnt"))
            .limit(5)
        )).all()

        top_users = (await s.execute(
            select(User.full_name, User.username, Order.user_id,
                   func.coalesce(func.sum(Order.total), 0).label("spent"))
            .join(Order, Order.user_id == User.id)
            .where(Order.status == "completed")
            .group_by(Order.user_id, User.full_name, User.username)
            .order_by(desc("spent"))
            .limit(5)
        )).all()

        pending_24h = (await s.execute(
            select(func.count(Order.id)).where(
                Order.status == "pending",
                Order.created_at < datetime.utcnow() - timedelta(hours=24),
            )
        )).scalar() or 0

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DE PEDIDOS\n\n"
        f"📊 Total: {total}\n"
        f"🟡 Pendentes: {counts['pending']}\n"
        f"🟢 Concluídos: {counts['completed']}\n"
        f"🔴 Cancelados: {counts['cancelled']}\n"
        f"💜 Reembolsados: {counts['refunded']}\n\n"
        f"💰 Faturamento total: R$ {revenue:.2f}\n"
        f"📅 Hoje: R$ {revenue_today:.2f}\n"
        f"📆 Este mês: R$ {revenue_month:.2f}\n"
        f"🎯 Ticket médio: R$ {avg_ticket:.2f}\n"
    )
    if pending_24h > 0:
        text += f"⚠️ Pendentes há +24h: {pending_24h}\n"
    text += "\n"

    if top_products:
        text += "🏆 TOP 5 PRODUTOS\n"
        for i, (name, cnt) in enumerate(top_products, 1):
            text += f"{i}. {name} — {cnt} pedido(s)\n"
        text += "\n"

    if top_users:
        text += "👑 TOP 5 CLIENTES\n"
        for i, (name, uname, uid, spent) in enumerate(top_users, 1):
            display = name or (f"@{uname}" if uname else f"User {uid}")
            text += f"{i}. {display} — R$ {spent:.2f}\n"

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="ord_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_orders")],
    ]))
    await cb.answer()


# ============ BUSCA ============
@router.callback_query(F.data == "ord_search")
async def search_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(OS.search)
    await cb.message.answer(
        "🔎 BUSCAR PEDIDO\n\n"
        "Envie:\n"
        "• <b>ID do pedido</b> (ex.: 123)\n"
        "• <b>ID do Telegram</b> do cliente\n"
        "• <b>@username</b> do cliente\n"
        "• <b>Nome</b> do cliente\n\n"
        "Digite /cancelar para abortar."
    )
    await cb.answer()


@router.message(OS.search)
async def search_execute(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    q = m.text.strip() if m.text else ""
    if q.lower() == "/cancelar":
        await state.clear()
        await m.answer("Operação cancelada.")
        return
    await state.clear()

    orders = []
    async with AsyncSessionLocal() as s:
        if q.isdigit():
            n = int(q)
            o = await s.get(Order, n)
            if o:
                orders = [o]
            else:
                stmt = (
                    select(Order)
                    .where(Order.user_id == n)
                    .order_by(desc(Order.created_at))
                    .limit(20)
                )
                orders = (await s.execute(stmt)).scalars().all()
        else:
            clean = q.lstrip("@")
            stmt = (
                select(Order)
                .join(User, User.id == Order.user_id)
                .where(or_(
                    User.username.ilike(f"%{clean}%"),
                    User.full_name.ilike(f"%{q}%"),
                ))
                .order_by(desc(Order.created_at))
                .limit(20)
            )
            orders = (await s.execute(stmt)).scalars().all()

    if not orders:
        await m.answer("❌ Nenhum pedido encontrado.")
        return

    text = f"🔎 RESULTADOS: {len(orders)} pedido(s)\n\n"
    buttons = []
    for o in orders:
        icon = status_icon(o.status)
        text += f"{icon} #{o.id:06d} — User {o.user_id} — R$ {o.total:.2f}\n"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} #{o.id:06d} — R$ {o.total:.2f}",
            callback_data=f"ord_view_{o.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_orders")])

    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ============ EXPORTAÇÃO ============
@router.callback_query(F.data == "ord_export")
async def export_report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        all_orders = (await s.execute(
            select(Order).order_by(desc(Order.created_at)).limit(500)
        )).scalars().all()

        users, prods = {}, {}
        if all_orders:
            uids = list({o.user_id for o in all_orders})
            pids = list({o.product_id for o in all_orders})
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}
            p = (await s.execute(select(Product).where(Product.id.in_(pids)))).scalars().all()
            prods = {x.id: x for x in p}

    lines = []
    lines.append("═" * 60)
    lines.append("BELLAMY | RELATÓRIO DE PEDIDOS")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append(f"Total exportado: {len(all_orders)} pedido(s) (últimos 500)")
    lines.append("═" * 60)
    lines.append("")

    for o in all_orders:
        user = users.get(o.user_id)
        prod = prods.get(o.product_id)
        lines.append(f"Pedido #{o.id:06d}")
        lines.append(f"  Data:       {o.created_at.strftime('%d/%m/%Y %H:%M')}")
        lines.append(f"  Status:     {o.status}")
        lines.append(f"  Pagamento:  {o.payment_method or 'wallet'}")
        lines.append(f"  Cliente:    {user.full_name if user else 'Desconhecido'} (ID: {o.user_id})")
        if user and user.username:
            lines.append(f"  Username:   @{user.username}")
        lines.append(f"  Produto:    {prod.name if prod else 'Desconhecido'} (#{o.product_id})")
        lines.append(f"  Quantidade: {o.quantity}")
        lines.append(f"  Total:      R$ {o.total:.2f}")
        if o.cancelled_reason:
            lines.append(f"  Motivo:     {o.cancelled_reason}")
        lines.append("")

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"pedidos_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption=f"📊 <b>Relatório de Pedidos</b>\n\nTotal: {len(all_orders)} pedido(s)",
    )
    await cb.answer("Relatório gerado.")
