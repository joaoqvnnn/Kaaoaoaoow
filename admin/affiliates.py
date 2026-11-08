from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, or_
from datetime import datetime, timedelta
import secrets
import string

from bot import dp, bot
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    Affiliate, AffiliateTransaction, Referral, User,
    Wallet, WalletTransaction, Order, AdminLog, BotSettings,
)
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

PAGE_SIZE = 10
TX_PAGE_SIZE = 12
REF_PAGE_SIZE = 12


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class AS(StatesGroup):
    search = State()
    adjust_amount = State()
    adjust_reason = State()
    set_config = State()


TX_LABEL = {
    "referral_bonus":   "🤝 Indicação confirmada",
    "purchase_bonus":   "🛒 Compra do indicado",
    "conversion":       "💎 Conversão em saldo",
    "admin_add":        "➕ Adição (admin)",
    "admin_remove":     "➖ Remoção (admin)",
    "admin_correct":    "🔧 Correção (admin)",
    "reversal":         "↩️ Estorno",
}

CONFIG_KEYS = {
    "points_per_referral":       ("🤝 Pontos por indicação",                 int,   0,   100000),
    "points_per_purchase":       ("🛒 Pontos por compra do indicado",        int,   0,   100000),
    "points_conversion_rate":    ("💎 Taxa de conversão (pts → R$ 1,00)",    float, 0.01, 10000),
    "min_points_to_convert":     ("📉 Mínimo de pontos para converter",      int,   1,   1000000),
    "max_points_per_conversion": ("📈 Máximo de pontos por conversão",       int,   0,   10000000),
    "min_purchase_for_points":   ("🛒 Valor mínimo de compra p/ pontuar (R$)", float, 0.0, 100000),
    "commission_percent":        ("💰 Comissão em R$ (% da compra)",         float, 0.0, 100),
    "max_referrals_per_user":    ("👥 Máximo de indicados por usuário",      int,   0,   1000000),
}


def gen_referral_code(length: int = 8) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


async def get_or_create_affiliate(user_id: int) -> Affiliate:
    async with AsyncSessionLocal() as s:
        aff = await s.get(Affiliate, user_id)
        if aff:
            return aff
        for _ in range(10):
            code = gen_referral_code()
            existing = (await s.execute(
                select(Affiliate).where(Affiliate.referral_code == code)
            )).scalar_one_or_none()
            if not existing:
                break
        else:
            code = gen_referral_code(12)

        aff = Affiliate(user_id=user_id, referral_code=code)
        s.add(aff)
        await s.commit()
        await s.refresh(aff)
        return aff


# ============================================================
# MENU
# ============================================================
@router.callback_query(F.data == "admin_affiliates")
async def affiliates_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    today = datetime.utcnow().date()
    month_start = today.replace(day=1)
    settings = await get_settings(force=True)

    async with AsyncSessionLocal() as s:
        total_affiliates = (await s.execute(
            select(func.count(Affiliate.user_id))
        )).scalar() or 0
        with_points = (await s.execute(
            select(func.count(Affiliate.user_id)).where(Affiliate.points_balance > 0)
        )).scalar() or 0
        total_points = (await s.execute(
            select(func.coalesce(func.sum(Affiliate.points_balance), 0))
        )).scalar() or 0
        total_earned = (await s.execute(
            select(func.coalesce(func.sum(Affiliate.total_balance_earned), 0))
        )).scalar() or 0.0

        refs_total = (await s.execute(
            select(func.count(Referral.id))
        )).scalar() or 0
        refs_confirmed = (await s.execute(
            select(func.count(Referral.id)).where(Referral.is_confirmed == True)
        )).scalar() or 0
        refs_pending = refs_total - refs_confirmed

        refs_today = (await s.execute(
            select(func.count(Referral.id))
            .where(Referral.created_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0
        refs_month = (await s.execute(
            select(func.count(Referral.id))
            .where(Referral.created_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0

        convs_total = (await s.execute(
            select(func.count(AffiliateTransaction.id))
            .where(AffiliateTransaction.type == "conversion")
        )).scalar() or 0
        convs_month = (await s.execute(
            select(func.count(AffiliateTransaction.id))
            .where(AffiliateTransaction.type == "conversion")
            .where(AffiliateTransaction.created_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0

    enabled = getattr(settings, "affiliates_enabled", True)
    approval = getattr(settings, "affiliate_approval_mode", "auto")
    rate = getattr(settings, "points_conversion_rate", 100.0)

    text = (
        "◈ BELLAMY | SISTEMA DE AFILIADOS\n\n"
        f"📌 Status: {'🟢 Ativado' if enabled else '🔴 Desativado'}\n"
        f"🔐 Aprovação: {'🤖 Automática' if approval == 'auto' else '👤 Manual'}\n"
        f"💱 Taxa de conversão: {rate:.2f} pts = R$ 1,00\n\n"
        f"👥 Afiliados cadastrados: {total_affiliates}\n"
        f"💎 Com pontos: {with_points}\n"
        f"🏆 Pontos em circulação: {int(total_points)}\n"
        f"💰 Total já convertido: R$ {total_earned:.2f}\n\n"
        f"🤝 INDICAÇÕES\n"
        f"📊 Total: {refs_total}\n"
        f"✅ Confirmadas: {refs_confirmed}\n"
        f"⏳ Pendentes: {refs_pending}\n"
        f"📅 Hoje: {refs_today} | Este mês: {refs_month}\n\n"
        f"💱 Conversões: {convs_total} | Mês: {convs_month}\n\n"
        "👇 Selecione uma opção:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Listar afiliados", callback_data="aff_list_0")],
        [InlineKeyboardButton(text="🏆 Top afiliados", callback_data="aff_top_0")],
        [InlineKeyboardButton(text="🔎 Buscar afiliado", callback_data="aff_search")],
        [InlineKeyboardButton(
            text=f"🤝 Indicações pendentes ({refs_pending})",
            callback_data="aff_refs_pending_0")],
        [InlineKeyboardButton(text="📜 Histórico de indicações", callback_data="aff_refs_all_0")],
        [InlineKeyboardButton(text="💱 Histórico de conversões", callback_data="aff_convs_0")],
        [InlineKeyboardButton(text="⚙️ Configurações do programa", callback_data="aff_config")],
        [InlineKeyboardButton(text="📊 Estatísticas", callback_data="aff_stats")],
        [InlineKeyboardButton(text="📥 Exportar relatório", callback_data="aff_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# LISTA DE AFILIADOS
# ============================================================
@router.callback_query(F.data.startswith("aff_list_"))
async def list_affiliates(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(Affiliate.user_id))
        )).scalar() or 0
        stmt = (
            select(Affiliate)
            .order_by(desc(Affiliate.total_points_earned))
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        affs = (await s.execute(stmt)).scalars().all()

        uids = [a.user_id for a in affs]
        users = {}
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}

    text = f"◈ BELLAMY | AFILIADOS\n\nTotal: {total} | Página {page + 1}\n\n"
    buttons = []
    if not affs:
        text += "Nenhum afiliado cadastrado."
    else:
        for a in affs:
            u = users.get(a.user_id)
            uname = (u.full_name if u else f"User {a.user_id}")[:22]
            uhandle = f"@{u.username}" if u and u.username else "—"
            text += (
                f"👤 {uname} ({uhandle})\n"
                f"   🏆 {a.points_balance} pts | "
                f"🤝 {a.confirmed_referrals}/{a.total_referrals}\n\n"
            )
            buttons.append([InlineKeyboardButton(
                text=f"👤 {uname} — {a.points_balance} pts",
                callback_data=f"aff_view_{a.user_id}",
            )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"aff_list_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"aff_list_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_affiliates")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# TOP AFILIADOS
# ============================================================
@router.callback_query(F.data.startswith("aff_top_"))
async def top_affiliates(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(Affiliate.user_id)).where(Affiliate.total_points_earned > 0)
        )).scalar() or 0
        stmt = (
            select(Affiliate)
            .where(Affiliate.total_points_earned > 0)
            .order_by(desc(Affiliate.total_points_earned))
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        affs = (await s.execute(stmt)).scalars().all()

        uids = [a.user_id for a in affs]
        users = {}
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}

    text = f"🏆 TOP AFILIADOS\n\nTotal: {total} | Página {page + 1}\n\n"
    buttons = []
    for i, a in enumerate(affs, start=page * PAGE_SIZE + 1):
        u = users.get(a.user_id)
        uname = (u.full_name if u else f"User {a.user_id}")[:22]
        medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}."))
        text += (
            f"{medal} {uname} — {a.total_points_earned} pts totais\n"
            f"     💎 {a.points_balance} pts disponíveis | 🤝 {a.confirmed_referrals}\n\n"
        )
        buttons.append([InlineKeyboardButton(
            text=f"{medal} {uname} — {a.total_points_earned} pts",
            callback_data=f"aff_view_{a.user_id}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"aff_top_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"aff_top_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_affiliates")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# DETALHE DO AFILIADO
# ============================================================
async def render_affiliate(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as s:
        user = await s.get(User, user_id)
        if not user:
            return (
                "❌ Usuário não encontrado.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_affiliates")]
                ]),
            )

        aff = await s.get(Affiliate, user_id)
        if not aff:
            # Criar se não existir
            pass

        refs_total = (await s.execute(
            select(func.count(Referral.id)).where(Referral.referrer_id == user_id)
        )).scalar() or 0
        refs_confirmed = (await s.execute(
            select(func.count(Referral.id))
            .where(Referral.referrer_id == user_id)
            .where(Referral.is_confirmed == True)
        )).scalar() or 0
        refs_pending = refs_total - refs_confirmed

    settings = await get_settings(force=True)
    rate = getattr(settings, "points_conversion_rate", 100.0)

    uname = user.full_name or f"User {user.id}"
    uhandle = f"@{user.username}" if user.username else "—"

    if not aff:
        text = (
            f"◈ BELLAMY | AFILIADO\n\n"
            f"👤 {uname}\n"
            f"💠 {uhandle}\n"
            f"🆔 <code>{user.id}</code>\n\n"
            f"⚠️ Este usuário ainda não possui perfil de afiliado.\n\n"
            f"👇 Toque abaixo para criar:"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Criar perfil de afiliado", callback_data=f"aff_create_{user_id}")],
            [InlineKeyboardButton(text="👤 Ver usuário", callback_data=f"usr_view_{user_id}")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_affiliates")],
        ])
        return text, kb

    points_value = aff.points_balance / rate if rate > 0 else 0.0
    conv_rate = (aff.confirmed_referrals / aff.total_referrals * 100) if aff.total_referrals else 0.0

    text = (
        f"◈ BELLAMY | AFILIADO\n\n"
        f"👤 {uname}\n"
        f"💠 {uhandle}\n"
        f"🆔 <code>{user.id}</code>\n\n"
        f"🔗 Código: <code>{aff.referral_code}</code>\n"
        f"📅 Cadastro: {aff.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"🏆 PONTOS\n"
        f"💎 Saldo de pontos: {aff.points_balance}\n"
        f"💰 Valor estimado: R$ {points_value:.2f}\n"
        f"📈 Total ganho: {aff.total_points_earned} pts\n"
        f"💱 Total convertido: {aff.total_points_converted} pts\n"
        f"💵 Total em R$: R$ {aff.total_balance_earned:.2f}\n\n"
        f"🤝 INDICAÇÕES\n"
        f"👥 Total: {aff.total_referrals}\n"
        f"✅ Confirmadas: {aff.confirmed_referrals}\n"
        f"⏳ Pendentes: {refs_pending}\n"
        f"📊 Taxa de confirmação: {conv_rate:.1f}%\n"
    )

    buttons = [
        [InlineKeyboardButton(text="➕ Adicionar pontos", callback_data=f"aff_add_{user_id}")],
        [InlineKeyboardButton(text="➖ Remover pontos", callback_data=f"aff_rem_{user_id}")],
        [InlineKeyboardButton(text="🔧 Corrigir pontos", callback_data=f"aff_corr_{user_id}")],
        [InlineKeyboardButton(text="💱 Converter pontos em R$", callback_data=f"aff_convert_{user_id}")],
        [InlineKeyboardButton(text="📜 Histórico de pontos", callback_data=f"aff_tx_{user_id}_0")],
        [InlineKeyboardButton(text="🤝 Ver indicados", callback_data=f"aff_refs_user_{user_id}_0")],
        [InlineKeyboardButton(text="👤 Ver usuário", callback_data=f"usr_view_{user_id}")],
        [InlineKeyboardButton(text="💎 Ver carteira", callback_data=f"wal_view_{user_id}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="aff_list_0")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("aff_view_"))
async def view_affiliate(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split("_")[-1])
    text, kb = await render_affiliate(uid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("aff_create_"))
async def create_affiliate(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split("_")[-1])
    aff = await get_or_create_affiliate(uid)
    await log_admin(cb.from_user.id, "affiliate_create", {"user_id": uid, "code": aff.referral_code})
    text, kb = await render_affiliate(uid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer(f"✅ Afiliado criado: {aff.referral_code}")


# ============================================================
# AJUSTE DE PONTOS
# ============================================================
@router.callback_query(F.data.startswith("aff_add_"))
async def add_points_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split("_")[-1])
    await state.update_data(target_user_id=uid, action_type="add")
    await state.set_state(AS.adjust_amount)
    await cb.message.answer(
        f"➕ ADICIONAR PONTOS\n\nUsuário: <code>{uid}</code>\n\n"
        f"Informe a quantidade de pontos a adicionar:"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("aff_rem_"))
async def rem_points_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split("_")[-1])
    await state.update_data(target_user_id=uid, action_type="remove")
    await state.set_state(AS.adjust_amount)
    await cb.message.answer(
        f"➖ REMOVER PONTOS\n\nUsuário: <code>{uid}</code>\n\n"
        f"Informe a quantidade de pontos a remover:"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("aff_corr_"))
async def corr_points_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split("_")[-1])
    await state.update_data(target_user_id=uid, action_type="correct")
    await state.set_state(AS.adjust_amount)
    await cb.message.answer(
        f"🔧 CORRIGIR PONTOS\n\nUsuário: <code>{uid}</code>\n\n"
        f"Informe o novo saldo de pontos:"
    )
    await cb.answer()


@router.message(AS.adjust_amount)
async def receive_points_amount(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        amount = int(m.text.strip())
        if amount < 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido. Envie um número inteiro ≥ 0.")
        return

    data = await state.get_data()
    at = data.get("action_type")
    if at in ("add", "remove") and amount <= 0:
        await m.answer("❌ O valor deve ser maior que zero.")
        return

    await state.update_data(amount=amount)
    await state.set_state(AS.adjust_reason)
    await m.answer("📝 Informe a justificativa (mínimo 3 caracteres):")


@router.message(AS.adjust_reason)
async def execute_points_adjust(m: Message, state: FSMContext):
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

    # Garantir que exista
    async with AsyncSessionLocal() as s:
        aff = await s.get(Affiliate, uid)
        if not aff:
            await state.clear()
            await m.answer("❌ Afiliado não encontrado. Crie o perfil primeiro.")
            return

        old = aff.points_balance

        if at == "add":
            aff.points_balance += amount
            aff.total_points_earned += amount
            tx_amount = amount
            tx_type = "admin_add"
        elif at == "remove":
            if aff.points_balance < amount:
                await state.clear()
                await m.answer(
                    f"❌ Pontos insuficientes. Saldo atual: {aff.points_balance} pts."
                )
                return
            aff.points_balance -= amount
            tx_amount = -amount
            tx_type = "admin_remove"
        elif at == "correct":
            diff = amount - aff.points_balance
            aff.points_balance = amount
            if diff > 0:
                aff.total_points_earned += diff
            tx_amount = diff
            tx_type = "admin_correct"
        else:
            await state.clear()
            return

        new = aff.points_balance
        s.add(AffiliateTransaction(
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

    text, kb = await render_affiliate(uid)
    await m.answer(
        f"✅ Operação concluída.\n\n"
        f"Pontos anteriores: {old}\n"
        f"Pontos novos: {new}\n"
        f"Justificativa: {reason}"
    )
    await m.answer(text, reply_markup=kb)


# ============================================================
# CONVERTER PONTOS EM SALDO (pelo admin)
# ============================================================
@router.callback_query(F.data.startswith("aff_convert_"))
async def convert_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        aff = await s.get(Affiliate, uid)
        if not aff:
            await cb.answer("Afiliado não encontrado.", show_alert=True)
            return
        if aff.points_balance <= 0:
            await cb.answer("Afiliado sem pontos para converter.", show_alert=True)
            return
        balance = aff.points_balance

    settings = await get_settings()
    rate = getattr(settings, "points_conversion_rate", 100.0)
    value = balance / rate if rate > 0 else 0.0

    await state.update_data(target_user_id=uid, action_type="convert", amount=balance)
    await state.set_state(AS.adjust_reason)

    await cb.message.answer(
        f"💱 CONVERTER PONTOS EM SALDO\n\n"
        f"Usuário: <code>{uid}</code>\n"
        f"Pontos: {balance}\n"
        f"Taxa: {rate:.2f} pts = R$ 1,00\n"
        f"Valor: R$ {value:.2f}\n\n"
        f"Informe a justificativa (mínimo 3 caracteres):"
    )
    await cb.answer()


# Intercepta a conversão no mesmo handler
@router.message(AS.adjust_reason, F.text)
async def _convert_if_needed(m: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("action_type") != "convert":
        # Deixa o handler anterior tratar
        return
    # Este handler é chamado DEPOIS do execute_points_adjust acima.
    # Como o aiogram executa o primeiro handler registrado, o anterior
    # já terá processado. Nada a fazer aqui — apenas segurança.
    pass


# ============================================================
# HISTÓRICO DE PONTOS
# ============================================================
@router.callback_query(F.data.startswith("aff_tx_"))
async def aff_transactions(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    rest = cb.data.replace("aff_tx_", "")
    parts = rest.rsplit("_", 1)
    try:
        uid = int(parts[0])
        page = int(parts[1])
    except (ValueError, IndexError):
        await cb.answer("Parâmetros inválidos.")
        return

    async with AsyncSessionLocal() as s:
        user = await s.get(User, uid)
        total = (await s.execute(
            select(func.count(AffiliateTransaction.id))
            .where(AffiliateTransaction.user_id == uid)
        )).scalar() or 0
        stmt = (
            select(AffiliateTransaction)
            .where(AffiliateTransaction.user_id == uid)
            .order_by(desc(AffiliateTransaction.created_at))
            .offset(page * TX_PAGE_SIZE)
            .limit(TX_PAGE_SIZE)
        )
        txs = (await s.execute(stmt)).scalars().all()
        aff = await s.get(Affiliate, uid)

    uname = (user.full_name if user else f"User {uid}")[:28]
    balance = aff.points_balance if aff else 0

    text = (
        f"📜 HISTÓRICO DE PONTOS — {uname}\n\n"
        f"Saldo atual: {balance} pts\n"
        f"Total: {total} | Página {page + 1}\n\n"
    )
    if not txs:
        text += "Nenhuma movimentação."
    else:
        for tx in txs:
            icon = "➕" if tx.amount >= 0 else "➖"
            label = TX_LABEL.get(tx.type, tx.type)
            text += (
                f"{icon} {abs(tx.amount)} pts — {label}\n"
                f"   {tx.description[:60]}\n"
                f"   {tx.balance_before} → {tx.balance_after}\n"
                f"   {tx.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
            )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"aff_tx_{uid}_{page - 1}"
        ))
    if (page + 1) * TX_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"aff_tx_{uid}_{page + 1}"
        ))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=f"aff_view_{uid}")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# INDICADOS DE UM AFILIADO
# ============================================================
@router.callback_query(F.data.startswith("aff_refs_user_"))
async def refs_of_user(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("aff_refs_user_", "")
    parts = rest.rsplit("_", 1)
    try:
        uid = int(parts[0])
        page = int(parts[1])
    except (ValueError, IndexError):
        await cb.answer("Parâmetros inválidos.")
        return
    await _render_refs(cb, page, referrer_id=uid)


# ============================================================
# LISTA GERAL DE INDICAÇÕES
# ============================================================
@router.callback_query(F.data.startswith("aff_refs_pending_"))
async def refs_pending(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])
    await _render_refs(cb, page, only_pending=True)


@router.callback_query(F.data.startswith("aff_refs_all_"))
async def refs_all(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])
    await _render_refs(cb, page, only_pending=False)


async def _render_refs(
    cb: CallbackQuery,
    page: int,
    referrer_id: int | None = None,
    only_pending: bool = False,
):
    async with AsyncSessionLocal() as s:
        stmt = select(Referral)
        cnt = select(func.count(Referral.id))

        if referrer_id:
            stmt = stmt.where(Referral.referrer_id == referrer_id)
            cnt = cnt.where(Referral.referrer_id == referrer_id)
        if only_pending:
            stmt = stmt.where(Referral.is_confirmed == False)
            cnt = cnt.where(Referral.is_confirmed == False)

        total = (await s.execute(cnt)).scalar() or 0
        stmt = stmt.order_by(desc(Referral.created_at)).offset(page * REF_PAGE_SIZE).limit(REF_PAGE_SIZE)
        refs = (await s.execute(stmt)).scalars().all()

        uids = set()
        for r in refs:
            uids.add(r.referrer_id)
            uids.add(r.referred_id)
        users = {}
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(list(uids))))).scalars().all()
            users = {x.id: x for x in u}

    title = "INDICAÇÕES"
    if referrer_id:
        title = f"INDICADOS — User {referrer_id}"
    if only_pending:
        title = "INDICAÇÕES PENDENTES"

    text = f"🤝 {title}\n\nTotal: {total} | Página {page + 1}\n\n"

    if not refs:
        text += "Nenhuma indicação."
    else:
        for r in refs:
            ref_user = users.get(r.referrer_id)
            refd_user = users.get(r.referred_id)
            ref_name = (ref_user.full_name if ref_user else f"User {r.referrer_id}")[:20]
            refd_name = (refd_user.full_name if refd_user else f"User {r.referred_id}")[:20]
            status = "✅" if r.is_confirmed else "⏳"
            text += (
                f"{status} {ref_name} → {refd_name}\n"
                f"   📅 {r.created_at.strftime('%d/%m/%Y %H:%M')}\n"
            )
            if r.confirmed_at:
                text += f"   ✅ Confirmada: {r.confirmed_at.strftime('%d/%m/%Y %H:%M')}\n"
            text += "\n"

    nav = []
    if referrer_id:
        prefix = f"aff_refs_user_{referrer_id}"
        back_cb = f"aff_view_{referrer_id}"
    elif only_pending:
        prefix = "aff_refs_pending"
        back_cb = "admin_affiliates"
    else:
        prefix = "aff_refs_all"
        back_cb = "admin_affiliates"

    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"{prefix}_{page - 1}"
        ))
    if (page + 1) * REF_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"{prefix}_{page + 1}"
        ))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=back_cb)])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# HISTÓRICO DE CONVERSÕES (GLOBAL)
# ============================================================
@router.callback_query(F.data.startswith("aff_convs_"))
async def conversions_history(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(AffiliateTransaction.id))
            .where(AffiliateTransaction.type == "conversion")
        )).scalar() or 0
        stmt = (
            select(AffiliateTransaction)
            .where(AffiliateTransaction.type == "conversion")
            .order_by(desc(AffiliateTransaction.created_at))
            .offset(page * TX_PAGE_SIZE)
            .limit(TX_PAGE_SIZE)
        )
        txs = (await s.execute(stmt)).scalars().all()

        uids = list({t.user_id for t in txs})
        users = {}
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}

    settings = await get_settings()
    rate = getattr(settings, "points_conversion_rate", 100.0)

    text = f"💱 HISTÓRICO DE CONVERSÕES\n\nTotal: {total} | Página {page + 1}\n\n"

    if not txs:
        text += "Nenhuma conversão registrada."
    else:
        for tx in txs:
            u = users.get(tx.user_id)
            uname = (u.full_name if u else f"User {tx.user_id}")[:22]
            pts = abs(tx.amount)
            value = pts / rate if rate > 0 else 0.0
            text += (
                f"👤 {uname} (<code>{tx.user_id}</code>)\n"
                f"   🏆 {pts} pts → R$ {value:.2f}\n"
                f"   📅 {tx.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
            )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"aff_convs_{page - 1}"))
    if (page + 1) * TX_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"aff_convs_{page + 1}"))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_affiliates")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# BUSCA
# ============================================================
@router.callback_query(F.data == "aff_search")
async def search_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(AS.search)
    await cb.message.answer(
        "🔎 BUSCAR AFILIADO\n\n"
        "Envie:\n"
        "• ID do Telegram\n"
        "• @username\n"
        "• Nome do usuário\n"
        "• Código de indicação\n\n"
        "Digite /cancelar para abortar."
    )
    await cb.answer()


@router.message(AS.search)
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
        found_affs = []

        # Por código de indicação
        code_match = (await s.execute(
            select(Affiliate).where(Affiliate.referral_code.ilike(f"%{q}%")).limit(10)
        )).scalars().all()
        found_affs.extend(code_match)

        # Por ID / username / nome
        if q.isdigit():
            aff = await s.get(Affiliate, int(q))
            if aff:
                found_affs.append(aff)
        else:
            clean = q.lstrip("@")
            u = (await s.execute(
                select(User).where(or_(
                    User.username.ilike(f"%{clean}%"),
                    User.full_name.ilike(f"%{q}%"),
                )).limit(20)
            )).scalars().all()
            for user in u:
                aff = await s.get(Affiliate, user.id)
                if aff:
                    found_affs.append(aff)

        # Deduplicate
        seen = set()
        unique = []
        for a in found_affs:
            if a.user_id not in seen:
                seen.add(a.user_id)
                unique.append(a)

        if not unique:
            await m.answer("❌ Nenhum afiliado encontrado.")
            return

        uids = [a.user_id for a in unique]
        users = {
            x.id: x for x in (await s.execute(
                select(User).where(User.id.in_(uids))
            )).scalars().all()
        }

    text = f"🔎 RESULTADOS: {len(unique)} afiliado(s)\n\n"
    buttons = []
    for a in unique:
        u = users.get(a.user_id)
        uname = (u.full_name if u else f"User {a.user_id}")[:22]
        text += f"👤 {uname} — <code>{a.referral_code}</code> — {a.points_balance} pts\n"
        buttons.append([InlineKeyboardButton(
            text=f"👤 {uname} — {a.points_balance} pts",
            callback_data=f"aff_view_{a.user_id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_affiliates")])

    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ============================================================
# CONFIGURAÇÕES
# ============================================================
async def config_view() -> tuple[str, InlineKeyboardMarkup]:
    s = await get_settings(force=True)
    enabled = getattr(s, "affiliates_enabled", True)
    approval = getattr(s, "affiliate_approval_mode", "auto")

    lines = [
        "◈ BELLAMY | CONFIGURAÇÕES DO PROGRAMA DE AFILIADOS\n\n",
        f"📌 Status: {'🟢 Ativado' if enabled else '🔴 Desativado'}\n",
        f"🔐 Aprovação: {'🤖 Automática' if approval == 'auto' else '👤 Manual'}\n\n",
    ]
    for key, (label, _, _, _) in CONFIG_KEYS.items():
        val = getattr(s, key, 0)
        if isinstance(val, float):
            lines.append(f"{label}: {val:.2f}\n")
        else:
            lines.append(f"{label}: {val}\n")

    text = "".join(lines)

    buttons = [
        [InlineKeyboardButton(
            text=("🔴 Desativar programa" if enabled else "🟢 Ativar programa"),
            callback_data="aff_cfg_t_enabled")],
        [InlineKeyboardButton(
            text=f"🔐 Aprovação: {'automática' if approval == 'auto' else 'manual'}",
            callback_data="aff_cfg_approval")],
    ]
    for key, (label, _, _, _) in CONFIG_KEYS.items():
        buttons.append([InlineKeyboardButton(
            text=f"✏️ {label}",
            callback_data=f"aff_cfg_set_{key}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_affiliates")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "aff_config")
async def config(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "aff_cfg_t_enabled")
async def toggle_enabled(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        cur = getattr(bs, "affiliates_enabled", True)
        bs.affiliates_enabled = not cur
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "affiliates_toggle", {"new": not cur})
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "aff_cfg_approval")
async def approval_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Automática (confirma sozinho)", callback_data="aff_setapproval_auto")],
        [InlineKeyboardButton(text="👤 Manual (admin aprova)", callback_data="aff_setapproval_manual")],
        [InlineKeyboardButton(text="↩️ Cancelar", callback_data="aff_config")],
    ])
    await cb.message.edit_text(
        "🔐 MODO DE APROVAÇÃO\n\nSelecione:",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("aff_setapproval_"))
async def set_approval(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    mode = cb.data.replace("aff_setapproval_", "")
    if mode not in ("auto", "manual"):
        await cb.answer("Modo inválido.")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.affiliate_approval_mode = mode
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "affiliate_approval_mode", {"value": mode})
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Modo atualizado.")


@router.callback_query(F.data.startswith("aff_cfg_set_"))
async def config_value_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("aff_cfg_set_", "")
    if key not in CONFIG_KEYS:
        await cb.answer("Configuração inválida.")
        return
    label, ftype, fmin, fmax = CONFIG_KEYS[key]
    await state.update_data(config_key=key)
    await state.set_state(AS.set_config)
    await cb.message.answer(
        f"✏️ {label}\n\nInforme o novo valor ({ftype.__name__}, entre {fmin} e {fmax}):"
    )
    await cb.answer()


@router.message(AS.set_config)
async def config_value_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    key = data.get("config_key")
    if key not in CONFIG_KEYS:
        await state.clear()
        return

    label, ftype, fmin, fmax = CONFIG_KEYS[key]
    raw = m.text.strip().replace(",", ".") if ftype is float else m.text.strip()
    try:
        val = ftype(raw)
        if val < fmin or val > fmax:
            raise ValueError
    except (ValueError, TypeError):
        await m.answer(f"❌ Valor inválido (mín {fmin}, máx {fmax}).")
        return

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        setattr(bs, key, val)
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, f"affiliate_cfg_{key}", {"value": val})
    await state.clear()

    text, kb = await config_view()
    await m.answer(f"✅ {label} atualizado para {val}.", reply_markup=kb)


# ============================================================
# ESTATÍSTICAS
# ============================================================
@router.callback_query(F.data == "aff_stats")
async def stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    today = datetime.utcnow().date()
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        total_affiliates = (await s.execute(
            select(func.count(Affiliate.user_id))
        )).scalar() or 0
        with_points = (await s.execute(
            select(func.count(Affiliate.user_id)).where(Affiliate.points_balance > 0)
        )).scalar() or 0
        total_points_earned = (await s.execute(
            select(func.coalesce(func.sum(Affiliate.total_points_earned), 0))
        )).scalar() or 0
        total_points_converted = (await s.execute(
            select(func.coalesce(func.sum(Affiliate.total_points_converted), 0))
        )).scalar() or 0
        total_balance_earned = (await s.execute(
            select(func.coalesce(func.sum(Affiliate.total_balance_earned), 0))
        )).scalar() or 0.0

        refs_total = (await s.execute(
            select(func.count(Referral.id))
        )).scalar() or 0
        refs_confirmed = (await s.execute(
            select(func.count(Referral.id)).where(Referral.is_confirmed == True)
        )).scalar() or 0

        refs_month = (await s.execute(
            select(func.count(Referral.id))
            .where(Referral.created_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0
        refs_confirmed_month = (await s.execute(
            select(func.count(Referral.id))
            .where(Referral.is_confirmed == True)
            .where(Referral.confirmed_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0

        convs_month = (await s.execute(
            select(func.count(AffiliateTransaction.id))
            .where(AffiliateTransaction.type == "conversion")
            .where(AffiliateTransaction.created_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0

        # Conversão de compra: usuários com indicação confirmada que já compraram
        buyers_with_refs = (await s.execute(
            select(func.count(func.distinct(Referral.referred_id)))
            .join(Order, Order.user_id == Referral.referred_id)
            .where(Referral.is_confirmed == True)
            .where(Order.status == "completed")
        )).scalar() or 0

    rate = (refs_confirmed / refs_total * 100) if refs_total else 0.0
    avg_points = (total_points_earned / total_affiliates) if total_affiliates else 0.0
    purchase_conv = (buyers_with_refs / refs_confirmed * 100) if refs_confirmed else 0.0

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DO PROGRAMA\n\n"
        f"👥 Afiliados: {total_affiliates}\n"
        f"💎 Com pontos: {with_points}\n"
        f"🏆 Pontos gerados: {int(total_points_earned)}\n"
        f"💱 Pontos convertidos: {int(total_points_converted)}\n"
        f"💰 Total pago em R$: R$ {total_balance_earned:.2f}\n"
        f"📊 Média de pontos/afiliado: {avg_points:.1f}\n\n"
        f"🤝 INDICAÇÕES\n"
        f"📊 Total: {refs_total}\n"
        f"✅ Confirmadas: {refs_confirmed} ({rate:.1f}%)\n"
        f"📅 Mês — novas: {refs_month} | confirmadas: {refs_confirmed_month}\n"
        f"🛒 Indicados que compraram: {buyers_with_refs}\n"
        f"📈 Conversão em compra: {purchase_conv:.1f}%\n\n"
        f"💱 Conversões este mês: {convs_month}\n"
    )

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="aff_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_affiliates")],
    ]))
    await cb.answer()


# ============================================================
# EXPORTAÇÃO
# ============================================================
@router.callback_query(F.data == "aff_export")
async def export_report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        affs = (await s.execute(
            select(Affiliate).order_by(desc(Affiliate.total_points_earned))
        )).scalars().all()

        uids = [a.user_id for a in affs]
        users = {}
        if uids:
            users = {
                x.id: x for x in (await s.execute(
                    select(User).where(User.id.in_(uids))
                )).scalars().all()
            }

        refs = (await s.execute(
            select(Referral).order_by(desc(Referral.created_at)).limit(500)
        )).scalars().all()

        tx_recent = (await s.execute(
            select(AffiliateTransaction)
            .order_by(desc(AffiliateTransaction.created_at))
            .limit(300)
        )).scalars().all()

    lines = []
    lines.append("═" * 70)
    lines.append("BELLAMY | RELATÓRIO DE AFILIADOS")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append(f"Afiliados: {len(affs)}")
    lines.append("═" * 70)
    lines.append("")
    lines.append("─" * 70)
    lines.append("AFILIADOS")
    lines.append("─" * 70)
    for a in affs:
        u = users.get(a.user_id)
        uname = u.full_name if u else "Desconhecido"
        uhandle = f"@{u.username}" if u and u.username else "—"
        lines.append(
            f"{a.user_id:<15} | {uname[:25]:<25} | {uhandle[:18]:<18} | "
            f"{a.referral_code:<10} | pts: {a.points_balance:<6} | "
            f"total: {a.total_points_earned:<6} | ind: {a.confirmed_referrals}"
        )
    lines.append("")
    lines.append("─" * 70)
    lines.append("ÚLTIMAS 500 INDICAÇÕES")
    lines.append("─" * 70)
    for r in refs:
        status = "CONFIRMADA" if r.is_confirmed else "PENDENTE"
        lines.append(
            f"{r.created_at.strftime('%d/%m/%Y %H:%M')} | "
            f"referrer: {r.referrer_id:<12} | referred: {r.referred_id:<12} | {status}"
        )
    lines.append("")
    lines.append("─" * 70)
    lines.append("ÚLTIMAS 300 MOVIMENTAÇÕES DE PONTOS")
    lines.append("─" * 70)
    for tx in tx_recent:
        label = TX_LABEL.get(tx.type, tx.type)
        lines.append(
            f"{tx.created_at.strftime('%d/%m/%Y %H:%M')} | "
            f"user: {tx.user_id:<12} | {tx.amount:+6d} | {label}"
        )

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"afiliados_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption=(
            f"📊 <b>Relatório de Afiliados</b>\n\n"
            f"👥 Afiliados: {len(affs)}\n"
            f"🤝 Indicações: {len(refs)}\n"
            f"💱 Movimentações: {len(tx_recent)}"
        ),
    )
    await cb.answer("Relatório gerado.")


# ============================================================
# HELPERS PARA O BOT DO CLIENTE
# ============================================================
async def process_referral_on_start(new_user_id: int, referral_code: str) -> bool:
    """
    Chame no /start quando o usuário enviar /start ref_<CODE>.
    Retorna True se a indicação foi registrada.
    """
    async with AsyncSessionLocal() as s:
        aff = (await s.execute(
            select(Affiliate).where(Affiliate.referral_code == referral_code)
        )).scalar_one_or_none()
        if not aff:
            return False
        if aff.user_id == new_user_id:
            return False

        existing = (await s.execute(
            select(Referral).where(Referral.referred_id == new_user_id)
        )).scalar_one_or_none()
        if existing:
            return False

        settings = await get_settings()
        max_refs = getattr(settings, "max_referrals_per_user", 0)
        if max_refs > 0 and aff.total_referrals >= max_refs:
            return False

        approval = getattr(settings, "affiliate_approval_mode", "auto")
        is_confirmed = (approval == "auto")

        s.add(Referral(
            referrer_id=aff.user_id,
            referred_id=new_user_id,
            is_confirmed=is_confirmed,
            confirmed_at=datetime.utcnow() if is_confirmed else None,
        ))
        aff.total_referrals += 1

        if is_confirmed:
            aff.confirmed_referrals += 1
            points = getattr(settings, "points_per_referral", 10)
            if points > 0:
                old = aff.points_balance
                aff.points_balance += points
                aff.total_points_earned += points
                s.add(AffiliateTransaction(
                    user_id=aff.user_id,
                    amount=points,
                    type="referral_bonus",
                    description=f"Indicação confirmada: user {new_user_id}",
                    balance_before=old,
                    balance_after=aff.points_balance,
                ))
        await s.commit()
        return True


async def process_purchase_points(buyer_id: int, order_total: float) -> None:
    """
    Chame em admin/orders.py após confirmar um pedido.
    Distribui pontos ao afiliado do comprador, se houver.
    """
    async with AsyncSessionLocal() as s:
        ref = (await s.execute(
            select(Referral)
            .where(Referral.referred_id == buyer_id)
            .where(Referral.is_confirmed == True)
            .limit(1)
        )).scalar_one_or_none()
        if not ref:
            return

        aff = await s.get(Affiliate, ref.referrer_id)
        if not aff:
            return

        settings = await get_settings()
        min_purchase = getattr(settings, "min_purchase_for_points", 0.0)
        if order_total < min_purchase:
            return

        points = getattr(settings, "points_per_purchase", 0)
        commission_pct = getattr(settings, "commission_percent", 0.0)

        if points > 0:
            old = aff.points_balance
            aff.points_balance += points
            aff.total_points_earned += points
            s.add(AffiliateTransaction(
                user_id=aff.user_id,
                amount=points,
                type="purchase_bonus",
                description=f"Compra do indicado {buyer_id} — R$ {order_total:.2f}",
                balance_before=old,
                balance_after=aff.points_balance,
            ))

        if commission_pct > 0:
            commission = order_total * (commission_pct / 100.0)
            wallet = await s.get(Wallet, aff.user_id)
            if not wallet:
                wallet = Wallet(user_id=aff.user_id, balance=0.0)
                s.add(wallet)
            before = wallet.balance
            wallet.balance += commission
            aff.total_balance_earned += commission
            s.add(WalletTransaction(
                user_id=aff.user_id,
                amount=commission,
                type="affiliate",
                description=f"Comissão compra indicado {buyer_id}",
                balance_before=before,
                balance_after=wallet.balance,
            ))

        await s.commit()


async def approve_referral(referral_id: int, admin_id: int) -> bool:
    """
    Aprova uma indicação manualmente e credita os pontos.
    """
    async with AsyncSessionLocal() as s:
        ref = await s.get(Referral, referral_id)
        if not ref or ref.is_confirmed:
            return False

        ref.is_confirmed = True
        ref.confirmed_at = datetime.utcnow()

        aff = await s.get(Affiliate, ref.referrer_id)
        if not aff:
            await s.commit()
            return True

        aff.confirmed_referrals += 1
        settings = await get_settings()
        points = getattr(settings, "points_per_referral", 10)
        if points > 0:
            old = aff.points_balance
            aff.points_balance += points
            aff.total_points_earned += points
            s.add(AffiliateTransaction(
                user_id=aff.user_id,
                amount=points,
                type="referral_bonus",
                description=f"Indicação {ref.id} aprovada manualmente",
                balance_before=old,
                balance_after=aff.points_balance,
            ))
        await s.commit()

    await log_admin(admin_id, "referral_approve", {"referral_id": referral_id})
    return True


async def convert_points_to_balance(
    user_id: int, points: int,
) -> tuple[bool, str, int | None]:
    """
    Converte pontos em saldo na carteira do afiliado.
    Retorna (sucesso, mensagem, id_operacao).
    id_operacao é o ID da AffiliateTransaction criada (None em caso de falha).
    """
    async with AsyncSessionLocal() as s:
        aff = await s.get(Affiliate, user_id)
        if not aff:
            return False, "Afiliado não encontrado.", None
        if points <= 0:
            return False, "Quantidade inválida.", None
        if aff.points_balance < points:
            return False, f"Pontos insuficientes (disponível: {aff.points_balance}).", None

        settings = await get_settings()
        rate = getattr(settings, "points_conversion_rate", 100.0)
        if rate <= 0:
            return False, "Taxa de conversão inválida.", None

        min_pts = getattr(settings, "min_points_to_convert", 1)
        if points < min_pts:
            return False, f"Mínimo para converter: {min_pts} pts.", None

        max_pts = getattr(settings, "max_points_per_conversion", 0)
        if max_pts > 0 and points > max_pts:
            return False, f"Máximo por conversão: {max_pts} pts.", None

        value = round(points / rate, 2)
        old = aff.points_balance
        aff.points_balance -= points
        aff.total_points_converted += points
        aff.total_balance_earned += value

        wallet = await s.get(Wallet, user_id)
        if not wallet:
            wallet = Wallet(user_id=user_id, balance=0.0)
            s.add(wallet)
            await s.flush()

        before = wallet.balance
        wallet.balance = round(wallet.balance + value, 2)
        new_balance = wallet.balance

        # Cria AffiliateTransaction e captura o ID antes do commit
        aff_tx = AffiliateTransaction(
            user_id=user_id,
            amount=-points,
            type="conversion",
            description=f"Conversão de {points} pts → R$ {value:.2f}",
            balance_before=old,
            balance_after=aff.points_balance,
        )
        s.add(aff_tx)
        await s.flush()
        op_id = aff_tx.id

        s.add(WalletTransaction(
            user_id=user_id,
            amount=value,
            type="points_conversion",
            description=f"{points} pts convertidos (op #{op_id})",
            balance_before=before,
            balance_after=new_balance,
        ))

        await s.commit()

    # Notificação ao usuário (com o ID da operação)
    try:
        await bot.send_message(
            user_id,
            f"💱 <b>CONVERSÃO REALIZADA!</b>\n\n"
            f"🏆 Pontos utilizados: {points}\n"
            f"💰 Valor creditado: R$ {value:.2f}\n"
            f"💎 Novo saldo: R$ {new_balance:.2f}\n"
            f"🆔 Operação: #{op_id}",
        )
    except Exception:
        pass

    return True, f"✅ Convertido: {points} pts → R$ {value:.2f}", op_id
