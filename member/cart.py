# Adicione no topo do arquivo (junto dos imports):
import asyncio
from models import OrderDelivery, ProductCredential  # adicionar aos imports existentes

_user_locks: dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


@router.callback_query(F.data == "cart_confirm")
async def cb_cart_confirm(cb: CallbackQuery, state: FSMContext):
    await state.clear()

    user_id = cb.from_user.id
    lock = _get_user_lock(user_id)

    if lock.locked():
        await cb.answer(
            "⏳ Seu pedido anterior ainda está sendo processado. Aguarde.",
            show_alert=True,
        )
        return

    async with lock:
        await cb.message.edit_text(
            "⏳ <b>PROCESSANDO SEU PEDIDO</b>\n\n"
            "Estamos preparando seus produtos.\n"
            "Aguarde..."
        )
        await cb.answer()

        try:
            result = await _execute_cart_purchase(user_id)
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

        pids = list({i.product_id for i in items})
        prods_rows = (await s.execute(
            select(Product).where(Product.id.in_(pids)).with_for_update()
        )).scalars().all()
        prod_map = {p.id: p for p in prods_rows}

        wallet_row = (await s.execute(
            select(Wallet).where(Wallet.user_id == user_id).with_for_update()
        )).scalar_one_or_none()

        settings = await get_settings()
        allow_oos = getattr(settings, "allow_out_of_stock_sales", False)

        # Validar estoque + credenciais
        total = 0.0
        valid = []
        creds_map: dict[int, list] = {}

        for item in items:
            p = prod_map.get(item.product_id)
            if not p or not p.is_active:
                return {"ok": False, "error": "Produto indisponível no carrinho."}

            if not allow_oos and p.stock < item.quantity:
                return {"ok": False, "error": f"Estoque insuficiente: {p.name}"}

            has_pool = (await s.execute(
                select(func.count(ProductCredential.id))
                .where(ProductCredential.product_id == p.id)
            )).scalar() or 0 > 0

            if has_pool:
                available = (await s.execute(
                    select(ProductCredential)
                    .where(ProductCredential.product_id == p.id)
                    .where(ProductCredential.is_sold == False)
                    .order_by(ProductCredential.id)
                    .limit(item.quantity)
                    .with_for_update()
                )).scalars().all()
                if len(available) < item.quantity:
                    return {"ok": False, "error": f"Sem unidades suficientes para {p.name}."}
                creds_map[p.id] = available

            price = _effective_price(p)
            subtotal = round(price * item.quantity, 2)
            total += subtotal
            valid.append((item, p, price, subtotal))

        total = round(total, 2)

        if not wallet_row:
            wallet_row = Wallet(user_id=user_id, balance=0.0)
            s.add(wallet_row)
            await s.flush()

        if wallet_row.balance < total:
            return {"ok": False, "error": "Saldo insuficiente."}

        before = wallet_row.balance
        wallet_row.balance = round(wallet_row.balance - total, 2)
        after = wallet_row.balance

        order_ids = []
        items_result = []

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
                processed_at=datetime.utcnow(),
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

            # OrderDelivery por unidade
            creds = creds_map.get(p.id, [])
            has_pool = len(creds) > 0
            for i in range(item.quantity):
                cred = creds[i] if i < len(creds) else None
                if cred:
                    cred.is_sold = True
                    cred.sold_at = datetime.utcnow()
                    cred.order_id = order.id
                    s.add(OrderDelivery(
                        order_id=order.id,
                        product_id=p.id,
                        credential_id=cred.id,
                        email=cred.email,
                        password=cred.password,
                        extra_data=cred.extra_data,
                    ))
                else:
                    s.add(OrderDelivery(
                        order_id=order.id,
                        product_id=p.id,
                        email=None,
                        password=None,
                        extra_data={"info": p.delivery_info or ""},
                    ))

            items_result.append({
                "name": p.name,
                "quantity": item.quantity,
                "subtotal": subtotal,
                "order_id": order.id,
                "delivery": {
                    "info": p.delivery_info or "",
                    "activation_link": p.activation_link or "",
                },
                "has_individual_credentials": has_pool,
            })

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
            "items": items_result,
        }

    # Efeitos colaterais
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
            from models import User as U
            u = await sess2.get(U, user_id)
        await dispatch_notification("new_purchase", {
            "nome": (u.full_name if u else "Cliente"),
            "user_id": str(user_id),
            "produto": ", ".join(i["name"] for i in items_result)[:100],
            "quantidade": str(sum(i["quantity"] for i in items_result)),
            "valor": f"{total:.2f}",
            "pedido_id": ",".join(str(x) for x in order_ids),
            "data": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        })
    except Exception as e:
        print(f"[cart] notificação: {e}")

    return result
