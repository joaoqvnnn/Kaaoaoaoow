from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, and_, or_
from datetime import datetime, timedelta
from io import StringIO
import csv
import json

from bot import dp
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    User, Product, Order, Wallet, WalletTransaction, Payment,
    StockMovement, CartItem, RechargeBonusTier, Coupon, CouponUsage,
    DeepLink, DeepLinkVisit, NotificationLog, ChannelPublication,
    Referral, Affiliate, AffiliateTransaction, MessageTemplate,
    ButtonConfig, MiniAppSettings, MiniAppSession, MiniAppThemeHistory,
    SupportTicket, SupportMessage, SupportAgent, CannedResponse, FAQEntry,
    AdminUser, AdminSession, LoginAttempt, BackupRecord,
    AdminLog, BotSettings, ReportExport,
)

router = Router()
dp.include_router(router)

HISTORY_PAGE_SIZE = 12


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class RPS(StatesGroup):
    custom_start = State()
    custom_end = State()


PERIODS = {
    "today":  ("📅 Hoje", 0),
    "yesterday": ("📆 Ontem", 1),
    "week":   ("📆 Últimos 7 dias", 7),
    "month":  ("🗓️ Últimos 30 dias", 30),
    "this_month": ("📊 Este mês", "month"),
    "all":    ("♾️ Todos os tempos", None),
    "custom": ("🎯 Personalizado", "custom"),
}

REPORT_TYPES = {
    "sales":         ("🛒 Vendas", "Pedidos, produtos vendidos e faturamento"),
    "financial":     ("💰 Financeiro", "Movimentações de carteira e recargas"),
    "recharges":     ("💳 Recargas", "Recargas PIX e bônus"),
    "products":      ("📦 Produtos", "Catálogo completo com desempenho"),
    "stock":         ("🗃️ Estoque", "Estoque atual e movimentações"),
    "users":         ("👥 Usuários", "Base de usuários com métricas"),
    "affiliates":    ("🤝 Afiliados", "Programa de indicações e pontos"),
    "campaigns":     ("🔗 Deep Links", "Performance de campanhas"),
    "notifications": ("🔔 Notificações", "Histórico de envios"),
}

FORMATS = {
    "csv": ("📊 CSV", "Abre no Excel, Sheets e LibreOffice"),
    "txt": ("📄 TXT", "Relatório formatado em texto"),
}


def period_bounds(key: str, custom_start: datetime | None = None, custom_end: datetime | None = None) -> tuple[datetime | None, datetime | None]:
    now = datetime.utcnow()
    if key == "today":
        return datetime.combine(now.date(), datetime.min.time()), now
    if key == "yesterday":
        y = now.date() - timedelta(days=1)
        return datetime.combine(y, datetime.min.time()), datetime.combine(y, datetime.max.time())
    if key == "week":
        return now - timedelta(days=7), now
    if key == "month":
        return now - timedelta(days=30), now
    if key == "this_month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now
    if key == "custom":
        return custom_start, custom_end
    return None, None


# ============================================================
# MENU
# ============================================================
@router.callback_query(F.data == "admin_reports")
async def reports_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        total_exports = (await s.execute(
            select(func.count(ReportExport.id))
        )).scalar() or 0
        exports_today = (await s.execute(
            select(func.count(ReportExport.id))
            .where(ReportExport.created_at >= datetime.combine(datetime.utcnow().date(), datetime.min.time()))
        )).scalar() or 0
        exports_month = (await s.execute(
            select(func.count(ReportExport.id))
            .where(ReportExport.created_at >= datetime.combine(
                datetime.utcnow().date().replace(day=1), datetime.min.time()))
        )).scalar() or 0

    text = (
        "◈ BELLAMY | RELATÓRIOS E AUDITORIA\n\n"
        "Exporte dados em CSV (Excel) ou TXT formatado.\n"
        "Todos os filtros de período podem ser aplicados.\n\n"
        f"📊 Exportações registradas: {total_exports}\n"
        f"📅 Hoje: {exports_today} | Este mês: {exports_month}\n\n"
        "👇 Selecione o tipo de relatório:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Vendas", callback_data="rpt_type_sales"),
         InlineKeyboardButton(text="💰 Financeiro", callback_data="rpt_type_financial")],
        [InlineKeyboardButton(text="💳 Recargas", callback_data="rpt_type_recharges"),
         InlineKeyboardButton(text="📦 Produtos", callback_data="rpt_type_products")],
        [InlineKeyboardButton(text="🗃️ Estoque", callback_data="rpt_type_stock"),
         InlineKeyboardButton(text="👥 Usuários", callback_data="rpt_type_users")],
        [InlineKeyboardButton(text="🤝 Afiliados", callback_data="rpt_type_affiliates"),
         InlineKeyboardButton(text="🔗 Campanhas", callback_data="rpt_type_campaigns")],
        [InlineKeyboardButton(text="🔔 Notificações", callback_data="rpt_type_notifications")],
        [InlineKeyboardButton(text="📥 Histórico de exportações", callback_data="rpt_history_0")],
        [InlineKeyboardButton(text="🗂️ Backup completo (JSON)", callback_data="rpt_backup")],
        [InlineKeyboardButton(text="📋 Auditoria de ações", callback_data="rpt_audit_0")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# SELEÇÃO DE PERÍODO
# ============================================================
@router.callback_query(F.data.startswith("rpt_type_"))
async def choose_period(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rtype = cb.data.replace("rpt_type_", "")
    if rtype not in REPORT_TYPES:
        await cb.answer("Tipo inválido.")
        return

    icon, desc = REPORT_TYPES[rtype]
    text = (
        f"◈ BELLAMY | {icon}\n\n"
        f"<i>{desc}</i>\n\n"
        f"📅 Selecione o período:"
    )
    buttons = []
    for key, (label, _) in PERIODS.items():
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"rpt_period_{rtype}_{key}"
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_reports")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("rpt_period_"))
async def choose_format(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    rest = cb.data.replace("rpt_period_", "")
    parts = rest.rsplit("_", 1)
    rtype = parts[0]
    period = parts[1]

    if rtype not in REPORT_TYPES or period not in PERIODS:
        await cb.answer("Parâmetros inválidos.")
        return

    if period == "custom":
        await state.update_data(report_type=rtype)
        await state.set_state(RPS.custom_start)
        await cb.message.answer(
            "🎯 PERÍODO PERSONALIZADO (1/2)\n\n"
            "Envie a <b>data inicial</b> no formato <code>DD/MM/AAAA</code>:"
        )
        await cb.answer()
        return

    icon, desc = REPORT_TYPES[rtype]
    period_label = PERIODS[period][0]

    text = (
        f"◈ BELLAMY | {icon}\n\n"
        f"<i>{desc}</i>\n"
        f"📅 Período: {period_label}\n\n"
        f"Selecione o formato de exportação:"
    )
    buttons = []
    for fkey, (flabel, fdesc) in FORMATS.items():
        buttons.append([InlineKeyboardButton(
            text=f"{flabel} — {fdesc}",
            callback_data=f"rpt_gen_{rtype}_{period}_{fkey}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=f"rpt_type_{rtype}")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.message(RPS.custom_start)
async def custom_start(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    try:
        start = datetime.strptime(raw, "%d/%m/%Y")
    except ValueError:
        await m.answer("❌ Formato inválido. Use <code>DD/MM/AAAA</code>.")
        return
    await state.update_data(custom_start=start)
    await state.set_state(RPS.custom_end)
    await m.answer("🎯 PERÍODO PERSONALIZADO (2/2)\n\nEnvie a <b>data final</b>:")


@router.message(RPS.custom_end)
async def custom_end(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    try:
        end = datetime.strptime(raw, "%d/%m/%Y").replace(hour=23, minute=59, second=59)
    except ValueError:
        await m.answer("❌ Formato inválido. Use <code>DD/MM/AAAA</code>.")
        return

    data = await state.get_data()
    start = data.get("custom_start")
    rtype = data.get("report_type")
    await state.clear()
    if not start or not rtype or end < start:
        await m.answer("❌ Datas inválidas.")
        return

    await m.answer(
        f"✅ Período: {start.strftime('%d/%m/%Y')} — {end.strftime('%d/%m/%Y')}\n\n"
        f"Selecione o formato:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="📊 CSV",
                callback_data=f"rpt_gendate_{rtype}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}_csv"
            )],
            [InlineKeyboardButton(
                text="📄 TXT",
                callback_data=f"rpt_gendate_{rtype}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}_txt"
            )],
        ]),
    )


@router.callback_query(F.data.startswith("rpt_gendate_"))
async def generate_custom(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("rpt_gendate_", "")
    parts = rest.split("_")
    if len(parts) != 4:
        await cb.answer("Parâmetros inválidos.")
        return
    rtype, start_s, end_s, fmt = parts
    try:
        start = datetime.strptime(start_s, "%Y%m%d")
        end = datetime.strptime(end_s, "%Y%m%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        await cb.answer("Datas inválidas.")
        return
    await _generate_report(cb, rtype, "custom", fmt, start, end)


# ============================================================
# GERAÇÃO DE RELATÓRIO
# ============================================================
@router.callback_query(F.data.startswith("rpt_gen_"))
async def generate(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("rpt_gen_", "")
    parts = rest.split("_")
    if len(parts) != 3:
        await cb.answer("Parâmetros inválidos.")
        return
    rtype, period, fmt = parts
    await _generate_report(cb, rtype, period, fmt, None, None)


async def _generate_report(cb, rtype, period, fmt, start, end):
    if rtype not in REPORT_TYPES:
        await cb.answer("Tipo inválido.")
        return

    if period != "custom":
        start, end = period_bounds(period)

    await cb.answer("⏳ Gerando relatório...")

    try:
        headers, rows = await _build_report(rtype, start, end)
    except Exception as e:
        await cb.message.answer(f"❌ Erro ao gerar: {str(e)[:200]}")
        return

    if fmt == "csv":
        content = _to_csv(headers, rows)
        ext = "csv"
        mime = "text/csv"
    else:
        content = _to_txt(rtype, headers, rows, start, end)
        ext = "txt"
        mime = "text/plain"

    filename = f"{rtype}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.{ext}"

    async with AsyncSessionLocal() as s:
        s.add(ReportExport(
            admin_id=cb.from_user.id,
            report_type=rtype,
            format=fmt,
            period_start=start,
            period_end=end,
            rows_count=len(rows),
            file_size=len(content),
        ))
        await s.commit()

    await log_admin(cb.from_user.id, "report_generated", {
        "type": rtype, "period": period, "format": fmt, "rows": len(rows),
    })

    file = BufferedInputFile(content, filename=filename)
    icon, desc = REPORT_TYPES[rtype]

    if period in PERIODS:
        p_label = PERIODS[period][0]
    else:
        p_label = f"{start.strftime('%d/%m/%Y')} — {end.strftime('%d/%m/%Y')}"

    await cb.message.answer_document(
        file,
        caption=(
            f"{icon} <b>{REPORT_TYPES[rtype][0].split(' ', 1)[1]}</b>\n\n"
            f"📅 Período: {p_label}\n"
            f"📊 Registros: {len(rows)}\n"
            f"📁 Formato: {fmt.upper()}"
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Novo relatório", callback_data="admin_reports")],
            [InlineKeyboardButton(text="📥 Histórico", callback_data="rpt_history_0")],
        ]),
    )


def _to_csv(headers: list[str], rows: list[list]) -> bytes:
    out = StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return out.getvalue().encode("utf-8-sig")  # BOM para Excel


def _to_txt(rtype: str, headers: list[str], rows: list[list],
            start: datetime | None, end: datetime | None) -> bytes:
    lines = []
    lines.append("═" * 70)
    lines.append(f"BELLAMY | RELATÓRIO — {REPORT_TYPES[rtype][0]}")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    if start and end:
        lines.append(f"Período: {start.strftime('%d/%m/%Y')} — {end.strftime('%d/%m/%Y')}")
    lines.append(f"Registros: {len(rows)}")
    lines.append("═" * 70)
    lines.append("")

    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
              for i, h in enumerate(headers)]
    header_line = " | ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)
    lines.append("-" * len(header_line))

    for row in rows:
        lines.append(" | ".join(
            str(row[i] if i < len(row) else "").ljust(widths[i])
            for i in range(len(headers))
        ))

    return "\n".join(lines).encode("utf-8")


# ============================================================
# BUILDERS POR TIPO
# ============================================================
async def _build_report(rtype: str, start: datetime | None, end: datetime | None):
    if rtype == "sales":
        return await _report_sales(start, end)
    if rtype == "financial":
        return await _report_financial(start, end)
    if rtype == "recharges":
        return await _report_recharges(start, end)
    if rtype == "products":
        return await _report_products()
    if rtype == "stock":
        return await _report_stock(start, end)
    if rtype == "users":
        return await _report_users(start, end)
    if rtype == "affiliates":
        return await _report_affiliates(start, end)
    if rtype == "campaigns":
        return await _report_campaigns(start, end)
    if rtype == "notifications":
        return await _report_notifications(start, end)
    return (["Erro"], [["Tipo inválido"]])


async def _report_sales(start, end):
    headers = ["ID", "Data", "Cliente ID", "Cliente", "Username", "Produto ID",
               "Produto", "Qtd", "Total (R$)", "Status", "Pagamento", "Motivo Cancel."]
    rows = []
    async with AsyncSessionLocal() as s:
        stmt = select(Order)
        if start:
            stmt = stmt.where(Order.created_at >= start)
        if end:
            stmt = stmt.where(Order.created_at <= end)
        stmt = stmt.order_by(desc(Order.created_at))
        orders = (await s.execute(stmt)).scalars().all()

        uids = list({o.user_id for o in orders})
        pids = list({o.product_id for o in orders})
        users = {}
        prods = {}
        if uids:
            users = {x.id: x for x in (await s.execute(
                select(User).where(User.id.in_(uids))
            )).scalars().all()}
        if pids:
            prods = {x.id: x for x in (await s.execute(
                select(Product).where(Product.id.in_(pids))
            )).scalars().all()}

    for o in orders:
        u = users.get(o.user_id)
        p = prods.get(o.product_id)
        rows.append([
            o.id,
            o.created_at.strftime("%d/%m/%Y %H:%M"),
            o.user_id,
            u.full_name if u else "—",
            (f"@{u.username}" if u and u.username else "—"),
            o.product_id,
            p.name if p else "—",
            o.quantity,
            f"{o.total:.2f}",
            o.status,
            o.payment_method or "wallet",
            o.cancelled_reason or "",
        ])
    return headers, rows


async def _report_financial(start, end):
    headers = ["ID", "Data", "User ID", "Usuário", "Tipo", "Valor (R$)",
               "Saldo Antes", "Saldo Depois", "Descrição"]
    rows = []
    async with AsyncSessionLocal() as s:
        stmt = select(WalletTransaction)
        if start:
            stmt = stmt.where(WalletTransaction.created_at >= start)
        if end:
            stmt = stmt.where(WalletTransaction.created_at <= end)
        stmt = stmt.order_by(desc(WalletTransaction.created_at))
        txs = (await s.execute(stmt)).scalars().all()

        uids = list({t.user_id for t in txs})
        users = {}
        if uids:
            users = {x.id: x for x in (await s.execute(
                select(User).where(User.id.in_(uids))
            )).scalars().all()}

    for t in txs:
        u = users.get(t.user_id)
        rows.append([
            t.id,
            t.created_at.strftime("%d/%m/%Y %H:%M"),
            t.user_id,
            u.full_name if u else "—",
            t.type,
            f"{t.amount:.2f}",
            f"{t.balance_before:.2f}",
            f"{t.balance_after:.2f}",
            (t.description or "")[:100],
        ])
    return headers, rows


async def _report_recharges(start, end):
    headers = ["ID", "Data", "User ID", "Usuário", "Valor (R$)", "Bônus (R$)",
               "Total (R$)", "Status", "Provedor", "MP Payment ID", "Confirmado em"]
    rows = []
    async with AsyncSessionLocal() as s:
        stmt = select(Payment)
        if start:
            stmt = stmt.where(Payment.created_at >= start)
        if end:
            stmt = stmt.where(Payment.created_at <= end)
        stmt = stmt.order_by(desc(Payment.created_at))
        pays = (await s.execute(stmt)).scalars().all()

        uids = list({p.user_id for p in pays})
        users = {}
        if uids:
            users = {x.id: x for x in (await s.execute(
                select(User).where(User.id.in_(uids))
            )).scalars().all()}

    for p in pays:
        u = users.get(p.user_id)
        bonus = getattr(p, "bonus_amount", 0.0) or 0.0
        total = p.amount + bonus
        rows.append([
            p.id,
            p.created_at.strftime("%d/%m/%Y %H:%M"),
            p.user_id,
            u.full_name if u else "—",
            f"{p.amount:.2f}",
            f"{bonus:.2f}",
            f"{total:.2f}",
            p.status,
            getattr(p, "provider", "mercadopago") or "—",
            getattr(p, "mp_payment_id", "") or "—",
            (getattr(p, "confirmed_at", None).strftime("%d/%m/%Y %H:%M")
             if getattr(p, "confirmed_at", None) else "—"),
        ])
    return headers, rows


async def _report_products():
    headers = ["ID", "Nome", "Categoria", "Preço (R$)", "Promo (R$)",
               "Estoque", "Reservado", "Vendidos", "Status", "Destaque", "Criado em"]
    rows = []
    async with AsyncSessionLocal() as s:
        products = (await s.execute(
            select(Product).order_by(Product.name)
        )).scalars().all()

    for p in products:
        rows.append([
            p.id,
            p.name,
            p.category or "—",
            f"{p.price:.2f}",
            f"{p.promo_price:.2f}" if p.promo_price else "—",
            p.stock,
            p.reserved_stock,
            p.sold_count,
            "ativo" if p.is_active else "inativo",
            "sim" if p.featured else "não",
            p.created_at.strftime("%d/%m/%Y %H:%M"),
        ])
    return headers, rows


async def _report_stock(start, end):
    headers = ["ID", "Data", "Produto ID", "Produto", "Admin", "Tipo",
               "Qtd", "Estoque Antes", "Estoque Depois", "Motivo"]
    rows = []
    async with AsyncSessionLocal() as s:
        stmt = select(StockMovement)
        if start:
            stmt = stmt.where(StockMovement.created_at >= start)
        if end:
            stmt = stmt.where(StockMovement.created_at <= end)
        stmt = stmt.order_by(desc(StockMovement.created_at))
        moves = (await s.execute(stmt)).scalars().all()

        pids = list({m.product_id for m in moves})
        prods = {}
        if pids:
            prods = {x.id: x for x in (await s.execute(
                select(Product).where(Product.id.in_(pids))
            )).scalars().all()}

    for m in moves:
        p = prods.get(m.product_id)
        rows.append([
            m.id,
            m.created_at.strftime("%d/%m/%Y %H:%M"),
            m.product_id,
            p.name if p else "—",
            m.admin_id,
            m.movement_type,
            m.quantity,
            m.stock_before,
            m.stock_after,
            m.reason or "",
        ])
    return headers, rows


async def _report_users(start, end):
    headers = ["ID", "Nome", "Username", "Cadastro", "Status", "Bloqueado",
               "Saldo (R$)", "Compras", "Total Gasto (R$)", "Recargas (R$)"]
    rows = []
    async with AsyncSessionLocal() as s:
        stmt = select(User)
        if start:
            stmt = stmt.where(User.created_at >= start)
        if end:
            stmt = stmt.where(User.created_at <= end)
        stmt = stmt.order_by(desc(User.created_at))
        users = (await s.execute(stmt)).scalars().all()

        uids = [u.id for u in users]
        wallets = {}
        purchases = {}
        spent = {}
        recharges = {}

        if uids:
            w_rows = (await s.execute(
                select(Wallet).where(Wallet.user_id.in_(uids))
            )).scalars().all()
            wallets = {w.user_id: w.balance for w in w_rows}

            ord_rows = (await s.execute(
                select(Order.user_id, func.count(Order.id), func.coalesce(func.sum(Order.total), 0))
                .where(Order.user_id.in_(uids))
                .where(Order.status == "completed")
                .group_by(Order.user_id)
            )).all()
            for uid, cnt, tot in ord_rows:
                purchases[uid] = cnt
                spent[uid] = tot

            pay_rows = (await s.execute(
                select(Payment.user_id, func.coalesce(func.sum(Payment.amount), 0))
                .where(Payment.user_id.in_(uids))
                .where(Payment.status == "confirmed")
                .group_by(Payment.user_id)
            )).all()
            recharges = {uid: tot for uid, tot in pay_rows}

    for u in users:
        rows.append([
            u.id,
            u.full_name,
            u.username or "—",
            u.created_at.strftime("%d/%m/%Y"),
            "ativo" if u.is_active else "inativo",
            "sim" if u.is_blocked else "não",
            f"{wallets.get(u.id, 0.0):.2f}",
            purchases.get(u.id, 0),
            f"{spent.get(u.id, 0.0):.2f}",
            f"{recharges.get(u.id, 0.0):.2f}",
        ])
    return headers, rows


async def _report_affiliates(start, end):
    headers = ["User ID", "Nome", "Username", "Código", "Pontos", "Pontos Ganhos",
               "Pontos Convertidos", "R$ Convertido", "Indicações", "Confirmadas"]
    rows = []
    async with AsyncSessionLocal() as s:
        affs = (await s.execute(
            select(Affiliate).order_by(desc(Affiliate.total_points_earned))
        )).scalars().all()

        uids = [a.user_id for a in affs]
        users = {}
        if uids:
            users = {x.id: x for x in (await s.execute(
                select(User).where(User.id.in_(uids))
            )).scalars().all()}

    for a in affs:
        u = users.get(a.user_id)
        rows.append([
            a.user_id,
            u.full_name if u else "—",
            (f"@{u.username}" if u and u.username else "—"),
            a.referral_code,
            a.points_balance,
            a.total_points_earned,
            a.total_points_converted,
            f"{a.total_balance_earned:.2f}",
            a.total_referrals,
            a.confirmed_referrals,
        ])
    return headers, rows


async def _report_campaigns(start, end):
    headers = ["ID", "Nome", "Código", "Destino", "Status", "Cliques",
               "Únicos", "Cadastros", "Compras", "Receita (R$)", "Criado em"]
    rows = []
    async with AsyncSessionLocal() as s:
        stmt = select(DeepLink)
        if start:
            stmt = stmt.where(DeepLink.created_at >= start)
        if end:
            stmt = stmt.where(DeepLink.created_at <= end)
        stmt = stmt.order_by(desc(DeepLink.clicks))
        links = (await s.execute(stmt)).scalars().all()

    for l in links:
        rows.append([
            l.id,
            l.name,
            l.code,
            l.destination_type,
            "ativo" if l.is_active else "inativo",
            l.clicks,
            l.unique_users,
            l.registrations,
            l.purchases,
            f"{l.revenue:.2f}",
            l.created_at.strftime("%d/%m/%Y"),
        ])
    return headers, rows


async def _report_notifications(start, end):
    headers = ["ID", "Data", "Evento", "Destinatário", "Sucesso", "Erro"]
    rows = []
    async with AsyncSessionLocal() as s:
        stmt = select(NotificationLog)
        if start:
            stmt = stmt.where(NotificationLog.sent_at >= start)
        if end:
            stmt = stmt.where(NotificationLog.sent_at <= end)
        stmt = stmt.order_by(desc(NotificationLog.sent_at))
        logs = (await s.execute(stmt)).scalars().all()

    for l in logs:
        rows.append([
            l.id,
            l.sent_at.strftime("%d/%m/%Y %H:%M"),
            l.event_key,
            l.target_id,
            "sim" if l.success else "não",
            (l.error or "")[:100],
        ])
    return headers, rows


# ============================================================
# HISTÓRICO DE EXPORTAÇÕES
# ============================================================
@router.callback_query(F.data.startswith("rpt_history_"))
async def history(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(ReportExport.id))
        )).scalar() or 0
        stmt = (
            select(ReportExport)
            .order_by(desc(ReportExport.created_at))
            .offset(page * HISTORY_PAGE_SIZE)
            .limit(HISTORY_PAGE_SIZE)
        )
        exports = (await s.execute(stmt)).scalars().all()

    text = f"📥 HISTÓRICO DE EXPORTAÇÕES\n\nTotal: {total} | Página {page + 1}\n\n"

    if not exports:
        text += "Nenhuma exportação registrada."
    else:
        for e in exports:
            icon = "📊" if e.format == "csv" else "📄"
            type_label = REPORT_TYPES.get(e.report_type, ("•", ""))[0]
            p = ""
            if e.period_start and e.period_end:
                p = f"{e.period_start.strftime('%d/%m')} — {e.period_end.strftime('%d/%m')}"
            text += (
                f"{icon} {e.created_at.strftime('%d/%m/%Y %H:%M')} — {type_label}\n"
                f"   {p} | {e.rows_count} registros | {e.file_size} bytes\n\n"
            )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"rpt_history_{page - 1}"))
    if (page + 1) * HISTORY_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"rpt_history_{page + 1}"))
    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_reports")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# BACKUP COMPLETO (JSON)
# ============================================================
@router.callback_query(F.data == "rpt_backup")
async def backup_json(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    await cb.answer("⏳ Gerando backup...")

    try:
        backup = await _build_json_backup()
    except Exception as e:
        await cb.message.answer(f"❌ Erro: {str(e)[:200]}")
        return

    content = json.dumps(backup, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"backup_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.json",
    )

    async with AsyncSessionLocal() as s:
        s.add(BackupRecord(
            admin_id=cb.from_user.id,
            note="Backup JSON gerado pelo painel",
            size_bytes=len(content),
            success=True,
        ))
        await s.commit()

    await log_admin(cb.from_user.id, "backup_json_generated", {"size": len(content)})

    await cb.message.answer_document(
        file,
        caption=f"🗂️ <b>Backup completo (JSON)</b>\n\n📦 Tamanho: {len(content)} bytes",
    )


async def _build_json_backup() -> dict:
    backup = {"generated_at": datetime.utcnow().isoformat(), "tables": {}}

    async with AsyncSessionLocal() as s:
        def serialize(obj, exclude=("token_hash",)):
            if hasattr(obj, "__table__"):
                return {c.name: getattr(obj, c.name) for c in obj.__table__.columns
                        if c.name not in exclude}
            return str(obj)

        tables = [
            ("users", User),
            ("products", Product),
            ("orders", Order),
            ("wallets", Wallet),
            ("wallet_transactions", WalletTransaction),
            ("payments", Payment),
            ("stock_movements", StockMovement),
            ("cart_items", CartItem),
            ("recharge_bonus_tiers", RechargeBonusTier),
            ("coupons", Coupon),
            ("coupon_usages", CouponUsage),
            ("deep_links", DeepLink),
            ("affiliates", Affiliate),
            ("affiliate_transactions", AffiliateTransaction),
            ("referrals", Referral),
            ("notification_settings", NotificationSetting if False else None),
        ]

        # Backup seletivo (sem tabelas gigantes de log)
        for name, model in [
            ("users", User),
            ("products", Product),
            ("orders", Order),
            ("wallets", Wallet),
            ("payments", Payment),
            ("coupons", Coupon),
            ("affiliates", Affiliate),
            ("referrals", Referral),
            ("deep_links", DeepLink),
        ]:
            try:
                rows = (await s.execute(select(model))).scalars().all()
                backup["tables"][name] = [serialize(r) for r in rows]
            except Exception as e:
                backup["tables"][name] = {"error": str(e)}

    return backup


# ============================================================
# AUDITORIA
# ============================================================
@router.callback_query(F.data.startswith("rpt_audit_"))
async def audit(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(AdminLog.id)))).scalar() or 0
        stmt = (
            select(AdminLog)
            .order_by(desc(AdminLog.created_at))
            .offset(page * 15)
            .limit(15)
        )
        logs = (await s.execute(stmt)).scalars().all()

        uids = list({l.admin_id for l in logs})
        users = {}
        if uids:
            users = {x.id: x for x in (await s.execute(
                select(User).where(User.id.in_(uids))
            )).scalars().all()}

    text = f"📋 AUDITORIA DE AÇÕES\n\nTotal: {total} | Página {page + 1}\n\n"

    if not logs:
        text += "Nenhuma ação registrada."
    else:
        for l in logs:
            u = users.get(l.admin_id)
            uname = (u.full_name if u else f"Admin {l.admin_id}")[:20]
            text += (
                f"🕐 {l.created_at.strftime('%d/%m %H:%M')} — {uname}\n"
                f"   ⚙️ <code>{l.action}</code>\n\n"
            )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"rpt_audit_{page - 1}"))
    if (page + 1) * 15 < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"rpt_audit_{page + 1}"))
    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="📥 Exportar auditoria", callback_data="rpt_audit_export")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_reports")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data == "rpt_audit_export")
async def audit_export(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        logs = (await s.execute(
            select(AdminLog).order_by(desc(AdminLog.created_at)).limit(2000)
        )).scalars().all()

    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["Data", "Admin ID", "Ação", "Detalhes"])
    for l in logs:
        writer.writerow([
            l.created_at.strftime("%d/%m/%Y %H:%M:%S"),
            l.admin_id,
            l.action,
            json.dumps(l.details, ensure_ascii=False) if l.details else "",
        ])

    content = out.getvalue().encode("utf-8-sig")
    file = BufferedInputFile(
        content,
        filename=f"auditoria_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv",
    )
    await cb.message.answer_document(
        file,
        caption=f"📊 <b>Auditoria completa</b>\n\n{len(logs)} ações",
    )
    await cb.answer("Exportado.")
