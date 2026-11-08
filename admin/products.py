from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, or_, desc

from bot import dp
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import Product, StockMovement, AdminLog, Order
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

PAGE_SIZE = 8
SKIP = "/pular"

class PS(StatesGroup):
    new_name = State()
    new_description = State()
    new_price = State()
    new_promo = State()
    new_category = State()
    new_stock = State()
    new_delivery = State()
    new_activation = State()
    new_image = State()
    new_validity = State()
    edit_field = State()
    restock_amount = State()
    withdraw_amount = State()
    withdraw_reason = State()
    search_query = State()
    stock_config_value = State()
    creds_bulk = State()


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as session:
        session.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await session.commit()


# ============ MENU PRINCIPAL ============
def products_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Cadastrar produto", callback_data="prd_new")],
        [InlineKeyboardButton(text="📦 Listar produtos", callback_data="prd_list_0")],
        [InlineKeyboardButton(text="🔎 Pesquisar produto", callback_data="prd_search")],
        [InlineKeyboardButton(text="📊 Estatísticas de produtos", callback_data="prd_stats")],
        [InlineKeyboardButton(text="⚙️ Estoque automático", callback_data="prd_stockconfig")],
        [InlineKeyboardButton(text="🗃️ Estoque completo", callback_data="admin_stock")],  # NOVO
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])


@router.callback_query(F.data == "admin_products")
async def products_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(Product.id)))).scalar() or 0
        active = (await s.execute(select(func.count(Product.id)).where(Product.is_active == True))).scalar() or 0
        out = (await s.execute(select(func.count(Product.id)).where(Product.stock <= 0))).scalar() or 0
    text = (
        "◈ BELLAMY | PRODUTOS\n\n"
        f"📦 Total: {total}\n"
        f"🟢 Ativos: {active}\n"
        f"⚠️ Sem estoque: {out}\n\n"
        "👇 Selecione uma opção:"
    )
    await cb.message.edit_text(text, reply_markup=products_menu_kb())
    await cb.answer()


# ============ CADASTRAR PRODUTO (FSM) ============
@router.callback_query(F.data == "prd_new")
async def new_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(PS.new_name)
    await cb.message.answer(
        "➕ CADASTRAR PRODUTO (1/9)\n\n"
        "Informe o nome do produto:"
    )
    await cb.answer()


@router.message(PS.new_name)
async def new_name(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    name = m.text.strip()
    if len(name) < 2:
        await m.answer("❌ Nome muito curto.")
        return
    await state.update_data(name=name)
    await state.set_state(PS.new_description)
    await m.answer("📝 (2/9) Informe a descrição do produto:")


@router.message(PS.new_description)
async def new_desc(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await state.update_data(description=m.text.strip())
    await state.set_state(PS.new_price)
    await m.answer("💰 (3/9) Informe o preço de venda (R$):")


@router.message(PS.new_price)
async def new_price(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        price = float(m.text.strip().replace(",", "."))
        if price <= 0: raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido.")
        return
    await state.update_data(price=price)
    await state.set_state(PS.new_promo)
    await m.answer("🎁 (4/9) Preço promocional (ou envie /pular):")


@router.message(PS.new_promo)
async def new_promo(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    text = m.text.strip()
    if text.lower() == SKIP:
        await state.update_data(promo_price=None)
    else:
        try:
            promo = float(text.replace(",", "."))
            if promo <= 0: raise ValueError
            await state.update_data(promo_price=promo)
        except ValueError:
            await m.answer("❌ Valor inválido ou envie /pular.")
            return
    await state.set_state(PS.new_category)
    await m.answer("📂 (5/9) Categoria (ou /pular):")


@router.message(PS.new_category)
async def new_category(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    text = m.text.strip()
    await state.update_data(category=None if text.lower() == SKIP else text)
    await state.set_state(PS.new_stock)
    await m.answer("📦 (6/9) Quantidade inicial em estoque:")


@router.message(PS.new_stock)
async def new_stock(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        stock = int(m.text.strip())
        if stock < 0: raise ValueError
    except ValueError:
        await m.answer("❌ Quantidade inválida.")
        return
    await state.update_data(stock=stock)
    await state.set_state(PS.new_delivery)
    await m.answer("📬 (7/9) Informações de entrega (ou /pular):")


@router.message(PS.new_delivery)
async def new_delivery(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    text = m.text.strip()
    await state.update_data(delivery_info=None if text.lower() == SKIP else text)
    await state.set_state(PS.new_activation)
    await m.answer("🔗 (8/9) Link de ativação (ou /pular):")


@router.message(PS.new_activation)
async def new_activation(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    text = m.text.strip()
    await state.update_data(activation_link=None if text.lower() == SKIP else text)
    await state.set_state(PS.new_image)
    await m.answer("🖼️ (9/9) URL da imagem (ou /pular):")


@router.message(PS.new_image)
async def new_image(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    text = m.text.strip()
    image_url = None if text.lower() == SKIP else text
    await state.update_data(image_url=image_url)
    await state.set_state(PS.new_validity)
    await m.answer(
        "⏳ (10/10) Dias de validade da assinatura (ou /pular = sem validade):\n\n"
        "Ex.: <code>30</code> = assinatura expira em 30 dias após a compra."
    )


@router.message(PS.new_validity)
async def new_validity(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    text = m.text.strip()
    if text.lower() == SKIP:
        validity_days = 0
    else:
        try:
            validity_days = int(text)
            if validity_days < 0:
                raise ValueError
        except ValueError:
            await m.answer("❌ Envie um número inteiro ≥ 0, ou /pular.")
            return

    data = await state.get_data()
    await state.clear()

    async with AsyncSessionLocal() as s:
        p = Product(
            name=data["name"],
            description=data["description"],
            price=data["price"],
            promo_price=data.get("promo_price"),
            category=data.get("category"),
            stock=data["stock"],
            delivery_info=data.get("delivery_info"),
            activation_link=data.get("activation_link"),
            image_url=data.get("image_url"),
            validity_days=validity_days,
            is_active=True,
        )
        s.add(p)
        await s.commit()
        await s.refresh(p)
        pid = p.id

    await log_admin(m.from_user.id, "product_create", {
        "product_id": pid, "name": data["name"], "validity_days": validity_days,
    })

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Cadastrar outro", callback_data="prd_new")],
        [InlineKeyboardButton(text="👁️ Ver produto", callback_data=f"prd_view_{pid}")],
        [InlineKeyboardButton(text="📦 Listar produtos", callback_data="prd_list_0")],
        [InlineKeyboardButton(text="↩️ Menu de produtos", callback_data="admin_products")],
    ])
    await m.answer(
        f"✅ Produto <b>{data['name']}</b> criado com sucesso!\n"
        f"ID: #{pid}\n"
        f"⏳ Validade: {validity_days if validity_days > 0 else 'sem validade'}",
        reply_markup=kb,
    )


# ============ LISTAGEM ============
async def build_list(page: int):
    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(Product.id)))).scalar() or 0
        stmt = (
            select(Product)
            .order_by(Product.display_order.asc(), desc(Product.created_at))
            .offset(page * PAGE_SIZE).limit(PAGE_SIZE)
        )
        items = (await s.execute(stmt)).scalars().all()
    return items, total


@router.callback_query(F.data.startswith("prd_list_"))
async def list_products(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])
    items, total = await build_list(page)

    text = f"◈ BELLAMY | LISTA DE PRODUTOS\n\nTotal: {total} | Página {page + 1}\n\n"
    buttons = []
    if not items:
        text += "Nenhum produto cadastrado."
    for p in items:
        status = "🟢" if p.is_active else "🔴"
        promo = f" (promo R$ {p.promo_price:.2f})" if p.promo_price else ""
        text += (
            f"{status} <b>{p.name}</b>\n"
            f"   ID #{p.id} | R$ {p.price:.2f}{promo}\n"
            f"   Estoque: {p.stock} | Vendidos: {p.sold_count}\n\n"
        )
        buttons.append([InlineKeyboardButton(
            text=f"{status} {p.name[:32]} — R$ {p.price:.2f}",
            callback_data=f"prd_view_{p.id}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Anterior", callback_data=f"prd_list_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="Próxima ➡️", callback_data=f"prd_list_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_products")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ DETALHES / EDIÇÃO ============
async def render_product(pid: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
        if not p:
            return "❌ Produto não encontrado.", InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Voltar", callback_data="prd_list_0")]
            ])

    status = "🟢 Ativo" if p.is_active else "🔴 Inativo"
    promo = f"\n🎁 Promoção: R$ {p.promo_price:.2f}" if p.promo_price else ""
    cat = f"\n📂 Categoria: {p.category}" if p.category else ""
    feat = "\n⭐ Destaque" if p.featured else ""
    text = (
        f"◈ BELLAMY | PRODUTO #{p.id}\n\n"
        f"📦 Nome: {p.name}\n"
        f"📝 Descrição: {p.description or '—'}{cat}\n"
        f"💰 Preço: R$ {p.price:.2f}{promo}\n"
        f"📦 Estoque: {p.stock}\n"
        f"🛒 Vendidos: {p.sold_count}\n"
        f"⚙️ Status: {status}{feat}\n"
        f"🔢 Min/Max por compra: {p.min_per_purchase}/{p.max_per_purchase}\n"
        f"⏳ Validade: {(str(p.validity_days) + ' dias') if p.validity_days else 'sem validade'}\n"
        f"📅 Cadastro: {p.created_at.strftime('%d/%m/%Y %H:%M')}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Nome", callback_data=f"prd_ef_name_{pid}"),
         InlineKeyboardButton(text="📝 Descrição", callback_data=f"prd_ef_desc_{pid}")],
        [InlineKeyboardButton(text="💰 Preço", callback_data=f"prd_ef_price_{pid}"),
         InlineKeyboardButton(text="🎁 Promo", callback_data=f"prd_ef_promo_{pid}")],
        [InlineKeyboardButton(text="📂 Categoria", callback_data=f"prd_ef_cat_{pid}"),
         InlineKeyboardButton(text="🖼️ Imagem", callback_data=f"prd_ef_img_{pid}")],
        [InlineKeyboardButton(text="📬 Entrega", callback_data=f"prd_ef_del_{pid}"),
         InlineKeyboardButton(text="🔗 Ativação", callback_data=f"prd_ef_act_{pid}")],
        [InlineKeyboardButton(text="🔢 Min compra", callback_data=f"prd_ef_min_{pid}"),
         InlineKeyboardButton(text="🔢 Max compra", callback_data=f"prd_ef_max_{pid}")],
        [InlineKeyboardButton(text="📊 Ordem", callback_data=f"prd_ef_ord_{pid}"),
         InlineKeyboardButton(text="⚠️ Alerta estoque", callback_data=f"prd_ef_alert_{pid}")],
        [InlineKeyboardButton(text="⏳ Validade", callback_data=f"prd_ef_validity_{pid}")],
        [InlineKeyboardButton(text="📥 Abastecer", callback_data=f"prd_restock_{pid}"),
         InlineKeyboardButton(text="📤 Retirar", callback_data=f"prd_withdraw_{pid}")],
        [InlineKeyboardButton(text="🔐 Credenciais de acesso", callback_data=f"prd_creds_{pid}")],
        [InlineKeyboardButton(
            text=("🔴 Desativar" if p.is_active else "🟢 Ativar"),
            callback_data=f"prd_toggle_{pid}")],
        [InlineKeyboardButton(
            text=("⭐ Remover destaque" if p.featured else "⭐ Destacar"),
            callback_data=f"prd_featured_{pid}")],
        [InlineKeyboardButton(text="🗑️ Excluir produto", callback_data=f"prd_delete_{pid}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="prd_list_0")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("prd_view_"))
async def view_product(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    text, kb = await render_product(pid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ EDIÇÃO DE CAMPO ============
FIELD_PROMPTS = {
    "name":  ("Informe o NOVO NOME do produto:", "name", str),
    "desc":  ("Informe a NOVA DESCRIÇÃO do produto:", "description", str),
    "price": ("Informe o NOVO PREÇO (R$):", "price", float),
    "promo": ("Informe o NOVO PREÇO PROMOCIONAL (ou 0 para remover):", "promo_price", float),
    "cat":   ("Informe a NOVA CATEGORIA (ou /pular para remover):", "category", str),
    "img":   ("Informe a NOVA URL da imagem (ou /pular para remover):", "image_url", str),
    "del":   ("Informe as NOVAS INFORMAÇÕES DE ENTREGA (ou /pular):", "delivery_info", str),
    "act":   ("Informe o NOVO LINK DE ATIVAÇÃO (ou /pular):", "activation_link", str),
    "min":   ("Informe a NOVA quantidade mínima por compra (>=1):", "min_per_purchase", int),
    "max":   ("Informe a NOVA quantidade máxima por compra:", "max_per_purchase", int),
    "ord":   ("Informe a NOVA ordem de exibição (0 = primeiro):", "display_order", int),
    "alert": ("Informe o NOVO limite de alerta de estoque (0 = usa global):", "min_stock_alert", int),
    "validity": ("Informe os dias de validade (0 = sem validade):", "validity_days", int),
}


@router.callback_query(F.data.startswith("prd_ef_"))
async def edit_field_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    parts = cb.data.split("_")
    field_key = parts[2]
    pid = int(parts[3])
    if field_key not in FIELD_PROMPTS:
        await cb.answer("Campo inválido.")
        return
    prompt, _, _ = FIELD_PROMPTS[field_key]
    await state.update_data(product_id=pid, field_key=field_key)
    await state.set_state(PS.edit_field)
    await cb.message.answer(f"✏️ {prompt}")
    await cb.answer()


@router.message(PS.edit_field)
async def edit_field_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    data = await state.get_data()
    pid = data["product_id"]
    field_key = data["field_key"]
    _, attr_name, attr_type = FIELD_PROMPTS[field_key]

    raw = m.text.strip() if m.text else ""
    if raw.lower() == SKIP:
        new_value = None
    else:
        try:
            if attr_type == float:
                new_value = float(raw.replace(",", "."))
                if field_key == "promo" and new_value == 0:
                    new_value = None
            elif attr_type == int:
                new_value = int(raw)
            else:
                new_value = raw
        except ValueError:
            await m.answer("❌ Valor inválido.")
            return

    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
        if not p:
            await state.clear()
            await m.answer("❌ Produto não encontrado.")
            return
        setattr(p, attr_name, new_value)
        await s.commit()

    await log_admin(m.from_user.id, "product_edit", {"product_id": pid, "field": field_key, "new": str(new_value)[:200]})
    await state.clear()

    text, kb = await render_product(pid)
    await m.answer(f"✅ Campo atualizado.")
    await m.answer(text, reply_markup=kb)


# ============ TOGGLE ATIVO / DESTAQUE ============
@router.callback_query(F.data.startswith("prd_toggle_"))
async def toggle_active(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
        p.is_active = not p.is_active
        new = p.is_active
        await s.commit()
    await log_admin(cb.from_user.id, "product_toggle_active", {"product_id": pid, "new": new})
    text, kb = await render_product(pid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("✅ Status atualizado.")


@router.callback_query(F.data.startswith("prd_featured_"))
async def toggle_featured(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
        p.featured = not p.featured
        new = p.featured
        await s.commit()
    await log_admin(cb.from_user.id, "product_toggle_featured", {"product_id": pid, "new": new})
    text, kb = await render_product(pid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("✅ Destaque atualizado.")


# ============ ABASTECER ============
@router.callback_query(F.data.startswith("prd_restock_"))
async def restock_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
    await state.update_data(product_id=pid)
    await state.set_state(PS.restock_amount)
    await cb.message.answer(
        f"📦 ABASTECIMENTO DE ESTOQUE\n\n"
        f"Produto: {p.name}\n"
        f"Estoque atual: {p.stock}\n\n"
        f"Informe a quantidade a adicionar:"
    )
    await cb.answer()


@router.message(PS.restock_amount)
async def restock_amount(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        qty = int(m.text.strip())
        if qty <= 0: raise ValueError
    except ValueError:
        await m.answer("❌ Quantidade inválida.")
        return
    data = await state.get_data()
    pid = data["product_id"]
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
    await state.update_data(qty=qty)
    await m.answer(
        f"📦 CONFIRMAR ABASTECIMENTO\n\n"
        f"Produto: {p.name}\n"
        f"Estoque anterior: {p.stock}\n"
        f"Quantidade adicionada: {qty}\n"
        f"Novo estoque: {p.stock + qty}\n\n"
        f"Confirmar?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Confirmar", callback_data=f"prd_restock_ok_{pid}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"prd_view_{pid}")],
        ])
    )
    await state.set_state(None)


@router.callback_query(F.data.startswith("prd_restock_ok_"))
async def restock_confirm(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    data = await state.get_data()
    qty = data.get("qty")
    if not qty:
        await cb.answer("Sessão expirada.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
        if not p:
            await cb.answer("Produto não encontrado.", show_alert=True)
            return
        before = p.stock
        p.stock += qty
        after = p.stock
        s.add(StockMovement(
            product_id=pid, admin_id=cb.from_user.id,
            movement_type="restock", quantity=qty,
            stock_before=before, stock_after=after,
        ))
        await s.commit()

    await log_admin(cb.from_user.id, "stock_restock", {
        "product_id": pid, "qty": qty, "before": before, "after": after,
    })
    await state.clear()

    settings = await get_settings(force=True)
    if settings.notify_restock_enabled:
        # Módulo de notificação de canal será implementado no Módulo correspondente.
        pass

    text, kb = await render_product(pid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("✅ Estoque abastecido.")


# ============ RETIRAR ============
@router.callback_query(F.data.startswith("prd_withdraw_"))
async def withdraw_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
    await state.update_data(product_id=pid)
    await state.set_state(PS.withdraw_amount)
    await cb.message.answer(
        f"📤 RETIRAR DO ESTOQUE\n\n"
        f"Produto: {p.name}\n"
        f"Estoque atual: {p.stock}\n\n"
        f"Informe a quantidade a retirar:"
    )
    await cb.answer()


@router.message(PS.withdraw_amount)
async def withdraw_amount(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        qty = int(m.text.strip())
        if qty <= 0: raise ValueError
    except ValueError:
        await m.answer("❌ Quantidade inválida.")
        return
    data = await state.get_data()
    pid = data["product_id"]
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
    if qty > p.stock:
        await m.answer(f"❌ Estoque insuficiente (disponível: {p.stock}).")
        return
    await state.update_data(qty=qty)
    await state.set_state(PS.withdraw_reason)
    await m.answer("📝 Informe a justificativa da retirada (mínimo 3 caracteres):")


@router.message(PS.withdraw_reason)
async def withdraw_reason(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    reason = m.text.strip()
    if len(reason) < 3:
        await m.answer("❌ Justificativa muito curta.")
        return
    data = await state.get_data()
    pid = data["product_id"]
    qty = data["qty"]

    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
        if not p or qty > p.stock:
            await state.clear()
            await m.answer("❌ Produto sem estoque suficiente.")
            return
        before = p.stock
        p.stock -= qty
        after = p.stock
        s.add(StockMovement(
            product_id=pid, admin_id=m.from_user.id,
            movement_type="withdraw", quantity=qty,
            stock_before=before, stock_after=after,
            reason=reason,
        ))
        await s.commit()

    await log_admin(m.from_user.id, "stock_withdraw", {
        "product_id": pid, "qty": qty, "before": before, "after": after, "reason": reason,
    })
    await state.clear()

    text, kb = await render_product(pid)
    await m.answer(f"✅ {qty} unidade(s) retirada(s). Estoque: {after}")
    await m.answer(text, reply_markup=kb)


# ============ EXCLUIR ============
@router.callback_query(F.data.startswith("prd_delete_"))
async def delete_prompt(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
    if not p:
        await cb.answer("Produto não encontrado.", show_alert=True)
        return
    await cb.message.edit_text(
        f"🗑️ EXCLUIR PRODUTO\n\n"
        f"Produto: {p.name} (#{p.id})\n\n"
        f"⚠️ Esta ação é irreversível!\n"
        f"Vendas já realizadas não serão afetadas.\n\n"
        f"Confirmar exclusão?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Sim, excluir", callback_data=f"prd_delete_ok_{pid}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"prd_view_{pid}")],
        ])
    )
    await cb.answer()


@router.callback_query(F.data.startswith("prd_delete_ok_"))
async def delete_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    pid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
        if not p:
            await cb.answer("Produto não encontrado.", show_alert=True)
            return
        name = p.name
        await s.delete(p)
        await s.commit()
    await log_admin(cb.from_user.id, "product_delete", {"product_id": pid, "name": name})
    await cb.message.edit_text(
        f"✅ Produto <b>{name}</b> excluído.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 Listar produtos", callback_data="prd_list_0")],
            [InlineKeyboardButton(text="↩️ Menu de produtos", callback_data="admin_products")],
        ])
    )
    await cb.answer()


# ============ PESQUISA ============
@router.callback_query(F.data == "prd_search")
async def search_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(PS.search_query)
    await cb.message.answer("🔎 Envie o nome ou categoria do produto:")
    await cb.answer()


@router.message(PS.search_query)
async def search_run(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    q = m.text.strip()
    async with AsyncSessionLocal() as s:
        stmt = select(Product).where(or_(
            Product.name.ilike(f"%{q}%"),
            Product.category.ilike(f"%{q}%"),
        )).limit(20)
        items = (await s.execute(stmt)).scalars().all()
    await state.clear()

    if not items:
        await m.answer("❌ Nenhum produto encontrado.")
        return

    buttons = []
    text = f"🔎 RESULTADOS: {len(items)} produto(s)\n\n"
    for p in items:
        status = "🟢" if p.is_active else "🔴"
        text += f"{status} <b>{p.name}</b> — R$ {p.price:.2f} | Estoque: {p.stock}\n"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {p.name[:32]}",
            callback_data=f"prd_view_{p.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_products")])
    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ============ ESTATÍSTICAS ============
@router.callback_query(F.data == "prd_stats")
async def product_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(Product.id)))).scalar() or 0
        active = (await s.execute(select(func.count(Product.id)).where(Product.is_active == True))).scalar() or 0
        out = (await s.execute(select(func.count(Product.id)).where(Product.stock <= 0))).scalar() or 0
        low = (await s.execute(
            select(func.count(Product.id)).where(Product.stock > 0, Product.stock <= 5)
        )).scalar() or 0
        stock_total = (await s.execute(select(func.coalesce(func.sum(Product.stock), 0)))).scalar() or 0
        sold_total = (await s.execute(select(func.coalesce(func.sum(Product.sold_count), 0)))).scalar() or 0
        top = (await s.execute(
            select(Product).order_by(desc(Product.sold_count)).limit(5)
        )).scalars().all()

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DE PRODUTOS\n\n"
        f"📦 Total cadastrados: {total}\n"
        f"🟢 Ativos: {active}\n"
        f"⚠️ Esgotados: {out}\n"
        f"🟡 Estoque baixo (≤5): {low}\n"
        f"📊 Unidades em estoque: {stock_total}\n"
        f"🛒 Total vendidos: {sold_total}\n\n"
        "🏆 Top 5 mais vendidos:\n"
    )
    for p in top:
        text += f"• {p.name} — {p.sold_count} vendas\n"

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_products")]
    ]))
    await cb.answer()


# ============ CONFIG ESTOQUE AUTOMÁTICO ============
async def stock_config_view() -> tuple[str, InlineKeyboardMarkup]:
    s = await get_settings(force=True)
    text = (
        "◈ BELLAMY | ESTOQUE AUTOMÁTICO\n\n"
        f"🔔 Alertas de estoque: {'🟢 Ativados' if s.stock_alert_enabled else '🔴 Desativados'}\n"
        f"⚠️ Limite global de estoque baixo: {s.stock_low_threshold}\n"
        f"🛒 Vendas sem estoque: {'🟢 Permitidas' if s.allow_out_of_stock_sales else '🔴 Bloqueadas'}\n"
        f"📢 Notificar reposição no canal: {'🟢 Ativado' if s.notify_restock_enabled else '🔴 Desativado'}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar alertas" if s.stock_alert_enabled else "🟢 Ativar alertas"),
            callback_data="prd_cfg_toggle_alert")],
        [InlineKeyboardButton(text="⚠️ Alterar limite global", callback_data="prd_cfg_set_threshold")],
        [InlineKeyboardButton(
            text=("🟢 Permitir venda sem estoque" if not s.allow_out_of_stock_sales else "🔴 Bloquear venda sem estoque"),
            callback_data="prd_cfg_toggle_oos")],
        [InlineKeyboardButton(
            text=("🔴 Desativar aviso de reposição" if s.notify_restock_enabled else "🟢 Ativar aviso de reposição"),
            callback_data="prd_cfg_toggle_restock")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_products")],
    ])
    return text, kb


@router.callback_query(F.data == "prd_stockconfig")
async def stock_config(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    text, kb = await stock_config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "prd_cfg_toggle_alert")
async def cfg_toggle_alert(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(type(await get_settings()), 1)
        bs.stock_alert_enabled = not bs.stock_alert_enabled
        await s.commit()
    invalidate_cache()
    text, kb = await stock_config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "prd_cfg_toggle_oos")
async def cfg_toggle_oos(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        from models import BotSettings
        bs = await s.get(BotSettings, 1)
        bs.allow_out_of_stock_sales = not bs.allow_out_of_stock_sales
        await s.commit()
    invalidate_cache()
    text, kb = await stock_config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "prd_cfg_toggle_restock")
async def cfg_toggle_restock(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        from models import BotSettings
        bs = await s.get(BotSettings, 1)
        bs.notify_restock_enabled = not bs.notify_restock_enabled
        await s.commit()
    invalidate_cache()
    text, kb = await stock_config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "prd_cfg_set_threshold")
async def cfg_threshold_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(PS.stock_config_value)
    await cb.message.answer("Informe o novo limite global de estoque baixo (número inteiro ≥ 0):")
    await cb.answer()


@router.message(PS.stock_config_value)
async def cfg_threshold_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        val = int(m.text.strip())
        if val < 0: raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido.")
        return
    async with AsyncSessionLocal() as s:
        from models import BotSettings
        bs = await s.get(BotSettings, 1)
        bs.stock_low_threshold = val
        await s.commit()
    invalidate_cache()
    await state.clear()
    text, kb = await stock_config_view()
    await m.answer(f"✅ Limite atualizado para {val}.", reply_markup=kb)


# ============================================================
# GERENCIAMENTO DE CREDENCIAIS INDIVIDUAIS (Pacote A)
# ============================================================

from models import ProductCredential
from sqlalchemy import delete as sql_delete


@router.callback_query(F.data.startswith("prd_creds_"))
async def creds_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    try:
        pid = int(cb.data.replace("prd_creds_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
        if not p:
            await cb.answer("Produto não encontrado.", show_alert=True)
            return

        total = (await s.execute(
            select(func.count(ProductCredential.id))
            .where(ProductCredential.product_id == pid)
        )).scalar() or 0

        available = (await s.execute(
            select(func.count(ProductCredential.id))
            .where(ProductCredential.product_id == pid)
            .where(ProductCredential.is_sold == False)
        )).scalar() or 0

        sold = total - available

    mode = "🔐 Pool individual" if total > 0 else "📄 Delivery único"
    note = ""
    if total > 0:
        note = (
            f"\n⚠️ Este produto usa <b>pool de credenciais individuais</b>. "
            f"Cada venda consome 1 credencial. Se o pool acabar, "
            f"a venda é bloqueada mesmo com estoque &gt; 0."
        )
    else:
        note = (
            f"\n💡 Este produto ainda <b>não tem credenciais cadastradas</b> "
            f"e usa o campo de delivery único (informações de entrega). "
            f"Ao cadastrar a primeira credencial, o modo muda automaticamente."
        )

    text = (
        f"🔐 CREDENCIAIS — {p.name}\n\n"
        f"📊 Modo atual: {mode}\n"
        f"📦 Total cadastradas: {total}\n"
        f"🟢 Disponíveis: {available}\n"
        f"🔴 Vendidas: {sold}\n"
        f"{note}"
    )

    buttons = [
        [InlineKeyboardButton(
            text="➕ Adicionar credenciais em lote",
            callback_data=f"prd_creds_add_{pid}",
        )],
        [InlineKeyboardButton(
            text=f"📋 Listar disponíveis ({available})",
            callback_data=f"prd_creds_list_{pid}_avail_0",
        )],
        [InlineKeyboardButton(
            text=f"🔴 Ver vendidas ({sold})",
            callback_data=f"prd_creds_list_{pid}_sold_0",
        )],
        [InlineKeyboardButton(
            text="🗑️ Limpar todas as disponíveis",
            callback_data=f"prd_creds_clear_{pid}",
        )],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"prd_view_{pid}")],
    ]
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("prd_creds_add_"))
async def creds_add_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    try:
        pid = int(cb.data.replace("prd_creds_add_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return

    await state.update_data(creds_product_id=pid)
    await state.set_state(PS.creds_bulk)
    await cb.message.answer(
        f"➕ ADICIONAR CREDENCIAIS EM LOTE\n\n"
        f"Produto: <code>#{pid}</code>\n\n"
        f"Envie uma lista com 1 credencial por linha no formato:\n\n"
        f"<code>email|senha</code>\n"
        f"<code>email|senha|extra1|extra2</code>\n\n"
        f"Exemplo:\n"
        f"<code>user1@mail.com|senha123</code>\n"
        f"<code>user2@mail.com|senha456|perfil=premium</code>\n\n"
        f"Digite /cancelar para abortar."
    )
    await cb.answer()


@router.message(PS.creds_bulk)
async def creds_add_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    raw = (m.text or "").strip()
    if raw.lower() == "/cancelar":
        await state.clear()
        await m.answer("Operação cancelada.")
        return

    data = await state.get_data()
    pid = data.get("creds_product_id")
    await state.clear()

    if not pid:
        await m.answer("❌ Sessão expirada.")
        return

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        await m.answer("❌ Nenhuma linha válida.")
        return

    inserted = 0
    errors = []

    async with AsyncSessionLocal() as s:
        for i, line in enumerate(lines, 1):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                errors.append(f"Linha {i}: formato inválido")
                continue

            email = parts[0][:500]
            password = parts[1][:500]
            extra = None
            if len(parts) > 2:
                extra = {}
                for extra_part in parts[2:]:
                    if "=" in extra_part:
                        k, v = extra_part.split("=", 1)
                        extra[k.strip()] = v.strip()
                    else:
                        extra[f"extra_{len(extra) + 1}"] = extra_part

            s.add(ProductCredential(
                product_id=pid,
                email=email,
                password=password,
                extra_data=extra,
                is_sold=False,
            ))
            inserted += 1

        await s.commit()

    await log_admin(m.from_user.id, "product_creds_bulk_add", {
        "product_id": pid, "inserted": inserted, "errors": len(errors),
    })

    text = f"✅ <b>{inserted}</b> credencial(is) adicionada(s)."
    if errors:
        text += f"\n\n⚠️ Linhas ignoradas ({len(errors)}):\n" + "\n".join(errors[:10])

    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔐 Ver credenciais", callback_data=f"prd_creds_{pid}")],
    ]))


@router.callback_query(F.data.startswith("prd_creds_list_"))
async def creds_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    rest = cb.data.replace("prd_creds_list_", "")
    try:
        pid_s, filt, page_s = rest.rsplit("_", 2)
        pid = int(pid_s)
        page = int(page_s)
    except (ValueError, IndexError):
        await cb.answer("Parâmetros inválidos.", show_alert=True)
        return

    page_size = 8
    async with AsyncSessionLocal() as s:
        stmt = select(ProductCredential).where(ProductCredential.product_id == pid)
        cnt = select(func.count(ProductCredential.id)).where(ProductCredential.product_id == pid)

        if filt == "avail":
            stmt = stmt.where(ProductCredential.is_sold == False)
            cnt = cnt.where(ProductCredential.is_sold == False)
        elif filt == "sold":
            stmt = stmt.where(ProductCredential.is_sold == True)
            cnt = cnt.where(ProductCredential.is_sold == True)

        total = (await s.execute(cnt)).scalar() or 0
        stmt = stmt.order_by(ProductCredential.id).offset(page * page_size).limit(page_size)
        creds = (await s.execute(stmt)).scalars().all()

    title = "DISPONÍVEIS" if filt == "avail" else ("VENDIDAS" if filt == "sold" else "TODAS")
    max_page = max(0, (total - 1) // page_size) if total else 0

    lines = [f"🔐 CREDENCIAIS — {title}\n"]
    lines.append(f"Total: {total} | Página {page + 1} de {max_page + 1}\n")

    if not creds:
        lines.append("<i>Nenhuma credencial.</i>")
    else:
        for c in creds:
            icon = "🔴" if c.is_sold else "🟢"
            lines.append(
                f"{icon} #{c.id} | <code>{c.email or '—'}</code> | "
                f"<code>{c.password or '—'}</code>"
            )
            if c.extra_data and isinstance(c.extra_data, dict):
                extra_str = " ".join(f"{k}={v}" for k, v in c.extra_data.items())
                lines.append(f"   {extra_str[:80]}")
            if c.is_sold and c.order_id:
                lines.append(f"   📦 Pedido #{c.order_id}")

    buttons = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"prd_creds_list_{pid}_{filt}_{page - 1}"
        ))
    if (page + 1) * page_size < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"prd_creds_list_{pid}_{filt}_{page + 1}"
        ))
    if nav:
        buttons.append(nav)

    if filt == "avail" and creds:
        for c in creds:
            buttons.append([InlineKeyboardButton(
                text=f"🗑️ Excluir #{c.id}",
                callback_data=f"prd_creds_delone_{c.id}",
            )])

    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=f"prd_creds_{pid}")])

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("prd_creds_delone_"))
async def creds_delete_one(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    try:
        cid = int(cb.data.replace("prd_creds_delone_", ""))
    except (ValueError, TypeError):
        await cb.answer("Credencial inválida.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        c = await s.get(ProductCredential, cid)
        if not c:
            await cb.answer("Não encontrada.", show_alert=True)
            return
        if c.is_sold:
            await cb.answer("Não pode excluir uma credencial já vendida.", show_alert=True)
            return
        pid = c.product_id
        await s.delete(c)
        await s.commit()

    await log_admin(cb.from_user.id, "product_creds_delete_one", {"credential_id": cid})
    await cb.answer("Credencial excluída.")
    await creds_menu_redirect(cb, pid)


async def creds_menu_redirect(cb: CallbackQuery, pid: int):
    cb.data = f"prd_creds_{pid}"
    await creds_menu(cb)


@router.callback_query(F.data.startswith("prd_creds_clear_"))
async def creds_clear(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    try:
        pid = int(cb.data.replace("prd_creds_clear_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return

    await cb.message.edit_text(
        f"🗑️ LIMPAR CREDENCIAIS DISPONÍVEIS\n\n"
        f"Produto: <code>#{pid}</code>\n\n"
        f"⚠️ Todas as credenciais ainda <b>não vendidas</b> serão excluídas.\n"
        f"As vendidas são mantidas.\n\nConfirmar?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Sim, limpar", callback_data=f"prd_creds_clear_ok_{pid}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"prd_creds_{pid}")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("prd_creds_clear_ok_"))
async def creds_clear_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    try:
        pid = int(cb.data.replace("prd_creds_clear_ok_", ""))
    except (ValueError, TypeError):
        await cb.answer("Produto inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        result = await s.execute(
            sql_delete(ProductCredential)
            .where(ProductCredential.product_id == pid)
            .where(ProductCredential.is_sold == False)
        )
        await s.commit()
        deleted = result.rowcount or 0

    await log_admin(cb.from_user.id, "product_creds_clear", {
        "product_id": pid, "deleted": deleted,
    })
    await cb.answer(f"🗑️ {deleted} credencial(is) removida(s).", show_alert=True)
    await creds_menu_redirect(cb, pid)
