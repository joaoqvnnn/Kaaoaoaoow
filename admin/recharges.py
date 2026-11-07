from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, or_
from datetime import datetime, timedelta
import httpx
import json
import hmac
import hashlib

from bot import dp, bot
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    Payment, Wallet, WalletTransaction, User, AdminLog, BotSettings,
    RechargeBonusTier, Order,
)
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

PAGE_SIZE = 10
MP_API_BASE = "https://api.mercadopago.com"


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class RS(StatesGroup):
    cfg_value = State()
    tier_min = State()
    tier_max = State()
    tier_percent = State()
    tier_edit_value = State()
    search = State()
    cancel_reason = State()
    confirm_note = State()


STATUS_ICON = {
    "pending": "🟡",
    "confirmed": "🟢",
    "expired": "⚪",
    "cancelled": "🔴",
    "failed": "⚠️",
}
STATUS_LABEL = {
    "pending": "Pendente",
    "confirmed": "Confirmada",
    "expired": "Expirada",
    "cancelled": "Cancelada",
    "failed": "Falha",
}


# ============================================================
# MERCADO PAGO CLIENT
# ============================================================
class MercadoPagoClient:
    def __init__(self, access_token: str):
        self.access_token = access_token

    async def create_pix_payment(
        self,
        amount: float,
        description: str,
        external_reference: str,
        payer_email: str = "buyer@bellamy.local",
        expiration_minutes: int = 30,
    ) -> dict:
        expiration = datetime.utcnow() + timedelta(minutes=expiration_minutes)
        payload = {
            "transaction_amount": round(float(amount), 2),
            "description": description[:250],
            "payment_method_id": "pix",
            "external_reference": external_reference,
            "date_of_expiration": expiration.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
            "payer": {"email": payer_email},
        }
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": f"bellamy_{external_reference}",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{MP_API_BASE}/v1/payments", json=payload, headers=headers)
            if r.status_code not in (200, 201):
                raise Exception(f"MP create_pix failed [{r.status_code}]: {r.text[:300]}")
            return r.json()

    async def get_payment(self, payment_id: str) -> dict:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{MP_API_BASE}/v1/payments/{payment_id}", headers=headers)
            if r.status_code != 200:
                raise Exception(f"MP get_payment failed [{r.status_code}]: {r.text[:300]}")
            return r.json()

    @staticmethod
    def extract_pix_data(mp_payment: dict) -> dict:
        poi = mp_payment.get("point_of_interaction") or {}
        tx = poi.get("transaction_data") or {}
        return {
            "pix_code": tx.get("qr_code"),
            "qr_code_base64": tx.get("qr_code_base64"),
            "ticket_url": tx.get("ticket_url"),
        }


async def get_mp_client() -> MercadoPagoClient | None:
    s = await get_settings()
    token = getattr(s, "mp_access_token", None)
    if not token:
        return None
    return MercadoPagoClient(token)


# ============================================================
# MENU PRINCIPAL
# ============================================================
@router.callback_query(F.data == "admin_recharges")
async def recharges_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    today = datetime.utcnow().date()
    month_start = today.replace(day=1)
    now = datetime.utcnow()

    async with AsyncSessionLocal() as s:
        counts = {}
        for st in ("pending", "confirmed", "expired", "cancelled", "failed"):
            counts[st] = (await s.execute(
                select(func.count(Payment.id)).where(Payment.status == st)
            )).scalar() or 0
        total = sum(counts.values())

        confirmed_today = (await s.execute(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.status == "confirmed")
            .where(Payment.created_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0.0
        confirmed_month = (await s.execute(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.status == "confirmed")
            .where(Payment.created_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0.0
        confirmed_total = (await s.execute(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.status == "confirmed")
        )).scalar() or 0.0

        bonus_total = (await s.execute(
            select(func.coalesce(func.sum(Payment.bonus_amount), 0))
            .where(Payment.status == "confirmed")
        )).scalar() or 0.0

        expired_pending = (await s.execute(
            select(func.count(Payment.id))
            .where(Payment.status == "pending")
            .where(Payment.expires_at < now)
        )).scalar() or 0

    settings = await get_settings()
    mp_ok = bool(getattr(settings, "mp_access_token", None))

    text = (
        "◈ BELLAMY | RECARGAS E PIX\n\n"
        f"📊 Total de recargas: {total}\n"
        f"🟡 Pendentes: {counts['pending']}\n"
        f"🟢 Confirmadas: {counts['confirmed']}\n"
        f"⚪ Expiradas: {counts['expired']}\n"
        f"🔴 Canceladas: {counts['cancelled']}\n"
        f"⚠️ Falhas: {counts['failed']}\n\n"
        f"💰 Confirmado hoje: R$ {confirmed_today:.2f}\n"
        f"📆 Este mês: R$ {confirmed_month:.2f}\n"
        f"💎 Total histórico: R$ {confirmed_total:.2f}\n"
        f"🎁 Bônus distribuídos: R$ {bonus_total:.2f}\n\n"
        f"💳 Mercado Pago: {'🟢 Configurado' if mp_ok else '🔴 Não configurado'}\n"
        f"💵 Recargas: {'🟢 Ativas' if getattr(settings, 'recharge_enabled', True) else '🔴 Desativadas'}\n"
    )
    if expired_pending > 0:
        text += f"\n⚠️ {expired_pending} recarga(s) pendente(s) expirada(s) sem atualização"
    text += "\n\n👇 Selecione uma opção:"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🟡 Pendentes ({counts['pending']})", callback_data="rcg_list_pending_0")],
        [InlineKeyboardButton(text=f"🟢 Confirmadas ({counts['confirmed']})", callback_data="rcg_list_confirmed_0")],
        [InlineKeyboardButton(text=f"⚪ Expiradas ({counts['expired']})", callback_data="rcg_list_expired_0")],
        [InlineKeyboardButton(text=f"🔴 Canceladas ({counts['cancelled']})", callback_data="rcg_list_cancelled_0")],
        [InlineKeyboardButton(text=f"⚠️ Falhas ({counts['failed']})", callback_data="rcg_list_failed_0")],
        [InlineKeyboardButton(text=f"📋 Todas ({total})", callback_data="rcg_list_all_0")],
        [InlineKeyboardButton(text="⚙️ Configurar PIX", callback_data="rcg_config")],
        [InlineKeyboardButton(text="🎁 Bônus por faixa", callback_data="rcg_tiers")],
        [InlineKeyboardButton(text="🔎 Buscar recarga", callback_data="rcg_search")],
        [InlineKeyboardButton(text="📊 Estatísticas", callback_data="rcg_stats")],
        [InlineKeyboardButton(text="🧹 Expirar pendentes antigas", callback_data="rcg_expire_old")],
        [InlineKeyboardButton(text="📥 Exportar relatório", callback_data="rcg_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# LISTAGEM DE RECARGAS
# ============================================================
@router.callback_query(F.data.startswith("rcg_list_"))
async def list_recharges(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    parts = cb.data.split("_")
    status = parts[2]
    page = int(parts[3])

    async with AsyncSessionLocal() as s:
        stmt = select(Payment)
        cnt = select(func.count(Payment.id))
        if status != "all":
            stmt = stmt.where(Payment.status == status)
            cnt = cnt.where(Payment.status == status)
        total = (await s.execute(cnt)).scalar() or 0
        stmt = stmt.order_by(desc(Payment.created_at)).offset(page * PAGE_SIZE).limit(PAGE_SIZE)
        payments = (await s.execute(stmt)).scalars().all()

        users = {}
        if payments:
            uids = list({p.user_id for p in payments})
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}

    labels = {
        "all": "TODAS AS RECARGAS",
        "pending": "RECARGAS PENDENTES",
        "confirmed": "RECARGAS CONFIRMADAS",
        "expired": "RECARGAS EXPIRADAS",
        "cancelled": "RECARGAS CANCELADAS",
        "failed": "RECARGAS COM FALHA",
    }
    text = f"◈ BELLAMY | {labels.get(status, status.upper())}\n\n"
    text += f"Total: {total} | Página {page + 1}\n\n"

    buttons = []
    if not payments:
        text += "Nenhuma recarga encontrada."
    else:
        for p in payments:
            icon = STATUS_ICON.get(p.status, "⚪")
            u = users.get(p.user_id)
            uname = (u.full_name if u else f"User {p.user_id}")[:20]
            bonus_txt = f" (+R$ {p.bonus_amount:.2f})" if p.bonus_amount else ""
            text += (
                f"{icon} #{p.id:06d} — {uname}\n"
                f"   R$ {p.amount:.2f}{bonus_txt} | {p.created_at.strftime('%d/%m %H:%M')}\n\n"
            )
            buttons.append([InlineKeyboardButton(
                text=f"{icon} #{p.id:06d} — R$ {p.amount:.2f}",
                callback_data=f"rcg_view_{p.id}",
            )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"rcg_list_{status}_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"rcg_list_{status}_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_recharges")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# DETALHE DA RECARGA
# ============================================================
async def render_recharge(pid: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as s:
        p = await s.get(Payment, pid)
        if not p:
            return (
                "❌ Recarga não encontrada.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_recharges")]
                ]),
            )
        user = await s.get(User, p.user_id)

    icon = STATUS_ICON.get(p.status, "⚪")
    label = STATUS_LABEL.get(p.status, p.status)
    uname = user.full_name if user else f"User {p.user_id}"
    uhandle = f"@{user.username}" if user and user.username else "—"

    provider = getattr(p, "provider", None) or "mercadopago"
    method = getattr(p, "method", None) or "pix_mp"
    credited = getattr(p, "credited_amount", p.amount + (p.bonus_amount or 0))

    text = (
        f"◈ BELLAMY | RECARGA #{p.id:06d}\n\n"
        f"👤 Cliente: {uname}\n"
        f"💠 Username: {uhandle}\n"
        f"🆔 User ID: <code>{p.user_id}</code>\n\n"
        f"💰 Valor: R$ {p.amount:.2f}\n"
        f"🎁 Bônus: R$ {p.bonus_amount:.2f}\n"
        f"💎 Total creditado: R$ {credited:.2f}\n\n"
        f"📌 Status: {icon} {label}\n"
        f"💳 Provedor: {provider.title()}\n"
        f"🔧 Método: {method}\n"
    )
    if getattr(p, "mp_payment_id", None):
        text += f"🔗 MP Payment ID: <code>{p.mp_payment_id}</code>\n"
    if getattr(p, "external_reference", None):
        text += f"🏷️ Ref. externa: <code>{p.external_reference}</code>\n"
    text += f"📅 Criada: {p.created_at.strftime('%d/%m/%Y %H:%M')}\n"
    if getattr(p, "expires_at", None):
        text += f"⏱️ Expira: {p.expires_at.strftime('%d/%m/%Y %H:%M')}\n"
    if getattr(p, "confirmed_at", None):
        text += f"✅ Confirmada em: {p.confirmed_at.strftime('%d/%m/%Y %H:%M')}\n"
    if getattr(p, "confirmed_by", None):
        text += f"✅ Confirmada por: Admin <code>{p.confirmed_by}</code>\n"
    if getattr(p, "cancelled_at", None):
        text += f"❌ Cancelada em: {p.cancelled_at.strftime('%d/%m/%Y %H:%M')}\n"
    if getattr(p, "cancelled_by", None):
        text += f"❌ Cancelada por: Admin <code>{p.cancelled_by}</code>\n"
    if getattr(p, "cancelled_reason", None):
        text += f"📝 Motivo: {p.cancelled_reason}\n"

    if getattr(p, "pix_code", None) and p.status == "pending":
        code = p.pix_code
        text += f"\n🔑 <b>PIX copia e cola</b>\n<code>{code[:200]}</code>\n"
        if len(code) > 200:
            text += f"<i>({len(code)} caracteres totais)</i>\n"

    buttons = []
    if p.status == "pending":
        buttons.append([InlineKeyboardButton(text="🔄 Verificar no Mercado Pago", callback_data=f"rcg_check_{pid}")])
        buttons.append([InlineKeyboardButton(text="✅ Confirmar manualmente", callback_data=f"rcg_confirm_{pid}")])
        buttons.append([InlineKeyboardButton(text="❌ Cancelar recarga", callback_data=f"rcg_cancel_{pid}")])
    elif p.status == "confirmed":
        buttons.append([InlineKeyboardButton(text="💜 Estornar (devolve saldo)", callback_data=f"rcg_refund_{pid}")])
    elif p.status == "expired":
        buttons.append([InlineKeyboardButton(text="✅ Confirmar mesmo assim", callback_data=f"rcg_confirm_{pid}")])

    buttons.append([InlineKeyboardButton(text="👤 Ver usuário", callback_data=f"usr_view_{p.user_id}")])
    buttons.append([InlineKeyboardButton(text="💎 Ver carteira", callback_data=f"wal_view_{p.user_id}")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=f"rcg_list_{p.status}_0")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("rcg_view_"))
async def view_recharge(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    text, kb = await render_recharge(pid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# VERIFICAR STATUS NO MERCADO PAGO
# ============================================================
@router.callback_query(F.data.startswith("rcg_check_"))
async def check_mp(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    pid = int(cb.data.split("_")[-1])
    mp_id = None

    async with AsyncSessionLocal() as s:
        p = await s.get(Payment, pid)
        if not p:
            await cb.answer("Recarga não encontrada.", show_alert=True)
            return
        mp_id = getattr(p, "mp_payment_id", None)

    if not mp_id:
        await cb.answer("Recarga sem MP Payment ID.", show_alert=True)
        return

    client = await get_mp_client()
    if not client:
        await cb.answer("Mercado Pago não configurado.", show_alert=True)
        return

    try:
        mp = await client.get_payment(mp_id)
    except Exception as e:
        await cb.answer(f"Erro MP: {str(e)[:100]}", show_alert=True)
        return

    mp_status = mp.get("status", "unknown")
    if mp_status == "approved":
        await _credit_recharge(pid, source="mp_check", admin_id=cb.from_user.id)
        await cb.answer("✅ Pagamento aprovado e saldo creditado.")
    elif mp_status in ("cancelled", "rejected"):
        async with AsyncSessionLocal() as s:
            p = await s.get(Payment, pid)
            p.status = "cancelled"
            p.cancelled_at = datetime.utcnow()
            p.cancelled_by = cb.from_user.id
            p.cancelled_reason = f"MP status: {mp_status}"
            await s.commit()
        await log_admin(cb.from_user.id, "recharge_mp_check_cancel", {"payment_id": pid, "mp_status": mp_status})
        await cb.answer(f"Recarga cancelada no MP ({mp_status}).")
    else:
        await cb.answer(f"MP status: {mp_status}. Ainda não aprovado.")

    text, kb = await render_recharge(pid)
    await cb.message.edit_text(text, reply_markup=kb)


# ============================================================
# CONFIRMAR MANUALMENTE
# ============================================================
@router.callback_query(F.data.startswith("rcg_confirm_"))
async def confirm_manual_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    await state.update_data(payment_id=pid)
    await state.set_state(RS.confirm_note)
    await cb.message.answer(
        f"✅ CONFIRMAR MANUALMENTE\n\n"
        f"Recarga: #{pid:06d}\n\n"
        f"⚠️ Esta ação irá creditar o valor na carteira do usuário.\n"
        f"Use somente quando o pagamento for realmente confirmado.\n\n"
        f"Informe uma observação (obrigatório, mínimo 3 caracteres):"
    )
    await cb.answer()


@router.message(RS.confirm_note)
async def confirm_manual_execute(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    note = m.text.strip() if m.text else ""
    if len(note) < 3:
        await m.answer("❌ Observação muito curta.")
        return

    data = await state.get_data()
    pid = data.get("payment_id")
    if not pid:
        await state.clear()
        await m.answer("Sessão expirada.")
        return

    ok = await _credit_recharge(pid, source="manual_admin", admin_id=m.from_user.id, note=note)
    await state.clear()

    if not ok:
        await m.answer("❌ Não foi possível confirmar (recarga já confirmada ou inválida).")
        return

    text, kb = await render_recharge(pid)
    await m.answer(f"✅ Recarga #{pid:06d} confirmada manualmente.\n📝 {note}")
    await m.answer(text, reply_markup=kb)


# ============================================================
# CANCELAR
# ============================================================
@router.callback_query(F.data.startswith("rcg_cancel_"))
async def cancel_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    await state.update_data(payment_id=pid)
    await state.set_state(RS.cancel_reason)
    await cb.message.answer(
        f"❌ CANCELAR RECARGA #{pid:06d}\n\n"
        f"O valor NÃO será creditado. Informe o motivo (mínimo 3 caracteres):"
    )
    await cb.answer()


@router.message(RS.cancel_reason)
async def cancel_execute(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    reason = m.text.strip() if m.text else ""
    if len(reason) < 3:
        await m.answer("❌ Motivo muito curto.")
        return

    data = await state.get_data()
    pid = data.get("payment_id")
    if not pid:
        await state.clear()
        await m.answer("Sessão expirada.")
        return

    async with AsyncSessionLocal() as s:
        p = await s.get(Payment, pid)
        if not p or p.status != "pending":
            await state.clear()
            await m.answer("❌ Recarga não pode ser cancelada.")
            return
        p.status = "cancelled"
        p.cancelled_at = datetime.utcnow()
        p.cancelled_by = m.from_user.id
        p.cancelled_reason = reason
        await s.commit()

    await log_admin(m.from_user.id, "recharge_cancel", {
        "payment_id": pid, "reason": reason,
    })
    await state.clear()

    text, kb = await render_recharge(pid)
    await m.answer(f"✅ Recarga #{pid:06d} cancelada.")
    await m.answer(text, reply_markup=kb)


# ============================================================
# ESTORNO (reverte crédito)
# ============================================================
@router.callback_query(F.data.startswith("rcg_refund_"))
async def refund_prompt(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        p = await s.get(Payment, pid)
        if not p or p.status != "confirmed":
            await cb.answer("Apenas recargas confirmadas podem ser estornadas.", show_alert=True)
            return
        credited = getattr(p, "credited_amount", p.amount + (p.bonus_amount or 0))

    await cb.message.edit_text(
        f"💜 ESTORNAR RECARGA #{pid:06d}\n\n"
        f"⚠️ Esta ação irá <b>debitar</b> R$ {credited:.2f} da carteira do usuário.\n"
        f"Se o saldo for insuficiente, a operação será recusada.\n\n"
        f"Confirma o estorno?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💜 Sim, estornar", callback_data=f"rcg_refund_ok_{pid}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"rcg_view_{pid}")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("rcg_refund_ok_"))
async def refund_execute(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        p = await s.get(Payment, pid)
        if not p or p.status != "confirmed":
            await cb.answer("Recarga não pode ser estornada.", show_alert=True)
            return

        credited = getattr(p, "credited_amount", p.amount + (p.bonus_amount or 0))
        wallet = await s.get(Wallet, p.user_id)
        if not wallet:
            await cb.answer("Carteira não encontrada.", show_alert=True)
            return
        if wallet.balance < credited:
            await cb.answer(
                f"Saldo insuficiente (R$ {wallet.balance:.2f} < R$ {credited:.2f}).",
                show_alert=True,
            )
            return

        before = wallet.balance
        wallet.balance -= credited
        p.status = "cancelled"
        p.cancelled_at = datetime.utcnow()
        p.cancelled_by = cb.from_user.id
        p.cancelled_reason = "Estorno administrativo"

        s.add(WalletTransaction(
            user_id=p.user_id,
            amount=-credited,
            type="admin_remove",
            description=f"Estorno da recarga #{pid}",
            balance_before=before,
            balance_after=wallet.balance,
        ))
        await s.commit()

    await log_admin(cb.from_user.id, "recharge_refund", {
        "payment_id": pid, "amount": credited,
    })

    text, kb = await render_recharge(pid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer(f"💜 Estorno de R$ {credited:.2f} concluído.")


# ============================================================
# FUNÇÃO CENTRAL DE CRÉDITO
# ============================================================
async def _credit_recharge(
    payment_id: int,
    source: str,
    admin_id: int | None = None,
    note: str | None = None,
) -> bool:
    async with AsyncSessionLocal() as s:
        p = await s.get(Payment, payment_id)
        if not p:
            return False
        if p.status == "confirmed":
            return False

        credited = getattr(p, "credited_amount", None)
        if credited is None:
            credited = round(p.amount + (p.bonus_amount or 0), 2)

        wallet = await s.get(Wallet, p.user_id)
        if not wallet:
            wallet = Wallet(user_id=p.user_id, balance=0.0)
            s.add(wallet)

        before = wallet.balance
        wallet.balance += credited
        after = wallet.balance

        p.status = "confirmed"
        p.confirmed_at = datetime.utcnow()
        p.confirmed_by = admin_id
        if not getattr(p, "credited_amount", None):
            p.credited_amount = credited

        desc_parts = [f"Recarga #{payment_id}"]
        if p.bonus_amount:
            desc_parts.append(f"bônus R$ {p.bonus_amount:.2f}")
        if source != "webhook":
            desc_parts.append(f"fonte {source}")
        if note:
            desc_parts.append(note)

        s.add(WalletTransaction(
            user_id=p.user_id,
            amount=credited,
            type="recharge",
            description=" | ".join(desc_parts),
            balance_before=before,
            balance_after=after,
        ))
        await s.commit()

        user_id = p.user_id
        amount = p.amount
        bonus = p.bonus_amount or 0.0

    try:
        await bot.send_message(
            user_id,
            f"✅ <b>RECARGA CONFIRMADA!</b>\n\n"
            f"Seu pagamento foi identificado.\n\n"
            f"💎 Valor recebido: R$ {amount:.2f}\n"
            f"🎁 Bônus: R$ {bonus:.2f}\n"
            f"💰 Saldo creditado: R$ {credited:.2f}\n"
            f"💳 Novo saldo: R$ {after:.2f}"
        )
    except Exception:
        pass

    if admin_id:
        await log_admin(admin_id, "recharge_credit", {
            "payment_id": payment_id, "source": source,
            "amount": credited, "note": note,
        })

    return True


# ============================================================
# CONFIGURAÇÃO PIX
# ============================================================
async def config_view() -> tuple[str, InlineKeyboardMarkup]:
    s = await get_settings(force=True)
    mp_ok = bool(getattr(s, "mp_access_token", None))

    text = (
        "◈ BELLAMY | CONFIGURAÇÃO DE PIX\n\n"
        f"💵 Recargas: {'🟢 Ativas' if getattr(s, 'recharge_enabled', True) else '🔴 Desativadas'}\n"
        f"💰 Valor mínimo: R$ {getattr(s, 'recharge_min', 5.0):.2f}\n"
        f"💰 Valor máximo: R$ {getattr(s, 'recharge_max', 500.0):.2f}\n"
        f"⏱️ Expiração PIX: {getattr(s, 'recharge_expiration_minutes', 30)} min\n"
        f"🎁 Bônus padrão: {getattr(s, 'recharge_bonus_default', 0.0):.2f}%\n"
        f"📊 Limite diário: {'sem limite' if not getattr(s, 'recharge_daily_limit', 0) else f'R$ {s.recharge_daily_limit:.2f}'}\n"
        f"📊 Limite por usuário: {'sem limite' if not getattr(s, 'recharge_per_user_limit', 0) else f'R$ {s.recharge_per_user_limit:.2f}'}\n"
        f"🕐 Horário: {getattr(s, 'recharge_hours_start', 0)}h às {getattr(s, 'recharge_hours_end', 24)}h\n\n"
        f"💳 Mercado Pago: {'🟢 Configurado' if mp_ok else '🔴 Não configurado'}\n"
    )
    if getattr(s, "mp_public_key", None):
        text += f"🔑 Public Key: <code>{s.mp_public_key[:20]}...</code>\n"
    if getattr(s, "mp_access_token", None):
        text += f"🔐 Access Token: <code>***{s.mp_access_token[-8:]}</code>\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar recargas" if getattr(s, "recharge_enabled", True) else "🟢 Ativar recargas"),
            callback_data="rcg_cfg_t_enabled")],
        [InlineKeyboardButton(text="💰 Alterar valor mínimo", callback_data="rcg_cfg_min")],
        [InlineKeyboardButton(text="💰 Alterar valor máximo", callback_data="rcg_cfg_max")],
        [InlineKeyboardButton(text="⏱️ Alterar expiração", callback_data="rcg_cfg_exp")],
        [InlineKeyboardButton(text="🎁 Alterar bônus padrão", callback_data="rcg_cfg_bonus")],
        [InlineKeyboardButton(text="📊 Limite diário", callback_data="rcg_cfg_daily")],
        [InlineKeyboardButton(text="📊 Limite por usuário", callback_data="rcg_cfg_user")],
        [InlineKeyboardButton(text="🕐 Horário de funcionamento", callback_data="rcg_cfg_hours")],
        [InlineKeyboardButton(text="💳 Credenciais Mercado Pago", callback_data="rcg_cfg_mp")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_recharges")],
    ])
    return text, kb


@router.callback_query(F.data == "rcg_config")
async def config(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "rcg_cfg_t_enabled")
async def cfg_toggle_enabled(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        cur = getattr(bs, "recharge_enabled", True)
        bs.recharge_enabled = not cur
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "recharge_toggle", {"new": not cur})
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


CFG_PROMPTS = {
    "min":      ("recharge_min",               "Novo valor mínimo (R$):",                     float, 0.01, 100000),
    "max":      ("recharge_max",               "Novo valor máximo (R$):",                     float, 1.0, 1000000),
    "exp":      ("recharge_expiration_minutes","Nova expiração do PIX (minutos, 5-1440):",    int,   5,   1440),
    "bonus":    ("recharge_bonus_default",     "Novo bônus padrão (%, 0-100):",               float, 0.0, 100),
    "daily":    ("recharge_daily_limit",       "Novo limite diário (R$, 0 = sem limite):",    float, 0.0, 1000000),
    "user":     ("recharge_per_user_limit",    "Novo limite por usuário (R$, 0 = sem):",      float, 0.0, 1000000),
}


@router.callback_query(F.data.startswith("rcg_cfg_"))
async def cfg_field_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("rcg_cfg_", "")

    if key == "hours":
        await state.set_state(RS.cfg_value)
        await state.update_data(cfg_key="hours")
        await cb.message.answer("Informe o horário no formato <code>INÍCIO-FIM</code> (ex.: 8-22, ou 0-24):")
        await cb.answer()
        return

    if key == "mp":
        await state.set_state(RS.cfg_value)
        await state.update_data(cfg_key="mp")
        await cb.message.answer(
            "💳 CONFIGURAR MERCADO PAGO\n\n"
            "Envie no formato:\n"
            "<code>ACCESS_TOKEN|PUBLIC_KEY</code>\n\n"
            "Exemplo:\n"
            "<code>APP_USR-xxxx|APP_USR-yyyy</code>\n\n"
            "Digite /limpar para remover as credenciais."
        )
        await cb.answer()
        return

    if key not in CFG_PROMPTS:
        await cb.answer("Campo inválido.")
        return

    field, prompt, _, _, _ = CFG_PROMPTS[key]
    await state.set_state(RS.cfg_value)
    await state.update_data(cfg_key=key)
    await cb.message.answer(f"✏️ {prompt}")
    await cb.answer()


@router.message(RS.cfg_value)
async def cfg_field_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    key = data.get("cfg_key")
    await state.clear()

    if key == "mp":
        if m.text.strip().lower() == "/limpar":
            async with AsyncSessionLocal() as s:
                bs = await s.get(BotSettings, 1)
                bs.mp_access_token = None
                bs.mp_public_key = None
                await s.commit()
            invalidate_cache()
            await log_admin(m.from_user.id, "recharge_mp_clear", {})
            text, kb = await config_view()
            await m.answer("✅ Credenciais removidas.", reply_markup=kb)
            return
        try:
            parts = m.text.strip().split("|", 1)
            if len(parts) != 2 or len(parts[0]) < 10:
                raise ValueError
            access, public = parts[0].strip(), parts[1].strip()
        except Exception:
            await m.answer("❌ Formato inválido. Use <code>ACCESS_TOKEN|PUBLIC_KEY</code>.")
            return
        async with AsyncSessionLocal() as s:
            bs = await s.get(BotSettings, 1)
            bs.mp_access_token = access
            bs.mp_public_key = public
            await s.commit()
        invalidate_cache()
        await log_admin(m.from_user.id, "recharge_mp_config", {})
        text, kb = await config_view()
        await m.answer("✅ Credenciais do Mercado Pago configuradas.", reply_markup=kb)
        return

    if key == "hours":
        try:
            h_start, h_end = m.text.strip().split("-")
            start, end = int(h_start), int(h_end)
            if not (0 <= start < end <= 24):
                raise ValueError
        except Exception:
            await m.answer("❌ Formato inválido. Use <code>INÍCIO-FIM</code> (ex.: 8-22).")
            return
        async with AsyncSessionLocal() as s:
            bs = await s.get(BotSettings, 1)
            bs.recharge_hours_start = start
            bs.recharge_hours_end = end
            await s.commit()
        invalidate_cache()
        await log_admin(m.from_user.id, "recharge_hours", {"start": start, "end": end})
        text, kb = await config_view()
        await m.answer(f"✅ Horário atualizado: {start}h–{end}h.", reply_markup=kb)
        return

    if key not in CFG_PROMPTS:
        await m.answer("❌ Campo inválido.")
        return

    field, _, ftype, fmin, fmax = CFG_PROMPTS[key]
    try:
        raw = m.text.strip().replace(",", ".") if ftype is float else m.text.strip()
        val = ftype(raw)
        if val < fmin or val > fmax:
            raise ValueError
    except Exception:
        await m.answer(f"❌ Valor inválido (mín {fmin}, máx {fmax}).")
        return

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        setattr(bs, field, val)
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, f"recharge_cfg_{key}", {"new": val})
    text, kb = await config_view()
    await m.answer(f"✅ Valor atualizado: {val}", reply_markup=kb)


# ============================================================
# BÔNUS POR FAIXA
# ============================================================
def _tier_kb(tiers: list, editing_id: int | None = None) -> InlineKeyboardMarkup:
    buttons = []
    for t in tiers:
        status = "🟢" if t.is_active else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} R$ {t.min_amount:.0f}–{t.max_amount:.0f} → {t.bonus_percentage:.1f}%",
            callback_data=f"rcg_tier_{t.id}",
        )])
    buttons.append([InlineKeyboardButton(text="➕ Adicionar faixa", callback_data="rcg_tier_new")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="rcg_config")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "rcg_tiers")
async def tiers_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        tiers = (await s.execute(
            select(RechargeBonusTier).order_by(RechargeBonusTier.min_amount.asc())
        )).scalars().all()

    text = (
        "🎁 BÔNUS POR FAIXA DE RECARGA\n\n"
        f"Total de faixas: {len(tiers)}\n\n"
    )
    if not tiers:
        text += "Nenhuma faixa cadastrada. O bônus padrão será usado."
    else:
        for t in tiers:
            st = "🟢 Ativa" if t.is_active else "🔴 Inativa"
            text += (
                f"• R$ {t.min_amount:.2f} – R$ {t.max_amount:.2f} → "
                f"{t.bonus_percentage:.2f}% ({st})\n"
            )
    text += "\n👇 Toque em uma faixa para editar:"

    await cb.message.edit_text(text, reply_markup=_tier_kb(tiers))
    await cb.answer()


@router.callback_query(F.data.startswith("rcg_tier_"))
async def tier_detail(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    key = cb.data.replace("rcg_tier_", "")
    if key == "new":
        await _start_new_tier(cb)
        return

    try:
        tid = int(key)
    except ValueError:
        await cb.answer("Faixa inválida.")
        return

    async with AsyncSessionLocal() as s:
        t = await s.get(RechargeBonusTier, tid)
        if not t:
            await cb.answer("Faixa não encontrada.", show_alert=True)
            return

    text = (
        f"🎁 FAIXA #{t.id}\n\n"
        f"💰 Valor mínimo: R$ {t.min_amount:.2f}\n"
        f"💰 Valor máximo: R$ {t.max_amount:.2f}\n"
        f"🎁 Bônus: {t.bonus_percentage:.2f}%\n"
        f"📌 Status: {'🟢 Ativa' if t.is_active else '🔴 Inativa'}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Editar valor mínimo", callback_data=f"rcg_tier_edit_{t.id}_min")],
        [InlineKeyboardButton(text="✏️ Editar valor máximo", callback_data=f"rcg_tier_edit_{t.id}_max")],
        [InlineKeyboardButton(text="✏️ Editar percentual", callback_data=f"rcg_tier_edit_{t.id}_pct")],
        [InlineKeyboardButton(
            text=("🔴 Desativar" if t.is_active else "🟢 Ativar"),
            callback_data=f"rcg_tier_toggle_{t.id}")],
        [InlineKeyboardButton(text="🗑️ Excluir faixa", callback_data=f"rcg_tier_del_{t.id}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="rcg_tiers")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


async def _start_new_tier(cb: CallbackQuery):
    await cb.message.answer(
        "➕ NOVA FAIXA (1/3)\n\nInforme o valor mínimo (R$):"
    )
    # Usa o estado via callback não sendo trivial — usamos uma sessão global implícita
    # Simplificamos: usamos callback data para guiar o fluxo por mensagens
    # (implementação direta abaixo com callback steps)
    await cb.answer()


# Fluxo de criação / edição controlado por callback simplificado
_new_tier_buffer: dict[int, dict] = {}


@router.callback_query(F.data == "rcg_tier_new")
async def tier_new_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(RS.tier_min)
    await cb.message.answer("➕ NOVA FAIXA (1/3)\n\nInforme o valor mínimo (R$):")
    await cb.answer()


@router.message(RS.tier_min)
async def tier_new_min(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        v = float(m.text.strip().replace(",", "."))
        if v <= 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido.")
        return
    await state.update_data(tier_min=v)
    await state.set_state(RS.tier_max)
    await m.answer("➕ NOVA FAIXA (2/3)\n\nInforme o valor máximo (R$):")


@router.message(RS.tier_max)
async def tier_new_max(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        v = float(m.text.strip().replace(",", "."))
    except ValueError:
        await m.answer("❌ Valor inválido.")
        return
    data = await state.get_data()
    if v <= data.get("tier_min", 0):
        await m.answer("❌ O máximo deve ser maior que o mínimo.")
        return
    await state.update_data(tier_max=v)
    await state.set_state(RS.tier_percent)
    await m.answer("➕ NOVA FAIXA (3/3)\n\nInforme o percentual de bônus (0-100):")


@router.message(RS.tier_percent)
async def tier_new_percent(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        v = float(m.text.strip().replace(",", "."))
        if v < 0 or v > 100:
            raise ValueError
    except ValueError:
        await m.answer("❌ Percentual inválido (0-100).")
        return
    data = await state.get_data()
    async with AsyncSessionLocal() as s:
        t = RechargeBonusTier(
            min_amount=data["tier_min"],
            max_amount=data["tier_max"],
            bonus_percentage=v,
            is_active=True,
        )
        s.add(t)
        await s.commit()
        await s.refresh(t)
        tid = t.id
    await log_admin(m.from_user.id, "tier_create", {
        "tier_id": tid, "min": data["tier_min"], "max": data["tier_max"], "pct": v,
    })
    await state.clear()

    async with AsyncSessionLocal() as s:
        tiers = (await s.execute(
            select(RechargeBonusTier).order_by(RechargeBonusTier.min_amount.asc())
        )).scalars().all()
    await m.answer(
        f"✅ Faixa criada: R$ {data['tier_min']:.2f}–{data['tier_max']:.2f} → {v:.2f}%",
        reply_markup=_tier_kb(tiers),
    )


@router.callback_query(F.data.startswith("rcg_tier_edit_"))
async def tier_edit_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    parts = cb.data.split("_")
    tid = int(parts[3])
    field = parts[4]
    await state.update_data(tier_id=tid, tier_field=field)
    await state.set_state(RS.tier_edit_value)
    prompts = {
        "min": "Novo valor mínimo (R$):",
        "max": "Novo valor máximo (R$):",
        "pct": "Novo percentual de bônus (0-100):",
    }
    await cb.message.answer(f"✏️ {prompts.get(field, 'Novo valor:')}")
    await cb.answer()


@router.message(RS.tier_edit_value)
async def tier_edit_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    tid = data.get("tier_id")
    field = data.get("tier_field")
    if not tid or not field:
        await state.clear()
        return

    try:
        v = float(m.text.strip().replace(",", "."))
        if v < 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido.")
        return

    async with AsyncSessionLocal() as s:
        t = await s.get(RechargeBonusTier, tid)
        if not t:
            await state.clear()
            await m.answer("❌ Faixa não encontrada.")
            return
        mapping = {"min": "min_amount", "max": "max_amount", "pct": "bonus_percentage"}
        attr = mapping[field]
        if attr == "max_amount" and v <= t.min_amount:
            await m.answer("❌ O máximo deve ser maior que o mínimo.")
            return
        if attr == "min_amount" and v >= t.max_amount:
            await m.answer("❌ O mínimo deve ser menor que o máximo.")
            return
        setattr(t, attr, v)
        await s.commit()

    await log_admin(m.from_user.id, "tier_edit", {
        "tier_id": tid, "field": field, "new": v,
    })
    await state.clear()

    async with AsyncSessionLocal() as s:
        tiers = (await s.execute(
            select(RechargeBonusTier).order_by(RechargeBonusTier.min_amount.asc())
        )).scalars().all()
    await m.answer("✅ Faixa atualizada.", reply_markup=_tier_kb(tiers))


@router.callback_query(F.data.startswith("rcg_tier_toggle_"))
async def tier_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        t = await s.get(RechargeBonusTier, tid)
        if not t:
            await cb.answer("Faixa não encontrada.", show_alert=True)
            return
        t.is_active = not t.is_active
        new = t.is_active
        await s.commit()
    await log_admin(cb.from_user.id, "tier_toggle", {"tier_id": tid, "new": new})
    async with AsyncSessionLocal() as s:
        tiers = (await s.execute(
            select(RechargeBonusTier).order_by(RechargeBonusTier.min_amount.asc())
        )).scalars().all()
    await cb.message.edit_text(
        "🎁 BÔNUS POR FAIXA DE RECARGA\n\nFaixas atualizadas.",
        reply_markup=_tier_kb(tiers),
    )
    await cb.answer("Atualizado.")


@router.callback_query(F.data.startswith("rcg_tier_del_"))
async def tier_delete(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        t = await s.get(RechargeBonusTier, tid)
        if not t:
            await cb.answer("Faixa não encontrada.", show_alert=True)
            return
        await s.delete(t)
        await s.commit()
    await log_admin(cb.from_user.id, "tier_delete", {"tier_id": tid})
    async with AsyncSessionLocal() as s:
        tiers = (await s.execute(
            select(RechargeBonusTier).order_by(RechargeBonusTier.min_amount.asc())
        )).scalars().all()
    await cb.message.edit_text(
        f"✅ Faixa #{tid} excluída.",
        reply_markup=_tier_kb(tiers),
    )
    await cb.answer("Faixa excluída.")


# ============================================================
# BUSCA
# ============================================================
@router.callback_query(F.data == "rcg_search")
async def search_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(RS.search)
    await cb.message.answer(
        "🔎 BUSCAR RECARGA\n\n"
        "Envie:\n"
        "• ID da recarga\n"
        "• ID do Telegram do usuário\n"
        "• @username\n"
        "• Nome do usuário\n\n"
        "Digite /cancelar para abortar."
    )
    await cb.answer()


@router.message(RS.search)
async def search_execute(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    q = m.text.strip() if m.text else ""
    if q.lower() == "/cancelar":
        await state.clear()
        await m.answer("Operação cancelada.")
        return
    await state.clear()

    payments = []
    async with AsyncSessionLocal() as s:
        if q.isdigit():
            n = int(q)
            p = await s.get(Payment, n)
            if p:
                payments = [p]
            else:
                stmt = (
                    select(Payment)
                    .where(Payment.user_id == n)
                    .order_by(desc(Payment.created_at))
                    .limit(20)
                )
                payments = (await s.execute(stmt)).scalars().all()
        else:
            clean = q.lstrip("@")
            stmt = (
                select(Payment)
                .join(User, User.id == Payment.user_id)
                .where(or_(
                    User.username.ilike(f"%{clean}%"),
                    User.full_name.ilike(f"%{q}%"),
                ))
                .order_by(desc(Payment.created_at))
                .limit(20)
            )
            payments = (await s.execute(stmt)).scalars().all()

    if not payments:
        await m.answer("❌ Nenhuma recarga encontrada.")
        return

    text = f"🔎 RESULTADOS: {len(payments)} recarga(s)\n\n"
    buttons = []
    for p in payments:
        icon = STATUS_ICON.get(p.status, "⚪")
        text += f"{icon} #{p.id:06d} — R$ {p.amount:.2f} — User {p.user_id}\n"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} #{p.id:06d} — R$ {p.amount:.2f}",
            callback_data=f"rcg_view_{p.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_recharges")])
    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ============================================================
# ESTATÍSTICAS
# ============================================================
@router.callback_query(F.data == "rcg_stats")
async def stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    today = datetime.utcnow().date()
    week_start = today - timedelta(days=7)
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(Payment.id)))).scalar() or 0
        counts = {}
        for st in ("pending", "confirmed", "expired", "cancelled", "failed"):
            counts[st] = (await s.execute(
                select(func.count(Payment.id)).where(Payment.status == st)
            )).scalar() or 0

        def volume_since(dt):
            return (
                select(func.coalesce(func.sum(Payment.amount), 0))
                .where(Payment.status == "confirmed")
                .where(Payment.created_at >= dt)
            )

        vol_today = (await s.execute(volume_since(
            datetime.combine(today, datetime.min.time())
        ))).scalar() or 0.0
        vol_week = (await s.execute(volume_since(
            datetime.combine(week_start, datetime.min.time())
        ))).scalar() or 0.0
        vol_month = (await s.execute(volume_since(
            datetime.combine(month_start, datetime.min.time())
        ))).scalar() or 0.0
        vol_total = (await s.execute(volume_since(datetime(2000, 1, 1)))).scalar() or 0.0

        avg_ticket = (vol_total / counts["confirmed"]) if counts["confirmed"] else 0.0

        bonus_total = (await s.execute(
            select(func.coalesce(func.sum(Payment.bonus_amount), 0))
            .where(Payment.status == "confirmed")
        )).scalar() or 0.0

        conv_rate = (counts["confirmed"] / total * 100) if total else 0.0

        top_users = (await s.execute(
            select(User.full_name, User.username, Payment.user_id,
                   func.coalesce(func.sum(Payment.amount), 0).label("total"))
            .join(Payment, Payment.user_id == User.id)
            .where(Payment.status == "confirmed")
            .group_by(Payment.user_id, User.full_name, User.username)
            .order_by(desc("total"))
            .limit(5)
        )).all()

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DE RECARGAS\n\n"
        f"📊 Total: {total}\n"
        f"🟢 Confirmadas: {counts['confirmed']}\n"
        f"🟡 Pendentes: {counts['pending']}\n"
        f"⚪ Expiradas: {counts['expired']}\n"
        f"🔴 Canceladas: {counts['cancelled']}\n"
        f"⚠️ Falhas: {counts['failed']}\n\n"
        f"📈 Taxa de conversão: {conv_rate:.1f}%\n"
        f"🎯 Ticket médio: R$ {avg_ticket:.2f}\n"
        f"🎁 Bônus distribuídos: R$ {bonus_total:.2f}\n\n"
        f"📅 Hoje: R$ {vol_today:.2f}\n"
        f"📆 Semana: R$ {vol_week:.2f}\n"
        f"🗓️ Mês: R$ {vol_month:.2f}\n"
        f"💎 Total histórico: R$ {vol_total:.2f}\n\n"
    )
    if top_users:
        text += "🏆 TOP 5 DEPOSITANTES\n"
        for i, (name, uname, uid, total_v) in enumerate(top_users, 1):
            display = name or (f"@{uname}" if uname else f"User {uid}")
            text += f"{i}. {display} — R$ {total_v:.2f}\n"

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="rcg_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_recharges")],
    ]))
    await cb.answer()


# ============================================================
# EXPIRAR PENDENTES ANTIGAS
# ============================================================
@router.callback_query(F.data == "rcg_expire_old")
async def expire_old(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    now = datetime.utcnow()
    async with AsyncSessionLocal() as s:
        pending = (await s.execute(
            select(Payment)
            .where(Payment.status == "pending")
            .where(Payment.expires_at < now)
        )).scalars().all()
        count = len(pending)
        for p in pending:
            p.status = "expired"
            p.cancelled_at = now
            p.cancelled_reason = "Expirada automaticamente (admin)"
        await s.commit()

    await log_admin(cb.from_user.id, "recharge_expire_old", {"count": count})
    await cb.answer(f"✅ {count} recarga(s) marcada(s) como expirada(s).", show_alert=True)


# ============================================================
# EXPORTAÇÃO
# ============================================================
@router.callback_query(F.data == "rcg_export")
async def export_report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        all_payments = (await s.execute(
            select(Payment).order_by(desc(Payment.created_at)).limit(500)
        )).scalars().all()

        uids = list({p.user_id for p in all_payments})
        users = {}
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}

    lines = []
    lines.append("═" * 60)
    lines.append("BELLAMY | RELATÓRIO DE RECARGAS")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append(f"Total: {len(all_payments)} recarga(s) (últimas 500)")
    lines.append("═" * 60)
    lines.append("")

    for p in all_payments:
        u = users.get(p.user_id)
        uname = u.full_name if u else "Desconhecido"
        uhandle = f"@{u.username}" if u and u.username else "—"
        lines.append(f"Recarga #{p.id:06d}")
        lines.append(f"  Data:       {p.created_at.strftime('%d/%m/%Y %H:%M')}")
        lines.append(f"  Status:     {p.status}")
        lines.append(f"  Valor:      R$ {p.amount:.2f}")
        lines.append(f"  Bônus:      R$ {p.bonus_amount:.2f}")
        lines.append(f"  Cliente:    {uname} ({uhandle}) — ID {p.user_id}")
        mp_id = getattr(p, "mp_payment_id", None)
        if mp_id:
            lines.append(f"  MP ID:      {mp_id}")
        if getattr(p, "cancelled_reason", None):
            lines.append(f"  Motivo:     {p.cancelled_reason}")
        lines.append("")

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"recargas_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption=f"📊 <b>Relatório de Recargas</b>\n\nTotal: {len(all_payments)} recarga(s)",
    )
    await cb.answer("Relatório gerado.")


# ============================================================
# WEBHOOK HANDLER (Mercado Pago)
# ============================================================
async def handle_mp_webhook(request) -> dict:
    """
    Adicionar em app.py no Módulo 20:
        from admin.recharges import handle_mp_webhook
        @app.post("/webhook/mercadopago")
        async def mp_wh(request: Request):
            return await handle_mp_webhook(request)
    """
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid_json"}

    if payload.get("type") != "payment":
        return {"ok": True, "ignored": True}

    data = payload.get("data") or {}
    mp_payment_id = data.get("id")
    if not mp_payment_id:
        return {"ok": False, "error": "no_payment_id"}

    client = await get_mp_client()
    if not client:
        return {"ok": False, "error": "mp_not_configured"}

    try:
        mp = await client.get_payment(str(mp_payment_id))
    except Exception as e:
        return {"ok": False, "error": f"mp_fetch_failed: {e}"}

    status = mp.get("status", "unknown")
    ext_ref = mp.get("external_reference")

    if not ext_ref:
        return {"ok": True, "ignored": True}

    # ext_ref = "rcg_<id>"
    if not str(ext_ref).startswith("rcg_"):
        return {"ok": True, "ignored": True}

    try:
        local_id = int(str(ext_ref).split("_", 1)[1])
    except Exception:
        return {"ok": False, "error": "bad_external_ref"}

    if status == "approved":
        await _credit_recharge(local_id, source="webhook")
        return {"ok": True, "credited": local_id}

    if status in ("cancelled", "rejected", "refunded", "charged_back"):
        async with AsyncSessionLocal() as s:
            p = await s.get(Payment, local_id)
            if p and p.status == "pending":
                p.status = "cancelled"
                p.cancelled_at = datetime.utcnow()
                p.cancelled_reason = f"MP webhook: {status}"
                await s.commit()
        return {"ok": True, "cancelled": local_id, "mp_status": status}

    return {"ok": True, "pending": local_id, "mp_status": status}
