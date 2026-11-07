from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ForceReply,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func
from datetime import datetime, timedelta

from bot import dp
from database import AsyncSessionLocal
from models import (
    Product, User, Wallet, WalletTransaction, Order,
    CartItem, StockMovement, DeepLinkVisit,
)
from settings_cache import get_settings

from admin.customization import get_rendered_message, get_rendered_image

router = Router()
dp.include_router(router)


class BuyStates(StatesGroup):
    waiting_quantity = State()


def _effective_price(p: Product) -> float:
    return p.promo_price if p.promo_price and p.promo_price > 0 else p.price


def _mask(text: str) -> str:
    if not text:
        return "•••••••••••••••"
    return "•" * min(len(text), 24)


# ============================================================
# DETALHE DO PRODUTO
# ============================================================
def _personalize_detail_kb(kb: InlineKeyboardMarkup, product_id: int) -> InlineKeyboardMarkup:
    cb_map = {
        "buy_now":   f"pd_buy_now_{product_id}",
        "buy_qty":   f"pd_buy_qty_{product_id}",
        "add_cart":  f"pd_add_cart_{product_id}",
        "show_recharge": "show_recharge",
        "show_catalog":  "show_catalog",
    }
    new_rows = []
    for row in kb.inline_keyboard:
        new_row = []
        for b in row:
            target_cb = cb_map.get(b.callback_data, b.callback_data)
            new_row.append(InlineKeyboardButton(
                text=b.text, callback_data=target_cb, url=b.url,
            ))
        if new_row:
            new_rows.append(new_row)
    return InlineKeyboardMarkup(inline_keyboard=new_rows)


async def _build_detail(product_id: int, user_id: int):
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, product_id)
        if not p or not p.is_active:
            return None
        u = await s.get(User, user_id)
        w = await s.get(Wallet, user_id)

    settings = await get_settings()
    allow_oos = getattr(settings, "allow_out_of_stock_sales", False)
    price = _effective_price(p)
    balance = w.balance if w else 0.0
    username = (u.username if u and u.username else "sem_username")
    display_name = (u.full_name if u and u.full_name else "Cliente")

    variables = {
        "nome_produto": p.name,
        "preco": f"{price:.2f}",
        "saldo": f"{balance:.2f}",
        "estoque": str(p.stock),
        "descricao_produto": (p.description or "Sem descrição")[:300],
        "quantidade_visualizando": "1",
        "nome_usuario": display_name,
        "username": username,
    }

    text, template_kb = await get_rendered_message("product_detail", variables)
    if not text:
        text = (
            f"◈ BELLAMY | PRODUTO\n\n"
            f"▸ Produto: {p.name}\n"
            f"▸ Preço: R$ {price:.2f}\n"
            f"▸ Seu saldo: R$ {balance:.2f}\n"
            f"▸ Disponibilidade: {p.stock}\n\n"
            f"⌁ {p.description or 'Sem descrição'}"
        )

    if template_kb:
        kb = _personalize_detail_kb(template_kb, product_id)
    else:
        buttons = []
        buttons.append([InlineKeyboardButton(
            text="🛒 Comprar agora",
            callback_data=f"pd_buy_now_{product_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text="🔢 Comprar quantidade",
            callback_data=f"pd_buy_qty_{product_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text="🧺 Adicionar ao carrinho",
            callback_data=f"pd_add_cart_{product_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text="💎 Adicionar saldo", callback_data="show_recharge",
        )])
        buttons.append([InlineKeyboardButton(
            text="↩️ Voltar", callback_data="show_catalog",
        )])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    if p.stock <= 0 and not allow_oos:
        new_rows = []
        for row in kb.inline_keyboard:
            new_row = [b for b in row if not (b.callback_data or "").startswith("pd_buy_")]
            if new_row:
                new_rows.append(new_row)
        kb = InlineKeyboardMarkup(inline_keyboard=new_rows) if new_rows else None

    image_url = await get_rendered_image("product_detail") or p.image_url
    return text, kb, image_url


@router.callback_query(F.data.startswith("show_product_"))
async def cb_show_product(cb: CallbackQuery, state: FSMContext):
    try:
        pid = int(cb.data.replace("show_product_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return

    await state.clear()
    result = await _build_detail(pid, cb.from_user.id)
    if not result:
        await cb.answer("Produto indisponível.", show_alert=True)
        return

    text, kb, image_url = result

    try:
        await cb.message.delete()
    except Exception:
        pass

    if image_url:
        try:
            await cb.message.answer_photo(photo=image_url, caption=text, reply_markup=kb)
            await cb.answer()
            return
        except Exception:
            pass

    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# VALIDAÇÃO DE COMPRA
# ============================================================
async def _check_buy(product_id: int, user_id: int, quantity: int):
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, product_id)
        w = await s.get(Wallet, user_id)

    if not p or not p.is_active:
        return False, "Produto indisponível.", {}

    settings = await get_settings()
    allow_oos = getattr(settings, "allow_out_of_stock_sales", False)

    if not allow_oos and p.stock < quantity:
        return False, f"Estoque insuficiente (disponível: {p.stock}).", {}

    if quantity < (p.min_per_purchase or 1):
        return False, f"Quantidade mínima: {p.min_per_purchase}.", {}

    if p.max_per_purchase and p.max_per_purchase > 0 and quantity > p.max_per_purchase:
        return False, f"Quantidade máxima: {p.max_per_purchase}.", {}

    price = _effective_price(p)
    total = round(price * quantity, 2)
    balance = w.balance if w else 0.0

    if balance < total:
        return False, "insufficient_balance", {
            "product": p, "balance": balance, "total": total,
            "missing": round(total - balance, 2),
            "quantity": quantity, "price": price,
        }

    return True, "", {
        "product": p, "balance": balance, "total": total,
        "quantity": quantity, "price": price,
    }


async def _show_insufficient_balance(target, missing: float, product_id: int):
    text = (
        f"⚠️ SALDO INSUFICIENTE\n\n"
        f"Não foi possível continuar com sua compra.\n\n"
        f"💰 Valor que falta: R$ {missing:.2f}\n\n"
        f"Adicione saldo à sua carteira e tente novamente."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Adicionar saldo", callback_data="show_recharge")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"show_product_{product_id}")],
    ])
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


async def _show_confirmation(target, product_id: int, quantity: int, data: dict):
    p = data["product"]
    balance = data["balance"]
    total = data["total"]
    price = data["price"]
    after = round(balance - total, 2)

    text = (
        f"🛍️ CONFIRMAÇÃO DA COMPRA\n\n"
        f"Seu pedido está pronto para confirmação!\n\n"
        f"◈ Produto: {p.name}\n"
        f"◈ Quantidade: {quantity}\n"
        f"◈ Valor unitário: R$ {price:.2f}\n"
        f"◈ Total: R$ {total:.2f}\n\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"💎 Saldo atual: R$ {balance:.2f}\n"
        f"💰 Saldo após a compra: R$ {after:.2f}\n\n"
        f"Deseja confirmar seu pedido?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Confirmar compra",
            callback_data=f"pd_confirm_{product_id}_{quantity}",
        )],
        [InlineKeyboardButton(
            text="↩️ Cancelar",
            callback_data=f"pd_cancel_{product_id}",
        )],
    ])

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


# ============================================================
# BUY NOW
# ============================================================
@router.callback_query(F.data.startswith("pd_buy_now_"))
async def cb_buy_now(cb: CallbackQuery):
    try:
        pid = int(cb.data.replace("pd_buy_now_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return

    ok, err, data = await _check_buy(pid, cb.from_user.id, 1)

    if not ok:
        if err == "insufficient_balance":
            await _show_insufficient_balance(cb, data["missing"], pid)
            await cb.answer()
        else:
            await cb.answer(err, show_alert=True)
        return

    await _show_confirmation(cb, pid, 1, data)
    await cb.answer()


# ============================================================
# BUY QUANTITY
# ============================================================
@router.callback_query(F.data.startswith("pd_buy_qty_"))
async def cb_buy_qty(cb: CallbackQuery):
    try:
        pid = int(cb.data.replace("pd_buy_qty_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)

    if not p:
        await cb.answer("Produto não encontrado.", show_alert=True)
        return

    settings = await get_settings()
    allow_oos = getattr(settings, "allow_out_of_stock_sales", False)
    price = _effective_price(p)

    text = (
        f"🔢 COMPRA POR QUANTIDADE\n\n"
        f"📦 Produto: {p.name}\n"
        f"💰 Valor unitário: R$ {price:.2f}\n"
        f"💎 Seu saldo: R$ {(await _get_balance(cb.from_user.id)):.2f}\n"
        f"📦 Disponível: {p.stock}\n\n"
        f"Quantas unidades deseja comprar?"
    )

    buttons = []
    row = []
    for q in (1, 2, 3, 5, 10):
        if q <= p.stock or allow_oos:
            row.append(InlineKeyboardButton(
                text=str(q),
                callback_data=f"pd_qty_pick_{pid}_{q}",
            ))
            if len(row) == 3:
                buttons.append(row)
                row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(
        text="✏️ Digitar quantidade",
        callback_data=f"pd_qty_custom_{pid}",
    )])
    buttons.append([InlineKeyboardButton(
        text="↩️ Voltar", callback_data=f"show_product_{pid}",
    )])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


async def _get_balance(user_id: int) -> float:
    async with AsyncSessionLocal() as s:
        w = await s.get(Wallet, user_id)
        return w.balance if w else 0.0


@router.callback_query(F.data.startswith("pd_qty_pick_"))
async def cb_qty_pick(cb: CallbackQuery):
    rest = cb.data.replace("pd_qty_pick_", "")
    try:
        pid_s, qty_s = rest.rsplit("_", 1)
        pid = int(pid_s)
        qty = int(qty_s)
    except (ValueError, IndexError):
        await cb.answer("Parâmetros inválidos.", show_alert=True)
        return

    ok, err, data = await _check_buy(pid, cb.from_user.id, qty)

    if not ok:
        if err == "insufficient_balance":
            await _show_insufficient_balance(cb, data["missing"], pid)
            await cb.answer()
        else:
            await cb.answer(err, show_alert=True)
        return

    await _show_confirmation(cb, pid, qty, data)
    await cb.answer()


@router.callback_query(F.data.startswith("pd_qty_custom_"))
async def cb_qty_custom(cb: CallbackQuery, state: FSMContext):
    try:
        pid = int(cb.data.replace("pd_qty_custom_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)

    if not p:
        await cb.answer("Produto não encontrado.", show_alert=True)
        return

    await state.update_data(qty_product_id=pid)
    await state.set_state(BuyStates.waiting_quantity)

    await cb.message.answer(
        f"✏️ INFORME A QUANTIDADE\n\n"
        f"📦 Produto: {p.name}\n"
        f"📦 Disponível: {p.stock}\n"
        f"💰 Valor unitário: R$ {_effective_price(p):.2f}\n\n"
        f"Envie apenas o número (ou /cancelar para sair):",
        reply_markup=ForceReply(input_field_placeholder="Quantidade"),
    )
    await cb.answer()


@router.message(BuyStates.waiting_quantity)
async def msg_quantity_input(m: Message, state: FSMContext):
    text = (m.text or "").strip()
    if text.lower() in ("/cancelar", "cancelar"):
        await state.clear()
        await m.answer("Operação cancelada.")
        return

    data = await state.get_data()
    pid = data.get("qty_product_id")
    await state.clear()

    if not pid:
        await m.answer("❌ Sessão expirada. Abra o produto novamente.")
        return

    try:
        qty = int(text)
        if qty <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await m.answer("❌ Envie apenas um número inteiro positivo.")
        return

    ok, err, result = await _check_buy(pid, m.from_user.id, qty)

    if not ok:
        if err == "insufficient_balance":
            await _show_insufficient_balance(m, result["missing"], pid)
        else:
            await m.answer(f"❌ {err}")
        return

    await _show_confirmation(m, pid, qty, result)


# ============================================================
# ADICIONAR AO CARRINHO
# ============================================================
@router.callback_query(F.data.startswith("pd_add_cart_"))
async def cb_add_cart(cb: CallbackQuery):
    try:
        pid = int(cb.data.replace("pd_add_cart_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return

    settings = await get_settings()
    if not getattr(settings, "cart_enabled", True):
        await cb.answer("🛒 O carrinho está desativado no momento.", show_alert=True)
        return

    max_items = getattr(settings, "cart_max_items_per_user", 20)

    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
        if not p or not p.is_active:
            await cb.answer("Produto indisponível.", show_alert=True)
            return

        existing = (await s.execute(
            select(CartItem)
            .where(CartItem.user_id == cb.from_user.id)
            .where(CartItem.product_id == pid)
        )).scalar_one_or_none()

        cart_count = (await s.execute(
            select(func.count(CartItem.id))
            .where(CartItem.user_id == cb.from_user.id)
        )).scalar() or 0

        if cart_count >= max_items and not existing:
            await cb.answer(f"❌ Limite de {max_items} itens no carrinho.", show_alert=True)
            return

        if existing:
            existing.quantity += 1
            existing.updated_at = datetime.utcnow()
        else:
            s.add(CartItem(user_id=cb.from_user.id, product_id=pid, quantity=1))

        await s.commit()

        total_in_cart = (await s.execute(
            select(func.coalesce(func.sum(CartItem.quantity), 0))
            .where(CartItem.user_id == cb.from_user.id)
        )).scalar() or 0

    await cb.answer(f"✅ Adicionado! ({total_in_cart} no carrinho)", show_alert=False)


# ============================================================
# CANCELAR COMPRA
# ============================================================
@router.callback_query(F.data.startswith("pd_cancel_"))
async def cb_cancel_purchase(cb: CallbackQuery):
    try:
        pid = int(cb.data.replace("pd_cancel_", ""))
    except (ValueError, TypeError):
        pid = 0

    text = (
        f"❌ COMPRA CANCELADA\n\n"
        f"Seu pedido foi cancelado e nenhuma cobrança foi realizada."
    )
    buttons = []
    if pid:
        buttons.append([InlineKeyboardButton(
            text="↩️ Voltar ao produto", callback_data=f"show_product_{pid}",
        )])
    buttons.append([InlineKeyboardButton(
        text="🏠 Menu principal", callback_data="main_menu",
    )])
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# EXECUTAR COMPRA (CONFIRMADO)
# ============================================================
@router.callback_query(F.data.startswith("pd_confirm_"))
async def cb_confirm_purchase(cb: CallbackQuery):
    rest = cb.data.replace("pd_confirm_", "")
    try:
        pid_s, qty_s = rest.rsplit("_", 1)
        pid = int(pid_s)
        qty = int(qty_s)
    except (ValueError, IndexError):
        await cb.answer("Parâmetros inválidos.", show_alert=True)
        return

    await cb.message.edit_text(
        "⏳ <b>PROCESSANDO SEU PEDIDO</b>\n\n"
        "Estamos preparando seus produtos.\n"
        "Aguarde enquanto o sistema confirma a operação."
    )
    await cb.answer()

    try:
        result = await _execute_purchase(cb.from_user.id, pid, qty)
    except Exception as e:
        await cb.message.edit_text(
            f"❌ ERRO AO PROCESSAR\n\n<code>{str(e)[:200]}</code>\n\n"
            f"Entre em contato com o suporte informando o ocorrido.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
            ]),
        )
        return

    if not result["ok"]:
        await cb.message.edit_text(
            f"❌ {result['error']}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Voltar ao produto", callback_data=f"show_product_{pid}")],
                [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
            ]),
        )
        return

    await _show_delivery(cb, result)


async def _execute_purchase(user_id: int, product_id: int, quantity: int) -> dict:
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, product_id)
        w = await s.get(Wallet, user_id)

        if not p or not p.is_active:
            return {"ok": False, "error": "Produto indisponível."}

        settings = await get_settings()
        allow_oos = getattr(settings, "allow_out_of_stock_sales", False)

        if not allow_oos and p.stock < quantity:
            return {"ok": False, "error": f"Estoque insuficiente (disponível: {p.stock})."}

        price = _effective_price(p)
        total = round(price * quantity, 2)

        if not w:
            w = Wallet(user_id=user_id, balance=0.0)
            s.add(w)

        if w.balance < total:
            return {"ok": False, "error": "Saldo insuficiente."}

        before = w.balance
        w.balance = round(w.balance - total, 2)
        after = w.balance

        stock_before = p.stock
        p.stock -= quantity
        p.sold_count += quantity

        order = Order(
            user_id=user_id,
            product_id=product_id,
            quantity=quantity,
            total=total,
            status="completed",
            payment_method="wallet",
            delivery_data={
                "info": p.delivery_info or "",
                "activation_link": p.activation_link or "",
            },
        )
        s.add(order)
        await s.flush()
        order_id = order.id

        s.add(WalletTransaction(
            user_id=user_id,
            amount=-total,
            type="purchase",
            description=f"Compra #{order_id} - {p.name} x{quantity}",
            balance_before=before,
            balance_after=after,
        ))

        s.add(StockMovement(
            product_id=product_id,
            admin_id=0,
            movement_type="sale",
            quantity=quantity,
            stock_before=stock_before,
            stock_after=p.stock,
            reason=f"Venda pelo bot - pedido #{order_id}",
        ))

        await s.commit()

        result = {
            "ok": True,
            "order_id": order_id,
            "product_name": p.name,
            "product_id": product_id,
            "quantity": quantity,
            "total": total,
            "new_balance": after,
            "created_at": order.created_at,
            "delivery": {
                "info": p.delivery_info or "",
                "activation_link": p.activation_link or "",
            },
        }

    # Efeitos colaterais (não bloqueiam a compra se falharem)
    try:
        from admin.channels import publish_purchase_announcement
        await publish_purchase_announcement(order_id)
    except Exception as e:
        print(f"[purchase] canal erro: {e}")

    try:
        from admin.affiliates import process_purchase_points
        await process_purchase_points(user_id, total)
    except Exception as e:
        print(f"[purchase] afiliados erro: {e}")

    try:
        from admin.deeplinks import register_deeplink_conversion
        cutoff = datetime.utcnow() - timedelta(days=30)
        async with AsyncSessionLocal() as sess2:
            visit = (await sess2.execute(
                select(DeepLinkVisit)
                .where(DeepLinkVisit.user_id == user_id)
                .where(DeepLinkVisit.visited_at >= cutoff)
                .order_by(DeepLinkVisit.visited_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if visit:
                await register_deeplink_conversion(visit.deep_link_id, user_id, total)
    except Exception as e:
        print(f"[purchase] deeplink erro: {e}")

    try:
        from admin.notifications import dispatch_notification
        async with AsyncSessionLocal() as sess3:
            user = await sess3.get(User, user_id)
        await dispatch_notification("new_purchase", {
            "nome": (user.full_name if user else "Cliente"),
            "user_id": str(user_id),
            "produto": result["product_name"],
            "quantidade": str(quantity),
            "valor": f"{total:.2f}",
            "pedido_id": str(order_id),
            "data": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        })
    except Exception as e:
        print(f"[purchase] notificação erro: {e}")

    return result


# ============================================================
# TELA DE ENTREGA
# ============================================================
async def _show_delivery(target, result: dict):
    info = result["delivery"].get("info", "") or "Consulte o suporte para receber seus dados."
    activation = result["delivery"].get("activation_link", "") or ""

    text = (
        f"✅ <b>COMPRA CONCLUÍDA!</b>\n\n"
        f"Seu pedido foi confirmado e seus dados de acesso estão disponíveis.\n\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"📦 Produto: {result['product_name']}\n"
        f"🆔 Pedido: #{result['order_id']}\n"
        f"📅 Data: {result['created_at'].strftime('%d/%m/%Y %H:%M')}\n\n"
        f"🔐 <b>DADOS DE ACESSO</b>\n\n"
        f"{_mask(info)}\n\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"💡 Seus dados estão protegidos. Use os botões abaixo para visualizar."
    )

    buttons = [
        [InlineKeyboardButton(
            text="🔓 Ativar visualização",
            callback_data=f"pd_show_creds_{result['order_id']}",
        )],
    ]
    if activation:
        buttons.append([InlineKeyboardButton(text="🌐 Ativar diretamente", url=activation)])
    buttons.append([InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")])

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


# ============================================================
# TOGGLE DE CREDENCIAIS
# ============================================================
@router.callback_query(F.data.startswith("pd_show_creds_"))
async def cb_toggle_show_creds(cb: CallbackQuery):
    try:
        oid = int(cb.data.replace("pd_show_creds_", ""))
    except (ValueError, TypeError):
        await cb.answer("Pedido inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        order = await s.get(Order, oid)
        if not order or order.user_id != cb.from_user.id:
            await cb.answer("Pedido não encontrado.", show_alert=True)
            return
        p = await s.get(Product, order.product_id)

    delivery = order.delivery_data or {}
    info = delivery.get("info", "") or "Consulte o suporte."
    activation = delivery.get("activation_link", "") or ""

    text = (
        f"✅ <b>COMPRA CONCLUÍDA!</b>\n\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"📦 Produto: {p.name if p else '—'}\n"
        f"🆔 Pedido: #{oid}\n"
        f"📅 Data: {order.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"🔐 <b>DADOS DE ACESSO</b>\n\n"
        f"<code>{info}</code>\n\n"
        f"━━━━━━━━━━━━━━"
    )

    buttons = [
        [InlineKeyboardButton(
            text="🔒 Ocultar dados",
            callback_data=f"pd_hide_creds_{oid}",
        )],
    ]
    if activation:
        buttons.append([InlineKeyboardButton(text="🌐 Ativar diretamente", url=activation)])
    buttons.append([InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer("Dados revelados. Não compartilhe.")


@router.callback_query(F.data.startswith("pd_hide_creds_"))
async def cb_toggle_hide_creds(cb: CallbackQuery):
    try:
        oid = int(cb.data.replace("pd_hide_creds_", ""))
    except (ValueError, TypeError):
        await cb.answer("Pedido inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        order = await s.get(Order, oid)
        if not order or order.user_id != cb.from_user.id:
            await cb.answer("Pedido não encontrado.", show_alert=True)
            return
        p = await s.get(Product, order.product_id)

    delivery = order.delivery_data or {}
    info = delivery.get("info", "") or ""
    activation = delivery.get("activation_link", "") or ""

    text = (
        f"✅ <b>COMPRA CONCLUÍDA!</b>\n\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"📦 Produto: {p.name if p else '—'}\n"
        f"🆔 Pedido: #{oid}\n"
        f"📅 Data: {order.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"🔐 <b>DADOS DE ACESSO</b>\n\n"
        f"{_mask(info)}\n\n"
        f"━━━━━━━━━━━━━━"
    )

    buttons = [
        [InlineKeyboardButton(
            text="🔓 Ativar visualização",
            callback_data=f"pd_show_creds_{oid}",
        )],
    ]
    if activation:
        buttons.append([InlineKeyboardButton(text="🌐 Ativar diretamente", url=activation)])
    buttons.append([InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()
