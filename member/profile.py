from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from datetime import datetime
from io import BytesIO

from bot import dp
from database import AsyncSessionLocal
from models import (
    User, Wallet, WalletTransaction, Order, Payment,
    Affiliate, Referral,
)
from settings_cache import get_settings

from admin.customization import get_rendered_message

router = Router()
dp.include_router(router)

TX_TYPE_LABEL = {
    "purchase": "🛒 Compra",
    "recharge": "💎 Recarga",
    "refund": "💜 Reembolso",
    "bonus": "🎁 Bônus",
    "affiliate": "🤝 Comissão",
    "points_conversion": "🏆 Conversão",
    "admin_add": "➕ Crédito (admin)",
    "admin_remove": "➖ Débito (admin)",
    "admin_correct": "🔧 Correção",
    "withdraw": "📤 Saque",
}


# ============================================================
# HELPERS DE MÉTRICAS
# ============================================================
async def _collect_profile_data(user_id: int) -> dict:
    async with AsyncSessionLocal() as s:
        u = await s.get(User, user_id)
        w = await s.get(Wallet, user_id)

        total_purchases = (await s.execute(
            select(func.count(Order.id))
            .where(Order.user_id == user_id)
            .where(Order.status == "completed")
        )).scalar() or 0

        total_spent = (await s.execute(
            select(func.coalesce(func.sum(Order.total), 0))
            .where(Order.user_id == user_id)
            .where(Order.status == "completed")
        )).scalar() or 0.0

        total_recharges = (await s.execute(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.user_id == user_id)
            .where(Payment.status == "confirmed")
        )).scalar() or 0.0

        aff = await s.get(Affiliate, user_id)
        points = aff.points_balance if aff else 0
        referrals_count = (await s.execute(
            select(func.count(Referral.id))
            .where(Referral.referrer_id == user_id)
            .where(Referral.is_confirmed == True)
        )).scalar() or 0

    settings = await get_settings()
    bot_username = getattr(settings, "bot_username", None)
    ref_code = aff.referral_code if aff else None
    referral_link = (
        f"https://t.me/{bot_username}?start=ref_{ref_code}"
        if (bot_username and ref_code) else "—"
    )

    return {
        "user": u,
        "balance": w.balance if w else 0.0,
        "total_purchases": total_purchases,
        "total_spent": total_spent,
        "total_recharges": total_recharges,
        "points": points,
        "referrals_count": referrals_count,
        "referral_link": referral_link,
    }


# ============================================================
# TELA DE PERFIL
# ============================================================
@router.callback_query(F.data == "show_profile")
async def cb_show_profile(cb: CallbackQuery, state: FSMContext):
    await state.clear()

    settings = await get_settings()
    if not getattr(settings, "allow_profile_access", True):
        await cb.answer("O acesso ao perfil está temporariamente desabilitado.", show_alert=True)
        return

    data = await _collect_profile_data(cb.from_user.id)
    u = data["user"]

    variables = {
        "nome_usuario": (u.full_name if u else "Cliente"),
        "username": (u.username if u and u.username else "sem_username"),
        "telegram_id": str(cb.from_user.id),
        "saldo": f"{data['balance']:.2f}",
        "total_compras": str(data["total_purchases"]),
        "total_recargas": f"{data['total_recharges']:.2f}",
        "total_gifts": "0.00",
        "total_saques": "0.00",
        "pontos_indicacao": str(data["points"]),
        "total_indicados": str(data["referrals_count"]),
        "link_indicacao": data["referral_link"],
    }

    text, template_kb = await get_rendered_message("profile", variables)
    if not text:
        text = (
            f"◈ BELLAMY | MEU PERFIL\n\n"
            f"👤 {variables['nome_usuario']}\n"
            f"💠 @{variables['username']}\n"
            f"🆔 {variables['telegram_id']}\n\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"💎 Saldo: R$ {variables['saldo']}\n"
            f"🛒 Compras: {variables['total_compras']}\n"
            f"💎 Recargas: R$ {variables['total_recargas']}\n"
            f"🏆 Pontos: {variables['pontos_indicacao']}\n"
            f"👥 Indicados: {variables['total_indicados']}"
        )

    buttons = []
    if template_kb:
        for row in template_kb.inline_keyboard:
            new_row = []
            for b in row:
                cb_data = b.callback_data or ""
                mapping = {
                    "show_history": "show_history",
                    "show_subscriptions": "show_subscriptions",
                    "main_menu": "main_menu",
                }
                new_row.append(InlineKeyboardButton(
                    text=b.text,
                    callback_data=mapping.get(cb_data, cb_data),
                    url=b.url,
                ))
            if new_row:
                buttons.append(new_row)

    if not buttons:
        buttons = [
            [InlineKeyboardButton(text="📄 Histórico", callback_data="show_history")],
            [InlineKeyboardButton(text="📺 Minhas assinaturas", callback_data="show_subscriptions")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="main_menu")],
        ]

    if data["referral_link"] != "—":
        buttons.insert(0, [
            InlineKeyboardButton(text="🤝 Meu link de indicação", callback_data="show_affiliates"),
        ])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# HISTÓRICO (TELA)
# ============================================================
@router.callback_query(F.data == "show_history")
async def cb_show_history(cb: CallbackQuery, state: FSMContext):
    await state.clear()

    async with AsyncSessionLocal() as s:
        total_purchases = (await s.execute(
            select(func.count(Order.id))
            .where(Order.user_id == cb.from_user.id)
            .where(Order.status == "completed")
        )).scalar() or 0

        total_spent = (await s.execute(
            select(func.coalesce(func.sum(Order.total), 0))
            .where(Order.user_id == cb.from_user.id)
            .where(Order.status == "completed")
        )).scalar() or 0.0

        total_recharges = (await s.execute(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.user_id == cb.from_user.id)
            .where(Payment.status == "confirmed")
        )).scalar() or 0.0

        total_tx = (await s.execute(
            select(func.count(WalletTransaction.id))
            .where(WalletTransaction.user_id == cb.from_user.id)
        )).scalar() or 0

        w = await s.get(Wallet, cb.from_user.id)

    balance = w.balance if w else 0.0

    text = (
        "◈ BELLAMY | HISTÓRICO\n\n"
        "Confira suas movimentações.\n\n"
        f"💰 Compras: {total_purchases} (R$ {total_spent:.2f})\n"
        f"💎 Recargas: R$ {total_recharges:.2f}\n"
        f"📊 Movimentações: {total_tx}\n"
        f"💳 Saldo atual: R$ {balance:.2f}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📄 Gerar histórico em PDF",
            callback_data="history_pdf",
        )],
        [InlineKeyboardButton(
            text="📜 Ver movimentações",
            callback_data="history_tx_0",
        )],
        [InlineKeyboardButton(
            text="↩️ Voltar ao perfil",
            callback_data="show_profile",
        )],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# LISTAGEM PAGINADA DE MOVIMENTAÇÕES
# ============================================================
PAGE_SIZE = 10


@router.callback_query(F.data.startswith("history_tx_"))
async def cb_history_tx(cb: CallbackQuery):
    try:
        page = int(cb.data.replace("history_tx_", ""))
    except (ValueError, TypeError):
        page = 0
    page = max(0, page)

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(WalletTransaction.id))
            .where(WalletTransaction.user_id == cb.from_user.id)
        )).scalar() or 0

        stmt = (
            select(WalletTransaction)
            .where(WalletTransaction.user_id == cb.from_user.id)
            .order_by(WalletTransaction.created_at.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        txs = (await s.execute(stmt)).scalars().all()

        w = await s.get(Wallet, cb.from_user.id)

    max_page = max(0, (total - 1) // PAGE_SIZE) if total else 0
    if page > max_page:
        page = max_page

    if not txs:
        text = (
            "📜 MOVIMENTAÇÕES\n\n"
            "Você ainda não possui movimentações na carteira."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="show_history")],
        ])
        await cb.message.edit_text(text, reply_markup=kb)
        await cb.answer()
        return

    lines = [f"📜 MOVIMENTAÇÕES\n"]
    lines.append(f"Total: {total} | Página {page + 1} de {max_page + 1}")
    if w:
        lines.append(f"💳 Saldo atual: R$ {w.balance:.2f}")
    lines.append("")

    for tx in txs:
        sign = "➕" if tx.amount >= 0 else "➖"
        label = TX_TYPE_LABEL.get(tx.type, tx.type)
        ts = tx.created_at.strftime("%d/%m/%Y %H:%M")
        lines.append(
            f"{sign} R$ {abs(tx.amount):.2f} — {label}\n"
            f"   {tx.description[:60]}\n"
            f"   Saldo: R$ {tx.balance_before:.2f} → R$ {tx.balance_after:.2f}\n"
            f"   {ts}\n"
        )

    buttons = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"history_tx_{page - 1}",
        ))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"history_tx_{page + 1}",
        ))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(
        text="📄 Gerar PDF completo", callback_data="history_pdf",
    )])
    buttons.append([InlineKeyboardButton(
        text="↩️ Voltar", callback_data="show_history",
    )])

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


# ============================================================
# GERAR PDF
# ============================================================
@router.callback_query(F.data == "history_pdf")
async def cb_history_pdf(cb: CallbackQuery):
    await cb.answer("⏳ Gerando PDF...")

    try:
        pdf_bytes = await _build_history_pdf(cb.from_user.id)
    except Exception as e:
        await cb.message.answer(
            f"❌ Erro ao gerar PDF: {str(e)[:200]}"
        )
        return

    if not pdf_bytes:
        await cb.message.answer(
            "❌ Não foi possível gerar o PDF. Verifique se possui movimentações."
        )
        return

    filename = f"historico_{cb.from_user.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.pdf"
    file = BufferedInputFile(pdf_bytes, filename=filename)

    await cb.message.answer_document(
        file,
        caption=(
            "📄 <b>Histórico de movimentações</b>\n\n"
            "Contém todas as transações da sua carteira com data, "
            "tipo, valor e saldo."
        ),
    )


async def _build_history_pdf(user_id: int) -> bytes | None:
    """Gera um PDF do histórico usando fpdf2 (import tardio)."""
    try:
        from fpdf import FPDF
    except ImportError:
        # Fallback: gera TXT se fpdf2 não estiver instalado
        return await _build_history_txt(user_id)

    async with AsyncSessionLocal() as s:
        u = await s.get(User, user_id)
        w = await s.get(Wallet, user_id)
        txs = (await s.execute(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == user_id)
            .order_by(WalletTransaction.created_at.desc())
            .limit(500)
        )).scalars().all()

    if not txs:
        return None

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Cabeçalho
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "BELLAMY | Historico de Movimentacoes", ln=True, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC", ln=True, align="C")
    pdf.ln(4)

    # Dados do usuário
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Dados do Cliente", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, f"Nome: {(u.full_name if u else 'Cliente')}", ln=True)
    pdf.cell(0, 5, f"Username: @{(u.username if u and u.username else 'sem_username')}", ln=True)
    pdf.cell(0, 5, f"Telegram ID: {user_id}", ln=True)
    pdf.cell(0, 5, f"Saldo atual: R$ {(w.balance if w else 0.0):.2f}", ln=True)
    pdf.ln(4)

    # Cabeçalho da tabela
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(40, 40, 80)
    pdf.set_text_color(255, 255, 255)

    col_widths = [10, 32, 40, 22, 22, 22, 32]
    headers = ["#", "Data", "Descricao", "Tipo", "Valor", "Saldo apos", "Referencia"]

    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 7, h, border=1, fill=True, align="C")
    pdf.ln()

    # Linhas
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 8)

    for idx, tx in enumerate(txs, 1):
        sign = "+" if tx.amount >= 0 else "-"
        tipo = TX_TYPE_LABEL.get(tx.type, tx.type).replace("🛒", "").replace("💎", "")
        tipo = tipo.replace("💜", "").replace("🎁", "").replace("🤝", "").replace("🏆", "").strip()
        tipo = tipo.replace("➕", "+").replace("➖", "-").replace("🔧", "").strip()
        desc = (tx.description or "")[:45]

        vals = [
            str(idx),
            tx.created_at.strftime("%d/%m/%y %H:%M"),
            desc,
            tipo[:15],
            f"{sign} R$ {abs(tx.amount):.2f}",
            f"R$ {tx.balance_after:.2f}",
            "",
        ]
        for i, v in enumerate(vals):
            pdf.cell(col_widths[i], 6, v, border=1)
        pdf.ln()

    # Rodapé
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 5, "Este documento contem dados confidenciais. Nao compartilhe.", ln=True, align="C")
    pdf.cell(0, 5, "BELLAMY | Digital Store", ln=True, align="C")

    return bytes(pdf.output())


async def _build_history_txt(user_id: int) -> bytes:
    """Fallback em TXT caso fpdf2 não esteja disponível."""
    async with AsyncSessionLocal() as s:
        u = await s.get(User, user_id)
        w = await s.get(Wallet, user_id)
        txs = (await s.execute(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == user_id)
            .order_by(WalletTransaction.created_at.desc())
            .limit(500)
        )).scalars().all()

    lines = []
    lines.append("═" * 70)
    lines.append("BELLAMY | HISTÓRICO DE MOVIMENTAÇÕES")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append("═" * 70)
    lines.append("")
    lines.append(f"Cliente: {(u.full_name if u else '—')}")
    lines.append(f"Username: @{(u.username if u and u.username else 'sem_username')}")
    lines.append(f"Telegram ID: {user_id}")
    lines.append(f"Saldo atual: R$ {(w.balance if w else 0.0):.2f}")
    lines.append("")
    lines.append("─" * 70)
    lines.append(f"{'Data':<16} | {'Tipo':<16} | {'Valor':>10} | {'Saldo após':>12}")
    lines.append("─" * 70)

    for tx in txs:
        label = TX_TYPE_LABEL.get(tx.type, tx.type)
        lines.append(
            f"{tx.created_at.strftime('%d/%m/%y %H:%M'):<16} | "
            f"{label[:16]:<16} | "
            f"{tx.amount:>+10.2f} | "
            f"{tx.balance_after:>12.2f}"
        )
        lines.append(f"   {tx.description}")

    return "\n".join(lines).encode("utf-8")
