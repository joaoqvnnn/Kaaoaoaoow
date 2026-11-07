from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, and_
from datetime import datetime
from io import BytesIO

from bot import dp
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import Product, StockMovement, AdminLog, BotSettings
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

HIST_PAGE_SIZE = 10


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class SS(StatesGroup):
    bulk_input = State()
    bulk_confirm = State()
    set_max = State()
    set_reserve_minutes = State()


# ============ MENU PRINCIPAL ============
def stock_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Visão geral", callback_data="stk_overview")],
        [InlineKeyboardButton(text="📜 Histórico de movimentos", callback_data="stk_hist_0_all")],
        [InlineKeyboardButton(text="⚠️ Estoque baixo", callback_data="stk_low")],
        [InlineKeyboardButton(text="🔴 Produtos esgotados", callback_data="stk_out")],
        [InlineKeyboardButton(text="📦 Abastecimento em lote", callback_data="stk_bulk")],
        [InlineKeyboardButton(text="🔒 Gerenciar reservas", callback_data="stk_reserves")],
        [InlineKeyboardButton(text="🔔 Configurar alertas", callback_data="stk_alertcfg")],
        [InlineKeyboardButton(text="📊 Relatório de estoque", callback_data="stk_report")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_products")],
    ])


@router.callback_query(F.data == "admin_stock")
async def stock_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await cb.message.edit_text(
        "◈ BELLAMY | GERENCIAMENTO DE ESTOQUE\n\n"
        "Centro completo de controle de estoque, "
        "movimentações e reservas.\n\n"
        "👇 Selecione uma opção:",
        reply_markup=stock_menu_kb(),
    )
    await cb.answer()


# ============ VISÃO GERAL ============
@router.callback_query(F.data == "stk_overview")
async def stock_overview(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    settings = await get_settings(force=True)
    threshold = settings.stock_low_threshold

    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(Product.id)))).scalar() or 0
        active = (await s.execute(select(func.count(Product.id)).where(Product.is_active == True))).scalar() or 0
        out = (await s.execute(select(func.count(Product.id)).where(Product.stock <= 0))).scalar() or 0
        low = (await s.execute(select(func.count(Product.id)).where(
            Product.stock > 0, Product.stock <= threshold
        ))).scalar() or 0
        healthy = (await s.execute(select(func.count(Product.id)).where(Product.stock > threshold))).scalar() or 0
        total_units = (await s.execute(select(func.coalesce(func.sum(Product.stock), 0)))).scalar() or 0
        reserved_units = (await s.execute(select(func.coalesce(func.sum(Product.reserved_stock), 0)))).scalar() or 0

    available = total_units - reserved_units
    text = (
        "◈ BELLAMY | VISÃO GERAL DO ESTOQUE\n\n"
        f"📦 Produtos cadastrados: {total}\n"
        f"🟢 Ativos: {active}\n"
        f"🔴 Esgotados: {out}\n"
        f"⚠️ Estoque baixo (≤ {threshold}): {low}\n"
        f"✅ Estoque saudável: {healthy}\n\n"
        f"📊 Unidades em estoque: {total_units}\n"
        f"🔒 Unidades reservadas: {reserved_units}\n"
        f"💎 Unidades disponíveis: {available}\n"
    )
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="stk_overview")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_stock")],
    ]))
    await cb.answer()


# ============ HISTÓRICO DE MOVIMENTOS ============
async def build_history(page: int, ftype: str):
    async with AsyncSessionLocal() as s:
        stmt = select(StockMovement)
        cnt = select(func.count(StockMovement.id))
        if ftype != "all":
            stmt = stmt.where(StockMovement.movement_type == ftype)
            cnt = cnt.where(StockMovement.movement_type == ftype)
        total = (await s.execute(cnt)).scalar() or 0
        stmt = stmt.order_by(desc(StockMovement.created_at)).offset(page * HIST_PAGE_SIZE).limit(HIST_PAGE_SIZE)
        items = (await s.execute(stmt)).scalars().all()

        product_names = {}
        if items:
            pids = list({i.product_id for i in items})
            prods = (await s.execute(select(Product).where(Product.id.in_(pids)))).scalars().all()
            product_names = {p.id: p.name for p in prods}

    return items, total, product_names


@router.callback_query(F.data.startswith("stk_hist_"))
async def history_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    parts = cb.data.split("_")
    page = int(parts[2])
    ftype = parts[3] if len(parts) > 3 else "all"

    items, total, names = await build_history(page, ftype)

    labels = {
        "all": "TODOS", "restock": "ABASTECIMENTOS",
        "withdraw": "RETIRADAS", "sale": "VENDAS", "adjustment": "AJUSTES",
    }
    icons = {"restock": "📥", "withdraw": "📤", "sale": "🛒", "adjustment": "🔧"}

    text = f"📜 HISTÓRICO — {labels.get(ftype, ftype.upper())}\n\n"
    text += f"Total: {total} | Página {page + 1}\n\n"
    if not items:
        text += "Nenhum registro."
    else:
        for m in items:
            icon = icons.get(m.movement_type, "•")
            pname = names.get(m.product_id, f"Produto #{m.product_id}")
            text += (
                f"{icon} {m.created_at.strftime('%d/%m %H:%M')} — {pname}\n"
                f"   {m.quantity:+d} un | {m.stock_before} → {m.stock_after}\n"
                f"   Admin {m.admin_id}"
                + (f" | {m.reason}" if m.reason else "") + "\n\n"
            )

    buttons = []
    filter_row = []
    for ft, lbl in [("all", "📋"), ("restock", "📥"), ("withdraw", "📤"), ("sale", "🛒")]:
        filter_row.append(InlineKeyboardButton(
            text=f"{lbl} {labels[ft]}",
            callback_data=f"stk_hist_0_{ft}",
        ))
    buttons.append(filter_row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"stk_hist_{page - 1}_{ftype}"))
    if (page + 1) * HIST_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"stk_hist_{page + 1}_{ftype}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_stock")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ ESTOQUE BAIXO ============
@router.callback_query(F.data == "stk_low")
async def low_stock(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    settings = await get_settings(force=True)
    threshold = settings.stock_low_threshold
    async with AsyncSessionLocal() as s:
        stmt = (
            select(Product)
            .where(Product.stock > 0)
            .where(Product.stock <= threshold)
            .order_by(Product.stock.asc())
            .limit(30)
        )
        items = (await s.execute(stmt)).scalars().all()

    text = f"⚠️ ESTOQUE BAIXO (≤ {threshold})\n\n"
    if not items:
        text += "✅ Nenhum produto com estoque baixo."
    else:
        text += f"{len(items)} produto(s):\n\n"
        for p in items:
            text += f"• {p.name} — {p.stock} un\n"

    buttons = []
    for p in items:
        buttons.append([InlineKeyboardButton(
            text=f"📥 Abastecer {p.name[:28]} (estoque: {p.stock})",
            callback_data=f"prd_restock_{p.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_stock")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ ESGOTADOS ============
@router.callback_query(F.data == "stk_out")
async def out_of_stock(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        stmt = (
            select(Product)
            .where(Product.stock <= 0)
            .order_by(Product.created_at.desc())
            .limit(30)
        )
        items = (await s.execute(stmt)).scalars().all()

    text = "🔴 PRODUTOS ESGOTADOS\n\n"
    if not items:
        text += "✅ Nenhum produto esgotado."
    else:
        text += f"{len(items)} produto(s):\n\n"
        for p in items:
            status = "🟢" if p.is_active else "🔴"
            text += f"{status} {p.name} — R$ {p.price:.2f}\n"

    buttons = []
    for p in items:
        buttons.append([InlineKeyboardButton(
            text=f"📥 Abastecer {p.name[:32]}",
            callback_data=f"prd_restock_{p.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_stock")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ ABASTECIMENTO EM LOTE ============
@router.callback_query(F.data == "stk_bulk")
async def bulk_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(SS.bulk_input)
    await cb.message.answer(
        "📦 ABASTECIMENTO EM LOTE\n\n"
        "Envie uma lista com um produto por linha no formato:\n\n"
        "<code>ID:QUANTIDADE</code>\n\n"
        "Exemplo:\n"
        "<code>1:50\n2:100\n5:25</code>\n\n"
        "Digite /cancelar para abortar."
    )
    await cb.answer()


@router.message(SS.bulk_input)
async def bulk_parse(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.text.strip().lower() == "/cancelar":
        await state.clear()
        await m.answer("Operação cancelada.")
        return

    entries = []
    errors = []
    for line in m.text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_s, qty_s = line.split(":")
            pid = int(pid_s.strip())
            qty = int(qty_s.strip())
            if qty <= 0:
                raise ValueError
            entries.append((pid, qty))
        except Exception:
            errors.append(line)

    if not entries:
        await m.answer("❌ Nenhuma entrada válida. Use o formato ID:QUANTIDADE.")
        return

    async with AsyncSessionLocal() as s:
        pids = [p for p, _ in entries]
        prods = (await s.execute(select(Product).where(Product.id.in_(pids)))).scalars().all()
        prod_map = {p.id: p for p in prods}

    valid = []
    invalid = list(errors)
    for pid, qty in entries:
        if pid not in prod_map:
            invalid.append(f"{pid}:{qty} (produto inexistente)")
        else:
            valid.append((pid, qty))

    if not valid:
        await m.answer("❌ Nenhum produto válido encontrado.")
        return

    await state.update_data(bulk_entries=valid)
    await state.set_state(SS.bulk_confirm)

    text = "📦 CONFIRMAR ABASTECIMENTO EM LOTE\n\n"
    for pid, qty in valid:
        p = prod_map[pid]
        text += f"• #{pid} {p.name}: +{qty} (atual: {p.stock} → {p.stock + qty})\n"
    if invalid:
        text += f"\n⚠️ Linhas ignoradas:\n" + "\n".join(f"  {x}" for x in invalid)
    text += "\n\nConfirmar todas as operações?"

    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirmar todas", callback_data="stk_bulk_ok")],
        [InlineKeyboardButton(text="❌ Cancelar", callback_data="stk_bulk_cancel")],
    ]))


@router.callback_query(F.data == "stk_bulk_cancel")
async def bulk_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Operação cancelada.")
    await cb.answer()


@router.callback_query(F.data == "stk_bulk_ok")
async def bulk_execute(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    data = await state.get_data()
    entries = data.get("bulk_entries", [])
    if not entries:
        await state.clear()
        await cb.answer("Sessão expirada.", show_alert=True)
        return

    applied = []
    async with AsyncSessionLocal() as s:
        for pid, qty in entries:
            p = await s.get(Product, pid)
            if not p:
                continue
            before = p.stock
            p.stock += qty
            after = p.stock
            s.add(StockMovement(
                product_id=pid,
                admin_id=cb.from_user.id,
                movement_type="restock",
                quantity=qty,
                stock_before=before,
                stock_after=after,
                reason="Abastecimento em lote",
            ))
            applied.append((p.name, qty, before, after))
        await s.commit()

    await log_admin(cb.from_user.id, "stock_bulk_restock", {
        "entries": [{"product_id": p, "qty": q} for p, q in entries],
    })
    await state.clear()

    text = "✅ ABASTECIMENTO EM LOTE CONCLUÍDO\n\n"
    for name, qty, before, after in applied:
        text += f"• {name}: +{qty} ({before} → {after})\n"

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_stock")],
    ]))
    await cb.answer("Concluído.")


# ============ RESERVAS ============
@router.callback_query(F.data == "stk_reserves")
async def reserves_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    settings = await get_settings(force=True)

    async with AsyncSessionLocal() as s:
        stmt = select(Product).where(Product.reserved_stock > 0).limit(30)
        reserved = (await s.execute(stmt)).scalars().all()
        total_reserved = (await s.execute(
            select(func.coalesce(func.sum(Product.reserved_stock), 0))
        )).scalar() or 0

    text = (
        "🔒 GERENCIAMENTO DE RESERVAS\n\n"
        f"Status: {'🟢 Ativado' if settings.stock_reserve_enabled else '🔴 Desativado'}\n"
        f"Tempo de reserva: {settings.stock_reserve_minutes} minutos\n"
        f"Total de unidades reservadas: {total_reserved}\n\n"
    )
    if reserved:
        text += "Produtos com reservas:\n"
        for p in reserved:
            text += f"• {p.name} — {p.reserved_stock} reservada(s)\n"
    else:
        text += "Nenhuma reserva ativa."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar reservas" if settings.stock_reserve_enabled else "🟢 Ativar reservas"),
            callback_data="stk_reserve_toggle")],
        [InlineKeyboardButton(text="⏱️ Alterar tempo de reserva", callback_data="stk_reserve_minutes")],
        [InlineKeyboardButton(text="🔓 Liberar todas as reservas", callback_data="stk_reserve_clear")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_stock")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "stk_reserve_toggle")
async def reserve_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.stock_reserve_enabled = not bs.stock_reserve_enabled
        new = bs.stock_reserve_enabled
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "reserve_toggle", {"new": new})
    await reserves_view(cb)


@router.callback_query(F.data == "stk_reserve_minutes")
async def reserve_minutes_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(SS.set_reserve_minutes)
    await cb.message.answer("Informe o tempo de reserva em minutos (1-1440):")
    await cb.answer()


@router.message(SS.set_reserve_minutes)
async def reserve_minutes_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        v = int(m.text.strip())
        if v < 1 or v > 1440:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido (1-1440).")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.stock_reserve_minutes = v
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "reserve_minutes", {"new": v})
    await state.clear()
    await m.answer(f"✅ Tempo de reserva atualizado para {v} minutos.")


@router.callback_query(F.data == "stk_reserve_clear")
async def reserve_clear(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await cb.message.edit_text(
        "🔓 LIBERAR TODAS AS RESERVAS\n\n"
        "Todos os `reserved_stock` serão zerados. O estoque real NÃO muda.\n\n"
        "Confirmar?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Sim, liberar", callback_data="stk_reserve_clear_ok")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data="stk_reserves")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data == "stk_reserve_clear_ok")
async def reserve_clear_ok(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        prods = (await s.execute(select(Product).where(Product.reserved_stock > 0))).scalars().all()
        affected = 0
        for p in prods:
            p.reserved_stock = 0
            affected += 1
        await s.commit()
    await log_admin(cb.from_user.id, "reserve_clear_all", {"affected": affected})
    await cb.message.edit_text(
        f"✅ Reservas liberadas em {affected} produto(s).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_stock")],
        ]),
    )
    await cb.answer()


# ============ CONFIGURAR ALERTAS ============
async def alertcfg_view() -> tuple[str, InlineKeyboardMarkup]:
    s = await get_settings(force=True)
    text = (
        "🔔 CONFIGURAÇÕES DE ALERTAS DE ESTOQUE\n\n"
        f"🔔 Alertas de estoque: {'🟢 Ativados' if s.stock_alert_enabled else '🔴 Desativados'}\n"
        f"⚠️ Limite global de estoque baixo: {s.stock_low_threshold}\n"
        f"📦 Estoque máximo global: {s.stock_max_global if s.stock_max_global > 0 else 'sem limite'}\n"
        f"📢 Aviso de reposição no canal: {'🟢 Ativado' if s.notify_restock_enabled else '🔴 Desativado'}\n"
        f"🛒 Vendas sem estoque: {'🟢 Permitidas' if s.allow_out_of_stock_sales else '🔴 Bloqueadas'}\n"
        f"🔒 Reserva temporária: {'🟢 Ativada' if s.stock_reserve_enabled else '🔴 Desativada'}\n"
        f"⏱️ Tempo de reserva: {s.stock_reserve_minutes} minutos\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar alertas" if s.stock_alert_enabled else "🟢 Ativar alertas"),
            callback_data="stk_cfg_t_alert")],
        [InlineKeyboardButton(text="⚠️ Alterar limite de estoque baixo", callback_data="stk_cfg_lim")],
        [InlineKeyboardButton(text="📦 Alterar estoque máximo", callback_data="stk_cfg_max")],
        [InlineKeyboardButton(
            text=("🔴 Desativar aviso de reposição" if s.notify_restock_enabled else "🟢 Ativar aviso de reposição"),
            callback_data="stk_cfg_t_restock")],
        [InlineKeyboardButton(
            text=("🔴 Bloquear venda sem estoque" if s.allow_out_of_stock_sales else "🟢 Permitir venda sem estoque"),
            callback_data="stk_cfg_t_oos")],
        [InlineKeyboardButton(
            text=("🔴 Desativar reserva" if s.stock_reserve_enabled else "🟢 Ativar reserva"),
            callback_data="stk_cfg_t_reserve")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_stock")],
    ])
    return text, kb


@router.callback_query(F.data == "stk_alertcfg")
async def alertcfg(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    text, kb = await alertcfg_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


async def _toggle_field(cb: CallbackQuery, field: str, action_name: str):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        setattr(bs, field, not getattr(bs, field))
        new = getattr(bs, field)
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, action_name, {"new": new})
    text, kb = await alertcfg_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "stk_cfg_t_alert")
async def cfg_t_alert(cb: CallbackQuery):
    await _toggle_field(cb, "stock_alert_enabled", "stock_alert_toggle")


@router.callback_query(F.data == "stk_cfg_t_restock")
async def cfg_t_restock(cb: CallbackQuery):
    await _toggle_field(cb, "notify_restock_enabled", "restock_notify_toggle")


@router.callback_query(F.data == "stk_cfg_t_oos")
async def cfg_t_oos(cb: CallbackQuery):
    await _toggle_field(cb, "allow_out_of_stock_sales", "oos_sales_toggle")


@router.callback_query(F.data == "stk_cfg_t_reserve")
async def cfg_t_reserve(cb: CallbackQuery):
    await _toggle_field(cb, "stock_reserve_enabled", "reserve_enabled_toggle")


@router.callback_query(F.data == "stk_cfg_lim")
async def cfg_lim_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(SS.set_max)
    await state.update_data(config_field="stock_low_threshold")
    await cb.message.answer("Informe o novo limite global de estoque baixo (≥ 0):")
    await cb.answer()


@router.callback_query(F.data == "stk_cfg_max")
async def cfg_max_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(SS.set_max)
    await state.update_data(config_field="stock_max_global")
    await cb.message.answer("Informe o novo estoque máximo global (0 = sem limite):")
    await cb.answer()


@router.message(SS.set_max)
async def cfg_max_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        v = int(m.text.strip())
        if v < 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido.")
        return
    data = await state.get_data()
    field = data.get("config_field")
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        setattr(bs, field, v)
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, f"stock_config_{field}", {"new": v})
    await state.clear()
    text, kb = await alertcfg_view()
    await m.answer(f"✅ Valor atualizado para {v}.", reply_markup=kb)


# ============ RELATÓRIO ============
@router.callback_query(F.data == "stk_report")
async def report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    settings = await get_settings(force=True)
    threshold = settings.stock_low_threshold

    async with AsyncSessionLocal() as s:
        products = (await s.execute(select(Product).order_by(Product.stock.desc()))).scalars().all()
        recent = (await s.execute(
            select(StockMovement).order_by(desc(StockMovement.created_at)).limit(20)
        )).scalars().all()

    total_units = sum(p.stock for p in products)
    total_reserved = sum(p.reserved_stock for p in products)
    out = [p for p in products if p.stock <= 0]
    low = [p for p in products if 0 < p.stock <= threshold]

    lines = []
    lines.append("═" * 40)
    lines.append("BELLAMY | RELATÓRIO DE ESTOQUE")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append("═" * 40)
    lines.append("")
    lines.append("RESUMO")
    lines.append(f"  Total de produtos: {len(products)}")
    lines.append(f"  Unidades em estoque: {total_units}")
    lines.append(f"  Unidades reservadas: {total_reserved}")
    lines.append(f"  Unidades disponíveis: {total_units - total_reserved}")
    lines.append(f"  Esgotados: {len(out)}")
    lines.append(f"  Estoque baixo (≤ {threshold}): {len(low)}")
    lines.append("")
    lines.append("─" * 40)
    lines.append("TODOS OS PRODUTOS")
    lines.append("─" * 40)
    for p in products:
        lines.append(
            f"#{p.id:<4} {p.name[:30]:<30} "
            f"est: {p.stock:<5} res: {p.reserved_stock:<3} "
            f"vend: {p.sold_count:<5} R$ {p.price:.2f}"
        )
    lines.append("")
    lines.append("─" * 40)
    lines.append("ÚLTIMOS 20 MOVIMENTOS")
    lines.append("─" * 40)
    for mv in recent:
        lines.append(
            f"{mv.created_at.strftime('%d/%m %H:%M')} | "
            f"{mv.movement_type:<10} | prod {mv.product_id:<4} | "
            f"{mv.quantity:+d} | {mv.stock_before}→{mv.stock_after}"
        )
    lines.append("")
    lines.append("═" * 40)
    lines.append("FIM DO RELATÓRIO")
    lines.append("═" * 40)

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(content, filename=f"estoque_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt")

    await cb.message.answer_document(
        file,
        caption=(
            f"📊 <b>Relatório de Estoque</b>\n\n"
            f"📦 Produtos: {len(products)}\n"
            f"💎 Unidades disponíveis: {total_units - total_reserved}\n"
            f"🔴 Esgotados: {len(out)}\n"
            f"⚠️ Estoque baixo: {len(low)}"
        ),
    )
    await cb.answer("Relatório gerado.")
