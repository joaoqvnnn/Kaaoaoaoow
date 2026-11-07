from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, and_
from datetime import datetime, timedelta

from bot import dp, bot
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    User, Product, Order, Wallet, Payment, Referral,
    BotSettings, AdminLog,
)
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

PAGE_SIZE = 10
TOP_N = 50


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class RKS(StatesGroup):
    set_top_size = State()
    set_public_size = State()


CATEGORIES = {
    "services":     ("🛠️", "Serviços", "Produtos mais vendidos"),
    "recharges":    ("💳", "Recargas", "Mais recargas confirmadas"),
    "purchases":    ("🛒", "Compras", "Mais pedidos concluídos"),
    "balance":      ("💎", "Saldo", "Maiores saldos atuais"),
    "depositors":   ("💰", "Depositadores", "Maior volume em R$"),
    "referrals":    ("🤝", "Indicações", "Mais indicações válidas"),
}

PERIODS = {
    "today":     ("📅 Hoje", 1),
    "week":      ("📆 7 dias", 7),
    "month":     ("🗓️ Este mês", 30),
    "all":       ("♾️ Todos os tempos", 0),
}


def period_start(key: str) -> datetime | None:
    if key == "today":
        return datetime.combine(datetime.utcnow().date(), datetime.min.time())
    if key == "week":
        return datetime.utcnow() - timedelta(days=7)
    if key == "month":
        return datetime.utcnow() - timedelta(days=30)
    return None


# ============================================================
# MENU
# ============================================================
@router.callback_query(F.data == "admin_ranking")
async def ranking_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    settings = await get_settings()
    ranking_on = getattr(settings, "ranking_enabled", True)
    public_on = getattr(settings, "ranking_public", True)
    public_size = getattr(settings, "ranking_public_size", 10)
    period = getattr(settings, "ranking_default_period", "month")

    async with AsyncSessionLocal() as s:
        total_users = (await s.execute(select(func.count(User.id)))).scalar() or 0
        total_orders = (await s.execute(
            select(func.count(Order.id)).where(Order.status == "completed")
        )).scalar() or 0
        total_recharges = (await s.execute(
            select(func.count(Payment.id)).where(Payment.status == "confirmed")
        )).scalar() or 0
        total_referrals = (await s.execute(
            select(func.count(Referral.id)).where(Referral.is_confirmed == True)
        )).scalar() or 0

    text = (
        "◈ BELLAMY | RANKING E CLASSIFICAÇÕES\n\n"
        f"⚙️ Ranking no bot: {'🟢 Ativado' if ranking_on else '🔴 Desativado'}\n"
        f"👁️ Ranking público: {'🟢 Sim' if public_on else '🔴 Não'}\n"
        f"📊 Tamanho público: Top {public_size}\n"
        f"📅 Período padrão: {PERIODS.get(period, ('', 0))[0]}\n\n"
        f"📊 DADOS DISPONÍVEIS\n"
        f"👥 Usuários: {total_users}\n"
        f"🛒 Pedidos concluídos: {total_orders}\n"
        f"💳 Recargas confirmadas: {total_recharges}\n"
        f"🤝 Indicações válidas: {total_referrals}\n\n"
        "👇 Selecione uma categoria:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{CATEGORIES['services'][0]} {CATEGORIES['services'][1]}",
            callback_data="rk_view_services_month"),
         InlineKeyboardButton(
            text=f"{CATEGORIES['recharges'][0]} {CATEGORIES['recharges'][1]}",
            callback_data="rk_view_recharges_month")],
        [InlineKeyboardButton(
            text=f"{CATEGORIES['purchases'][0]} {CATEGORIES['purchases'][1]}",
            callback_data="rk_view_purchases_month"),
         InlineKeyboardButton(
            text=f"{CATEGORIES['balance'][0]} {CATEGORIES['balance'][1]}",
            callback_data="rk_view_balance_all")],
        [InlineKeyboardButton(
            text=f"{CATEGORIES['depositors'][0]} {CATEGORIES['depositors'][1]}",
            callback_data="rk_view_depositors_month"),
         InlineKeyboardButton(
            text=f"{CATEGORIES['referrals'][0]} {CATEGORIES['referrals'][1]}",
            callback_data="rk_view_referrals_month")],
        [InlineKeyboardButton(text="⚙️ Configurações do ranking", callback_data="rk_config")],
        [InlineKeyboardButton(text="📊 Estatísticas gerais", callback_data="rk_stats")],
        [InlineKeyboardButton(text="📥 Exportar todos os rankings", callback_data="rk_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# VIEW POR CATEGORIA + PERÍODO
# ============================================================
@router.callback_query(F.data.startswith("rk_view_"))
async def view_ranking(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    parts = cb.data.split("_")
    category = parts[2]
    period = parts[3]

    if category not in CATEGORIES:
        await cb.answer("Categoria inválida.")
        return
    if period not in PERIODS:
        period = "month"

    icon, label, desc = CATEGORIES[category]
    start = period_start(period)

    items = await _compute_ranking(category, start, TOP_N)

    text = (
        f"◈ BELLAMY | RANKING — {icon} {label}\n\n"
        f"📝 {desc}\n"
        f"📅 Período: {PERIODS[period][0]}\n"
        f"📊 Top {min(len(items), TOP_N)}\n\n"
    )

    if not items:
        text += "Nenhum dado disponível neste período."
    else:
        for i, item in enumerate(items, 1):
            medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}."))
            text += f"{medal} {item['label']} — {item['value']}\n"
            if item.get("detail"):
                text += f"    <i>{item['detail']}</i>\n"

    buttons = []
    period_row = []
    for pkey, (plabel, _) in PERIODS.items():
        mark = "☑️" if pkey == period else "⬜"
        period_row.append(InlineKeyboardButton(
            text=f"{mark} {plabel}",
            callback_data=f"rk_view_{category}_{pkey}",
        ))
    # 2 por linha
    for i in range(0, len(period_row), 2):
        buttons.append(period_row[i:i + 2])

    buttons.append([InlineKeyboardButton(
        text="👁️ Ver detalhes", callback_data=f"rk_detail_{category}_{period}")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_ranking")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# CÁLCULO DO RANKING
# ============================================================
async def _compute_ranking(category: str, start: datetime | None, limit: int) -> list[dict]:
    async with AsyncSessionLocal() as s:
        if category == "services":
            stmt = (
                select(
                    Product.id, Product.name,
                    func.coalesce(func.sum(Order.quantity), 0).label("qty"),
                    func.coalesce(func.sum(Order.total), 0).label("rev"),
                    func.count(Order.id).label("orders"),
                )
                .join(Order, Order.product_id == Product.id)
                .where(Order.status == "completed")
            )
            if start:
                stmt = stmt.where(Order.created_at >= start)
            stmt = stmt.group_by(Product.id, Product.name).order_by(desc("qty")).limit(limit)
            rows = (await s.execute(stmt)).all()
            return [
                {
                    "label": f"<b>{r[1]}</b>",
                    "value": f"{int(r[2])} vendas",
                    "detail": f"🛒 {r[4]} pedidos | 💰 R$ {r[3]:.2f}",
                    "product_id": r[0],
                }
                for r in rows
            ]

        if category == "recharges":
            stmt = (
                select(
                    User.id, User.full_name, User.username,
                    func.count(Payment.id).label("cnt"),
                    func.coalesce(func.sum(Payment.amount), 0).label("total"),
                )
                .join(Payment, Payment.user_id == User.id)
                .where(Payment.status == "confirmed")
            )
            if start:
                stmt = stmt.where(Payment.created_at >= start)
            stmt = stmt.group_by(User.id, User.full_name, User.username).order_by(desc("cnt")).limit(limit)
            rows = (await s.execute(stmt)).all()
            return [
                {
                    "label": _display_user(r[1], r[2], r[0]),
                    "value": f"{r[3]} recargas",
                    "detail": f"💰 R$ {r[4]:.2f}",
                    "user_id": r[0],
                }
                for r in rows
            ]

        if category == "purchases":
            stmt = (
                select(
                    User.id, User.full_name, User.username,
                    func.count(Order.id).label("cnt"),
                    func.coalesce(func.sum(Order.total), 0).label("total"),
                )
                .join(Order, Order.user_id == User.id)
                .where(Order.status == "completed")
            )
            if start:
                stmt = stmt.where(Order.created_at >= start)
            stmt = stmt.group_by(User.id, User.full_name, User.username).order_by(desc("cnt")).limit(limit)
            rows = (await s.execute(stmt)).all()
            return [
                {
                    "label": _display_user(r[1], r[2], r[0]),
                    "value": f"{r[3]} compras",
                    "detail": f"💰 R$ {r[4]:.2f}",
                    "user_id": r[0],
                }
                for r in rows
            ]

        if category == "balance":
            stmt = (
                select(
                    User.id, User.full_name, User.username, Wallet.balance,
                )
                .join(Wallet, Wallet.user_id == User.id)
                .where(Wallet.balance > 0)
                .order_by(desc(Wallet.balance))
                .limit(limit)
            )
            rows = (await s.execute(stmt)).all()
            return [
                {
                    "label": _display_user(r[1], r[2], r[0]),
                    "value": f"R$ {r[3]:.2f}",
                    "detail": None,
                    "user_id": r[0],
                }
                for r in rows
            ]

        if category == "depositors":
            stmt = (
                select(
                    User.id, User.full_name, User.username,
                    func.coalesce(func.sum(Payment.amount), 0).label("total"),
                    func.count(Payment.id).label("cnt"),
                )
                .join(Payment, Payment.user_id == User.id)
                .where(Payment.status == "confirmed")
            )
            if start:
                stmt = stmt.where(Payment.created_at >= start)
            stmt = stmt.group_by(User.id, User.full_name, User.username).order_by(desc("total")).limit(limit)
            rows = (await s.execute(stmt)).all()
            return [
                {
                    "label": _display_user(r[1], r[2], r[0]),
                    "value": f"R$ {r[3]:.2f}",
                    "detail": f"💳 {r[4]} recarga(s)",
                    "user_id": r[0],
                }
                for r in rows
            ]

        if category == "referrals":
            stmt = (
                select(
                    User.id, User.full_name, User.username,
                    func.count(Referral.id).label("cnt"),
                )
                .join(Referral, Referral.referrer_id == User.id)
                .where(Referral.is_confirmed == True)
            )
            if start:
                stmt = stmt.where(Referral.confirmed_at >= start)
            stmt = stmt.group_by(User.id, User.full_name, User.username).order_by(desc("cnt")).limit(limit)
            rows = (await s.execute(stmt)).all()
            return [
                {
                    "label": _display_user(r[1], r[2], r[0]),
                    "value": f"{r[3]} indicações",
                    "detail": None,
                    "user_id": r[0],
                }
                for r in rows
            ]

    return []


def _display_user(full_name: str | None, username: str | None, uid: int) -> str:
    name = full_name or (f"@{username}" if username else f"User {uid}")
    if username and full_name:
        return f"<b>{name}</b> (@{username})"
    return f"<b>{name}</b>"


# ============================================================
# DETALHE (com links para usuário/produto)
# ============================================================
@router.callback_query(F.data.startswith("rk_detail_"))
async def detail_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    parts = cb.data.split("_")
    category = parts[2]
    period = parts[3]
    if category not in CATEGORIES:
        await cb.answer("Categoria inválida.")
        return
    if period not in PERIODS:
        period = "month"

    start = period_start(period)
    items = await _compute_ranking(category, start, TOP_N)

    icon, label, desc = CATEGORIES[category]
    buttons = []
    for i, item in enumerate(items[:30], 1):
        if "user_id" in item:
            buttons.append([InlineKeyboardButton(
                text=f"{i}. {_strip_html(item['label'])[:40]}",
                callback_data=f"usr_view_{item['user_id']}",
            )])
        elif "product_id" in item:
            buttons.append([InlineKeyboardButton(
                text=f"{i}. {_strip_html(item['label'])[:40]}",
                callback_data=f"prd_view_{item['product_id']}",
            )])

    buttons.append([InlineKeyboardButton(
        text="↩️ Voltar ao ranking",
        callback_data=f"rk_view_{category}_{period}",
    )])

    await cb.message.edit_text(
        f"◈ BELLAMY | DETALHE — {icon} {label}\n\n"
        f"Toque em um item para abrir o perfil/produto correspondente.\n\n"
        f"📅 Período: {PERIODS[period][0]}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


def _strip_html(s: str) -> str:
    return s.replace("<b>", "").replace("</b>", "")


# ============================================================
# CONFIGURAÇÕES
# ============================================================
async def config_view() -> tuple[str, InlineKeyboardMarkup]:
    s = await get_settings(force=True)
    ranking_on = getattr(s, "ranking_enabled", True)
    public_on = getattr(s, "ranking_public", True)
    public_size = getattr(s, "ranking_public_size", 10)
    period = getattr(s, "ranking_default_period", "month")

    text = (
        "◈ BELLAMY | CONFIGURAÇÕES DO RANKING\n\n"
        f"⚙️ Ranking no bot: {'🟢 Ativado' if ranking_on else '🔴 Desativado'}\n"
        f"👁️ Ranking público: {'🟢 Sim' if public_on else '🔴 Não'}\n"
        f"📊 Tamanho do ranking público: Top {public_size}\n"
        f"📅 Período padrão: {PERIODS.get(period, ('', 0))[0]}\n"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar ranking" if ranking_on else "🟢 Ativar ranking"),
            callback_data="rk_cfg_t_enabled")],
        [InlineKeyboardButton(
            text=("🔴 Ocultar do público" if public_on else "🟢 Tornar público"),
            callback_data="rk_cfg_t_public")],
        [InlineKeyboardButton(
            text=f"📊 Tamanho público (Top {public_size})",
            callback_data="rk_cfg_public_size")],
        [InlineKeyboardButton(
            text=f"📅 Período padrão ({PERIODS.get(period, ('', 0))[0]})",
            callback_data="rk_cfg_period")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_ranking")],
    ])
    return text, kb


@router.callback_query(F.data == "rk_config")
async def config(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "rk_cfg_t_enabled")
async def toggle_enabled(cb: CallbackQuery):
    await _toggle(cb, "ranking_enabled", "ranking_toggle")


@router.callback_query(F.data == "rk_cfg_t_public")
async def toggle_public(cb: CallbackQuery):
    await _toggle(cb, "ranking_public", "ranking_public_toggle")


async def _toggle(cb: CallbackQuery, field: str, action: str):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        cur = getattr(bs, field, True)
        setattr(bs, field, not cur)
        new = getattr(bs, field)
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, action, {"new": new})
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "rk_cfg_public_size")
async def set_public_size_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(RKS.set_public_size)
    await cb.message.answer("Informe o tamanho do ranking público (3-50):")
    await cb.answer()


@router.message(RKS.set_public_size)
async def set_public_size_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        val = int(m.text.strip())
        if val < 3 or val > 50:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido (3-50).")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.ranking_public_size = val
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "ranking_public_size_set", {"value": val})
    await state.clear()
    text, kb = await config_view()
    await m.answer(f"✅ Tamanho atualizado: Top {val}.", reply_markup=kb)


@router.callback_query(F.data == "rk_cfg_period")
async def set_period_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    buttons = []
    for key, (label, _) in PERIODS.items():
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"rk_setperiod_{key}"
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Cancelar", callback_data="rk_config")])
    await cb.message.edit_text(
        "📅 PERÍODO PADRÃO DO RANKING\n\nSelecione:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("rk_setperiod_"))
async def set_period(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    period = cb.data.replace("rk_setperiod_", "")
    if period not in PERIODS:
        await cb.answer("Período inválido.")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.ranking_default_period = period
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "ranking_period_set", {"value": period})
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Período atualizado.")


# ============================================================
# ESTATÍSTICAS GERAIS
# ============================================================
@router.callback_query(F.data == "rk_stats")
async def stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    # Resumo dos 3 primeiros de cada categoria no mês
    lines = []
    for cat_key in ["services", "recharges", "purchases", "balance", "depositors", "referrals"]:
        items = await _compute_ranking(cat_key, period_start("month"), 3)
        icon, label, _ = CATEGORIES[cat_key]
        lines.append(f"\n{icon} <b>{label} (mês)</b>")
        if not items:
            lines.append("   <i>sem dados</i>")
            continue
        for i, item in enumerate(items, 1):
            medal = "🥇" if i == 1 else ("🥈" if i == 2 else "🥉")
            lines.append(f"   {medal} {item['label']} — {item['value']}")

    text = "◈ BELLAMY | ESTATÍSTICAS DE RANKING\n" + "\n".join(lines)

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="rk_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_ranking")],
    ]))
    await cb.answer()


# ============================================================
# EXPORTAÇÃO
# ============================================================
@router.callback_query(F.data == "rk_export")
async def export_report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    lines = []
    lines.append("═" * 70)
    lines.append("BELLAMY | RELATÓRIO DE RANKINGS")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append("═" * 70)

    for cat_key, (icon, label, desc) in CATEGORIES.items():
        lines.append("")
        lines.append("─" * 70)
        lines.append(f"{label.upper()} — {desc}")
        lines.append("─" * 70)

        for pkey, (plabel, _) in PERIODS.items():
            items = await _compute_ranking(cat_key, period_start(pkey), TOP_N)
            lines.append("")
            lines.append(f"  Período: {plabel}")
            if not items:
                lines.append("    (sem dados)")
                continue
            for i, item in enumerate(items, 1):
                clean = _strip_html(item["label"])
                detail = f" | {item['detail']}" if item.get("detail") else ""
                lines.append(f"    {i:>2}. {clean:<30} — {item['value']}{detail}")

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"rankings_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption="📊 <b>Relatório de Rankings</b>\n\nTodas as categorias e períodos.",
    )
    await cb.answer("Relatório gerado.")


# ============================================================
# HELPER PARA O BOT DO CLIENTE
# ============================================================
async def get_public_ranking(category: str) -> list[dict]:
    """
    Retorna o ranking público (Top N) para o bot do cliente usar
    na tela /ranking sem expor todos os dados internos.
    """
    if category not in CATEGORIES:
        return []
    settings = await get_settings()
    if not getattr(settings, "ranking_public", True):
        return []
    size = getattr(settings, "ranking_public_size", 10)
    period = getattr(settings, "ranking_default_period", "month")
    return await _compute_ranking(category, period_start(period), size)
