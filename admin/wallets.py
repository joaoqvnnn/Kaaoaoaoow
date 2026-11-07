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
    Wallet, WalletTransaction, User, Order, AdminLog,
)

router = Router()
dp.include_router(router)

PAGE_SIZE = 10
TX_PAGE_SIZE = 12


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class WS(StatesGroup):
    search = State()
    adjust_amount = State()
    adjust_reason = State()


TX_LABELS = {
    "admin_add": "➕ Adição (admin)",
    "admin_remove": "➖ Remoção (admin)",
    "admin_correct": "🔧 Correção (admin)",
    "recharge": "💎 Recarga",
    "purchase": "🛒 Compra",
    "refund": "💜 Reembolso",
    "bonus": "🎁 Bônus",
    "affiliate": "🤝 Afiliado",
    "points_conversion": "🏆 Conversão de pontos",
    "withdraw": "📤 Saque",
}


def tx_icon(tx_type: str, amount: float) -> str:
    if amount >= 0:
        return "➕"
    return "➖"


# ============ MENU ============
@router.callback_query(F.data == "admin_wallets")
async def wallets_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    today = datetime.utcnow().date()
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        total_wallets = (await s.execute(select(func.count(Wallet.user_id)))).scalar() or 0
        total_balance = (await s.execute(
            select(func.coalesce(func.sum(Wallet.balance), 0))
        )).scalar() or 0.0
        with_balance = (await s.execute(
            select(func.count(Wallet.user_id)).where(Wallet.balance > 0)
        )).scalar() or 0
        zero_balance = total_wallets - with_balance

        tx_total = (await s.execute(select(func.count(WalletTransaction.id)))).scalar() or 0
        tx_today = (await s.execute(
            select(func.count(WalletTransaction.id))
            .where(WalletTransaction.created_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0
        tx_month = (await s.execute(
            select(func.count(WalletTransaction.id))
            .where(WalletTransaction.created_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0

        credits_total = (await s.execute(
            select(func.coalesce(func.sum(WalletTransaction.amount), 0))
            .where(WalletTransaction.amount > 0)
        )).scalar() or 0.0
        debits_total = (await s.execute(
            select(func.coalesce(func.sum(WalletTransaction.amount), 0))
            .where(WalletTransaction.amount < 0)
        )).scalar() or 0.0

    text = (
        "◈ BELLAMY | CARTEIRAS E SALDOS\n\n"
        f"💎 Carteiras criadas: {total_wallets}\n"
        f"🟢 Com saldo: {with_balance}\n"
        f"⚪ Zeradas: {zero_balance}\n\n"
        f"💰 Saldo total distribuído: R$ {total_balance:.2f}\n"
        f"📈 Créditos acumulados: R$ {credits_total:.2f}\n"
        f"📉 Débitos acumulados: R$ {abs(debits_total):.2f}\n\n"
        f"📜 Movimentações registradas: {tx_total}\n"
        f"📅 Hoje: {tx_today} | Este mês: {tx_month}\n\n"
        "👇 Selecione uma opção:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Top saldos", callback_data="wal_top_0")],
        [InlineKeyboardButton(text="👥 Todas as carteiras", callback_data="wal_list_0")],
        [InlineKeyboardButton(text="🟢 Carteiras com saldo", callback_data="wal_list_pos_0")],
        [InlineKeyboardButton(text="📜 Movimentações", callback_data="wal_tx_all_0")],
        [InlineKeyboardButton(text="🔎 Buscar usuário", callback_data="wal_search")],
        [InlineKeyboardButton(text="📊 Estatísticas", callback_data="wal_stats")],
        [InlineKeyboardButton(text="📥 Exportar relatório", callback_data="wal_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ TOP SALDOS ============
@router.callback_query(F.data.startswith("wal_top_"))
async def top_balances(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(Wallet.user_id)).where(Wallet.balance > 0)
        )).scalar() or 0
        stmt = (
            select(Wallet)
            .where(Wallet.balance > 0)
            .order_by(desc(Wallet.balance))
            .offset(page * PAGE_SIZE).limit(PAGE_SIZE)
        )
        wallets = (await s.execute(stmt)).scalars().all()
        uids = [w.user_id for w in wallets]
        users = {}
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}
        total_balance = (await s.execute(
            select(func.coalesce(func.sum(Wallet.balance), 0))
        )).scalar() or 0.0

    text = (
        f"🏆 TOP SALDOS\n\n"
        f"Total de carteiras com saldo: {total}\n"
        f"Saldo total distribuído: R$ {total_balance:.2f}\n"
        f"Página {page + 1}\n\n"
    )
    buttons = []
    if not wallets:
        text += "Nenhuma carteira com saldo."
    for i, w in enumerate(wallets, start=page * PAGE_SIZE + 1):
        u = users.get(w.user_id)
        uname = (u.full_name if u else f"User {w.user_id}")[:28]
        uhandle = f"@{u.username}" if u and u.username else "—"
        text += f"{i}. {uname} ({uhandle}) — R$ {w.balance:.2f}\n"
        buttons.append([InlineKeyboardButton(
            text=f"{i}. {uname} — R$ {w.balance:.2f}",
            callback_data=f"wal_view_{w.user_id}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"wal_top_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"wal_top_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_wallets")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ LISTA DE CARTEIRAS ============
async def build_wallet_list(page: int, only_positive: bool):
    async with AsyncSessionLocal() as s:
        base = select(func.count(Wallet.user_id))
        stmt = select(Wallet)
        if only_positive:
            base = base.where(Wallet.balance > 0)
            stmt = stmt.where(Wallet.balance > 0)
        total = (await s.execute(base)).scalar() or 0
        stmt = stmt.order_by(desc(Wallet.balance)).offset(page * PAGE_SIZE).limit(PAGE_SIZE)
        wallets = (await s.execute(stmt)).scalars().all()
        uids = [w.user_id for w in wallets]
        users = {}
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}
    return wallets, total, users


@router.callback_query(F.data.startswith("wal_list_"))
async def list_wallets(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    parts = cb.data.split("_")
    is_pos = parts[2] == "pos"
    page = int(parts[3])

    wallets, total, users = await build_wallet_list(page, is_pos)
    title = "CARTEIRAS COM SALDO" if is_pos else "TODAS AS CARTEIRAS"

    text = f"◈ BELLAMY | {title}\n\nTotal: {total} | Página {page + 1}\n\n"
    buttons = []
    if not wallets:
        text += "Nenhuma carteira encontrada."
    else:
        for w in wallets:
            u = users.get(w.user_id)
            uname = (u.full_name if u else f"User {w.user_id}")[:25]
            uhandle = f"@{u.username}" if u and u.username else "—"
            icon = "🟢" if w.balance > 0 else "⚪"
            text += f"{icon} {uname} ({uhandle}) — R$ {w.balance:.2f}\n"
            buttons.append([InlineKeyboardButton(
                text=f"{icon} {uname} — R$ {w.balance:.2f}",
                callback_data=f"wal_view_{w.user_id}",
            )])

    prefix = "wal_list_pos" if is_pos else "wal_list"
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"{prefix}_{page - 1}"
        ))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"{prefix}_{page + 1}"
        ))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_wallets")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ DETALHE DA CARTEIRA ============
async def render_wallet(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as s:
        user = await s.get(User, user_id)
        if not user:
            return (
                "❌ Usuário não encontrado.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_wallets")]
                ]),
            )
        wallet = await s.get(Wallet, user_id)
        if not wallet:
            wallet = Wallet(user_id=user_id, balance=0.0)
            s.add(wallet)
            await s.commit()
            await s.refresh(wallet)

        tx_count = (await s.execute(
            select(func.count(WalletTransaction.id))
            .where(WalletTransaction.user_id == user_id)
        )).scalar() or 0
        total_credits = (await s.execute(
            select(func.coalesce(func.sum(WalletTransaction.amount), 0))
            .where(WalletTransaction.user_id == user_id, WalletTransaction.amount > 0)
        )).scalar() or 0.0
        total_debits = (await s.execute(
            select(func.coalesce(func.sum(WalletTransaction.amount), 0))
            .where(WalletTransaction.user_id == user_id, WalletTransaction.amount < 0)
        )).scalar() or 0.0

        last_tx = (await s.execute(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == user_id)
            .order_by(desc(WalletTransaction.created_at))
            .limit(1)
        )).scalar_one_or_none()

        orders_count = (await s.execute(
            select(func.count(Order.id))
            .where(Order.user_id == user_id, Order.status == "completed")
        )).scalar() or 0
        orders_total = (await s.execute(
            select(func.coalesce(func.sum(Order.total), 0))
            .where(Order.user_id == user_id, Order.status == "completed")
        )).scalar() or 0.0

    uname = user.full_name or f"User {user.id}"
    uhandle = f"@{user.username}" if user.username else "—"

    text = (
        f"◈ BELLAMY | CARTEIRA\n\n"
        f"👤 {uname}\n"
        f"💠 {uhandle}\n"
        f"🆔 <code>{user.id}</code>\n\n"
        f"💎 Saldo atual: R$ {wallet.balance:.2f}\n\n"
        f"📜 Movimentações: {tx_count}\n"
        f"📈 Total creditado: R$ {total_credits:.2f}\n"
        f"📉 Total debitado: R$ {abs(total_debits):.2f}\n"
    )
    if last_tx:
        text += (
            f"🕒 Última mov.: {last_tx.created_at.strftime('%d/%m/%Y %H:%M')}\n"
        )
    text += (
        f"\n🛒 Compras: {orders_count} — R$ {orders_total:.2f}\n"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="➕ Adicionar saldo", callback_data=f"wal_add_{user_id}")],
        [InlineKeyboardButton(
            text="➖ Remover saldo", callback_data=f"wal_rem_{user_id}")],
        [InlineKeyboardButton(
            text="🔧 Corrigir saldo", callback_data=f"wal_corr_{user_id}")],
        [InlineKeyboardButton(
            text="📜 Ver extrato completo", callback_data=f"wal_tx_user_{user_id}_0")],
        [InlineKeyboardButton(
            text="👤 Ver perfil completo", callback_data=f"usr_view_{user_id}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_wallets")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("wal_view_"))
async def view_wallet(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split("_")[-1])
    text, kb = await render_wallet(uid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ AJUSTE MANUAL (ADD / REMOVE / CORRECT) ============
@router.callback_query(F.data.startswith("wal_add_"))
async def add_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split("_")[-1])
    await state.update_data(target_user_id=uid, action_type="add")
    await state.set_state(WS.adjust_amount)
    await cb.message.answer(
        f"➕ ADICIONAR SALDO\n\nUsuário: <code>{uid}</code>\n\n"
        f"Informe o valor a adicionar (R$):"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("wal_rem_"))
async def rem_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split("_")[-1])
    await state.update_data(target_user_id=uid, action_type="remove")
    await state.set_state(WS.adjust_amount)
    await cb.message.answer(
        f"➖ REMOVER SALDO\n\nUsuário: <code>{uid}</code>\n\n"
        f"Informe o valor a remover (R$):"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("wal_corr_"))
async def corr_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split("_")[-1])
    await state.update_data(target_user_id=uid, action_type="correct")
    await state.set_state(WS.adjust_amount)
    await cb.message.answer(
        f"🔧 CORRIGIR SALDO\n\nUsuário: <code>{uid}</code>\n\n"
        f"Informe o novo saldo (R$):"
    )
    await cb.answer()


@router.message(WS.adjust_amount)
async def receive_amount(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        amount = float(m.text.strip().replace(",", "."))
    except ValueError:
        await m.answer("❌ Valor inválido. Envie um número (ex.: 25.50).")
        return

    data = await state.get_data()
    at = data.get("action_type")
    if at in ("add", "remove") and amount <= 0:
        await m.answer("❌ O valor deve ser maior que zero.")
        return
    if at == "correct" and amount < 0:
        await m.answer("❌ O novo saldo não pode ser negativo.")
        return

    await state.update_data(amount=amount)
    await state.set_state(WS.adjust_reason)
    await m.answer("📝 Informe a justificativa (mínimo 3 caracteres):")


@router.message(WS.adjust_reason)
async def execute_adjust(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    reason = m.text.strip() if m.text else ""
    if len(reason) < 3:
        await m.answer("❌ Justificativa muito curta.")
        return

    data = await state.get_data()
    uid = data.get("target_user_id")
    at = data.get("action_type")
    amount = data.get("amount")
    if not uid or not at or amount is None:
        await state.clear()
        await m.answer("Sessão expirada.")
        return

    async with AsyncSessionLocal() as s:
        wallet = await s.get(Wallet, uid)
        if not wallet:
            wallet = Wallet(user_id=uid, balance=0.0)
            s.add(wallet)
        old = wallet.balance

        if at == "add":
            wallet.balance += amount
            tx_amount = amount
            tx_type = "admin_add"
        elif at == "remove":
            if wallet.balance < amount:
                await state.clear()
                await m.answer(
                    f"❌ Saldo insuficiente. Saldo atual: R$ {wallet.balance:.2f}"
                )
                return
            wallet.balance -= amount
            tx_amount = -amount
            tx_type = "admin_remove"
        elif at == "correct":
            diff = amount - wallet.balance
            wallet.balance = amount
            tx_amount = diff
            tx_type = "admin_correct"
        else:
            await state.clear()
            return

        new = wallet.balance
        s.add(WalletTransaction(
            user_id=uid,
            amount=tx_amount,
            type=tx_type,
            description=f"Admin {m.from_user.id}: {reason}",
            balance_before=old,
            balance_after=new,
        ))
        await s.commit()

    await log_admin(m.from_user.id, tx_type, {
        "user_id": uid, "amount": tx_amount,
        "before": old, "after": new, "reason": reason,
    })
    await state.clear()

    text, kb = await render_wallet(uid)
    await m.answer(
        f"✅ Operação concluída.\n\n"
        f"Saldo anterior: R$ {old:.2f}\n"
        f"Saldo novo: R$ {new:.2f}\n"
        f"Justificativa: {reason}"
    )
    await m.answer(text, reply_markup=kb)


# ============ EXTRATO DE UM USUÁRIO ============
@router.callback_query(F.data.startswith("wal_tx_user_"))
async def user_extract(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    parts = cb.data.split("_")
    uid = int(parts[3])
    page = int(parts[4])

    async with AsyncSessionLocal() as s:
        user = await s.get(User, uid)
        total = (await s.execute(
            select(func.count(WalletTransaction.id))
            .where(WalletTransaction.user_id == uid)
        )).scalar() or 0
        stmt = (
            select(WalletTransaction)
            .where(WalletTransaction.user_id == uid)
            .order_by(desc(WalletTransaction.created_at))
            .offset(page * TX_PAGE_SIZE).limit(TX_PAGE_SIZE)
        )
        txs = (await s.execute(stmt)).scalars().all()
        wallet = await s.get(Wallet, uid)
        balance = wallet.balance if wallet else 0.0

    uname = (user.full_name if user else f"User {uid}")[:30]
    text = (
        f"📜 EXTRATO — {uname}\n\n"
        f"Saldo atual: R$ {balance:.2f}\n"
        f"Total de movimentações: {total} | Página {page + 1}\n\n"
    )
    if not txs:
        text += "Nenhuma movimentação."
    else:
        for tx in txs:
            icon = "➕" if tx.amount >= 0 else "➖"
            label = TX_LABELS.get(tx.type, tx.type)
            text += (
                f"{icon} R$ {abs(tx.amount):.2f} — {label}\n"
                f"   {tx.description[:60]}\n"
                f"   Saldo: {tx.balance_before:.2f} → {tx.balance_after:.2f}\n"
                f"   {tx.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
            )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"wal_tx_user_{uid}_{page - 1}"
        ))
    if (page + 1) * TX_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"wal_tx_user_{uid}_{page + 1}"
        ))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(
        text="↩️ Voltar", callback_data=f"wal_view_{uid}"
    )])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ MOVIMENTAÇÕES GLOBAIS ============
@router.callback_query(F.data.startswith("wal_tx_all_"))
async def all_transactions(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(WalletTransaction.id))
        )).scalar() or 0
        stmt = (
            select(WalletTransaction)
            .order_by(desc(WalletTransaction.created_at))
            .offset(page * TX_PAGE_SIZE).limit(TX_PAGE_SIZE)
        )
        txs = (await s.execute(stmt)).scalars().all()
        uids = list({t.user_id for t in txs})
        users = {}
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}

    text = (
        f"📜 MOVIMENTAÇÕES GLOBAIS\n\n"
        f"Total: {total} | Página {page + 1}\n\n"
    )
    if not txs:
        text += "Nenhuma movimentação."
    else:
        for tx in txs:
            icon = "➕" if tx.amount >= 0 else "➖"
            u = users.get(tx.user_id)
            uname = (u.full_name if u else f"User {tx.user_id}")[:22]
            label = TX_LABELS.get(tx.type, tx.type)
            text += (
                f"{icon} R$ {abs(tx.amount):.2f} — {label}\n"
                f"   👤 {uname} (<code>{tx.user_id}</code>)\n"
                f"   {tx.created_at.strftime('%d/%m %H:%M')}\n\n"
            )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"wal_tx_all_{page - 1}"
        ))
    if (page + 1) * TX_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"wal_tx_all_{page + 1}"
        ))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_wallets")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ BUSCA ============
@router.callback_query(F.data == "wal_search")
async def search_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(WS.search)
    await cb.message.answer(
        "🔎 BUSCAR CARTEIRA\n\n"
        "Envie o ID, @username ou nome do usuário:\n\n"
        "Digite /cancelar para abortar."
    )
    await cb.answer()


@router.message(WS.search)
async def search_execute(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    q = m.text.strip() if m.text else ""
    if q.lower() == "/cancelar":
        await state.clear()
        await m.answer("Operação cancelada.")
        return
    await state.clear()

    async with AsyncSessionLocal() as s:
        users = []
        if q.isdigit():
            u = await s.get(User, int(q))
            if u:
                users = [u]
        else:
            clean = q.lstrip("@")
            stmt = select(User).where(or_(
                User.username.ilike(f"%{clean}%"),
                User.full_name.ilike(f"%{q}%"),
            )).limit(20)
            users = (await s.execute(stmt)).scalars().all()

        if not users:
            await m.answer("❌ Nenhum usuário encontrado.")
            return

        uids = [u.id for u in users]
        wallets = (await s.execute(
            select(Wallet).where(Wallet.user_id.in_(uids))
        )).scalars().all()
        balances = {w.user_id: w.balance for w in wallets}

    text = f"🔎 RESULTADOS: {len(users)} usuário(s)\n\n"
    buttons = []
    for u in users:
        bal = balances.get(u.id, 0.0)
        text += f"👤 {u.full_name} (@{u.username or '—'}) — R$ {bal:.2f}\n"
        buttons.append([InlineKeyboardButton(
            text=f"💎 {u.full_name[:30]} — R$ {bal:.2f}",
            callback_data=f"wal_view_{u.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_wallets")])

    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ============ ESTATÍSTICAS ============
@router.callback_query(F.data == "wal_stats")
async def stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    today = datetime.utcnow().date()
    week_start = today - timedelta(days=7)
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        total_wallets = (await s.execute(select(func.count(Wallet.user_id)))).scalar() or 0
        total_balance = (await s.execute(
            select(func.coalesce(func.sum(Wallet.balance), 0))
        )).scalar() or 0.0
        max_balance = (await s.execute(
            select(func.coalesce(func.max(Wallet.balance), 0))
        )).scalar() or 0.0
        min_pos = (await s.execute(
            select(func.coalesce(func.min(Wallet.balance), 0))
            .where(Wallet.balance > 0)
        )).scalar() or 0.0

        avg_balance = total_balance / total_wallets if total_wallets else 0.0

        # Volume por período
        def total_between(d0, d1):
            return (
                select(func.count(WalletTransaction.id),
                       func.coalesce(func.sum(func.abs(WalletTransaction.amount)), 0))
                .where(WalletTransaction.created_at >= d0)
                .where(WalletTransaction.created_at < d1)
            )

        cnt_today, vol_today = (await s.execute(
            total_between(datetime.combine(today, datetime.min.time()),
                          datetime.combine(today, datetime.max.time()))
        )).one()
        cnt_week, vol_week = (await s.execute(
            total_between(datetime.combine(week_start, datetime.min.time()),
                          datetime.utcnow())
        )).one()
        cnt_month, vol_month = (await s.execute(
            total_between(datetime.combine(month_start, datetime.min.time()),
                          datetime.utcnow())
        )).one()

        # Tipos mais comuns
        top_types = (await s.execute(
            select(WalletTransaction.type,
                   func.count(WalletTransaction.id).label("cnt"),
                   func.coalesce(func.sum(func.abs(WalletTransaction.amount)), 0).label("vol"))
            .group_by(WalletTransaction.type)
            .order_by(desc("cnt"))
            .limit(6)
        )).all()

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DE CARTEIRAS\n\n"
        f"💎 Carteiras: {total_wallets}\n"
        f"💰 Saldo total: R$ {total_balance:.2f}\n"
        f"📊 Saldo médio: R$ {avg_balance:.2f}\n"
        f"🏆 Maior saldo: R$ {max_balance:.2f}\n"
        f"📉 Menor saldo (positivo): R$ {min_pos:.2f}\n\n"
        f"📅 Hoje: {cnt_today} mov. — R$ {vol_today:.2f}\n"
        f"📆 Semana: {cnt_week} mov. — R$ {vol_week:.2f}\n"
        f"🗓️ Mês: {cnt_month} mov. — R$ {vol_month:.2f}\n\n"
    )
    if top_types:
        text += "🔝 TIPOS MAIS FREQUENTES\n"
        for tp, cnt, vol in top_types:
            label = TX_LABELS.get(tp, tp)
            text += f"• {label}: {cnt} mov. — R$ {vol:.2f}\n"

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="wal_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_wallets")],
    ]))
    await cb.answer()


# ============ EXPORTAÇÃO ============
@router.callback_query(F.data == "wal_export")
async def export_report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        wallets = (await s.execute(
            select(Wallet).order_by(desc(Wallet.balance))
        )).scalars().all()

        uids = [w.user_id for w in wallets]
        users = {}
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}

        recent_tx = (await s.execute(
            select(WalletTransaction)
            .order_by(desc(WalletTransaction.created_at))
            .limit(200)
        )).scalars().all()

        tx_uids = list({t.user_id for t in recent_tx})
        tx_users = {}
        if tx_uids:
            u2 = (await s.execute(select(User).where(User.id.in_(tx_uids)))).scalars().all()
            tx_users = {x.id: x for x in u2}

        total_balance = sum(w.balance for w in wallets)

    lines = []
    lines.append("═" * 60)
    lines.append("BELLAMY | RELATÓRIO DE CARTEIRAS")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append(f"Total de carteiras: {len(wallets)}")
    lines.append(f"Saldo total distribuído: R$ {total_balance:.2f}")
    lines.append("═" * 60)
    lines.append("")
    lines.append("─" * 60)
    lines.append("SALDOS")
    lines.append("─" * 60)
    for w in wallets:
        u = users.get(w.user_id)
        uname = u.full_name if u else "Desconhecido"
        uhandle = f"@{u.username}" if u and u.username else "—"
        lines.append(
            f"{w.user_id:<15} | {uname[:25]:<25} | {uhandle[:15]:<15} "
            f"| R$ {w.balance:.2f}"
        )
    lines.append("")
    lines.append("─" * 60)
    lines.append("ÚLTIMAS 200 MOVIMENTAÇÕES")
    lines.append("─" * 60)
    for tx in recent_tx:
        u = tx_users.get(tx.user_id)
        uname = u.full_name if u else "Desconhecido"
        label = TX_LABELS.get(tx.type, tx.type)
        lines.append(
            f"{tx.created_at.strftime('%d/%m/%Y %H:%M')} | "
            f"{tx.user_id:<15} | {uname[:20]:<20} | "
            f"{tx.amount:+10.2f} | {label}"
        )

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"carteiras_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption=(
            f"📊 <b>Relatório de Carteiras</b>\n\n"
            f"💎 Carteiras: {len(wallets)}\n"
            f"💰 Saldo total: R$ {total_balance:.2f}\n"
            f"📜 Movimentações (últimas 200): {len(recent_tx)}"
        ),
    )
    await cb.answer("Relatório gerado.")
