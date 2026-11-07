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
from models import CartItem, Product, User, Order, AdminLog, BotSettings
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

PAGE_SIZE = 10
ABANDONED_HOURS = 24  # critério para "carrinho abandonado" no painel


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class CS(StatesGroup):
    search = State()
    change_qty = State()
    cleanup_hours = State()


# ============ MENU PRINCIPAL ============
@router.callback_query(F.data == "admin_cart")
async def cart_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    cutoff = datetime.utcnow() - timedelta(hours=ABANDONED_HOURS)
    async with AsyncSessionLocal() as s:
        total_items = (await s.execute(select(func.count(CartItem.id)))).scalar() or 0
        unique_users = (await s.execute(
            select(func.count(func.distinct(CartItem.user_id)))
        )).scalar() or 0
        abandoned_users = (await s.execute(
            select(func.count(func.distinct(CartItem.user_id)))
            .where(CartItem.updated_at < cutoff)
        )).scalar() or 0
        total_value = (await s.execute(
            select(func.coalesce(func.sum(CartItem.quantity * Product.price), 0))
            .select_from(CartItem)
            .join(Product, Product.id == CartItem.product_id)
        )).scalar() or 0.0
        total_qty = (await s.execute(
            select(func.coalesce(func.sum(CartItem.quantity), 0))
        )).scalar() or 0

    settings = await get_settings()

    text = (
        "◈ BELLAMY | GERENCIAMENTO DE CARRINHO\n\n"
        f"🛒 Itens nos carrinhos: {total_items}\n"
        f"📦 Unidades totais: {total_qty}\n"
        f"👥 Usuários com carrinho: {unique_users}\n"
        f"⏰ Carrinhos abandonados (+{ABANDONED_HOURS}h): {abandoned_users}\n"
        f"💰 Valor total em carrinhos: R$ {total_value:.2f}\n\n"
        f"⚙️ Limite por usuário: {getattr(settings, 'cart_max_items_per_user', 20)} itens\n"
        f"⏱️ Expiração: {getattr(settings, 'cart_expiry_hours', 48)}h\n"
        f"🧹 Limpeza automática: "
        f"{'🟢 Ativada' if getattr(settings, 'cart_auto_cleanup_enabled', True) else '🔴 Desativada'}\n\n"
        "👇 Selecione uma opção:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Carrinhos ativos", callback_data="cart_list_all_0")],
        [InlineKeyboardButton(
            text=f"⏰ Carrinhos abandonados ({abandoned_users})",
            callback_data="cart_list_abandoned_0")],
        [InlineKeyboardButton(text="🔎 Buscar por usuário", callback_data="cart_search")],
        [InlineKeyboardButton(text="🧹 Limpar carrinhos inativos", callback_data="cart_cleanup")],
        [InlineKeyboardButton(text="📈 Estatísticas", callback_data="cart_stats")],
        [InlineKeyboardButton(text="⚙️ Configurações de carrinho", callback_data="cart_config")],
        [InlineKeyboardButton(text="📥 Exportar relatório", callback_data="cart_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ LISTAGEM (agrupada por usuário) ============
async def build_cart_list(page: int, filter_type: str):
    cutoff = datetime.utcnow() - timedelta(hours=ABANDONED_HOURS)

    async with AsyncSessionLocal() as s:
        # Conta usuários únicos com carrinho no filtro
        cnt_q = select(func.count(func.distinct(CartItem.user_id)))
        if filter_type == "abandoned":
            cnt_q = cnt_q.where(CartItem.updated_at < cutoff)
        total = (await s.execute(cnt_q)).scalar() or 0

        # Paginação de usuários
        users_q = (
            select(CartItem.user_id, func.max(CartItem.updated_at).label("last"))
            .group_by(CartItem.user_id)
            .order_by(desc("last"))
        )
        if filter_type == "abandoned":
            users_q = users_q.having(func.max(CartItem.updated_at) < cutoff)
        users_q = users_q.offset(page * PAGE_SIZE).limit(PAGE_SIZE)
        rows = (await s.execute(users_q)).all()

        if not rows:
            return [], total, {}

        user_ids = [r[0] for r in rows]

        users_db = (await s.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        users_map = {u.id: u for u in users_db}

        # Resumo por usuário
        summary_q = (
            select(
                CartItem.user_id,
                func.count(CartItem.id).label("items"),
                func.coalesce(func.sum(CartItem.quantity), 0).label("qty"),
                func.coalesce(func.sum(CartItem.quantity * Product.price), 0).label("value"),
            )
            .select_from(CartItem)
            .join(Product, Product.id == CartItem.product_id)
            .where(CartItem.user_id.in_(user_ids))
            .group_by(CartItem.user_id)
        )
        summary_rows = (await s.execute(summary_q)).all()
        summary_map = {
            r[0]: {"items": r[1], "qty": r[2], "value": r[3]} for r in summary_rows
        }

    result = []
    for uid, last in rows:
        result.append({
            "user": users_map.get(uid),
            "user_id": uid,
            "last_update": last,
            "items": summary_map.get(uid, {"items": 0, "qty": 0, "value": 0.0}),
        })
    return result, total, users_map


@router.callback_query(F.data.startswith("cart_list_"))
async def list_carts(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    parts = cb.data.split("_")
    filter_type = parts[2]
    page = int(parts[3])

    carts, total, _ = await build_cart_list(page, filter_type)

    titles = {
        "all": "CARRINHOS ATIVOS",
        "abandoned": "CARRINHOS ABANDONADOS",
    }
    text = f"◈ BELLAMY | {titles.get(filter_type, filter_type.upper())}\n\n"
    text += f"Total de usuários: {total} | Página {page + 1}\n\n"

    if not carts:
        text += "Nenhum carrinho encontrado."
    else:
        for c in carts:
            u = c["user"]
            uname = (u.full_name if u else f"User {c['user_id']}")[:25]
            uhandle = f"@{u.username}" if u and u.username else "—"
            horas = int((datetime.utcnow() - c["last_update"]).total_seconds() / 3600)
            text += (
                f"👤 {uname} ({uhandle})\n"
                f"   🆔 <code>{c['user_id']}</code>\n"
                f"   🛒 {c['items']['items']} item(s) / "
                f"{c['items']['qty']} un — R$ {c['items']['value']:.2f}\n"
                f"   ⏱️ Última atualização: {horas}h atrás\n\n"
            )

    buttons = []
    for c in carts:
        u = c["user"]
        label = (u.full_name if u else f"User {c['user_id']}")[:24]
        buttons.append([InlineKeyboardButton(
            text=f"👤 {label} — R$ {c['items']['value']:.2f}",
            callback_data=f"cart_view_{c['user_id']}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"cart_list_{filter_type}_{page - 1}"
        ))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"cart_list_{filter_type}_{page + 1}"
        ))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_cart")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ DETALHE DO CARRINHO DO USUÁRIO ============
async def render_user_cart(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as s:
        user = await s.get(User, user_id)
        items = (await s.execute(
            select(CartItem)
            .where(CartItem.user_id == user_id)
            .order_by(CartItem.added_at)
        )).scalars().all()

        prods = {}
        if items:
            pids = list({i.product_id for i in items})
            prods = {
                p.id: p for p in
                (await s.execute(select(Product).where(Product.id.in_(pids)))).scalars().all()
            }

        order_count = (await s.execute(
            select(func.count(Order.id)).where(
                Order.user_id == user_id, Order.status == "completed"
            )
        )).scalar() or 0

    if not user:
        return (
            "❌ Usuário não encontrado.",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_cart")]
            ]),
        )

    total = 0.0
    total_qty = 0
    lines = []
    buttons = []
    for i, item in enumerate(items, 1):
        p = prods.get(item.product_id)
        if not p:
            lines.append(f"{i}. ⚠️ Produto #{item.product_id} (excluído) — {item.quantity}x")
            continue
        subtotal = p.price * item.quantity
        total += subtotal
        total_qty += item.quantity
        lines.append(
            f"{i}. {p.name}\n"
            f"   {item.quantity} × R$ {p.price:.2f} = R$ {subtotal:.2f}\n"
            f"   📦 Estoque: {p.stock}"
        )
        buttons.append([InlineKeyboardButton(
            text=f"📦 {i}. {p.name[:28]} — {item.quantity}x",
            callback_data=f"cart_item_{item.id}",
        )])

    uname = user.full_name or f"User {user.id}"
    uhandle = f"@{user.username}" if user.username else "—"

    text = (
        f"◈ BELLAMY | CARRINHO DO USUÁRIO\n\n"
        f"👤 {uname}\n"
        f"💠 {uhandle}\n"
        f"🆔 <code>{user.id}</code>\n"
        f"🛒 Compras concluídas: {order_count}\n\n"
        f"🛍️ ITENS NO CARRINHO ({len(items)})\n\n"
    )
    text += "\n\n".join(lines) if lines else "Carrinho vazio."
    text += f"\n\n━━━━━━━━━━━━━━\n📦 Total de itens: {len(items)}\n"
    text += f"🔢 Unidades: {total_qty}\n💰 Valor total: R$ {total:.2f}"

    if items:
        buttons.append([InlineKeyboardButton(
            text="🗑️ Limpar carrinho",
            callback_data=f"cart_clear_{user_id}",
        )])
    buttons.append([InlineKeyboardButton(
        text="👤 Ver usuário", callback_data=f"usr_view_{user_id}",
    )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="cart_list_all_0")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("cart_view_"))
async def view_user_cart(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split("_")[-1])
    text, kb = await render_user_cart(uid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ DETALHE DE UM ITEM ============
async def render_cart_item(item_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as s:
        item = await s.get(CartItem, item_id)
        if not item:
            return (
                "❌ Item não encontrado.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_cart")]
                ]),
            )
        p = await s.get(Product, item.product_id)
        user = await s.get(User, item.user_id)

    pname = p.name if p else f"Produto #{item.product_id}"
    price = p.price if p else 0.0
    subtotal = price * item.quantity
    uname = (user.full_name if user else f"User {item.user_id}")[:30]

    horas = int((datetime.utcnow() - item.added_at).total_seconds() / 3600)

    text = (
        f"◈ BELLAMY | ITEM DO CARRINHO\n\n"
        f"👤 Usuário: {uname}\n"
        f"🆔 User ID: <code>{item.user_id}</code>\n\n"
        f"📦 Produto: {pname}\n"
        f"🔢 Quantidade atual: {item.quantity}\n"
        f"💰 Preço unitário: R$ {price:.2f}\n"
        f"💳 Subtotal: R$ {subtotal:.2f}\n"
    )
    if p:
        text += f"📦 Estoque disponível: {p.stock}\n"
    text += (
        f"\n📅 Adicionado: {item.added_at.strftime('%d/%m/%Y %H:%M')}\n"
        f"⏱️ Há {horas}h no carrinho\n"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✏️ Alterar quantidade",
            callback_data=f"cart_qty_{item.id}")],
        [InlineKeyboardButton(
            text="🗑️ Remover do carrinho",
            callback_data=f"cart_remove_{item.id}")],
        [InlineKeyboardButton(
            text="↩️ Voltar",
            callback_data=f"cart_view_{item.user_id}")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("cart_item_"))
async def view_cart_item(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    item_id = int(cb.data.split("_")[-1])
    text, kb = await render_cart_item(item_id)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ ALTERAR QUANTIDADE ============
@router.callback_query(F.data.startswith("cart_qty_"))
async def change_qty_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    item_id = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        item = await s.get(CartItem, item_id)
        if not item:
            await cb.answer("Item não encontrado.", show_alert=True)
            return
        p = await s.get(Product, item.product_id)
        stock = p.stock if p else 0
        pname = p.name if p else f"Produto #{item.product_id}"

    await state.update_data(cart_item_id=item_id)
    await state.set_state(CS.change_qty)
    await cb.message.answer(
        f"✏️ ALTERAR QUANTIDADE\n\n"
        f"Produto: {pname}\n"
        f"Quantidade atual: {item.quantity}\n"
        f"Estoque disponível: {stock}\n\n"
        f"Informe a nova quantidade (1 a 999):"
    )
    await cb.answer()


@router.message(CS.change_qty)
async def change_qty_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    try:
        qty = int(m.text.strip())
        if qty < 1 or qty > 999:
            raise ValueError
    except ValueError:
        await m.answer("❌ Quantidade inválida (1 a 999).")
        return

    data = await state.get_data()
    item_id = data.get("cart_item_id")
    if not item_id:
        await state.clear()
        await m.answer("Sessão expirada.")
        return

    async with AsyncSessionLocal() as s:
        item = await s.get(CartItem, item_id)
        if not item:
            await state.clear()
            await m.answer("❌ Item não encontrado.")
            return

        old_qty = item.quantity
        item.quantity = qty
        item.updated_at = datetime.utcnow()
        user_id = item.user_id
        await s.commit()

    await log_admin(m.from_user.id, "cart_change_qty", {
        "item_id": item_id, "old": old_qty, "new": qty, "user_id": user_id,
    })
    await state.clear()

    text, kb = await render_cart_item(item_id)
    await m.answer(f"✅ Quantidade alterada de {old_qty} para {qty}.")
    await m.answer(text, reply_markup=kb)


# ============ REMOVER ITEM ============
@router.callback_query(F.data.startswith("cart_remove_"))
async def remove_item(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    item_id = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        item = await s.get(CartItem, item_id)
        if not item:
            await cb.answer("Item não encontrado.", show_alert=True)
            return
        user_id = item.user_id
        product_id = item.product_id
        qty = item.quantity
        await s.delete(item)
        await s.commit()

    await log_admin(cb.from_user.id, "cart_remove_item", {
        "item_id": item_id, "user_id": user_id,
        "product_id": product_id, "qty": qty,
    })

    text, kb = await render_user_cart(user_id)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("✅ Item removido do carrinho.")


# ============ LIMPAR CARRINHO ============
@router.callback_query(F.data.startswith("cart_clear_"))
async def clear_cart_prompt(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    uid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        count = (await s.execute(
            select(func.count(CartItem.id)).where(CartItem.user_id == uid)
        )).scalar() or 0

    await cb.message.edit_text(
        f"🗑️ LIMPAR CARRINHO\n\n"
        f"Usuário: <code>{uid}</code>\n"
        f"Itens no carrinho: {count}\n\n"
        f"⚠️ Todos os itens serão removidos do carrinho deste usuário.\n"
        f"O estoque NÃO será alterado.\n\n"
        f"Confirmar?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Sim, limpar", callback_data=f"cart_clear_ok_{uid}"
            )],
            [InlineKeyboardButton(
                text="↩️ Cancelar", callback_data=f"cart_view_{uid}"
            )],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cart_clear_ok_"))
async def clear_cart_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    uid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        items = (await s.execute(
            select(CartItem).where(CartItem.user_id == uid)
        )).scalars().all()
        removed = len(items)
        for item in items:
            await s.delete(item)
        await s.commit()

    await log_admin(cb.from_user.id, "cart_clear_user", {
        "user_id": uid, "items_removed": removed,
    })

    text, kb = await render_user_cart(uid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer(f"✅ {removed} item(ns) removido(s).")


# ============ BUSCA POR USUÁRIO ============
@router.callback_query(F.data == "cart_search")
async def search_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CS.search)
    await cb.message.answer(
        "🔎 BUSCAR CARRINHO\n\n"
        "Envie:\n"
        "• <b>ID do Telegram</b> do usuário\n"
        "• <b>@username</b>\n"
        "• <b>Nome</b> do usuário\n\n"
        "Digite /cancelar para abortar."
    )
    await cb.answer()


@router.message(CS.search)
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
        if q.isdigit():
            uid = int(q)
            user = await s.get(User, uid)
            users = [user] if user else []
        else:
            clean = q.lstrip("@")
            stmt = select(User).where(or_(
                User.username.ilike(f"%{clean}%"),
                User.full_name.ilike(f"%{q}%"),
            )).limit(20)
            users = (await s.execute(stmt)).scalars().all()

        if not users:
            await m.answer("❌ Usuário não encontrado.")
            return

        results = []
        for u in users:
            count = (await s.execute(
                select(func.count(CartItem.id)).where(CartItem.user_id == u.id)
            )).scalar() or 0
            if count > 0:
                results.append((u, count))

    if not results:
        await m.answer("❌ Nenhum desses usuários tem carrinho ativo.")
        return

    text = f"🔎 RESULTADOS: {len(results)} usuário(s) com carrinho\n\n"
    buttons = []
    for u, count in results:
        text += f"👤 {u.full_name} (@{u.username or '—'}) — {count} item(s)\n"
        buttons.append([InlineKeyboardButton(
            text=f"🛒 {u.full_name[:30]} — {count} item(s)",
            callback_data=f"cart_view_{u.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_cart")])

    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ============ LIMPEZA DE CARRINHOS INATIVOS ============
@router.callback_query(F.data == "cart_cleanup")
async def cleanup_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    await state.set_state(CS.cleanup_hours)
    await cb.message.answer(
        "🧹 LIMPEZA DE CARRINHOS INATIVOS\n\n"
        "Informe há quantas horas um carrinho deve estar sem atualização "
        "para ser removido automaticamente (ex.: 48):"
    )
    await cb.answer()


@router.message(CS.cleanup_hours)
async def cleanup_execute(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    try:
        hours = int(m.text.strip())
        if hours < 1 or hours > 720:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido (1 a 720 horas).")
        return

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    async with AsyncSessionLocal() as s:
        items = (await s.execute(
            select(CartItem).where(CartItem.updated_at < cutoff)
        )).scalars().all()
        affected_users = len({i.user_id for i in items})
        removed = len(items)
        for item in items:
            await s.delete(item)
        await s.commit()

    await log_admin(m.from_user.id, "cart_bulk_cleanup", {
        "hours": hours, "items_removed": removed, "users_affected": affected_users,
    })
    await state.clear()

    await m.answer(
        f"✅ LIMPEZA CONCLUÍDA\n\n"
        f"⏱️ Critério: +{hours}h sem atualização\n"
        f"🗑️ Itens removidos: {removed}\n"
        f"👥 Usuários afetados: {affected_users}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_cart")]
        ]),
    )


# ============ ESTATÍSTICAS ============
@router.callback_query(F.data == "cart_stats")
async def stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    now = datetime.utcnow()
    cutoff_24 = now - timedelta(hours=24)
    cutoff_48 = now - timedelta(hours=48)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async with AsyncSessionLocal() as s:
        total_items = (await s.execute(select(func.count(CartItem.id)))).scalar() or 0
        total_users = (await s.execute(
            select(func.count(func.distinct(CartItem.user_id)))
        )).scalar() or 0
        abandoned_24 = (await s.execute(
            select(func.count(func.distinct(CartItem.user_id)))
            .where(CartItem.updated_at < cutoff_24)
        )).scalar() or 0
        abandoned_48 = (await s.execute(
            select(func.count(func.distinct(CartItem.user_id)))
            .where(CartItem.updated_at < cutoff_48)
        )).scalar() or 0

        total_value = (await s.execute(
            select(func.coalesce(func.sum(CartItem.quantity * Product.price), 0))
            .select_from(CartItem)
            .join(Product, Product.id == CartItem.product_id)
        )).scalar() or 0.0
        total_qty = (await s.execute(
            select(func.coalesce(func.sum(CartItem.quantity), 0))
        )).scalar() or 0

        avg_items = total_items / total_users if total_users else 0.0

        orders_month = (await s.execute(
            select(func.count(Order.id)).where(
                Order.status == "completed",
                Order.created_at >= month_start,
            )
        )).scalar() or 0

        top_products = (await s.execute(
            select(Product.name, func.sum(CartItem.quantity).label("qty"))
            .join(CartItem, CartItem.product_id == Product.id)
            .group_by(Product.id, Product.name)
            .order_by(desc("qty"))
            .limit(5)
        )).all()

    # Conversão estimada = pedidos concluídos no mês / usuários com carrinho
    conv = (orders_month / total_users * 100) if total_users > 0 else 0.0

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DE CARRINHO\n\n"
        f"🛒 Total de itens: {total_items}\n"
        f"📦 Unidades totais: {total_qty}\n"
        f"👥 Usuários com carrinho: {total_users}\n"
        f"📊 Média de itens/usuário: {avg_items:.2f}\n\n"
        f"💰 Valor total em carrinhos: R$ {total_value:.2f}\n\n"
        f"⏰ Abandonados +24h: {abandoned_24}\n"
        f"⏰ Abandonados +48h: {abandoned_48}\n"
        f"🟢 Pedidos concluídos este mês: {orders_month}\n"
        f"📈 Conversão estimada: {conv:.1f}%\n\n"
    )
    if top_products:
        text += "🏆 TOP 5 PRODUTOS EM CARRINHOS\n"
        for i, (name, qty) in enumerate(top_products, 1):
            text += f"{i}. {name} — {qty} un\n"

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="cart_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_cart")],
    ]))
    await cb.answer()


# ============ CONFIGURAÇÕES ============
async def config_view() -> tuple[str, InlineKeyboardMarkup]:
    s = await get_settings(force=True)
    expiry = getattr(s, "cart_expiry_hours", 48)
    max_items = getattr(s, "cart_max_items_per_user", 20)
    auto_cleanup = getattr(s, "cart_auto_cleanup_enabled", True)

    text = (
        "◈ BELLAMY | CONFIGURAÇÕES DE CARRINHO\n\n"
        f"⏱️ Expiração de carrinho: {expiry}h\n"
        f"🔢 Limite de itens por usuário: {max_items}\n"
        f"🧹 Limpeza automática: {'🟢 Ativada' if auto_cleanup else '🔴 Desativada'}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱️ Alterar expiração", callback_data="cart_cfg_expiry")],
        [InlineKeyboardButton(text="🔢 Alterar limite de itens", callback_data="cart_cfg_max")],
        [InlineKeyboardButton(
            text=("🔴 Desativar limpeza" if auto_cleanup else "🟢 Ativar limpeza"),
            callback_data="cart_cfg_toggle_cleanup")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_cart")],
    ])
    return text, kb


@router.callback_query(F.data == "cart_config")
async def config(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


class CartCfg(StatesGroup):
    expiry = State()
    max_items = State()


@router.callback_query(F.data == "cart_cfg_expiry")
async def cfg_expiry_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CartCfg.expiry)
    await cb.message.answer("Informe a expiração do carrinho em horas (1-720):")
    await cb.answer()


@router.message(CartCfg.expiry)
async def cfg_expiry_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        v = int(m.text.strip())
        if v < 1 or v > 720:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido (1-720).")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.cart_expiry_hours = v
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "cart_cfg_expiry", {"new": v})
    await state.clear()
    text, kb = await config_view()
    await m.answer(f"✅ Expiração definida para {v}h.", reply_markup=kb)


@router.callback_query(F.data == "cart_cfg_max")
async def cfg_max_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CartCfg.max_items)
    await cb.message.answer("Informe o limite de itens por usuário (1-100):")
    await cb.answer()


@router.message(CartCfg.max_items)
async def cfg_max_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        v = int(m.text.strip())
        if v < 1 or v > 100:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido (1-100).")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.cart_max_items_per_user = v
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "cart_cfg_max", {"new": v})
    await state.clear()
    text, kb = await config_view()
    await m.answer(f"✅ Limite definido para {v} itens.", reply_markup=kb)


@router.callback_query(F.data == "cart_cfg_toggle_cleanup")
async def cfg_toggle_cleanup(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        current = getattr(bs, "cart_auto_cleanup_enabled", True)
        bs.cart_auto_cleanup_enabled = not current
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "cart_cfg_toggle_cleanup", {"new": not current})
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


# ============ EXPORTAÇÃO ============
@router.callback_query(F.data == "cart_export")
async def export_report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        items = (await s.execute(
            select(CartItem).order_by(CartItem.user_id, CartItem.added_at)
        )).scalars().all()

        users, prods = {}, {}
        if items:
            uids = list({i.user_id for i in items})
            pids = list({i.product_id for i in items})
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}
            p = (await s.execute(select(Product).where(Product.id.in_(pids)))).scalars().all()
            prods = {x.id: x for x in p}

    lines = []
    lines.append("═" * 60)
    lines.append("BELLAMY | RELATÓRIO DE CARRINHOS")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append(f"Total de itens: {len(items)}")
    lines.append("═" * 60)
    lines.append("")

    current_user = None
    user_total = 0.0
    for item in items:
        if item.user_id != current_user:
            if current_user is not None:
                lines.append(f"  ─ Subtotal usuário: R$ {user_total:.2f}")
                lines.append("")
            current_user = item.user_id
            user_total = 0.0
            u = users.get(item.user_id)
            uname = u.full_name if u else "Desconhecido"
            uhandle = f"@{u.username}" if u and u.username else "—"
            lines.append(f"👤 {uname} ({uhandle}) — ID {item.user_id}")

        p = prods.get(item.product_id)
        pname = p.name if p else f"Produto #{item.product_id}"
        price = p.price if p else 0.0
        subtotal = price * item.quantity
        user_total += subtotal
        lines.append(
            f"  • {pname} | {item.quantity}x R$ {price:.2f} = R$ {subtotal:.2f} "
            f"| {item.added_at.strftime('%d/%m %H:%M')}"
        )

    if current_user is not None:
        lines.append(f"  ─ Subtotal usuário: R$ {user_total:.2f}")

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"carrinhos_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption=f"📊 <b>Relatório de Carrinhos</b>\n\nTotal de itens: {len(items)}",
    )
    await cb.answer("Relatório gerado.")
