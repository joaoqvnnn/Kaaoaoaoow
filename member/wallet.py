import base64
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ForceReply, BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func

from bot import dp
from database import AsyncSessionLocal
from models import (
    Payment, Wallet, User, RechargeBonusTier,
)
from settings_cache import get_settings

from admin.recharges import (
    MercadoPagoClient, get_mp_client, _credit_recharge,
)

router = Router()
dp.include_router(router)


class RechargeStates(StatesGroup):
    waiting_amount = State()


# ============================================================
# HELPERS
# ============================================================
async def _calc_bonus(amount: float) -> float:
    async with AsyncSessionLocal() as s:
        tiers = (await s.execute(
            select(RechargeBonusTier)
            .where(RechargeBonusTier.is_active == True)
            .order_by(RechargeBonusTier.min_amount)
        )).scalars().all()

    for t in tiers:
        if t.min_amount <= amount <= t.max_amount:
            return round(amount * (t.bonus_percentage / 100.0), 2)

    settings = await get_settings()
    default_pct = getattr(settings, "recharge_bonus_default", 0.0)
    return round(amount * (default_pct / 100.0), 2)


def _decode_qr_base64(b64: str | None) -> bytes | None:
    """Decodifica o QR code base64 do Mercado Pago.
    Aceita base64 puro ou com prefixo `data:image/...;base64,`.
    Retorna None se vazio ou inválido.
    """
    if not b64 or not isinstance(b64, str):
        return None
    try:
        clean = b64.strip()
        if clean.startswith("data:"):
            if "," in clean:
                clean = clean.split(",", 1)[1]
            else:
                return None
        return base64.b64decode(clean)
    except Exception:
        return None


async def _get_return_keyboard(state: FSMContext) -> list[list[InlineKeyboardButton]]:
    data = await state.get_data()
    ctx = data.get("return_ctx")
    buttons = []

    if ctx == "purchase_summary" and data.get("return_product_id"):
        qty = data.get("return_quantity", 1)
        buttons.append([InlineKeyboardButton(
            text="📋 Voltar ao resumo do pedido",
            callback_data=f"recharge_return_summary_{data['return_product_id']}_{qty}",
        )])
    elif ctx == "cart":
        buttons.append([InlineKeyboardButton(
            text="🛒 Voltar ao carrinho", callback_data="show_cart",
        )])
    elif ctx == "product" and data.get("return_product_id"):
        buttons.append([InlineKeyboardButton(
            text="📦 Voltar ao produto",
            callback_data=f"show_product_{data['return_product_id']}",
        )])

    buttons.append([InlineKeyboardButton(
        text="🏠 Menu principal", callback_data="main_menu",
    )])
    return buttons


# ============================================================
# MENU DE RECARGA
# ============================================================
@router.callback_query(F.data == "show_recharge")
async def cb_show_recharge(cb: CallbackQuery, state: FSMContext):
    settings = await get_settings()
    if not getattr(settings, "recharge_enabled", True):
        await cb.answer("💳 As recargas estão desativadas no momento.", show_alert=True)
        return

    mp_token = getattr(settings, "mp_access_token", None)
    if not mp_token:
        await cb.message.edit_text(
            "⚠️ O PIX está temporariamente indisponível.\n\n"
            "Entre em contato com o suporte.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
            ]),
        )
        await cb.answer()
        return

    text = (
        "💳 Selecione a opção desejada para adicionar saldo via PIX!\n\n"
        "🔹 Adicione crédito de forma rápida e segura com PIX.\n"
        "💸 Escolha o valor e faça o pagamento."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💠 PIX automático", callback_data="recharge_pix")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="recharge_back")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "recharge_back")
async def cb_recharge_back(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ctx = data.get("return_ctx")
    pid = data.get("return_product_id")
    qty = data.get("return_quantity", 1)

    await state.clear()

    if ctx == "purchase_summary" and pid:
        from member.product_detail import _check_buy, show_purchase_summary
        ok, err, result = await _check_buy(int(pid), cb.from_user.id, int(qty))
        if ok:
            await show_purchase_summary(cb, int(pid), int(qty), result)
            await cb.answer()
            return
        if err == "insufficient_balance":
            await cb.message.edit_text(
                f"⚠️ SALDO INSUFICIENTE\n\n"
                f"💰 Valor que falta: R$ {result['missing']:.2f}\n\n"
                f"Adicione saldo e tente novamente.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Adicionar saldo", callback_data="show_recharge")],
                    [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"show_product_{pid}")],
                ]),
            )
            await cb.answer()
            return

    if ctx == "cart":
        cb.data = "show_cart"
        from member import cart as cart_module
        await cart_module._render_cart(cb, cb.from_user.id)
        await cb.answer()
        return

    if ctx == "product" and pid:
        try:
            from member.product_detail import _build_detail
            result = await _build_detail(int(pid), cb.from_user.id)
            if result:
                text, kb, image_url = result
                if image_url:
                    try:
                        await cb.message.delete()
                    except Exception:
                        pass
                    await cb.message.answer_photo(
                        photo=image_url, caption=text, reply_markup=kb,
                    )
                else:
                    await cb.message.edit_text(text, reply_markup=kb)
                await cb.answer()
                return
        except Exception:
            pass

    from member.start import _send_welcome
    await _send_welcome(cb, edit=True)
    await cb.answer()


@router.callback_query(F.data.startswith("recharge_return_summary_"))
async def cb_recharge_return_summary(cb: CallbackQuery):
    rest = cb.data.replace("recharge_return_summary_", "")
    try:
        pid_s, qty_s = rest.rsplit("_", 1)
        pid = int(pid_s)
        qty = int(qty_s)
    except (ValueError, IndexError):
        await cb.answer("Parâmetros inválidos.", show_alert=True)
        return

    from member.product_detail import _check_buy, show_purchase_summary

    ok, err, data = await _check_buy(pid, cb.from_user.id, qty)
    if not ok:
        if err == "insufficient_balance":
            await cb.answer(
                f"Saldo ainda insuficiente. Falta R$ {data['missing']:.2f}",
                show_alert=True,
            )
        else:
            await cb.answer(err, show_alert=True)
        return

    await show_purchase_summary(cb, pid, qty, data)
    await cb.answer()


# ============================================================
# PIX — PEDIR VALOR
# ============================================================
@router.callback_query(F.data == "recharge_pix")
async def cb_recharge_pix(cb: CallbackQuery, state: FSMContext):
    settings = await get_settings()
    min_val = getattr(settings, "recharge_min", 5.0)
    max_val = getattr(settings, "recharge_max", 500.0)
    bonus_pct = getattr(settings, "recharge_bonus_default", 0.0)

    await state.set_state(RechargeStates.waiting_amount)
    await cb.message.answer(
        f"🏦 RECARGA DE SALDO\n\n"
        f"🔻 Depósito mínimo: R$ {min_val:.2f}\n"
        f"🔺 Depósito máximo: R$ {max_val:.2f}\n"
        f"🎁 Bônus de depósito: {bonus_pct:.2f}%\n\n"
        f"➡️ Envie o valor que deseja recarregar. Digite apenas números.",
        reply_markup=ForceReply(input_field_placeholder="Valor em R$"),
    )
    await cb.answer()


@router.message(RechargeStates.waiting_amount)
async def msg_recharge_amount(m: Message, state: FSMContext):
    raw = (m.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await m.answer("❌ Envie apenas um número válido (ex.: 50.00).")
        return

    settings = await get_settings()
    min_val = getattr(settings, "recharge_min", 5.0)
    max_val = getattr(settings, "recharge_max", 500.0)

    if amount < min_val:
        await m.answer(f"❌ Valor mínimo: R$ {min_val:.2f}")
        return
    if amount > max_val:
        await m.answer(f"❌ Valor máximo: R$ {max_val:.2f}")
        return

    daily_limit = getattr(settings, "recharge_daily_limit", 0.0)
    per_user_limit = getattr(settings, "recharge_per_user_limit", 0.0)

    if daily_limit > 0 or per_user_limit > 0:
        async with AsyncSessionLocal() as s:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            sum_today = (await s.execute(
                select(func.coalesce(func.sum(Payment.amount), 0))
                .where(Payment.user_id == m.from_user.id)
                .where(Payment.status == "confirmed")
                .where(Payment.created_at >= today_start)
            )).scalar() or 0.0
            sum_total = (await s.execute(
                select(func.coalesce(func.sum(Payment.amount), 0))
                .where(Payment.user_id == m.from_user.id)
                .where(Payment.status == "confirmed")
            )).scalar() or 0.0

        if daily_limit > 0 and (sum_today + amount) > daily_limit:
            await m.answer(
                f"❌ Limite diário de R$ {daily_limit:.2f} atingido.\n"
                f"Já recarregado hoje: R$ {sum_today:.2f}"
            )
            return

        if per_user_limit > 0 and (sum_total + amount) > per_user_limit:
            await m.answer(
                f"❌ Limite por usuário de R$ {per_user_limit:.2f} atingido."
            )
            return

    h_start = getattr(settings, "recharge_hours_start", 0)
    h_end = getattr(settings, "recharge_hours_end", 24)
    if h_start > 0 or h_end < 24:
        now_hour = datetime.utcnow().hour
        if not (h_start <= now_hour < h_end):
            await m.answer(
                f"⏰ As recargas estão disponíveis apenas entre "
                f"{h_start}h e {h_end}h."
            )
            return

    bonus = await _calc_bonus(amount)
    await state.clear()

    try:
        await _create_pix_charge(m, m.from_user.id, amount, bonus)
    except Exception as e:
        await m.answer(
            f"❌ Erro ao criar cobrança PIX.\n\n"
            f"<code>{str(e)[:200]}</code>\n\n"
            f"Tente novamente ou contate o suporte."
        )


# ============================================================
# CRIAR COBRANÇA PIX (com QR Code como imagem)
# ============================================================
async def _create_pix_charge(target: Message, user_id: int, amount: float, bonus: float):
    settings = await get_settings()
    expiration_min = getattr(settings, "recharge_expiration_minutes", 30)

    client = await get_mp_client()
    if not client:
        await target.answer("⚠️ PIX indisponível no momento.")
        return

    async with AsyncSessionLocal() as s:
        payment = Payment(
            user_id=user_id,
            amount=amount,
            bonus_amount=bonus,
            credited_amount=round(amount + bonus, 2),
            status="pending",
            provider="mercadopago",
            method="pix_mp",
            expires_at=datetime.utcnow() + timedelta(minutes=expiration_min),
        )
        s.add(payment)
        await s.commit()
        await s.refresh(payment)
        payment_id = payment.id

    ext_ref = f"rcg_{payment_id}"
    try:
        mp_resp = await client.create_pix_payment(
            amount=amount,
            description=f"Recarga Bellamy #{payment_id}",
            external_reference=ext_ref,
            expiration_minutes=expiration_min,
        )
    except Exception as e:
        async with AsyncSessionLocal() as s:
            p = await s.get(Payment, payment_id)
            p.status = "failed"
            p.cancelled_reason = str(e)[:200]
            await s.commit()
        raise

    pix_data = MercadoPagoClient.extract_pix_data(mp_resp)

    async with AsyncSessionLocal() as s:
        p = await s.get(Payment, payment_id)
        p.mp_payment_id = str(mp_resp.get("id"))
        p.external_reference = ext_ref
        p.pix_code = pix_data.get("pix_code")
        p.qr_code_base64 = pix_data.get("qr_code_base64")
        p.ticket_url = pix_data.get("ticket_url")
        await s.commit()

    pix_code = pix_data.get("pix_code") or "—"
    qr_base64 = pix_data.get("qr_code_base64")

    buttons = [
        [InlineKeyboardButton(
            text="⏳ Aguardando pagamento",
            callback_data=f"recharge_check_{payment_id}",
        )],
        [InlineKeyboardButton(
            text="📋 Copiar PIX",
            callback_data=f"recharge_copy_{payment_id}",
        )],
        [InlineKeyboardButton(
            text="↩️ Cancelar recarga",
            callback_data=f"recharge_cancel_{payment_id}",
        )],
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Caption curto (para caber na foto do Telegram)
    caption = (
        f"💰 <b>COMPRAR SALDO COM PIX AUTOMÁTICO</b>\n\n"
        f"⏱ Expira em: {expiration_min} minutos\n"
        f"💰 Valor: R$ {amount:.2f}\n"
        f"🎁 Bônus: R$ {bonus:.2f}\n"
        f"💎 Total a receber: R$ {amount + bonus:.2f}\n"
        f"✨ ID: {payment_id}"
    )

    qr_bytes = _decode_qr_base64(qr_base64)

    # Tentativa 1: enviar como foto com QR code
    if qr_bytes:
        try:
            photo_file = BufferedInputFile(
                qr_bytes, filename=f"qrcode_pix_{payment_id}.png",
            )
            await target.answer_photo(
                photo=photo_file,
                caption=caption,
                reply_markup=kb,
            )
            # Envia o PIX copia-e-cola em mensagem separada
            # (o código costuma ser grande e estouraria o caption da foto)
            await target.answer(
                f"💎 <b>PIX COPIA E COLA</b>\n\n"
                f"<code>{pix_code}</code>\n\n"
                f"🇧🇷 Após a confirmação do pagamento, seu saldo "
                f"será atualizado automaticamente."
            )
            return
        except Exception as e:
            print(f"[wallet] falha ao enviar QR como foto: {e}")
            # Cai no fallback abaixo

    # Fallback: texto apenas (quando não há QR ou o envio da foto falhou)
    full_text = (
        f"{caption}\n\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"💎 <b>PIX COPIA E COLA</b>\n\n"
        f"<code>{pix_code}</code>\n\n"
        f"🇧🇷 Após a confirmação do pagamento, seu saldo "
        f"será atualizado automaticamente."
    )
    await target.answer(full_text, reply_markup=kb)


# ============================================================
# VERIFICAR PAGAMENTO
# ============================================================
@router.callback_query(F.data.startswith("recharge_check_"))
async def cb_recharge_check(cb: CallbackQuery, state: FSMContext):
    try:
        pid = int(cb.data.replace("recharge_check_", ""))
    except (ValueError, TypeError):
        await cb.answer("Recarga inválida.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        payment = await s.get(Payment, pid)
        if not payment or payment.user_id != cb.from_user.id:
            await cb.answer("Recarga não encontrada.", show_alert=True)
            return

    if payment.status == "confirmed":
        await _show_recharge_confirmed(cb, payment, state)
        await cb.answer()
        return

    if payment.status in ("cancelled", "failed", "expired"):
        await cb.answer("Esta recarga já foi encerrada.", show_alert=True)
        return

    mp_id = getattr(payment, "mp_payment_id", None)
    if not mp_id:
        await cb.answer("Recarga sem dados do Mercado Pago.", show_alert=True)
        return

    client = await get_mp_client()
    if not client:
        await cb.answer("PIX indisponível.", show_alert=True)
        return

    try:
        mp = await client.get_payment(mp_id)
    except Exception as e:
        await cb.answer(f"Erro ao consultar: {str(e)[:100]}", show_alert=True)
        return

    status = mp.get("status", "unknown")

    if status == "approved":
        await _credit_recharge(pid, source="member_check")
        async with AsyncSessionLocal() as s:
            payment = await s.get(Payment, pid)
        await _show_recharge_confirmed(cb, payment, state)
        await cb.answer("✅ Pagamento confirmado!")
        return

    if status in ("cancelled", "rejected", "refunded", "charged_back"):
        async with AsyncSessionLocal() as s:
            p = await s.get(Payment, pid)
            p.status = "cancelled"
            p.cancelled_at = datetime.utcnow()
            p.cancelled_reason = f"MP status: {status}"
            await s.commit()
        await cb.answer(f"Pagamento {status}.", show_alert=True)
        return

    await cb.answer(
        "⏳ PAGAMENTO NÃO IDENTIFICADO\n\n"
        "Se você já pagou, aguarde a atualização bancária (até 2 minutos) "
        "e toque novamente.",
        show_alert=True,
    )


# ============================================================
# CONFIRMADO
# ============================================================
async def _show_recharge_confirmed(cb: CallbackQuery, payment: Payment, state: FSMContext):
    async with AsyncSessionLocal() as s:
        w = await s.get(Wallet, payment.user_id)

    new_balance = w.balance if w else 0.0

    text = (
        "✅ RECARGA CONFIRMADA!\n\n"
        "Seu pagamento foi identificado.\n\n"
        f"💎 Valor recebido: R$ {payment.amount:.2f}\n"
        f"🎁 Bônus: R$ {(payment.bonus_amount or 0):.2f}\n"
        f"💰 Novo saldo: R$ {new_balance:.2f}"
    )

    return_buttons = await _get_return_keyboard(state)
    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=return_buttons)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cb.message.answer(text, reply_markup=kb)


# ============================================================
# COPIAR PIX
# ============================================================
@router.callback_query(F.data.startswith("recharge_copy_"))
async def cb_recharge_copy(cb: CallbackQuery):
    try:
        pid = int(cb.data.replace("recharge_copy_", ""))
    except (ValueError, TypeError):
        await cb.answer("Recarga inválida.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        payment = await s.get(Payment, pid)
        if not payment or payment.user_id != cb.from_user.id:
            await cb.answer("Recarga não encontrada.", show_alert=True)
            return

    pix_code = getattr(payment, "pix_code", None) or ""
    if not pix_code:
        await cb.answer("Código PIX indisponível.", show_alert=True)
        return

    await cb.message.answer(
        f"📋 <b>PIX copia e cola:</b>\n\n<code>{pix_code}</code>"
    )
    await cb.answer("Código enviado.")


# ============================================================
# CANCELAR
# ============================================================
@router.callback_query(F.data.startswith("recharge_cancel_"))
async def cb_recharge_cancel(cb: CallbackQuery, state: FSMContext):
    try:
        pid = int(cb.data.replace("recharge_cancel_", ""))
    except (ValueError, TypeError):
        await cb.answer("Recarga inválida.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        payment = await s.get(Payment, pid)
        if not payment or payment.user_id != cb.from_user.id:
            await cb.answer("Recarga não encontrada.", show_alert=True)
            return
        if payment.status != "pending":
            await cb.answer("Recarga já encerrada.", show_alert=True)
            return
        payment.status = "cancelled"
        payment.cancelled_at = datetime.utcnow()
        payment.cancelled_by = cb.from_user.id
        payment.cancelled_reason = "Cancelado pelo cliente"
        await s.commit()

    text = (
        "❌ RECARGA CANCELADA\n\n"
        "A cobrança foi encerrada e nenhum valor será cobrado."
    )
    return_buttons = await _get_return_keyboard(state)
    await state.clear()

    await cb.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=return_buttons),
    )
    await cb.answer("Cancelado.")
