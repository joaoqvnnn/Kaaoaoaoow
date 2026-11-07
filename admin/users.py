from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, or_, desc
from datetime import datetime, timedelta

from bot import dp
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import User, Wallet, WalletTransaction, Order, AdminLog

router = Router()
dp.include_router(router)

PAGE_SIZE = 10

class UserStates(StatesGroup):
    waiting_search = State()
    waiting_balance_amount = State()
    waiting_balance_reason = State()


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin_action(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as session:
        session.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await session.commit()


async def get_user_wallet(session, user_id: int) -> Wallet:
    wallet = await session.get(Wallet, user_id)
    if not wallet:
        wallet = Wallet(user_id=user_id, balance=0.0)
        session.add(wallet)
        await session.commit()
        await session.refresh(wallet)
    return wallet


# ---------- Menu principal ----------
def users_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Listar usuários", callback_data="usr_list_0")],
        [InlineKeyboardButton(text="🔎 Pesquisar usuário", callback_data="usr_search")],
        [InlineKeyboardButton(text="🟢 Usuários ativos", callback_data="usr_active_0")],
        [InlineKeyboardButton(text="🚫 Usuários bloqueados", callback_data="usr_blocked_0")],
        [InlineKeyboardButton(text="💎 Carteiras", callback_data="usr_wallets")],
        [InlineKeyboardButton(text="🛒 Compras dos usuários", callback_data="usr_purchases_all")],
        [InlineKeyboardButton(text="📊 Estatísticas", callback_data="usr_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])


@router.callback_query(F.data == "admin_users")
async def users_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Acesso negado.", show_alert=True)
        return
    await callback.message.edit_text(
        "◈ BELLAMY | USUÁRIOS\n\nSelecione uma opção:",
        reply_markup=users_menu_keyboard(),
    )
    await callback.answer()


# ---------- Listagem paginada ----------
async def build_user_list(page: int, filter_type: str = "all"):
    async with AsyncSessionLocal() as session:
        stmt = select(User)
        count_stmt = select(func.count(User.id))

        if filter_type == "active":
            stmt = stmt.where(User.is_active == True, User.is_blocked == False)
            count_stmt = count_stmt.where(User.is_active == True, User.is_blocked == False)
        elif filter_type == "blocked":
            stmt = stmt.where(User.is_blocked == True)
            count_stmt = count_stmt.where(User.is_blocked == True)

        total = (await session.execute(count_stmt)).scalar() or 0
        stmt = stmt.order_by(desc(User.created_at)).offset(page * PAGE_SIZE).limit(PAGE_SIZE)
        users = (await session.execute(stmt)).scalars().all()
        return users, total


def format_user_list(users, page: int, total: int, filter_type: str):
    titles = {
        "all": "TODOS OS USUÁRIOS",
        "active": "USUÁRIOS ATIVOS",
        "blocked": "USUÁRIOS BLOQUEADOS",
    }
    prefix_map = {"all": "usr_list", "active": "usr_active", "blocked": "usr_blocked"}
    prefix = prefix_map[filter_type]

    text = f"◈ BELLAMY | {titles[filter_type]}\n\n"
    text += f"Total: {total} | Página {page + 1}\n\n"
    if not users:
        text += "Nenhum usuário encontrado."
    else:
        for u in users:
            status = "🔴" if u.is_blocked else "🟢"
            text += (
                f"{status} {u.full_name}\n"
                f"   @{u.username or 'sem username'} — ID: {u.id}\n\n"
            )

    buttons = []
    for u in users:
        buttons.append([InlineKeyboardButton(
            text=f"👤 {u.full_name[:30] or u.id}",
            callback_data=f"usr_view_{u.id}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Anterior", callback_data=f"{prefix}_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="Próxima ➡️", callback_data=f"{prefix}_{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_users")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("usr_list_"))
async def list_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    page = int(callback.data.split("_")[-1])
    users, total = await build_user_list(page, "all")
    text, kb = format_user_list(users, page, total, "all")
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("usr_active_"))
async def list_active(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    page = int(callback.data.split("_")[-1])
    users, total = await build_user_list(page, "active")
    text, kb = format_user_list(users, page, total, "active")
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("usr_blocked_"))
async def list_blocked(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    page = int(callback.data.split("_")[-1])
    users, total = await build_user_list(page, "blocked")
    text, kb = format_user_list(users, page, total, "blocked")
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ---------- Pesquisa ----------
@router.callback_query(F.data == "usr_search")
async def search_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await state.set_state(UserStates.waiting_search)
    await callback.message.answer("🔎 Envie o ID, @username ou nome do usuário:")
    await callback.answer()


@router.message(UserStates.waiting_search)
async def search_execute(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    query = message.text.strip()
    async with AsyncSessionLocal() as session:
        filters = []
        if query.isdigit():
            filters.append(User.id == int(query))
        clean = query.lstrip("@")
        filters.append(User.username.ilike(f"%{clean}%"))
        filters.append(User.full_name.ilike(f"%{query}%"))

        stmt = select(User).where(or_(*filters)).limit(20)
        users = (await session.execute(stmt)).scalars().all()

    await state.clear()

    if not users:
        await message.answer("❌ Nenhum usuário encontrado.")
        return

    buttons = []
    text = f"🔎 RESULTADOS DA BUSCA\n\n{len(users)} encontrado(s):\n\n"
    for u in users:
        status = "🔴" if u.is_blocked else "🟢"
        text += f"{status} {u.full_name} — @{u.username or 'sem'} (ID: {u.id})\n"
        buttons.append([InlineKeyboardButton(
            text=f"👤 {u.full_name[:30] or u.id}",
            callback_data=f"usr_view_{u.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_users")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------- Detalhes do usuário ----------
async def render_user_view(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, uid)
        if not user:
            return "❌ Usuário não encontrado.", InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Voltar", callback_data="usr_list_0")]
            ])

        wallet = await get_user_wallet(session, uid)
        total_purchases = (await session.execute(
            select(func.count(Order.id)).where(
                Order.user_id == uid, Order.status == "completed"
            )
        )).scalar() or 0
        total_spent = (await session.execute(
            select(func.coalesce(func.sum(Order.total), 0)).where(
                Order.user_id == uid, Order.status == "completed"
            )
        )).scalar() or 0.0

    status = "🔴 Bloqueado" if user.is_blocked else "🟢 Ativo"
    text = (
        f"◈ BELLAMY | USUÁRIO\n\n"
        f"👤 {user.full_name}\n"
        f"Username: @{user.username or 'sem'}\n"
        f"Telegram ID: {user.id}\n"
        f"Cadastro: {user.created_at.strftime('%d/%m/%Y %H:%M')}\n"
        f"Status: {status}\n\n"
        f"💎 Saldo: R$ {wallet.balance:.2f}\n\n"
        f"🛒 Compras: {total_purchases}\n"
        f"💰 Total gasto: R$ {total_spent:.2f}\n"
    )
    if user.block_reason:
        text += f"\n📝 Motivo do bloqueio: {user.block_reason}"
        if user.blocked_until:
            text += f"\n⏳ Até: {user.blocked_until.strftime('%d/%m/%Y %H:%M')}"

    buttons = [
        [InlineKeyboardButton(text="💎 Consultar carteira", callback_data=f"usr_wallet_{uid}")],
        [InlineKeyboardButton(text="📦 Consultar pedidos", callback_data=f"usr_purchases_{uid}")],
        [InlineKeyboardButton(text="➕ Adicionar saldo", callback_data=f"usr_add_{uid}")],
        [InlineKeyboardButton(text="➖ Remover saldo", callback_data=f"usr_rem_{uid}")],
        [InlineKeyboardButton(text="🔧 Corrigir saldo", callback_data=f"usr_corr_{uid}")],
    ]
    if user.is_blocked:
        buttons.append([InlineKeyboardButton(
            text="🔓 Desbloquear", callback_data=f"usr_unblock_{uid}"
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text="🔒 Bloquear usuário", callback_data=f"usr_block_{uid}"
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="usr_list_0")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("usr_view_"))
async def view_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    uid = int(callback.data.split("_")[-1])
    text, kb = await render_user_view(uid)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ---------- Carteira ----------
@router.callback_query(F.data.startswith("usr_wallet_"))
async def view_wallet(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    uid = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        wallet = await get_user_wallet(session, uid)
        result = await session.execute(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == uid)
            .order_by(desc(WalletTransaction.created_at))
            .limit(15)
        )
        txs = result.scalars().all()
        balance = wallet.balance

    text = f"💎 CARTEIRA DO USUÁRIO {uid}\n\nSaldo atual: R$ {balance:.2f}\n\n"
    if txs:
        text += "📋 Últimas movimentações:\n\n"
        for tx in txs:
            sinal = "➕" if tx.amount > 0 else "➖"
            text += (
                f"{sinal} R$ {abs(tx.amount):.2f} — {tx.type}\n"
                f"   {tx.description}\n"
                f"   {tx.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
            )
    else:
        text += "Nenhuma movimentação registrada."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"usr_view_{uid}")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ---------- Compras ----------
@router.callback_query(F.data == "usr_wallets")
async def wallets_overview(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        total = (await session.execute(
            select(func.count(Wallet.user_id))
        )).scalar() or 0
        total_balance = (await session.execute(
            select(func.coalesce(func.sum(Wallet.balance), 0))
        )).scalar() or 0.0
        result = await session.execute(
            select(Wallet).order_by(desc(Wallet.balance)).limit(10)
        )
        top = result.scalars().all()

    text = (
        f"💎 CARTEIRAS\n\n"
        f"Total de carteiras: {total}\n"
        f"Saldo total: R$ {total_balance:.2f}\n\n"
        f"🏆 Top 10 saldos:\n\n"
    )
    buttons = []
    for w in top:
        text += f"• User {w.user_id}: R$ {w.balance:.2f}\n"
        buttons.append([InlineKeyboardButton(
            text=f"👤 Ver user {w.user_id}", callback_data=f"usr_view_{w.user_id}"
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_users")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("usr_purchases_"))
async def view_purchases(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    data = callback.data.replace("usr_purchases_", "")

    if data == "all":
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Order).order_by(desc(Order.created_at)).limit(20)
            )
            orders = result.scalars().all()
        text = "🛒 ÚLTIMAS COMPRAS DO SISTEMA\n\n"
        if not orders:
            text += "Nenhuma compra registrada."
        for o in orders:
            text += (
                f"#{o.id} — User {o.user_id}\n"
                f"   R$ {o.total:.2f} — {o.status} — {o.created_at.strftime('%d/%m/%Y')}\n\n"
            )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_users")]
        ])
    else:
        uid = int(data)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Order)
                .where(Order.user_id == uid)
                .order_by(desc(Order.created_at))
                .limit(20)
            )
            orders = result.scalars().all()
        text = f"🛒 COMPRAS DO USUÁRIO {uid}\n\n"
        if not orders:
            text += "Nenhuma compra registrada."
        for o in orders:
            text += (
                f"#{o.id} — {o.quantity}x Produto {o.product_id}\n"
                f"   R$ {o.total:.2f} — {o.status} — {o.created_at.strftime('%d/%m/%Y')}\n\n"
            )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"usr_view_{uid}")]
        ])

    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ---------- Estatísticas ----------
@router.callback_query(F.data == "usr_stats")
async def user_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    today = datetime.utcnow().date()
    async with AsyncSessionLocal() as session:
        total = (await session.execute(select(func.count(User.id)))).scalar() or 0
        active = (await session.execute(
            select(func.count(User.id)).where(User.is_active == True, User.is_blocked == False)
        )).scalar() or 0
        blocked = (await session.execute(
            select(func.count(User.id)).where(User.is_blocked == True)
        )).scalar() or 0
        new_today = (await session.execute(
            select(func.count(User.id)).where(User.created_at >= today)
        )).scalar() or 0
        total_balance = (await session.execute(
            select(func.coalesce(func.sum(Wallet.balance), 0))
        )).scalar() or 0.0
        with_purchases = (await session.execute(
            select(func.count(func.distinct(Order.user_id))).where(Order.status == "completed")
        )).scalar() or 0

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DE USUÁRIOS\n\n"
        f"👥 Total: {total}\n"
        f"🟢 Ativos: {active}\n"
        f"🔴 Bloqueados: {blocked}\n"
        f"🆕 Novos hoje: {new_today}\n"
        f"💎 Saldo em carteiras: R$ {total_balance:.2f}\n"
        f"🛒 Usuários com compras: {with_purchases}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_users")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ---------- Bloqueio (permanente / temporário) ----------
@router.callback_query(F.data.startswith("usr_block_"))
async def block_choose_type(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    uid = int(callback.data.split("_")[-1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 Bloqueio permanente", callback_data=f"usr_blockperm_{uid}")],
        [InlineKeyboardButton(text="⏳ Bloqueio por 30 dias", callback_data=f"usr_blocktemp_{uid}")],
        [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"usr_view_{uid}")],
    ])
    await callback.message.edit_text(
        f"Escolha o tipo de bloqueio para o usuário {uid}:",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("usr_blockperm_"))
async def block_perm_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    uid = int(callback.data.split("_")[-1])
    await state.update_data(target_user_id=uid, action_type="block_perm")
    await state.set_state(UserStates.waiting_balance_reason)
    await callback.message.answer(f"Informe o motivo do bloqueio permanente do usuário {uid}:")
    await callback.answer()


@router.callback_query(F.data.startswith("usr_blocktemp_"))
async def block_temp_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    uid = int(callback.data.split("_")[-1])
    await state.update_data(target_user_id=uid, action_type="block_temp")
    await state.set_state(UserStates.waiting_balance_reason)
    await callback.message.answer(f"Informe o motivo do bloqueio (30 dias) do usuário {uid}:")
    await callback.answer()


@router.callback_query(F.data.startswith("usr_unblock_"))
async def unblock_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    uid = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        user = await session.get(User, uid)
        if not user:
            await callback.answer("Usuário não encontrado.", show_alert=True)
            return
        user.is_blocked = False
        user.blocked_until = None
        user.block_reason = None
        user.blocked_by = None
        await session.commit()
        await log_admin_action(callback.from_user.id, "unblock_user", {"user_id": uid})

    text, kb = await render_user_view(uid)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("✅ Usuário desbloqueado.")


# ---------- Operações financeiras ----------
@router.callback_query(F.data.startswith("usr_add_"))
async def add_balance_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    uid = int(callback.data.split("_")[-1])
    await state.update_data(target_user_id=uid, action_type="add")
    await state.set_state(UserStates.waiting_balance_amount)
    await callback.message.answer(f"➕ ADICIONAR SALDO\n\nUsuário: {uid}\n\nInforme o valor a adicionar (R$):")
    await callback.answer()


@router.callback_query(F.data.startswith("usr_rem_"))
async def remove_balance_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    uid = int(callback.data.split("_")[-1])
    await state.update_data(target_user_id=uid, action_type="remove")
    await state.set_state(UserStates.waiting_balance_amount)
    await callback.message.answer(f"➖ REMOVER SALDO\n\nUsuário: {uid}\n\nInforme o valor a remover (R$):")
    await callback.answer()


@router.callback_query(F.data.startswith("usr_corr_"))
async def correct_balance_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    uid = int(callback.data.split("_")[-1])
    await state.update_data(target_user_id=uid, action_type="correct")
    await state.set_state(UserStates.waiting_balance_amount)
    await callback.message.answer(f"🔧 CORRIGIR SALDO\n\nUsuário: {uid}\n\nInforme o novo saldo (R$):")
    await callback.answer()


@router.message(UserStates.waiting_balance_amount)
async def receive_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        amount = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("❌ Valor inválido. Envie um número (ex.: 25.50).")
        return

    data = await state.get_data()
    action_type = data.get("action_type")

    if action_type in ("add", "remove") and amount <= 0:
        await message.answer("❌ O valor deve ser maior que zero.")
        return
    if action_type == "correct" and amount < 0:
        await message.answer("❌ O novo saldo não pode ser negativo.")
        return

    await state.update_data(amount=amount)
    await state.set_state(UserStates.waiting_balance_reason)
    await message.answer("📝 Informe a justificativa (obrigatório, mínimo 3 caracteres):")


@router.message(UserStates.waiting_balance_reason)
async def receive_reason_and_execute(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    action_type = data.get("action_type")
    uid = data.get("target_user_id")
    reason = message.text.strip() if message.text else ""

    if not reason or len(reason) < 3:
        await message.answer("❌ Justificativa muito curta. Envie pelo menos 3 caracteres.")
        return

    # ---------- Bloqueios ----------
    if action_type in ("block_perm", "block_temp"):
        blocked_until = (
            datetime.utcnow() + timedelta(days=30)
            if action_type == "block_temp" else None
        )
        async with AsyncSessionLocal() as session:
            user = await session.get(User, uid)
            if not user:
                await message.answer("Usuário não encontrado.")
                await state.clear()
                return
            user.is_blocked = True
            user.block_reason = reason
            user.blocked_by = message.from_user.id
            user.blocked_until = blocked_until
            await session.commit()
            await log_admin_action(message.from_user.id, action_type, {
                "user_id": uid, "reason": reason,
                "until": blocked_until.isoformat() if blocked_until else None,
            })
        await state.clear()
        await message.answer(f"✅ Usuário {uid} bloqueado com sucesso.")
        return

    # ---------- Financeiro ----------
    amount = data.get("amount")
    async with AsyncSessionLocal() as session:
        wallet = await get_user_wallet(session, uid)
        old_balance = wallet.balance

        if action_type == "add":
            wallet.balance += amount
            tx_amount = amount
            tx_type = "admin_add"
        elif action_type == "remove":
            if wallet.balance < amount:
                await message.answer(
                    f"❌ Saldo insuficiente. Saldo atual: R$ {wallet.balance:.2f}"
                )
                await state.clear()
                return
            wallet.balance -= amount
            tx_amount = -amount
            tx_type = "admin_remove"
        elif action_type == "correct":
            diff = amount - wallet.balance
            wallet.balance = amount
            tx_amount = diff
            tx_type = "admin_correct"
        else:
            await state.clear()
            return

        new_balance = wallet.balance
        tx = WalletTransaction(
            user_id=uid,
            amount=tx_amount,
            type=tx_type,
            description=f"Admin {message.from_user.id}: {reason}",
            balance_before=old_balance,
            balance_after=new_balance,
        )
        session.add(tx)
        await session.commit()
        await log_admin_action(message.from_user.id, tx_type, {
            "user_id": uid,
            "amount": tx_amount,
            "old": old_balance,
            "new": new_balance,
            "reason": reason,
        })

    await state.clear()
    await message.answer(
        f"✅ Operação concluída.\n\n"
        f"Saldo anterior: R$ {old_balance:.2f}\n"
        f"Saldo novo: R$ {new_balance:.2f}\n"
        f"Justificativa: {reason}"
    )
