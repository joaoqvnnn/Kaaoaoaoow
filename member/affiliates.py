from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ForceReply, BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func
from datetime import datetime

from bot import dp
from database import AsyncSessionLocal
from models import (
    Affiliate, AffiliateTransaction, Referral, User, Wallet, Order,
)
from settings_cache import get_settings

from admin.affiliates import (
    get_or_create_affiliate, convert_points_to_balance,
)
from admin.customization import get_rendered_message

router = Router()
dp.include_router(router)

REF_PAGE_SIZE = 8
HIST_PAGE_SIZE = 10


class AffStates(StatesGroup):
    waiting_points_qty = State()


TX_LABEL = {
    "referral_bonus":   "🤝 Indicação confirmada",
    "purchase_bonus":   "🛒 Compra do indicado",
    "conversion":       "💎 Conversão em saldo",
    "admin_add":        "➕ Crédito (admin)",
    "admin_remove":     "➖ Débito (admin)",
    "admin_correct":    "🔧 Correção",
    "reversal":         "↩️ Estorno",
}


async def _get_affiliate_data(user_id: int) -> dict:
    aff = await get_or_create_affiliate(user_id)

    settings = await get_settings()
    bot_username = getattr(settings, "bot_username", None)
    rate = getattr(settings, "points_conversion_rate", 100.0)

    referral_link = (
        f"https://t.me/{bot_username}?start=ref_{aff.referral_code}"
        if bot_username else "—"
    )

    async with AsyncSessionLocal() as s:
        w = await s.get(Wallet, user_id)

    resgate_value = round(aff.points_balance / rate, 2) if rate > 0 else 0.0

    return {
        "aff": aff,
        "rate": rate,
        "referral_link": referral_link,
        "wallet_balance": w.balance if w else 0.0,
        "resgate_value": resgate_value,
    }


# ============================================================
# TELA PRINCIPAL DE AFILIADOS
# ============================================================
async def _render_main(target, user_id: int):
    settings = await get_settings()

    if not getattr(settings, "affiliates_enabled", True):
        text = "🤝 O programa de afiliados está temporariamente indisponível."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
        ])
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=kb)
        else:
            await target.answer(text, reply_markup=kb)
        return

    data = await _get_affiliate_data(user_id)
    aff = data["aff"]

    variables = {
        "pontos": str(aff.points_balance),
        "valor_resgate": f"{data['resgate_value']:.2f}",
        "total_indicados": str(aff.total_referrals),
        "indicacoes_confirmadas": str(aff.confirmed_referrals),
        "link_indicacao": data["referral_link"],
    }

    text, template_kb = await get_rendered_message("affiliates", variables)
    if not text:
        text = (
            f"◈ BELLAMY | AFILIADOS\n\n"
            f"Convide pessoas e acumule recompensas através das suas indicações.\n\n"
            f"🏆 Pontos acumulados: {aff.points_balance}\n"
            f"💎 Saldo disponível para resgate: R$ {data['resgate_value']:.2f}\n"
            f"👥 Pessoas indicadas: {aff.total_referrals}\n"
            f"✅ Indicações confirmadas: {aff.confirmed_referrals}\n\n"
            f"🔗 Seu link exclusivo:\n\n"
            f"{data['referral_link']}"
        )

    buttons = []
    conversion_enabled = getattr(settings, "points_conversion_enabled", True)

    if conversion_enabled:
        buttons.append([InlineKeyboardButton(
            text="💎 Trocar pontos por saldo",
            callback_data="show_convert_points",
        )])

    buttons.append([InlineKeyboardButton(text="👥 Meus indicados", callback_data="aff_refs_0")])
    buttons.append([InlineKeyboardButton(text="📊 Meu desempenho", callback_data="aff_perf")])
    buttons.append([InlineKeyboardButton(text="📜 Histórico de recompensas", callback_data="aff_hist_0")])
    buttons.append([InlineKeyboardButton(text="📖 Como funciona", callback_data="aff_how")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="main_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
        await target.message.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "show_affiliates")
async def cb_show_affiliates(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_main(cb, cb.from_user.id)
    await cb.answer()


# ============================================================
# CONVERSÃO — MENU
# ============================================================
@router.callback_query(F.data == "show_convert_points")
async def cb_show_convert(cb: CallbackQuery, state: FSMContext):
    await state.clear()

    settings = await get_settings()
    if not getattr(settings, "points_conversion_enabled", True):
        await cb.answer("Conversão temporariamente indisponível.", show_alert=True)
        return

    data = await _get_affiliate_data(cb.from_user.id)
    aff = data["aff"]
    rate = data["rate"]
    min_pts = getattr(settings, "min_points_to_convert", 1)

    if aff.points_balance < min_pts:
        await cb.answer(
            f"Você precisa de pelo menos {min_pts} pts para converter. "
            f"Seu saldo atual: {aff.points_balance} pts.",
            show_alert=True,
        )
        return

    value = round(aff.points_balance / rate, 2) if rate > 0 else 0.0

    text = (
        f"💎 TROCA DE PONTOS\n\n"
        f"Transforme seus pontos em saldo na carteira Bellamy.\n\n"
        f"🏆 Seus pontos: {aff.points_balance}\n"
        f"💱 Taxa: {rate:.2f} pts = R$ 1,00\n"
        f"💰 Valor disponível: R$ {value:.2f}"
    )

    buttons = [
        [InlineKeyboardButton(
            text=f"Converter todos ({aff.points_balance} pts)",
            callback_data=f"aff_convert_all_{aff.points_balance}",
        )],
        [InlineKeyboardButton(text="Escolher quantidade", callback_data="aff_convert_custom")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="show_affiliates")],
    ]
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# CONVERSÃO — TODOS OS PONTOS
# ============================================================
@router.callback_query(F.data.startswith("aff_convert_all_"))
async def cb_convert_all(cb: CallbackQuery):
    try:
        points = int(cb.data.replace("aff_convert_all_", ""))
    except (ValueError, TypeError):
        await cb.answer("Valor inválido.", show_alert=True)
        return

    data = await _get_affiliate_data(cb.from_user.id)
    rate = data["rate"]
    value = round(points / rate, 2) if rate > 0 else 0.0

    text = (
        f"◈ CONFIRMAR CONVERSÃO\n\n"
        f"🏆 Pontos utilizados: {points}\n"
        f"💰 Saldo que receberá: R$ {value:.2f}\n\n"
        f"Deseja confirmar a troca?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirmar troca", callback_data=f"aff_convert_confirm_{points}")],
        [InlineKeyboardButton(text="↩️ Cancelar", callback_data="show_convert_points")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# CONVERSÃO — QUANTIDADE PERSONALIZADA
# ============================================================
@router.callback_query(F.data == "aff_convert_custom")
async def cb_convert_custom(cb: CallbackQuery, state: FSMContext):
    data = await _get_affiliate_data(cb.from_user.id)
    aff = data["aff"]
    rate = data["rate"]

    settings = await get_settings()
    min_pts = getattr(settings, "min_points_to_convert", 1)
    max_pts = getattr(settings, "max_points_per_conversion", 0)

    extra = ""
    if max_pts > 0:
        extra = f"\n📌 Máximo por conversão: {max_pts} pts"

    await state.set_state(AffStates.waiting_points_qty)
    await cb.message.answer(
        f"✏️ QUANTIDADE PARA CONVERSÃO\n\n"
        f"🏆 Pontos disponíveis: {aff.points_balance}\n"
        f"💱 Taxa: {rate:.2f} pts = R$ 1,00"
        f"{extra}\n\n"
        f"Digite a quantidade de pontos que deseja converter "
        f"(mínimo {min_pts}):",
        reply_markup=ForceReply(input_field_placeholder="Pontos"),
    )
    await cb.answer()


@router.message(AffStates.waiting_points_qty)
async def msg_convert_custom(m: Message, state: FSMContext):
    raw = (m.text or "").strip()
    if raw.lower() in ("/cancelar", "cancelar"):
        await state.clear()
        await m.answer("Operação cancelada.")
        return

    try:
        points = int(raw)
        if points <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await m.answer("❌ Envie um número inteiro positivo.")
        return

    await state.clear()

    settings = await get_settings()
    min_pts = getattr(settings, "min_points_to_convert", 1)
    max_pts = getattr(settings, "max_points_per_conversion", 0)

    data = await _get_affiliate_data(m.from_user.id)
    aff = data["aff"]
    rate = data["rate"]

    if points < min_pts:
        await m.answer(f"❌ Mínimo: {min_pts} pts.")
        return
    if max_pts > 0 and points > max_pts:
        await m.answer(f"❌ Máximo por conversão: {max_pts} pts.")
        return
    if points > aff.points_balance:
        await m.answer(f"❌ Você tem apenas {aff.points_balance} pts.")
        return

    value = round(points / rate, 2) if rate > 0 else 0.0

    text = (
        f"◈ CONFIRMAR CONVERSÃO\n\n"
        f"🏆 Pontos utilizados: {points}\n"
        f"💰 Saldo que receberá: R$ {value:.2f}\n\n"
        f"Deseja confirmar a troca?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirmar troca", callback_data=f"aff_convert_confirm_{points}")],
        [InlineKeyboardButton(text="↩️ Cancelar", callback_data="show_convert_points")],
    ])
    await m.answer(text, reply_markup=kb)


# ============================================================
# CONVERSÃO — EXECUTAR (§14.4)
# ============================================================
@router.callback_query(F.data.startswith("aff_convert_confirm_"))
async def cb_convert_execute(cb: CallbackQuery):
    try:
        points = int(cb.data.replace("aff_convert_confirm_", ""))
    except (ValueError, TypeError):
        await cb.answer("Valor inválido.", show_alert=True)
        return

    ok, msg, op_id = await convert_points_to_balance(cb.from_user.id, points)

    if not ok:
        await cb.message.edit_text(
            f"❌ {msg}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Voltar", callback_data="show_affiliates")],
            ]),
        )
        await cb.answer("Conversão não realizada.", show_alert=True)
        return

    data = await _get_affiliate_data(cb.from_user.id)
    new_aff = data["aff"]
    rate = data["rate"]
    value = round(points / rate, 2) if rate > 0 else 0.0

    async with AsyncSessionLocal() as s:
        w = await s.get(Wallet, cb.from_user.id)
    new_balance = w.balance if w else 0.0

    op_line = f"\n\n🆔 Operação: #{op_id}" if op_id else ""

    text = (
        f"✅ CONVERSÃO REALIZADA!\n\n"
        f"Seus pontos foram convertidos com sucesso.\n\n"
        f"💎 Saldo adicionado: R$ {value:.2f}\n"
        f"🏆 Pontos utilizados: {points}\n"
        f"💰 Novo saldo: R$ {new_balance:.2f}"
        f"{op_line}\n\n"
        f"🏆 Pontos restantes: {new_aff.points_balance}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Minha carteira", callback_data="show_profile")],
        [InlineKeyboardButton(text="↩️ Voltar aos afiliados", callback_data="show_affiliates")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Conversão concluída.")


# ============================================================
# MEUS INDICADOS
# ============================================================
@router.callback_query(F.data.startswith("aff_refs_"))
async def cb_my_refs(cb: CallbackQuery):
    try:
        page = int(cb.data.replace("aff_refs_", ""))
    except (ValueError, TypeError):
        page = 0
    page = max(0, page)

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(Referral.id))
            .where(Referral.referrer_id == cb.from_user.id)
        )).scalar() or 0
        confirmed = (await s.execute(
            select(func.count(Referral.id))
            .where(Referral.referrer_id == cb.from_user.id)
            .where(Referral.is_confirmed == True)
        )).scalar() or 0

        stmt = (
            select(Referral)
            .where(Referral.referrer_id == cb.from_user.id)
            .order_by(Referral.created_at.desc())
            .offset(page * REF_PAGE_SIZE)
            .limit(REF_PAGE_SIZE)
        )
        refs = (await s.execute(stmt)).scalars().all()

        uids = [r.referred_id for r in refs]
        users = {}
        if uids:
            users = {
                u.id: u for u in (await s.execute(
                    select(User).where(User.id.in_(uids))
                )).scalars().all()
            }

    max_page = max(0, (total - 1) // REF_PAGE_SIZE) if total else 0
    pending = total - confirmed

    lines = [
        "👥 MEUS INDICADOS\n",
        f"👥 Total: {total}",
        f"✅ Confirmados: {confirmed}",
        f"⏳ Pendentes: {pending}",
        f"📄 Página {page + 1} de {max_page + 1}\n",
    ]

    if not refs:
        lines.append("<i>Você ainda não indicou ninguém.</i>")
    else:
        for i, r in enumerate(refs, 1 + page * REF_PAGE_SIZE):
            u = users.get(r.referred_id)
            name = (u.full_name if u else f"User {r.referred_id}")[:24]
            icon = "✅" if r.is_confirmed else "⏳"
            date = r.created_at.strftime("%d/%m/%Y")
            lines.append(f"{i}. {icon} {name} — {date}")

    buttons = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"aff_refs_{page - 1}"))
    if (page + 1) * REF_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"aff_refs_{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="show_affiliates")])

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


# ============================================================
# MEU DESEMPENHO
# ============================================================
@router.callback_query(F.data == "aff_perf")
async def cb_aff_perf(cb: CallbackQuery):
    data = await _get_affiliate_data(cb.from_user.id)
    aff = data["aff"]
    rate = data["rate"]

    async with AsyncSessionLocal() as s:
        buyers_count = (await s.execute(
            select(func.count(func.distinct(Referral.referred_id)))
            .join(Order, Order.user_id == Referral.referred_id)
            .where(Referral.referrer_id == cb.from_user.id)
            .where(Referral.is_confirmed == True)
            .where(Order.status == "completed")
        )).scalar() or 0

    rate_conv = (
        (aff.confirmed_referrals / aff.total_referrals * 100)
        if aff.total_referrals else 0.0
    )
    purchase_conv = (
        (buyers_count / aff.confirmed_referrals * 100)
        if aff.confirmed_referrals else 0.0
    )
    points_value = round(aff.points_balance / rate, 2) if rate > 0 else 0.0

    text = (
        f"📊 MEU DESEMPENHO\n\n"
        f"🏆 Pontos acumulados: {aff.total_points_earned}\n"
        f"💎 Pontos disponíveis: {aff.points_balance}\n"
        f"💱 Valor em R$: R$ {points_value:.2f}\n\n"
        f"👥 Total de indicações: {aff.total_referrals}\n"
        f"✅ Confirmadas: {aff.confirmed_referrals}\n"
        f"📈 Taxa de confirmação: {rate_conv:.1f}%\n\n"
        f"🛒 Indicados que compraram: {buyers_count}\n"
        f"📈 Conversão em compra: {purchase_conv:.1f}%\n\n"
        f"💵 Total já recebido em R$: R$ {aff.total_balance_earned:.2f}\n"
        f"💱 Pontos já convertidos: {aff.total_points_converted}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Ver histórico", callback_data="aff_hist_0")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="show_affiliates")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# HISTÓRICO DE RECOMPENSAS
# ============================================================
@router.callback_query(F.data.startswith("aff_hist_"))
async def cb_aff_hist(cb: CallbackQuery):
    try:
        page = int(cb.data.replace("aff_hist_", ""))
    except (ValueError, TypeError):
        page = 0
    page = max(0, page)

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(AffiliateTransaction.id))
            .where(AffiliateTransaction.user_id == cb.from_user.id)
        )).scalar() or 0

        stmt = (
            select(AffiliateTransaction)
            .where(AffiliateTransaction.user_id == cb.from_user.id)
            .order_by(AffiliateTransaction.created_at.desc())
            .offset(page * HIST_PAGE_SIZE)
            .limit(HIST_PAGE_SIZE)
        )
        txs = (await s.execute(stmt)).scalars().all()

        aff = await s.get(Affiliate, cb.from_user.id)

    max_page = max(0, (total - 1) // HIST_PAGE_SIZE) if total else 0
    balance = aff.points_balance if aff else 0

    lines = [f"📜 HISTÓRICO DE RECOMPENSAS\n"]
    lines.append(f"🏆 Saldo de pontos: {balance}")
    lines.append(f"📊 Total de movimentações: {total}")
    lines.append(f"📄 Página {page + 1} de {max_page + 1}\n")

    if not txs:
        lines.append("<i>Nenhuma movimentação registrada.</i>")
    else:
        for tx in txs:
            sign = "➕" if tx.amount >= 0 else "➖"
            label = TX_LABEL.get(tx.type, tx.type)
            date = tx.created_at.strftime("%d/%m/%Y %H:%M")
            lines.append(
                f"{sign} {abs(tx.amount)} pts — {label}\n"
                f"   {tx.description[:60]}\n"
                f"   {date}\n"
            )

    buttons = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"aff_hist_{page - 1}"))
    if (page + 1) * HIST_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"aff_hist_{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="📄 Gerar extrato PDF", callback_data="aff_hist_pdf")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="show_affiliates")])

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data == "aff_hist_pdf")
async def cb_aff_hist_pdf(cb: CallbackQuery):
    await cb.answer("⏳ Gerando extrato...")

    try:
        content = await _build_affiliate_history_pdf(cb.from_user.id)
    except Exception as e:
        await cb.message.answer(f"❌ Erro: {str(e)[:200]}")
        return

    if not content:
        await cb.message.answer("❌ Sem movimentações para exportar.")
        return

    filename = f"afiliados_{cb.from_user.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.pdf"
    file = BufferedInputFile(content, filename=filename)

    await cb.message.answer_document(
        file,
        caption="📄 <b>Extrato de recompensas</b>\n\nTodas as movimentações de pontos.",
    )


async def _build_affiliate_history_pdf(user_id: int) -> bytes:
    try:
        from fpdf import FPDF
    except ImportError:
        return await _build_affiliate_history_txt(user_id)

    async with AsyncSessionLocal() as s:
        u = await s.get(User, user_id)
        aff = await s.get(Affiliate, user_id)
        txs = (await s.execute(
            select(AffiliateTransaction)
            .where(AffiliateTransaction.user_id == user_id)
            .order_by(AffiliateTransaction.created_at.desc())
            .limit(500)
        )).scalars().all()

    if not txs and not aff:
        return b""

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "BELLAMY | Extrato de Afiliados", ln=True, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC", ln=True, align="C")
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Dados do Afiliado", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, f"Nome: {(u.full_name if u else '—')}", ln=True)
    pdf.cell(0, 5, f"Username: @{(u.username if u and u.username else 'sem_username')}", ln=True)
    pdf.cell(0, 5, f"Telegram ID: {user_id}", ln=True)
    if aff:
        pdf.cell(0, 5, f"Codigo: {aff.referral_code}", ln=True)
        pdf.cell(0, 5, f"Pontos disponiveis: {aff.points_balance}", ln=True)
        pdf.cell(0, 5, f"Pontos totais ganhos: {aff.total_points_earned}", ln=True)
        pdf.cell(0, 5, f"Total em R$: R$ {aff.total_balance_earned:.2f}", ln=True)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(40, 40, 80)
    pdf.set_text_color(255, 255, 255)

    cols = [10, 32, 60, 30, 30, 30]
    heads = ["#", "Data", "Descricao", "Tipo", "Pontos", "Saldo apos"]
    for i, h in enumerate(heads):
        pdf.cell(cols[i], 7, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 8)

    for idx, tx in enumerate(txs, 1):
        tipo = TX_LABEL.get(tx.type, tx.type)
        for e in ("🤝", "🛒", "💎", "➕", "➖", "🔧", "↩️", "🏆"):
            tipo = tipo.replace(e, "").strip()
        vals = [
            str(idx),
            tx.created_at.strftime("%d/%m/%y %H:%M"),
            (tx.description or "")[:40],
            tipo[:15],
            f"{tx.amount:+d}",
            str(tx.balance_after),
        ]
        for i, v in enumerate(vals):
            pdf.cell(cols[i], 6, v, border=1)
        pdf.ln()

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 5, "BELLAMY | Digital Store", ln=True, align="C")

    return bytes(pdf.output())


async def _build_affiliate_history_txt(user_id: int) -> bytes:
    async with AsyncSessionLocal() as s:
        u = await s.get(User, user_id)
        aff = await s.get(Affiliate, user_id)
        txs = (await s.execute(
            select(AffiliateTransaction)
            .where(AffiliateTransaction.user_id == user_id)
            .order_by(AffiliateTransaction.created_at.desc())
            .limit(500)
        )).scalars().all()

    lines = []
    lines.append("═" * 60)
    lines.append("BELLAMY | EXTRATO DE AFILIADOS")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append("═" * 60)
    lines.append("")
    lines.append(f"Cliente: {(u.full_name if u else '—')}")
    lines.append(f"Username: @{(u.username if u and u.username else 'sem_username')}")
    lines.append(f"Telegram ID: {user_id}")
    if aff:
        lines.append(f"Código: {aff.referral_code}")
        lines.append(f"Pontos disponíveis: {aff.points_balance}")
        lines.append(f"Pontos totais: {aff.total_points_earned}")
    lines.append("")
    lines.append("─" * 60)

    for tx in txs:
        label = TX_LABEL.get(tx.type, tx.type)
        lines.append(
            f"{tx.created_at.strftime('%d/%m/%y %H:%M')} | "
            f"{tx.amount:+5d} pts | {label} | {tx.description[:40]}"
        )

    return "\n".join(lines).encode("utf-8")


# ============================================================
# COMO FUNCIONA
# ============================================================
@router.callback_query(F.data == "aff_how")
async def cb_aff_how(cb: CallbackQuery):
    settings = await get_settings()
    rate = getattr(settings, "points_conversion_rate", 100.0)
    min_pts = getattr(settings, "min_points_to_convert", 100)
    pts_ref = getattr(settings, "points_per_referral", 10)
    pts_purchase = getattr(settings, "points_per_purchase", 0)

    text = (
        "📖 COMO FUNCIONA O PROGRAMA?\n\n"
        "1. Compartilhe seu link exclusivo.\n"
        "2. Novos usuários entram através dele.\n"
        "3. O sistema identifica as indicações.\n"
        "4. Quando as condições forem cumpridas, você recebe pontos.\n"
        "5. Troque seus pontos por saldo na carteira.\n\n"
        "━━━━━━━━━━━━━━\n\n"
        "📊 <b>Regras</b>\n\n"
        f"🤝 Por indicação confirmada: {pts_ref} pts\n"
    )
    if pts_purchase > 0:
        text += f"🛒 Por compra do indicado: {pts_purchase} pts\n"
    text += (
        f"\n💱 Taxa de conversão: {rate:.2f} pts = R$ 1,00\n"
        f"📉 Mínimo para converter: {min_pts} pts\n"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Copiar meu link", callback_data="aff_copy_link")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="show_affiliates")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "aff_copy_link")
async def cb_aff_copy_link(cb: CallbackQuery):
    data = await _get_affiliate_data(cb.from_user.id)
    link = data["referral_link"]

    if link == "—":
        await cb.answer("Link indisponível no momento.", show_alert=True)
        return

    await cb.message.answer(
        f"🔗 <b>Seu link exclusivo:</b>\n\n<code>{link}</code>\n\n"
        f"Compartilhe e ganhe pontos por cada pessoa que se cadastrar."
    )
    await cb.answer("Link enviado.")
