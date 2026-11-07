from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from datetime import datetime

from bot import dp
from database import AsyncSessionLocal
from models import (
    Product, CartItem, Wallet, WalletTransaction, Order, StockMovement,
)
from settings_cache import get_settings

router = Router()
dp.include_router(router)


def _effective_price(p: Product) -> float:
    return p.promo_price if p.promo_price and p.promo_price > 0 else p.price


async def _fetch_cart(user_id: int):
    async with AsyncSessionLocal() as s:
        items = (await s.execute(
            select(CartItem)
            .where(CartItem.user_id == user_id)
            .order_by(CartItem.added_at)
        )).scalars().all()

        prods = {}
        if items:
            pids = list({i.product_id for i in items})
            prods = {
                p.id: p for p in (await s.execute(
                    select(Product).where(Product.id.in_(pids))
                )).scalars().all()
            }

        w = await s.get(Wallet, user_id)

    return items, prods, (w.balance if w else 0.0)


async def _render_cart(target, user_id: int):
    settings = await get_settings()
    if not getattr(settings, "cart_enabled", True):
        text = "🛒 O carrinho está desativado no momento."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
        ])
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=kb)
        else:
            await target.answer(text, reply_markup=kb)
        return

    items, prods, balance = await _fetch_cart(user_id)

    if not items:
        text = (
            "◈ BELLAMY | MEU CARRINHO\n\n"
            "Seu carrinho está vazio.\n\n"
            "Explore o catálogo e adicione produtos."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛍️ Ver produtos", callback_data="show_catalog")],
            [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
        ])
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=kb)
        else:
            await target.answer(text, reply_markup=kb)
        return

    total = 0.0
    total_qty = 0
    lines = []
    buttons = []

    for idx, item in enumerate(items, 1):
        p = prods.get(item.product_id)
        if not p:
            continue
        price = _effective_price(p)
        subtotal = price * item.quantity
        total += subtotal
        total_qty += item.quantity
        lines.append(
            f"{idx}. <b>{p.name}</b>\n"
            f"   {item.quantity} × R$ {price:.2f} = R$ {subtotal:.2f}"
        )
        buttons.append([InlineKeyboardButton(
            text=f"📦 {idx}. {p.name[:28]} ({item.quantity}x)",
            callback_data=f"cart_item_{item.id}",
        )])

    text = (
        "◈ BELLAMY | MEU CARRINHO\n\n"
        + "\n\n".join(lines)
        + "\n\n━━━━━━━━━━━━━━\n"
        f"📦 Itens: {len(items)} ({total_qty} un)\n"
        f"💰 Total: R$ {total:.2f}\n"
        f"💎 Saldo: R$ {balance:.2f}"
    )

    buttons.append([InlineKeyboardButton(text="🛒 Comprar", callback_data="cart_buy")])
    buttons.append([InlineKeyboardButton(text="🗑️ Limpar carrinho", callback_data="cart_clear")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="main_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


# ============================================================
# ENTRADA
# ============================================================
@router.callback_query(F.data == "show_cart")
async def cb_show_cart(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_cart(cb, cb.from_user.id)
    await cb.answer()


# ============================================================
# DETALHE DO ITEM
# ============================================================
async def _render_cart_item(cb: CallbackQuery, item_id: int):
    async with AsyncSessionLocal() as s:
        item = await s.get(CartItem, item_id)
        if not item or item.user_id != cb.from_user.id:
            await _render_cart(cb, cb.from_user.id)
            return
        p = await s.get(Product, item.product_id)

    if not p:
        await _render_cart(cb, cb.from_user.id)
        return

    price = _effective_price(p)
    subtotal = price * item.quantity

    text = (
        f"📦 ITEM DO CARRINHO\n\n"
        f"Produto: <b>{p.name}</b>\n"
        f"Quantidade: {item.quantity}\n"
        f"Preço unitário: R$ {price:.2f}\n"
        f"Subtotal: R$ {subtotal:.2f}\n"
        f"📦 Estoque disponível: {p.stock}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Aumentar", callback_data=f"cart_inc_{item_id}"),
         InlineKeyboardButton(text="➖ Diminuir", callback_data=f"cart_dec_{item_id}")],
        [InlineKeyboardButton(text="🗑️ Remover do carrinho", callback_data=f"cart_remove_{item_id}")],
        [InlineKeyboardButton(text="↩️ Voltar ao carrinho", callback_data="show_cart")],
    ])
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data.startswith("cart_item_"))
async def cb_cart_item(cb: CallbackQuery):
    try:
        item_id = int(cb.data.replace("cart_item_", ""))
    except (ValueError, TypeError):
        await cb.answer("Item inválido.", show_alert=True)
        return
    await _render_cart_item(cb, item_id)
    await cb.answer()


# ============================================================
# AUMENTAR / DIMINUIR / REMOVER
# ============================================================
@router.callback_query(F.data.startswith("cart_inc_"))
async def cb_cart_inc(cb: CallbackQuery):
    try:
        item_id = int(cb.data.replace("cart_inc_", ""))
    except (ValueError, TypeError):
        await cb.answer("Item inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        item = await s.get(CartItem, item_id)
        if not item or item.user_id != cb.from_user.id:
            await cb.answer("Item não encontrado.", show_alert=True)
            return
        p = await s.get(Product, item.product_id)

        settings = await get_settings()
        allow_oos = getattr(settings, "allow_out_of_stock_sales", False)

        if p and not allow_oos and item.quantity >= p.stock:
            await cb.answer("❌ Estoque máximo atingido.", show_alert=True)
            return

        item.quantity += 1
        item.updated_at = datetime.utcnow()
        await s.commit()

    await cb.answer("✅ Quantidade aumentada.")
    await _render_cart_item(cb, item_id)


@router.callback_query(F.data.startswith("cart_dec_"))
async def cb_cart_dec(cb: CallbackQuery):
    try:
        item_id = int(cb.data.replace("cart_dec_", ""))
    except (ValueError, TypeError):
        await cb.answer("Item inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        item = await s.get(CartItem, item_id)
        if not item or item.user_id != cb.from_user.id:
            await cb.answer("Item não encontrado.", show_alert=True)
            return

        if item.quantity <= 1:
            await s.delete(item)
            await s.commit()
            await cb.answer("Item removido.")
            await _render_cart(cb, cb.from_user.id)
            return

        item.quantity -= 1
        item.updated_at = datetime.utcnow()
        await s.commit()

    await cb.answer("✅ Quantidade diminuída.")
    await _render_cart_item(cb, item_id)


@router.callback_query(F.data.startswith("cart_remove_"))
async def cb_cart_remove(cb: CallbackQuery):
    try:
        item_id = int(cb.data.replace("cart_remove_", ""))
    except (ValueError, TypeError):
        await cb.answer("Item inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        item = await s.get(CartItem, item_id)
        if not item or item.user_id != cb.from_user.id:
            await cb.answer("Item não encontrado.", show_alert=True)
            return
        await s.delete(item)
        await s.commit()

    await cb.answer("✅ Item removido.")
    await _render_cart(cb, cb.from_user.id)


# ============================================================
# LIMPAR CARRINHO
# ============================================================
@router.callback_query(F.data == "cart_clear")
async def cb_cart_clear(cb: CallbackQuery):
    async with AsyncSessionLocal() as s:
        items = (await s.execute(
            select(CartItem).where(CartItem.user_id == cb.from_user.id)
        )).scalars().all()
        for x in items:
            await s.delete(x)
        await s.commit()

    await cb.answer("✅ Carrinho limpo.")
    await _render_cart(cb, cb.from_user.id)


# ============================================================
# COMPRAR (verifica saldo)
# ============================================================
@router.callback_query(F.data == "cart_buy")
async def cb_cart_buy(cb: CallbackQuery, state: FSMContext):
    items, prods, balance = await _fetch_cart(cb.from_user.id)

    if not items:
        await cb.answer("Carrinho vazio.", show_alert=True)
        return

    total = 0.0
    lines = []
    for item in items:
        p = prods.get(item.product_id)
        if not p:
            continue
        price = _effective_price(p)
        subtotal = price * item.quantity
        total += subtotal
        lines.append(f"• {p.name} × {item.quantity} — R$ {subtotal:.2f}")

    total = round(total, 2)

    if balance < total:
        missing = round(total - balance, 2)
        await state.update_data(return_ctx="cart")
        await cb.message.edit_text(
            "⚠️ SALDO INSUFICIENTE\n\n"
            "Não foi possível continuar com sua compra.\n\n"
            f"💰 Valor que falta: R$ {missing:.2f}\n\n"
            "Adicione saldo à sua carteira e tente novamente.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Adicionar saldo", callback_data="show_recharge")],
                [InlineKeyboardButton(text="↩️ Voltar ao carrinho", callback_data="show_cart")],
            ]),
        )
        await cb.answer()
        return

    after = round(balance - total, 2)

    text = (
        "🛍️ CONFIRMAÇÃO DO CARRINHO\n\n"
        "Confira seu pedido:\n\n"
        + "\n".join(lines)
        + "\n\n━━━━━━━━━━━━━━\n\n"
        f"💰 Valor total: R$ {total:.2f}\n"
        f"💎 Saldo atual: R$ {balance:.2f}\n"
        f"💳 Saldo após compra: R$ {after:.2f}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirmar compra", callback_data="cart_confirm")],
        [InlineKeyboardButton(text="↩️ Cancelar", callback_data="show_cart")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# CONFIRMAR (executa)
# ============================================================
@router.callback_query(F.data == "cart_confirm")
async def cb_cart_confirm(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text(
        "⏳ <b>PROCESSANDO SEU PEDIDO</b>\n\n"
        "Estamos preparando seus produtos.\n"
        "Aguarde..."
    )
    await cb.answer()

    try:
        result = await _execute_cart_purchase(cb.from_user.id)
    except Exception as e:
        await cb.message.edit_text(
            f"❌ ERRO AO PROCESSAR\n\n<code>{str(e)[:200]}</code>\n\n"
            "Entre em contato com o suporte.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
            ]),
        )
        return

    if not result["ok"]:
        await cb.message.edit_text(
            f"❌ {result['error']}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Ver carrinho", callback_data="show_cart")],
            ]),
        )
        return

    await _show_cart_delivery(cb, result)


async def _execute_cart_purchase(user_id: int) -> dict:
    async with AsyncSessionLocal() as s:
        items = (await s.execute(
            select(CartItem).where(CartItem.user_id == user_id)
        )).scalars().all()

        if not items:
            return {"ok": False, "error": "Carrinho vazio."}

        pids = [i.product_id for i in items]
        prods = (await s.execute(
            select(Product).where(Product.id.in_(pids))
        )).scalars().all()
        prod_map = {p.id: p for p in prods}

        settings = await get_settings()
        allow_oos = getattr(settings, "allow_out_of_stock_sales", False)

        total = 0.0
        valid = []
        for item in items:
            p = prod_map.get(item.product_id)
            if not p or not p.is_active:
                return {"ok": False, "error": "Produto indisponível no carrinho."}
            if not allow_oos and p.stock < item.quantity:
                return {"ok": False, "error": f"Estoque insuficiente: {p.name}"}

            price = _effective_price(p)
            subtotal = round(price * item.quantity, 2)
            total += subtotal
            valid.append((item, p, price, subtotal))

        total = round(total, 2)

        w = await s.get(Wallet, user_id)
        if not w:
            w = Wallet(user_id=user_id, balance=0.0)
            s.add(w)

        if w.balance < total:
            return {"ok": False, "error": "Saldo insuficiente."}

        before = w.balance
        w.balance = round(w.balance - total, 2)
        after = w.balance

        order_ids = []
        for item, p, price, subtotal in valid:
            order = Order(
                user_id=user_id,
                product_id=p.id,
                quantity=item.quantity,
                total=subtotal,
                status="completed",
                payment_method="wallet",
                delivery_data={
                    "info": p.delivery_info or "",
                    "activation_link": p.activation_link or "",
                },
            )
            s.add(order)
            await s.flush()
            order_ids.append(order.id)

            stock_before = p.stock
            p.stock -= item.quantity
            p.sold_count += item.quantity
            s.add(StockMovement(
                product_id=p.id,
                admin_id=0,
                movement_type="sale",
                quantity=item.quantity,
                stock_before=stock_before,
                stock_after=p.stock,
                reason=f"Venda carrinho - pedido #{order.id}",
            ))

        s.add(WalletTransaction(
            user_id=user_id,
            amount=-total,
            type="purchase",
            description=f"Compra carrinho ({len(order_ids)} itens)",
            balance_before=before,
            balance_after=after,
        ))

        for item in items:
            await s.delete(item)

        await s.commit()

        result = {
            "ok": True,
            "order_ids": order_ids,
            "total": total,
            "new_balance": after,
            "items": [
                {
                    "name": p.name,
                    "quantity": item.quantity,
                    "subtotal": subtotal,
                    "delivery": {
                        "info": p.delivery_info or "",
                        "activation_link": p.activation_link or "",
                    },
                }
                for item, p, price, subtotal in valid
            ],
        }

    for oid in order_ids:
        try:
            from admin.channels import publish_purchase_announcement
            await publish_purchase_announcement(oid)
        except Exception as e:
            print(f"[cart] canal: {e}")

    try:
        from admin.affiliates import process_purchase_points
        await process_purchase_points(user_id, total)
    except Exception as e:
        print(f"[cart] afiliados: {e}")

    try:
        from admin.notifications import dispatch_notification
        async with AsyncSessionLocal() as sess2:
            from models import User
            u = await sess2.get(User, user_id)
        await dispatch_notification("new_purchase", {
            "nome": (u.full_name if u else "Cliente"),
            "user_id": str(user_id),
            "produto": ", ".join(i["name"] for i in result["items"])[:100],
            "quantidade": str(sum(i["quantity"] for i in result["items"])),
            "valor": f"{total:.2f}",
            "pedido_id": ",".join(str(x) for x in order_ids),
            "data": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        })
    except Exception as e:
        print(f"[cart] notificação: {e}")

    return result


async def _show_cart_delivery(target, result: dict):
    lines = []
    for i, item in enumerate(result["items"], 1):
        info = item["delivery"].get("info", "") or "Consulte o suporte."
        lines.append(
            f"<b>#{i} {item['name']}</b> (×{item['quantity']})\n"
            f"<code>{info[:200]}</code>"
        )

    text = (
        "✅ <b>COMPRA CONCLUÍDA!</b>\n\n"
        f"📦 {len(result['items'])} item(ns) adquirido(s)\n"
        f"💰 Total pago: R$ {result['total']:.2f}\n"
        f"💎 Saldo restante: R$ {result['new_balance']:.2f}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        "🔐 <b>SEUS ACESSOS</b>\n\n"
        + "\n\n".join(lines)
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
    ])

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)
