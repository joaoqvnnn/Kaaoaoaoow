from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ForceReply, InputMediaPhoto,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func
from datetime import datetime, timedelta
import asyncio

from bot import dp
from database import AsyncSessionLocal
from models import (
    Product, User, Wallet, WalletTransaction, Order,
    CartItem, StockMovement, ProductCredential, OrderDelivery, DeepLinkVisit,
)
from settings_cache import get_settings

from admin.customization import get_rendered_message, get_rendered_image

router = Router()
dp.include_router(router)


class BuyStates(StatesGroup):
    waiting_quantity = State()


# ============================================================
# LOCKS E CACHE
# ============================================================
_user_locks: dict[int, asyncio.Lock] = {}
_viewers: dict[int, dict[int, datetime]] = {}
_VIEW_TTL = timedelta(minutes=5)


def _get_user_lock(user_id: int) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


def _register_viewer(product_id: int, user_id: int) -> int:
    now = datetime.utcnow()
    bucket = _viewers.setdefault(product_id, {})
    for uid, ts in list(bucket.items()):
        if now - ts > _VIEW_TTL:
            del bucket[uid]
    bucket[user_id] = now
    return len(bucket)


def _effective_price(p: Product) -> float:
    return p.promo_price if p.promo_price and p.promo_price > 0 else p.price


def _mask_field(text: str | None) -> str:
    return "•" * 12 if text else "—"


async def _get_balance(user_id: int) -> float:
    async with AsyncSessionLocal() as s:
        w = await s.get(Wallet, user_id)
        return w.balance if w else 0.0


# ============================================================
# HELPERS DE UPDATE
# ============================================================
async def _update_to_product(
    target: Message | CallbackQuery,
    text: str,
    kb: InlineKeyboardMarkup | None,
    image_url: str | None,
):
    """§1 — edita sempre que possível."""
    is_cb = isinstance(target, CallbackQuery)
    msg = target.message if is_cb else target

    has_photo_now = bool(getattr(msg, "photo", None))
    has_photo_new = bool(image_url)

    if not has_photo_now and not has_photo_new:
        try:
            await msg.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
        await msg.answer(text, reply_markup=kb)
        return

    if has_photo_now and has_photo_new:
        try:
            await msg.edit_media(
                media=InputMediaPhoto(media=image_url, caption=text),
                reply_markup=kb,
            )
            return
        except Exception:
            pass
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer_photo(photo=image_url, caption=text, reply_markup=kb)
        return

    if not has_photo_now and has_photo_new:
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer_photo(photo=image_url, caption=text, reply_markup=kb)
        return

    if has_photo_now and not has_photo_new:
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(text, reply_markup=kb)
        return


async def _safe_edit_or_answer(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None):
    """Edita se for texto; deleta+envia se for foto (evita falha silenciosa)."""
    if cb.message.photo:
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.message.answer(text, reply_markup=kb)
        return
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cb.message.answer(text, reply_markup=kb)


# ============================================================
# DETALHE DO PRODUTO
# ============================================================
def _personalize_detail_kb(kb: InlineKeyboardMarkup, product_id: int) -> InlineKeyboardMarkup:
    cb_map = {
        "buy_now":       f"pd_buy_now_{product_id}",
        "buy_qty":       f"pd_buy_qty_{product_id}",
        "add_cart":      f"pd_add_cart_{product_id}",
        "show_recharge": f"pd_add_balance_{product_id}",  # ← corrigido (#4)
        "show_catalog":  "show_catalog",
    }
    new_rows = []
    for row in kb.inline_keyboard:
        new_row = []
        for b in row:
            new_row.append(InlineKeyboardButton(
                text=b.text,
                callback_data=cb_map.get(b.callback_data, b.callback_data),
                url=b.url,
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
    viewers = _register_viewer(product_id, user_id)

    variables = {
        "nome_produto": p.name,
        "preco": f"{price:.2f}",
        "saldo": f"{balance:.2f}",
        "estoque": str(p.stock),
        "descricao_produto": (p.description or "Sem descrição")[:300],
        "quantidade_visualizando": str(viewers),
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
            f"⌁ {p.description or 'Sem descrição'}\n\n"
            f"◈ {viewers} pessoa(s) visualizando este produto."
        )

    if template_kb:
        kb = _personalize_detail_kb(template_kb, product_id)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Comprar agora", callback_data=f"pd_buy_now_{product_id}")],
            [InlineKeyboardButton(text="🔢 Comprar quantidade", callback_data=f"pd_buy_qty_{product_id}")],
            [InlineKeyboardButton(text="🧺 Adicionar ao carrinho", callback_data=f"pd_add_cart_{product_id}")],
            [InlineKeyboardButton(text="💎 Adicionar saldo", callback_data=f"pd_add_balance_{product_id}")],  # ← corrigido (#4)
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="show_catalog")],
        ])

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
    await _update_to_product(cb, text, kb, image_url)
    await cb.answer()


# ============================================================
# §8.5 — ADICIONAR SALDO DIRETO DO PRODUTO (contexto preservado)
# ============================================================
@router.callback_query(F.data.startswith("pd_add_balance_"))
async def cb_pd_add_balance(cb: CallbackQuery, state: FSMContext):
    try:
        pid = int(cb.data.replace("pd_add_balance_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return

    # Salva o contexto de origem ANTES de entrar no fluxo único de recarga
    await state.update_data(
        return_ctx="product",
        return_product_id=pid,
    )

    settings = await get_settings()
    if not getattr(settings, "recharge_enabled", True):
        await cb.answer("💳 As recargas estão desativadas no momento.", show_alert=True)
        return

    mp_token = getattr(settings, "mp_access_token", None)
    if not mp_token:
        await _safe_edit_or_answer(
            cb,
            "⚠️ O PIX está temporariamente indisponível.\n\nEntre em contato com o suporte.",
            InlineKeyboardMarkup(inline_keyboard=[
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
    await _safe_edit_or_answer(cb, text, kb)
    await cb.answer()


# ============================================================
# VALIDAÇÃO DE COMPRA
# ============================================================
async def _check_buy(product_id: int, user_id: int, quantity: int):
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, product_id)
        w = await s.get(Wallet, user_id)

        cred_count = (await s.execute(
            select(func.count(ProductCredential.id))
            .where(ProductCredential.product_id == product_id)
            .where(ProductCredential.is_sold == False)
        )).scalar() or 0
        has_pool = (await s.execute(
            select(func.count(ProductCredential.id))
            .where(ProductCredential.product_id == product_id)
        )).scalar() or 0 > 0

    if not p or not p.is_active:
        return False, "Produto indisponível.", {}

    settings = await get_settings()
    allow_oos = getattr(settings, "allow_out_of_stock_sales", False)

    if not allow_oos and p.stock < quantity:
        return False, f"Estoque insuficiente (disponível: {p.stock}).", {}

    if has_pool and cred_count < quantity:
        return False, f"Sem unidades disponíveis para entrega imediata (disponível: {cred_count}).", {}

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


async def _show_insufficient_balance(
    target, missing: float, product_id: int, state: FSMContext,
    return_ctx: str = "product", quantity: int = 1,
):
    await state.update_data(
        return_ctx=return_ctx,
        return_product_id=product_id,
        return_quantity=quantity,
    )
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
        await _safe_edit_or_answer(target, text, kb)
    else:
        await target.answer(text, reply_markup=kb)


# ============================================================
# §9.2 — RESUMO DO PEDIDO
# ============================================================
async def show_purchase_summary(target, product_id: int, quantity: int, data: dict):
    p = data["product"]
    balance = data["balance"]
    total = data["total"]
    price = data["price"]

    text = (
        f"◈ RESUMO DO PEDIDO\n\n"
        f"📦 Produto: {p.name}\n"
        f"🔢 Quantidade: {quantity}\n"
        f"💰 Valor unitário: R$ {price:.2f}\n\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"💳 Total: R$ {total:.2f}\n"
        f"💎 Seu saldo: R$ {balance:.2f}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Continuar",
            callback_data=f"pd_continue_{product_id}_{quantity}",
        )],
        [InlineKeyboardButton(
            text="🔢 Alterar quantidade",
            callback_data=f"pd_change_qty_{product_id}",
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
# §9.4 — CONFIRMAR PEDIDO (quantidade > 1)
# ============================================================
async def _render_confirmation_quantity(target, product, quantity: int, price: float, balance: float):
    total = round(price * quantity, 2)
    after = round(balance - total, 2)

    text = (
        f"🛍️ CONFIRMAR PEDIDO\n\n"
        f"📦 Produto: {product.name}\n"
        f"🔢 Quantidade: {quantity}\n"
        f"💰 Total: R$ {total:.2f}\n\n"
        f"💎 Saldo atual: R$ {balance:.2f}\n"
        f"💳 Saldo após compra: R$ {after:.2f}\n\n"
        f"Deseja confirmar?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Confirmar compra",
            callback_data=f"pd_confirm_{product.id}_{quantity}",
        )],
        [InlineKeyboardButton(
            text="✏️ Alterar quantidade",
            callback_data=f"pd_change_qty_{product.id}",
        )],
        [InlineKeyboardButton(
            text="❌ Cancelar pedido",
            callback_data=f"pd_cancel_{product.id}",
        )],
    ])

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


# ============================================================
# §6.1 — CONFIRMAÇÃO DA COMPRA (comprar agora)
# ============================================================
async def _render_confirmation_buy_now(target, product, price: float, balance: float):
    total = round(price, 2)
    after = round(balance - total, 2)

    text = (
        f"🛍️ CONFIRMAÇÃO DA COMPRA\n\n"
        f"Seu pedido está pronto para confirmação!\n\n"
        f"◈ Produto: {product.name}\n"
        f"◈ Quantidade: 1\n"
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
            callback_data=f"pd_confirm_{product.id}_1",
        )],
        [InlineKeyboardButton(
            text="↩️ Cancelar",
            callback_data=f"pd_cancel_{product.id}",
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
async def cb_buy_now(cb: CallbackQuery, state: FSMContext):
    try:
        pid = int(cb.data.replace("pd_buy_now_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return

    ok, err, data = await _check_buy(pid, cb.from_user.id, 1)

    if not ok:
        if err == "insufficient_balance":
            await _show_insufficient_balance(
                cb, data["missing"], pid, state,
                return_ctx="product", quantity=1,
            )
            await cb.answer()
        else:
            await cb.answer(err, show_alert=True)
        return

    await _render_confirmation_buy_now(cb, data["product"], data["price"], data["balance"])
    await cb.answer()


# ============================================================
# MENU DE ESCOLHA DE QUANTIDADE
# ============================================================
async def _show_qty_menu(cb: CallbackQuery, pid: int):
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)

    if not p:
        await cb.answer("Produto não encontrado.", show_alert=True)
        return

    settings = await get_settings()
    allow_oos = getattr(settings, "allow_out_of_stock_sales", False)
    price = _effective_price(p)
    balance = await _get_balance(cb.from_user.id)

    suggestions = getattr(settings, "quantity_suggestions", None) or [1, 2, 3, 5, 10]
    try:
        suggestions = [int(x) for x in suggestions if int(x) > 0]
    except (TypeError, ValueError):
        suggestions = [1, 2, 3, 5, 10]
    if not suggestions:
        suggestions = [1, 2, 3, 5, 10]

    text = (
        f"🔢 COMPRA POR QUANTIDADE\n\n"
        f"📦 Produto: {p.name}\n"
        f"💰 Valor unitário: R$ {price:.2f}\n"
        f"💎 Seu saldo: R$ {balance:.2f}\n"
        f"📦 Disponível: {p.stock}\n\n"
        f"Quantas unidades deseja comprar?"
    )

    buttons = []
    row = []
    for q in suggestions:
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


@router.callback_query(F.data.startswith("pd_buy_qty_"))
async def cb_buy_qty(cb: CallbackQuery):
    try:
        pid = int(cb.data.replace("pd_buy_qty_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return
    await _show_qty_menu(cb, pid)
    await cb.answer()


@router.callback_query(F.data.startswith("pd_change_qty_"))
async def cb_change_qty(cb: CallbackQuery):
    try:
        pid = int(cb.data.replace("pd_change_qty_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return
    await _show_qty_menu(cb, pid)
    await cb.answer()


# ============================================================
# ESCOLHA DE QUANTIDADE → §9.2
# ============================================================
@router.callback_query(F.data.startswith("pd_qty_pick_"))
async def cb_qty_pick(cb: CallbackQuery, state: FSMContext):
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
            await _show_insufficient_balance(
                cb, data["missing"], pid, state,
                return_ctx="purchase_summary", quantity=qty,
            )
            await cb.answer()
        else:
            await cb.answer(err, show_alert=True)
        return

    await show_purchase_summary(cb, pid, qty, data)
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
            # Salva o contexto de novo (state.clear() apagou) — preserva retorno após recarga
            await state.update_data(
                return_ctx="purchase_summary",
                return_product_id=pid,
                return_quantity=qty,
            )
            await _show_insufficient_balance_nostate(m, result["missing"], pid, qty)
        else:
            await m.answer(f"❌ {err}")
        return

    await show_purchase_summary(m, pid, qty, result)


async def _show_insufficient_balance_nostate(
    target, missing: float, product_id: int, quantity: int,
):
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
    await target.answer(text, reply_markup=kb)


# ============================================================
# CONTINUAR (§9.2 → §9.4)
# ============================================================
@router.callback_query(F.data.startswith("pd_continue_"))
async def cb_continue(cb: CallbackQuery, state: FSMContext):
    rest = cb.data.replace("pd_continue_", "")
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
            await _show_insufficient_balance(
                cb, data["missing"], pid, state,
                return_ctx="purchase_summary", quantity=qty,
            )
            await cb.answer()
        else:
            await cb.answer(err, show_alert=True)
        return

    await _render_confirmation_quantity(
        cb, data["product"], qty, data["price"], data["balance"],
    )
    await cb.answer()


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
# CANCELAR
# ============================================================
@router.callback_query(F.data.startswith("pd_cancel_"))
async def cb_cancel_purchase(cb: CallbackQuery, state: FSMContext):
    try:
        pid = int(cb.data.replace("pd_cancel_", ""))
    except (ValueError, TypeError):
        pid = 0

    await state.clear()

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
# CONFIRMAR COMPRA (execução)
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

    user_id = cb.from_user.id
    lock = _get_user_lock(user_id)

    if lock.locked():
        await cb.answer(
            "⏳ Sua compra anterior ainda está sendo processada. Aguarde.",
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
            result = await _execute_purchase(user_id, pid, qty)
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


# ============================================================
# EXECUÇÃO ATÔMICA
# ============================================================
async def _execute_purchase(user_id: int, product_id: int, quantity: int) -> dict:
    async with AsyncSessionLocal() as s:
        product_row = (await s.execute(
            select(Product).where(Product.id == product_id).with_for_update()
        )).scalar_one_or_none()
        wallet_row = (await s.execute(
            select(Wallet).where(Wallet.user_id == user_id).with_for_update()
        )).scalar_one_or_none()

        if not product_row or not product_row.is_active:
            return {"ok": False, "error": "Produto indisponível."}

        settings = await get_settings()
        allow_oos = getattr(settings, "allow_out_of_stock_sales", False)

        if not allow_oos and product_row.stock < quantity:
            return {"ok": False, "error": f"Estoque insuficiente (disponível: {product_row.stock})."}

        has_pool = (await s.execute(
            select(func.count(ProductCredential.id))
            .where(ProductCredential.product_id == product_id)
        )).scalar() or 0 > 0

        credentials = []
        if has_pool:
            creds = (await s.execute(
                select(ProductCredential)
                .where(ProductCredential.product_id == product_id)
                .where(ProductCredential.is_sold == False)
                .order_by(ProductCredential.id)
                .limit(quantity)
                .with_for_update()
            )).scalars().all()
            if len(creds) < quantity:
                return {"ok": False, "error": "Sem unidades disponíveis para entrega."}
            credentials = creds

        price = _effective_price(product_row)
        total = round(price * quantity, 2)

        if not wallet_row:
            wallet_row = Wallet(user_id=user_id, balance=0.0)
            s.add(wallet_row)
            await s.flush()

        if wallet_row.balance < total:
            return {"ok": False, "error": "Saldo insuficiente."}

        before = wallet_row.balance
        wallet_row.balance = round(wallet_row.balance - total, 2)
        after = wallet_row.balance

        stock_before = product_row.stock
        product_row.stock -= quantity
        product_row.sold_count += quantity

        order = Order(
            user_id=user_id,
            product_id=product_id,
            quantity=quantity,
            total=total,
            status="completed",
            payment_method="wallet",
            delivery_data={
                "info": product_row.delivery_info or "",
                "activation_link": product_row.activation_link or "",
            },
            processed_at=datetime.utcnow(),
        )
        s.add(order)
        await s.flush()
        order_id = order.id

        for i in range(quantity):
            cred = credentials[i] if i < len(credentials) else None
            if cred:
                cred.is_sold = True
                cred.sold_at = datetime.utcnow()
                cred.order_id = order_id
                s.add(OrderDelivery(
                    order_id=order_id,
                    product_id=product_id,
                    credential_id=cred.id,
                    email=cred.email,
                    password=cred.password,
                    extra_data=cred.extra_data,
                ))
            else:
                s.add(OrderDelivery(
                    order_id=order_id,
                    product_id=product_id,
                    email=None,
                    password=None,
                    extra_data={"info": product_row.delivery_info or ""},
                ))

        s.add(WalletTransaction(
            user_id=user_id,
            amount=-total,
            type="purchase",
            description=f"Compra #{order_id} - {product_row.name} x{quantity}",
            balance_before=before,
            balance_after=after,
        ))

        s.add(StockMovement(
            product_id=product_id,
            admin_id=0,
            movement_type="sale",
            quantity=quantity,
            stock_before=stock_before,
            stock_after=product_row.stock,
            reason=f"Venda pelo bot - pedido #{order_id}",
        ))

        await s.commit()

        result = {
            "ok": True,
            "order_id": order_id,
            "product_name": product_row.name,
            "product_id": product_id,
            "quantity": quantity,
            "total": total,
            "new_balance": after,
            "created_at": order.created_at,
            "delivery": {
                "info": product_row.delivery_info or "",
                "activation_link": product_row.activation_link or "",
            },
            "has_individual_credentials": has_pool,
        }

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
    order_id = result["order_id"]
    activation = result["delivery"].get("activation_link", "") or ""
    has_individual = result.get("has_individual_credentials", False)

    if has_individual:
        async with AsyncSessionLocal() as s:
            deliveries = (await s.execute(
                select(OrderDelivery)
                .where(OrderDelivery.order_id == order_id)
                .order_by(OrderDelivery.id)
            )).scalars().all()

        lines = []
        for i, d in enumerate(deliveries, 1):
            lines.append(
                f"<b>Acesso #{i}</b>\n"
                f"📧 E-mail: {_mask_field(d.email)}\n"
                f"🔑 Senha: {_mask_field(d.password)}"
            )

        text = (
            f"✅ <b>COMPRA CONCLUÍDA!</b>\n\n"
            f"Seu pedido foi confirmado e seus dados de acesso estão disponíveis.\n\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"📦 Produto: {result['product_name']}\n"
            f"🆔 Pedido: #{order_id}\n"
            f"📅 Data: {result['created_at'].strftime('%d/%m/%Y %H:%M')}\n"
            f"🔢 Quantidade: {result['quantity']}\n"
            f"💰 Total pago: R$ {result['total']:.2f}\n\n"
            f"🔐 <b>DADOS DE ACESSO</b>\n\n"
            + "\n\n".join(lines)
            + f"\n\n━━━━━━━━━━━━━━\n\n"
            f"💡 Seus dados estão protegidos. Use os botões abaixo para visualizar."
        )
    else:
        info = result["delivery"].get("info", "") or "Consulte o suporte para receber seus dados."
        text = (
            f"✅ <b>COMPRA CONCLUÍDA!</b>\n\n"
            f"Seu pedido foi confirmado e seus dados de acesso estão disponíveis.\n\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"📦 Produto: {result['product_name']}\n"
            f"🆔 Pedido: #{order_id}\n"
            f"📅 Data: {result['created_at'].strftime('%d/%m/%Y %H:%M')}\n\n"
            f"🔐 <b>DADOS DE ACESSO</b>\n\n"
            f"📧 E-mail: {_mask_field(info)}\n\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"💡 Seus dados estão protegidos. Use os botões abaixo para visualizar."
        )

    buttons = [
        [InlineKeyboardButton(
            text="🔓 Ativar visualização",
            callback_data=f"pd_show_creds_{order_id}",
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
# TOGGLE CREDENCIAIS
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

        deliveries = (await s.execute(
            select(OrderDelivery).where(OrderDelivery.order_id == oid).order_by(OrderDelivery.id)
        )).scalars().all()

    has_individual = len(deliveries) > 0 and any(d.email or d.password for d in deliveries)

    if has_individual:
        lines = []
        for i, d in enumerate(deliveries, 1):
            if d.is_revoked:
                lines.append(f"<b>Acesso #{i}</b> — ❌ revogado")
                continue
            email = d.email or "—"
            pwd = d.password or "—"
            extra = ""
            if d.extra_data and isinstance(d.extra_data, dict):
                for k, v in d.extra_data.items():
                    if k != "info":
                        extra += f"\n   {k}: <code>{v}</code>"
            lines.append(
                f"<b>Acesso #{i}</b>\n"
                f"📧 E-mail: <code>{email}</code>\n"
                f"🔑 Senha: <code>{pwd}</code>{extra}"
            )

        text = (
            f"✅ <b>COMPRA CONCLUÍDA!</b>\n\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"📦 Produto: {p.name if p else '—'}\n"
            f"🆔 Pedido: #{oid}\n"
            f"📅 Data: {order.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
            f"🔐 <b>SEUS ACESSOS</b>\n\n"
            + "\n\n".join(lines)
            + f"\n\n━━━━━━━━━━━━━━"
        )
    else:
        delivery = order.delivery_data or {}
        info = delivery.get("info", "") or "Consulte o suporte."
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

    activation = (order.delivery_data or {}).get("activation_link", "")
    buttons = [
        [InlineKeyboardButton(text="🔒 Ocultar dados", callback_data=f"pd_hide_creds_{oid}")],
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

        deliveries = (await s.execute(
            select(OrderDelivery).where(OrderDelivery.order_id == oid).order_by(OrderDelivery.id)
        )).scalars().all()

    has_individual = len(deliveries) > 0 and any(d.email or d.password for d in deliveries)

    if has_individual:
        lines = []
        for i, d in enumerate(deliveries, 1):
            lines.append(
                f"<b>Acesso #{i}</b>\n"
                f"📧 E-mail: {_mask_field(d.email)}\n"
                f"🔑 Senha: {_mask_field(d.password)}"
            )
        text = (
            f"✅ <b>COMPRA CONCLUÍDA!</b>\n\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"📦 Produto: {p.name if p else '—'}\n"
            f"🆔 Pedido: #{oid}\n"
            f"📅 Data: {order.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
            f"🔐 <b>SEUS ACESSOS</b>\n\n"
            + "\n\n".join(lines)
            + f"\n\n━━━━━━━━━━━━━━"
        )
    else:
        info = (order.delivery_data or {}).get("info", "") or ""
        text = (
            f"✅ <b>COMPRA CONCLUÍDA!</b>\n\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"📦 Produto: {p.name if p else '—'}\n"
            f"🆔 Pedido: #{oid}\n"
            f"📅 Data: {order.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
            f"🔐 <b>DADOS DE ACESSO</b>\n\n"
            f"📧 E-mail: {_mask_field(info)}\n\n"
            f"━━━━━━━━━━━━━━"
        )

    activation = (order.delivery_data or {}).get("activation_link", "")
    buttons = [
        [InlineKeyboardButton(text="🔓 Ativar visualização", callback_data=f"pd_show_creds_{oid}")],
    ]
    if activation:
        buttons.append([InlineKeyboardButton(text="🌐 Ativar diretamente", url=activation)])
    buttons.append([InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()
