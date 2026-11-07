from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, or_
from datetime import datetime, timedelta
import random
import string

from bot import dp
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    Coupon, CouponUsage, User, Order, AdminLog, BotSettings,
    Wallet, WalletTransaction,
)
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

PAGE_SIZE = 10
USAGE_PAGE_SIZE = 12


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class CS(StatesGroup):
    new_code = State()
    new_value = State()
    new_min = State()
    new_max = State()
    new_valid_until = State()
    new_usage_limit = State()
    new_usage_per_user = State()
    edit_field = State()
    search = State()
    bonus_amount = State()


TYPE_LABEL = {"percentage": "Percentual (%)", "fixed": "Valor fixo (R$)"}
TYPE_ICON = {"percentage": "📊", "fixed": "💵"}


def gen_code(length: int = 10) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


def coupon_status(c: Coupon, now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.utcnow()
    if not c.is_active:
        return "🔴", "Inativo"
    if c.valid_from and now < c.valid_from:
        return "⏳", "Agendado"
    if c.valid_until and now > c.valid_until:
        return "⚪", "Expirado"
    if c.usage_limit > 0 and c.used_count >= c.usage_limit:
        return "🚫", "Esgotado"
    return "🟢", "Ativo"


def parse_datetime_input(text: str) -> datetime | None:
    t = text.strip().lower()
    if t in ("/pular", "/sem-limite", "sem-limite", "sem limite", "0"):
        return None
    # Duração relativa
    if t.endswith("d") and t[:-1].isdigit():
        days = int(t[:-1])
        return datetime.utcnow() + timedelta(days=days)
    if t.endswith("h") and t[:-1].isdigit():
        hours = int(t[:-1])
        return datetime.utcnow() + timedelta(hours=hours)
    # Data absoluta
    formats = ["%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]
    for fmt in formats:
        try:
            dt = datetime.strptime(text.strip(), fmt)
            if fmt == "%d/%m/%Y" or fmt == "%d-%m-%Y" or fmt == "%Y-%m-%d":
                dt = dt.replace(hour=23, minute=59, second=59)
            return dt
        except ValueError:
            continue
    return None


# ============ MENU ============
@router.callback_query(F.data == "admin_coupons")
async def coupons_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    now = datetime.utcnow()
    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(Coupon.id)))).scalar() or 0
        active = (await s.execute(
            select(func.count(Coupon.id)).where(Coupon.is_active == True)
        )).scalar() or 0
        usages_total = (await s.execute(
            select(func.count(CouponUsage.id))
        )).scalar() or 0
        discount_total = (await s.execute(
            select(func.coalesce(func.sum(CouponUsage.discount_amount), 0))
        )).scalar() or 0.0
        usages_today = (await s.execute(
            select(func.count(CouponUsage.id))
            .where(CouponUsage.used_at >= datetime.combine(
                now.date(), datetime.min.time()
            ))
        )).scalar() or 0

        settings = await get_settings()
        welcome_on = getattr(settings, "welcome_bonus_enabled", False)
        welcome_val = getattr(settings, "welcome_bonus_amount", 0.0)
        first_on = getattr(settings, "first_purchase_bonus_enabled", False)
        first_pct = getattr(settings, "first_purchase_bonus_percentage", 0.0)

    text = (
        "◈ BELLAMY | CUPONS E BÔNUS\n\n"
        "🎟️ CUPONS\n"
        f"📋 Total: {total}\n"
        f"🟢 Ativos: {active}\n"
        f"🔁 Utilizações: {usages_total}\n"
        f"💸 Desconto concedido: R$ {discount_total:.2f}\n"
        f"📅 Utilizações hoje: {usages_today}\n\n"
        "🎁 BÔNUS AUTOMÁTICOS\n"
        f"👋 Boas-vindas: {'🟢 R$ ' + f'{welcome_val:.2f}' if welcome_on else '🔴 Desativado'}\n"
        f"🛍️ Primeira compra: {'🟢 ' + f'{first_pct:.1f}%' if first_on else '🔴 Desativado'}\n\n"
        "👇 Selecione uma opção:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Criar cupom", callback_data="cpn_new")],
        [InlineKeyboardButton(text=f"📋 Listar cupons ({total})", callback_data="cpn_list_all_0")],
        [InlineKeyboardButton(text="🟢 Apenas ativos", callback_data="cpn_list_active_0")],
        [InlineKeyboardButton(text="⚪ Expirados/Inativos", callback_data="cpn_list_inactive_0")],
        [InlineKeyboardButton(text="🔎 Buscar cupom", callback_data="cpn_search")],
        [InlineKeyboardButton(text="📜 Histórico de utilizações", callback_data="cpn_usage_0")],
        [InlineKeyboardButton(text="🎁 Configurar bônus automáticos", callback_data="cpn_bonus_cfg")],
        [InlineKeyboardButton(text="📊 Estatísticas", callback_data="cpn_stats")],
        [InlineKeyboardButton(text="📥 Exportar relatório", callback_data="cpn_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ LISTAGEM ============
@router.callback_query(F.data.startswith("cpn_list_"))
async def list_coupons(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    parts = cb.data.split("_")
    ftype = parts[2]
    page = int(parts[3])

    now = datetime.utcnow()
    async with AsyncSessionLocal() as s:
        stmt = select(Coupon)
        cnt = select(func.count(Coupon.id))
        if ftype == "active":
            stmt = stmt.where(Coupon.is_active == True)
            cnt = cnt.where(Coupon.is_active == True)
        elif ftype == "inactive":
            stmt = stmt.where(or_(
                Coupon.is_active == False,
                Coupon.valid_until < now,
            ))
            cnt = cnt.where(or_(
                Coupon.is_active == False,
                Coupon.valid_until < now,
            ))

        total = (await s.execute(cnt)).scalar() or 0
        stmt = stmt.order_by(desc(Coupon.created_at)).offset(page * PAGE_SIZE).limit(PAGE_SIZE)
        coupons = (await s.execute(stmt)).scalars().all()

    titles = {
        "all": "TODOS OS CUPONS",
        "active": "CUPONS ATIVOS",
        "inactive": "CUPONS EXPIRADOS/INATIVOS",
    }
    text = f"◈ BELLAMY | {titles.get(ftype, ftype.upper())}\n\n"
    text += f"Total: {total} | Página {page + 1}\n\n"

    buttons = []
    if not coupons:
        text += "Nenhum cupom encontrado."
    else:
        for c in coupons:
            s_icon, s_label = coupon_status(c, now)
            tipo = TYPE_ICON.get(c.type, "🎟️")
            val = f"{c.value:.1f}%" if c.type == "percentage" else f"R$ {c.value:.2f}"
            usage_info = (
                f"{c.used_count}/{c.usage_limit}" if c.usage_limit > 0
                else f"{c.used_count}/∞"
            )
            text += (
                f"{s_icon} <code>{c.code}</code> — {tipo} {val}\n"
                f"   {s_label} | Usos: {usage_info}\n\n"
            )
            buttons.append([InlineKeyboardButton(
                text=f"{s_icon} {c.code} — {val}",
                callback_data=f"cpn_view_{c.id}",
            )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"cpn_list_{ftype}_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"cpn_list_{ftype}_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_coupons")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ CRIAR CUPOM (FSM) ============
@router.callback_query(F.data == "cpn_new")
async def new_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CS.new_code)
    await state.update_data(new={})
    await cb.message.answer(
        "➕ CRIAR CUPOM (1/7)\n\n"
        "Informe o <b>código</b> do cupom (ex.: <code>BELLAMY10</code>)\n\n"
        "Ou envie /gerar para criar automaticamente."
    )
    await cb.answer()


@router.message(CS.new_code)
async def new_code(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""

    if raw.lower() == "/gerar":
        code = gen_code(10)
    else:
        code = raw.upper().replace(" ", "")
        if len(code) < 3 or len(code) > 30:
            await m.answer("❌ Código deve ter entre 3 e 30 caracteres (sem espaços).")
            return
        if not all(c.isalnum() or c in "-_" for c in code):
            await m.answer("❌ Use apenas letras, números, hífen e underline.")
            return

    async with AsyncSessionLocal() as s:
        exists = (await s.execute(
            select(Coupon).where(Coupon.code == code)
        )).scalar_one_or_none()
    if exists:
        await m.answer(f"❌ O código <code>{code}</code> já existe. Envie outro.")
        return

    data = await state.get_data()
    data["new"]["code"] = code
    await state.update_data(new=data["new"])

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Percentual (%)", callback_data="cpn_type_percentage")],
        [InlineKeyboardButton(text="💵 Valor fixo (R$)", callback_data="cpn_type_fixed")],
        [InlineKeyboardButton(text="❌ Cancelar", callback_data="cpn_cancel_new")],
    ])
    await state.set_state(None)
    await m.answer(
        f"➕ CRIAR CUPOM (2/7)\n\n"
        f"Código: <code>{code}</code>\n\n"
        f"Escolha o <b>tipo de desconto</b>:",
        reply_markup=kb,
    )


@router.callback_query(F.data == "cpn_cancel_new")
async def cancel_new(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Criação de cupom cancelada.")
    await cb.answer()


@router.callback_query(F.data.startswith("cpn_type_"))
async def new_type(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    ctype = cb.data.replace("cpn_type_", "")
    data = await state.get_data()
    data["new"]["type"] = ctype
    await state.update_data(new=data["new"])
    await state.set_state(CS.new_value)

    if ctype == "percentage":
        prompt = "Informe o <b>percentual de desconto</b> (ex.: 10 para 10%):"
    else:
        prompt = "Informe o <b>valor do desconto</b> em R$ (ex.: 15.50):"
    await cb.message.edit_text(f"➕ CRIAR CUPOM (3/7)\n\n{prompt}")
    await cb.answer()


@router.message(CS.new_value)
async def new_value(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        val = float(m.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido.")
        return

    data = await state.get_data()
    ctype = data["new"]["type"]
    if ctype == "percentage" and val > 100:
        await m.answer("❌ Percentual não pode ser maior que 100%.")
        return

    data["new"]["value"] = val
    await state.update_data(new=data["new"])
    await state.set_state(CS.new_min)
    await m.answer(
        "➕ CRIAR CUPOM (4/7)\n\n"
        "Informe o <b>valor mínimo de compra</b> em R$ (ex.: 30.00).\n\n"
        "Envie <code>0</code> se não houver mínimo."
    )


@router.message(CS.new_min)
async def new_min(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        val = float(m.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido.")
        return
    data = await state.get_data()
    data["new"]["min_purchase"] = val
    await state.update_data(new=data["new"])

    if data["new"]["type"] == "percentage":
        await state.set_state(CS.new_max)
        await m.answer(
            "➕ CRIAR CUPOM (5/7)\n\n"
            "Informe o <b>desconto máximo</b> em R$ (limite do desconto percentual).\n\n"
            "Envie <code>0</code> para não ter limite."
        )
    else:
        await state.set_state(CS.new_valid_until)
        await m.answer(
            "➕ CRIAR CUPOM (5/7)\n\n"
            "Informe a <b>validade</b> do cupom:\n\n"
            "• <code>7d</code> — 7 dias\n"
            "• <code>30d</code> — 30 dias\n"
            "• <code>24/12/2026</code> — data específica\n"
            "• <code>24/12/2026 18:30</code> — data e hora\n"
            "• <code>/sem-limite</code> — não expira"
        )


@router.message(CS.new_max)
async def new_max(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        val = float(m.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido.")
        return
    data = await state.get_data()
    data["new"]["max_discount"] = val if val > 0 else None
    await state.update_data(new=data["new"])
    await state.set_state(CS.new_valid_until)
    await m.answer(
        "➕ CRIAR CUPOM (6/7)\n\n"
        "Informe a <b>validade</b> do cupom:\n\n"
        "• <code>7d</code> — 7 dias\n"
        "• <code>30d</code> — 30 dias\n"
        "• <code>24/12/2026</code> — data específica\n"
        "• <code>24/12/2026 18:30</code> — data e hora\n"
        "• <code>/sem-limite</code> — não expira"
    )


@router.message(CS.new_valid_until)
async def new_valid(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    val = parse_datetime_input(raw)
    if val is None and raw.lower() not in ("/pular", "/sem-limite", "sem-limite", "sem limite", "0"):
        await m.answer("❌ Data inválida. Use <code>7d</code>, <code>24/12/2026</code> ou <code>/sem-limite</code>.")
        return

    data = await state.get_data()
    data["new"]["valid_until"] = val
    await state.update_data(new=data["new"])
    await state.set_state(CS.new_usage_limit)
    await m.answer(
        "➕ CRIAR CUPOM (7/7)\n\n"
        "Informe o <b>limite total de utilizações</b>.\n\n"
        "Envie <code>0</code> para ilimitado."
    )


@router.message(CS.new_usage_limit)
async def new_usage_limit(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        val = int(m.text.strip())
        if val < 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido.")
        return
    data = await state.get_data()
    data["new"]["usage_limit"] = val
    await state.update_data(new=data["new"])
    await state.set_state(CS.new_usage_per_user)
    await m.answer(
        "➕ CRIAR CUPOM (7/7 — final)\n\n"
        "Informe o <b>limite por usuário</b>.\n\n"
        "Envie <code>0</code> para ilimitado."
    )


@router.message(CS.new_usage_per_user)
async def new_usage_per_user(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        val = int(m.text.strip())
        if val < 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido.")
        return

    data = await state.get_data()
    data["new"]["usage_per_user"] = val
    n = data["new"]
    await state.clear()

    async with AsyncSessionLocal() as s:
        coupon = Coupon(
            code=n["code"],
            type=n["type"],
            value=n["value"],
            min_purchase=n["min_purchase"],
            max_discount=n.get("max_discount"),
            valid_from=datetime.utcnow(),
            valid_until=n.get("valid_until"),
            usage_limit=n["usage_limit"],
            usage_per_user=n["usage_per_user"],
            is_active=True,
            applies_to="all",
            created_by=m.from_user.id,
        )
        s.add(coupon)
        await s.commit()
        await s.refresh(coupon)
        cid = coupon.id

    await log_admin(m.from_user.id, "coupon_create", {
        "coupon_id": cid, "code": n["code"], "type": n["type"],
        "value": n["value"], "usage_limit": n["usage_limit"],
    })

    tipo = "Percentual" if n["type"] == "percentage" else "Valor fixo"
    val_txt = f"{n['value']}%" if n["type"] == "percentage" else f"R$ {n['value']:.2f}"
    min_txt = f"R$ {n['min_purchase']:.2f}" if n["min_purchase"] > 0 else "sem mínimo"
    max_txt = f"R$ {n['max_discount']:.2f}" if n.get("max_discount") else "sem limite"
    valid_txt = n["valid_until"].strftime("%d/%m/%Y %H:%M") if n.get("valid_until") else "não expira"
    limit_txt = str(n["usage_limit"]) if n["usage_limit"] > 0 else "ilimitado"
    per_user_txt = str(n["usage_per_user"]) if n["usage_per_user"] > 0 else "ilimitado"

    text = (
        f"✅ <b>CUPOM CRIADO</b>\n\n"
        f"🎟️ Código: <code>{n['code']}</code>\n"
        f"📊 Tipo: {tipo}\n"
        f"💰 Valor: {val_txt}\n"
        f"🛒 Mínimo: {min_txt}\n"
        f"🔒 Máx. desconto: {max_txt}\n"
        f"📅 Validade: {valid_txt}\n"
        f"🔢 Limite total: {limit_txt}\n"
        f"👤 Limite por usuário: {per_user_txt}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Criar outro", callback_data="cpn_new")],
        [InlineKeyboardButton(text="👁️ Ver cupom", callback_data=f"cpn_view_{cid}")],
        [InlineKeyboardButton(text="📋 Listar cupons", callback_data="cpn_list_all_0")],
        [InlineKeyboardButton(text="↩️ Menu de cupons", callback_data="admin_coupons")],
    ])
    await m.answer(text, reply_markup=kb)


# ============ DETALHE DO CUPOM ============
async def render_coupon(cid: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as s:
        c = await s.get(Coupon, cid)
        if not c:
            return (
                "❌ Cupom não encontrado.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_coupons")]
                ]),
            )
        usages_count = (await s.execute(
            select(func.count(CouponUsage.id)).where(CouponUsage.coupon_id == cid)
        )).scalar() or 0
        discount_sum = (await s.execute(
            select(func.coalesce(func.sum(CouponUsage.discount_amount), 0))
            .where(CouponUsage.coupon_id == cid)
        )).scalar() or 0.0

    s_icon, s_label = coupon_status(c)
    tipo = TYPE_LABEL.get(c.type, c.type)
    val_txt = f"{c.value}%" if c.type == "percentage" else f"R$ {c.value:.2f}"
    min_txt = f"R$ {c.min_purchase:.2f}" if c.min_purchase > 0 else "sem mínimo"
    max_txt = f"R$ {c.max_discount:.2f}" if c.max_discount else "sem limite"
    valid_from_txt = c.valid_from.strftime("%d/%m/%Y %H:%M") if c.valid_from else "—"
    valid_until_txt = c.valid_until.strftime("%d/%m/%Y %H:%M") if c.valid_until else "não expira"
    limit_txt = str(c.usage_limit) if c.usage_limit > 0 else "ilimitado"
    per_user_txt = str(c.usage_per_user) if c.usage_per_user > 0 else "ilimitado"

    text = (
        f"◈ BELLAMY | CUPOM #{c.id}\n\n"
        f"🎟️ Código: <code>{c.code}</code>\n"
        f"📌 Status: {s_icon} {s_label}\n"
        f"📊 Tipo: {tipo}\n"
        f"💰 Valor: {val_txt}\n"
        f"🛒 Mínimo: {min_txt}\n"
        f"🔒 Máx. desconto: {max_txt}\n\n"
        f"📅 Início: {valid_from_txt}\n"
        f"📅 Validade: {valid_until_txt}\n\n"
        f"🔢 Limite total: {limit_txt}\n"
        f"👤 Limite por usuário: {per_user_txt}\n\n"
        f"📊 UTILIZAÇÃO\n"
        f"🔁 Utilizações: {c.used_count}\n"
        f"📜 Registros de uso: {usages_count}\n"
        f"💸 Desconto concedido: R$ {discount_sum:.2f}\n"
    )

    buttons = [
        [InlineKeyboardButton(text="✏️ Editar valor", callback_data=f"cpn_edit_{cid}_value")],
        [InlineKeyboardButton(text="✏️ Editar mínimo", callback_data=f"cpn_edit_{cid}_min")],
        [InlineKeyboardButton(text="✏️ Editar máx. desconto", callback_data=f"cpn_edit_{cid}_max")],
        [InlineKeyboardButton(text="✏️ Editar validade", callback_data=f"cpn_edit_{cid}_valid")],
        [InlineKeyboardButton(text="✏️ Editar limite total", callback_data=f"cpn_edit_{cid}_limit")],
        [InlineKeyboardButton(text="✏️ Editar limite/usuário", callback_data=f"cpn_edit_{cid}_peruser")],
        [InlineKeyboardButton(
            text=("🔴 Desativar" if c.is_active else "🟢 Ativar"),
            callback_data=f"cpn_toggle_{cid}")],
        [InlineKeyboardButton(text="📜 Histórico de uso", callback_data=f"cpn_usage_coupon_{cid}_0")],
        [InlineKeyboardButton(text="🗑️ Excluir cupom", callback_data=f"cpn_delete_{cid}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="cpn_list_all_0")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("cpn_view_"))
async def view_coupon(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    cid = int(cb.data.split("_")[-1])
    text, kb = await render_coupon(cid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ EDITAR CAMPO ============
EDIT_PROMPTS = {
    "value": ("Novo valor do desconto (número):", "value", float),
    "min": ("Novo valor mínimo de compra (R$, 0 = sem mínimo):", "min_purchase", float),
    "max": ("Novo desconto máximo (R$, 0 = sem limite):", "max_discount", float),
    "valid": ("Nova validade (<code>7d</code>, <code>24/12/2026</code> ou <code>/sem-limite</code>):", "valid_until", "date"),
    "limit": ("Novo limite total de utilizações (0 = ilimitado):", "usage_limit", int),
    "peruser": ("Novo limite por usuário (0 = ilimitado):", "usage_per_user", int),
}


@router.callback_query(F.data.startswith("cpn_edit_"))
async def edit_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    parts = cb.data.split("_")
    cid = int(parts[2])
    field = parts[3]
    if field not in EDIT_PROMPTS:
        await cb.answer("Campo inválido.")
        return
    prompt, _, _ = EDIT_PROMPTS[field]
    await state.update_data(coupon_id=cid, edit_field=field)
    await state.set_state(CS.edit_field)
    await cb.message.answer(f"✏️ {prompt}")
    await cb.answer()


@router.message(CS.edit_field)
async def edit_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    cid = data.get("coupon_id")
    field = data.get("edit_field")
    if not cid or not field:
        await state.clear()
        return

    _, attr, ftype = EDIT_PROMPTS[field]
    raw = m.text.strip() if m.text else ""

    if ftype == "date":
        new_val = parse_datetime_input(raw)
        if new_val is None and raw.lower() not in ("/pular", "/sem-limite", "sem-limite", "sem limite", "0"):
            await m.answer("❌ Data inválida.")
            return
    else:
        try:
            if ftype is float:
                new_val = float(raw.replace(",", "."))
                if new_val < 0:
                    raise ValueError
                if field == "max" and new_val == 0:
                    new_val = None
            else:
                new_val = int(raw)
                if new_val < 0:
                    raise ValueError
        except ValueError:
            await m.answer("❌ Valor inválido.")
            return

    async with AsyncSessionLocal() as s:
        c = await s.get(Coupon, cid)
        if not c:
            await state.clear()
            await m.answer("❌ Cupom não encontrado.")
            return

        if attr == "value" and c.type == "percentage" and isinstance(new_val, (int, float)) and new_val > 100:
            await m.answer("❌ Percentual não pode ser maior que 100%.")
            return

        setattr(c, attr, new_val)
        await s.commit()

    await log_admin(m.from_user.id, "coupon_edit", {
        "coupon_id": cid, "field": field,
        "new": str(new_val) if new_val is not None else None,
    })
    await state.clear()

    text, kb = await render_coupon(cid)
    await m.answer("✅ Campo atualizado.")
    await m.answer(text, reply_markup=kb)


# ============ TOGGLE ATIVO ============
@router.callback_query(F.data.startswith("cpn_toggle_"))
async def toggle_active(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    cid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        c = await s.get(Coupon, cid)
        if not c:
            await cb.answer("Cupom não encontrado.", show_alert=True)
            return
        c.is_active = not c.is_active
        new = c.is_active
        await s.commit()
    await log_admin(cb.from_user.id, "coupon_toggle", {"coupon_id": cid, "new": new})
    text, kb = await render_coupon(cid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Status atualizado.")


# ============ EXCLUIR ============
@router.callback_query(F.data.startswith("cpn_delete_"))
async def delete_prompt(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    cid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        c = await s.get(Coupon, cid)
        if not c:
            await cb.answer("Cupom não encontrado.", show_alert=True)
            return
        usages = (await s.execute(
            select(func.count(CouponUsage.id)).where(CouponUsage.coupon_id == cid)
        )).scalar() or 0
    await cb.message.edit_text(
        f"🗑️ EXCLUIR CUPOM\n\n"
        f"Código: <code>{c.code}</code>\n"
        f"Utilizações: {usages}\n\n"
        f"⚠️ O histórico de utilização também será removido.\n"
        f"Esta ação não pode ser desfeita.\n\n"
        f"Confirmar?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Sim, excluir", callback_data=f"cpn_delete_ok_{cid}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"cpn_view_{cid}")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cpn_delete_ok_"))
async def delete_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    cid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        c = await s.get(Coupon, cid)
        if not c:
            await cb.answer("Cupom não encontrado.", show_alert=True)
            return
        code = c.code
        usages = (await s.execute(
            select(CouponUsage).where(CouponUsage.coupon_id == cid)
        )).scalars().all()
        for u in usages:
            await s.delete(u)
        await s.delete(c)
        await s.commit()
    await log_admin(cb.from_user.id, "coupon_delete", {"coupon_id": cid, "code": code})
    await cb.message.edit_text(
        f"✅ Cupom <code>{code}</code> excluído.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Listar cupons", callback_data="cpn_list_all_0")],
            [InlineKeyboardButton(text="↩️ Menu de cupons", callback_data="admin_coupons")],
        ]),
    )
    await cb.answer()


# ============ HISTÓRICO DE USO (GLOBAL) ============
@router.callback_query(F.data.startswith("cpn_usage_"))
async def usages_global(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    data_str = cb.data.replace("cpn_usage_", "")

    # Distinguir "cpn_usage_0" (global) vs "cpn_usage_coupon_<id>_<page>" (por cupom)
    if data_str.startswith("coupon_"):
        parts = data_str.split("_")
        cid = int(parts[1])
        page = int(parts[2])
        await _render_usages(cb, page, coupon_id=cid)
        return

    page = int(data_str)
    await _render_usages(cb, page, coupon_id=None)


async def _render_usages(cb: CallbackQuery, page: int, coupon_id: int | None):
    async with AsyncSessionLocal() as s:
        stmt = select(CouponUsage)
        cnt = select(func.count(CouponUsage.id))
        if coupon_id:
            stmt = stmt.where(CouponUsage.coupon_id == coupon_id)
            cnt = cnt.where(CouponUsage.coupon_id == coupon_id)
        total = (await s.execute(cnt)).scalar() or 0
        stmt = stmt.order_by(desc(CouponUsage.used_at)).offset(page * USAGE_PAGE_SIZE).limit(USAGE_PAGE_SIZE)
        usages = (await s.execute(stmt)).scalars().all()

        users = {}
        coupons = {}
        if usages:
            uids = list({u.user_id for u in usages})
            cids = list({u.coupon_id for u in usages})
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}
            c = (await s.execute(select(Coupon).where(Coupon.id.in_(cids)))).scalars().all()
            coupons = {x.id: x for x in c}

        title = "HISTÓRICO DE UTILIZAÇÕES"
        if coupon_id:
            c = coupons.get(coupon_id)
            if c:
                title = f"USO DO CUPOM {c.code}"

        total_discount = 0.0
        if coupon_id:
            total_discount = (await s.execute(
                select(func.coalesce(func.sum(CouponUsage.discount_amount), 0))
                .where(CouponUsage.coupon_id == coupon_id)
            )).scalar() or 0.0

    text = f"📜 {title}\n\nTotal: {total} | Página {page + 1}\n"
    if coupon_id:
        text += f"💸 Desconto total concedido: R$ {total_discount:.2f}\n"
    text += "\n"

    if not usages:
        text += "Nenhuma utilização registrada."
    else:
        for u in usages:
            usr = users.get(u.user_id)
            uname = (usr.full_name if usr else f"User {u.user_id}")[:20]
            cpn = coupons.get(u.coupon_id)
            code = cpn.code if cpn else f"#{u.coupon_id}"
            text += (
                f"🎟️ <code>{code}</code> — {uname}\n"
                f"   💸 R$ {u.discount_amount:.2f} | "
                f"{u.used_at.strftime('%d/%m %H:%M')}\n"
            )
            if u.order_id:
                text += f"   🛒 Pedido #{u.order_id}\n"
            text += "\n"

    nav = []
    if coupon_id:
        prefix = f"cpn_usage_coupon_{coupon_id}"
        back_cb = f"cpn_view_{coupon_id}"
    else:
        prefix = "cpn_usage"
        back_cb = "admin_coupons"

    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"{prefix}_{page - 1}"
        ))
    if (page + 1) * USAGE_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"{prefix}_{page + 1}"
        ))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=back_cb)])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("cpn_usage_coupon_"))
async def usages_by_coupon(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    parts = cb.data.split("_")
    cid = int(parts[3])
    page = int(parts[4])
    await _render_usages(cb, page, coupon_id=cid)


# ============ BUSCA ============
@router.callback_query(F.data == "cpn_search")
async def search_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CS.search)
    await cb.message.answer(
        "🔎 BUSCAR CUPOM\n\n"
        "Envie o código (parcial ou completo) do cupom:\n\n"
        "Digite /cancelar para abortar."
    )
    await cb.answer()


@router.message(CS.search)
async def search_execute(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    q = m.text.strip().upper() if m.text else ""
    if q.lower() == "/cancelar":
        await state.clear()
        await m.answer("Operação cancelada.")
        return
    await state.clear()

    async with AsyncSessionLocal() as s:
        stmt = select(Coupon).where(
            Coupon.code.ilike(f"%{q}%")
        ).order_by(desc(Coupon.created_at)).limit(30)
        coupons = (await s.execute(stmt)).scalars().all()

    if not coupons:
        await m.answer("❌ Nenhum cupom encontrado.")
        return

    text = f"🔎 RESULTADOS: {len(coupons)} cupom(ns)\n\n"
    buttons = []
    for c in coupons:
        icon, label = coupon_status(c)
        text += f"{icon} <code>{c.code}</code> — {label}\n"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {c.code}",
            callback_data=f"cpn_view_{c.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_coupons")])
    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ============ ESTATÍSTICAS ============
@router.callback_query(F.data == "cpn_stats")
async def stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(Coupon.id)))).scalar() or 0
        active = (await s.execute(
            select(func.count(Coupon.id)).where(Coupon.is_active == True)
        )).scalar() or 0
        usages_total = (await s.execute(select(func.count(CouponUsage.id)))).scalar() or 0
        discount_total = (await s.execute(
            select(func.coalesce(func.sum(CouponUsage.discount_amount), 0))
        )).scalar() or 0.0
        usages_month = (await s.execute(
            select(func.count(CouponUsage.id))
            .where(CouponUsage.used_at >= month_start)
        )).scalar() or 0
        discount_month = (await s.execute(
            select(func.coalesce(func.sum(CouponUsage.discount_amount), 0))
            .where(CouponUsage.used_at >= month_start)
        )).scalar() or 0.0
        unique_users = (await s.execute(
            select(func.count(func.distinct(CouponUsage.user_id)))
        )).scalar() or 0

        avg_discount = discount_total / usages_total if usages_total else 0.0

        top_coupons = (await s.execute(
            select(Coupon.code, func.count(CouponUsage.id).label("cnt"),
                   func.coalesce(func.sum(CouponUsage.discount_amount), 0).label("disc"))
            .join(CouponUsage, CouponUsage.coupon_id == Coupon.id)
            .group_by(Coupon.id, Coupon.code)
            .order_by(desc("cnt"))
            .limit(5)
        )).all()

        top_users = (await s.execute(
            select(User.full_name, User.username, CouponUsage.user_id,
                   func.count(CouponUsage.id).label("cnt"),
                   func.coalesce(func.sum(CouponUsage.discount_amount), 0).label("disc"))
            .join(User, User.id == CouponUsage.user_id)
            .group_by(CouponUsage.user_id, User.full_name, User.username)
            .order_by(desc("disc"))
            .limit(5)
        )).all()

        orders_with_coupon = (await s.execute(
            select(func.count(func.distinct(CouponUsage.order_id)))
            .where(CouponUsage.order_id.isnot(None))
        )).scalar() or 0

        orders_total = (await s.execute(
            select(func.count(Order.id)).where(Order.status == "completed")
        )).scalar() or 0

    adoption = (orders_with_coupon / orders_total * 100) if orders_total > 0 else 0.0

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DE CUPONS\n\n"
        f"🎟️ Cupons criados: {total}\n"
        f"🟢 Ativos: {active}\n\n"
        f"🔁 Utilizações totais: {usages_total}\n"
        f"📅 Utilizações este mês: {usages_month}\n"
        f"👥 Usuários únicos: {unique_users}\n"
        f"🛒 Pedidos com cupom: {orders_with_coupon}\n"
        f"📈 Adoção de cupons: {adoption:.1f}%\n\n"
        f"💸 Desconto total: R$ {discount_total:.2f}\n"
        f"📅 Desconto este mês: R$ {discount_month:.2f}\n"
        f"🎯 Desconto médio por uso: R$ {avg_discount:.2f}\n\n"
    )

    if top_coupons:
        text += "🏆 TOP 5 CUPONS MAIS USADOS\n"
        for i, (code, cnt, disc) in enumerate(top_coupons, 1):
            text += f"{i}. <code>{code}</code> — {cnt} uso(s) — R$ {disc:.2f}\n"
        text += "\n"

    if top_users:
        text += "👑 TOP 5 USUÁRIOS (por desconto obtido)\n"
        for i, (name, uname, uid, cnt, disc) in enumerate(top_users, 1):
            display = name or (f"@{uname}" if uname else f"User {uid}")
            text += f"{i}. {display} — {cnt} uso(s) — R$ {disc:.2f}\n"

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="cpn_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_coupons")],
    ]))
    await cb.answer()


# ============ CONFIGURAR BÔNUS AUTOMÁTICOS ============
async def bonus_cfg_view() -> tuple[str, InlineKeyboardMarkup]:
    s = await get_settings(force=True)
    welcome_on = getattr(s, "welcome_bonus_enabled", False)
    welcome_val = getattr(s, "welcome_bonus_amount", 0.0)
    first_on = getattr(s, "first_purchase_bonus_enabled", False)
    first_pct = getattr(s, "first_purchase_bonus_percentage", 0.0)

    text = (
        "◈ BELLAMY | BÔNUS AUTOMÁTICOS\n\n"
        "Configurações de bônus que o sistema aplica automaticamente "
        "sem necessidade de cupom.\n\n"
        f"👋 <b>Bônus de boas-vindas</b>\n"
        f"Status: {'🟢 Ativado' if welcome_on else '🔴 Desativado'}\n"
        f"Valor: R$ {welcome_val:.2f}\n\n"
        f"🛍️ <b>Bônus de primeira compra</b>\n"
        f"Status: {'🟢 Ativado' if first_on else '🔴 Desativado'}\n"
        f"Percentual: {first_pct:.2f}%\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar boas-vindas" if welcome_on else "🟢 Ativar boas-vindas"),
            callback_data="cpn_bn_t_welcome")],
        [InlineKeyboardButton(text="💰 Alterar valor boas-vindas", callback_data="cpn_bn_val_welcome")],
        [InlineKeyboardButton(
            text=("🔴 Desativar primeira compra" if first_on else "🟢 Ativar primeira compra"),
            callback_data="cpn_bn_t_first")],
        [InlineKeyboardButton(text="📊 Alterar % primeira compra", callback_data="cpn_bn_val_first")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_coupons")],
    ])
    return text, kb


@router.callback_query(F.data == "cpn_bonus_cfg")
async def bonus_cfg(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    text, kb = await bonus_cfg_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "cpn_bn_t_welcome")
async def toggle_welcome(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        cur = getattr(bs, "welcome_bonus_enabled", False)
        bs.welcome_bonus_enabled = not cur
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "welcome_bonus_toggle", {"new": not cur})
    text, kb = await bonus_cfg_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "cpn_bn_t_first")
async def toggle_first(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        cur = getattr(bs, "first_purchase_bonus_enabled", False)
        bs.first_purchase_bonus_enabled = not cur
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "first_purchase_bonus_toggle", {"new": not cur})
    text, kb = await bonus_cfg_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "cpn_bn_val_welcome")
async def set_welcome_val_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.update_data(bonus_field="welcome_bonus_amount")
    await state.set_state(CS.bonus_amount)
    await cb.message.answer("Informe o valor do bônus de boas-vindas em R$ (0-1000):")
    await cb.answer()


@router.callback_query(F.data == "cpn_bn_val_first")
async def set_first_val_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.update_data(bonus_field="first_purchase_bonus_percentage")
    await state.set_state(CS.bonus_amount)
    await cb.message.answer("Informe o percentual de bônus na primeira compra (0-100):")
    await cb.answer()


@router.message(CS.bonus_amount)
async def set_bonus_val(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        v = float(m.text.strip().replace(",", "."))
        if v < 0 or v > 1000:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido (0-1000).")
        return

    data = await state.get_data()
    field = data.get("bonus_field")
    if not field:
        await state.clear()
        return

    if field == "first_purchase_bonus_percentage" and v > 100:
        await m.answer("❌ Percentual não pode ser maior que 100%.")
        return

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        setattr(bs, field, v)
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, f"bonus_{field}", {"new": v})
    await state.clear()
    text, kb = await bonus_cfg_view()
    await m.answer(f"✅ Valor atualizado: {v}", reply_markup=kb)


# ============ EXPORTAÇÃO ============
@router.callback_query(F.data == "cpn_export")
async def export_report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        coupons = (await s.execute(
            select(Coupon).order_by(desc(Coupon.created_at))
        )).scalars().all()

        usages = (await s.execute(
            select(CouponUsage).order_by(desc(CouponUsage.used_at)).limit(500)
        )).scalars().all()

        users = {}
        coupons_map = {c.id: c for c in coupons}
        uids = list({u.user_id for u in usages})
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}

    lines = []
    lines.append("═" * 70)
    lines.append("BELLAMY | RELATÓRIO DE CUPONS")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append(f"Cupons cadastrados: {len(coupons)}")
    lines.append(f"Utilizações (últimas 500): {len(usages)}")
    lines.append("═" * 70)
    lines.append("")

    lines.append("─" * 70)
    lines.append("CUPONS")
    lines.append("─" * 70)
    for c in coupons:
        _, status = coupon_status(c)
        tipo = "Percentual" if c.type == "percentage" else "Valor fixo"
        val = f"{c.value}%" if c.type == "percentage" else f"R$ {c.value:.2f}"
        valid_until = c.valid_until.strftime("%d/%m/%Y") if c.valid_until else "não expira"
        limit = c.usage_limit if c.usage_limit > 0 else "ilimitado"
        lines.append(
            f"#{c.id:<5} {c.code:<15} | {tipo:<12} | {val:<10} | "
            f"usos: {c.used_count}/{limit} | validade: {valid_until} | {status}"
        )
    lines.append("")

    lines.append("─" * 70)
    lines.append("ÚLTIMAS 500 UTILIZAÇÕES")
    lines.append("─" * 70)
    for u in usages:
        usr = users.get(u.user_id)
        uname = usr.full_name if usr else "Desconhecido"
        uhandle = f"@{usr.username}" if usr and usr.username else "—"
        cpn = coupons_map.get(u.coupon_id)
        code = cpn.code if cpn else f"#{u.coupon_id}"
        lines.append(
            f"{u.used_at.strftime('%d/%m/%Y %H:%M')} | "
            f"{code:<15} | {uname[:22]:<22} ({uhandle[:15]:<15}) | "
            f"R$ {u.discount_amount:>8.2f} | Pedido #{u.order_id or '—'}"
        )

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"cupons_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption=(
            f"📊 <b>Relatório de Cupons</b>\n\n"
            f"🎟️ Cupons: {len(coupons)}\n"
            f"🔁 Utilizações: {len(usages)}"
        ),
    )
    await cb.answer("Relatório gerado.")
