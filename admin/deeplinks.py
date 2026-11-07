from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, or_
from datetime import datetime
import random
import string

from bot import dp
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    DeepLink, DeepLinkVisit, Product, User, AdminLog, BotSettings,
)
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

PAGE_SIZE = 10
VISIT_PAGE_SIZE = 12

DEST_LABEL = {
    "menu": "🏠 Menu principal",
    "catalog": "🛍️ Catálogo",
    "product": "📦 Produto específico",
    "affiliate": "🤝 Afiliados",
    "recharge": "💎 Adicionar saldo",
    "miniapp": "📱 Mini App",
    "campaign": "🎯 Campanha personalizada",
}


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class DS(StatesGroup):
    new_name = State()
    new_destination_value = State()
    new_campaign = State()
    edit_name = State()
    search = State()


def gen_code(length: int = 8) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=length))


def build_link(bot_username: str, code: str) -> str:
    if not bot_username:
        return f"(bot_username não configurado) — payload: dl_{code}"
    return f"https://t.me/{bot_username}?start=dl_{code}"


def link_status(dl: DeepLink) -> tuple[str, str]:
    return ("🟢", "Ativo") if dl.is_active else ("🔴", "Desativado")


# ============ MENU ============
@router.callback_query(F.data == "admin_deeplinks")
async def deeplinks_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(DeepLink.id)))).scalar() or 0
        active = (await s.execute(
            select(func.count(DeepLink.id)).where(DeepLink.is_active == True)
        )).scalar() or 0
        total_clicks = (await s.execute(
            select(func.coalesce(func.sum(DeepLink.clicks), 0))
        )).scalar() or 0
        total_unique = (await s.execute(
            select(func.coalesce(func.sum(DeepLink.unique_users), 0))
        )).scalar() or 0
        total_regs = (await s.execute(
            select(func.coalesce(func.sum(DeepLink.registrations), 0))
        )).scalar() or 0
        total_purch = (await s.execute(
            select(func.coalesce(func.sum(DeepLink.purchases), 0))
        )).scalar() or 0
        total_rev = (await s.execute(
            select(func.coalesce(func.sum(DeepLink.revenue), 0))
        )).scalar() or 0.0

    settings = await get_settings()
    bot_username = getattr(settings, "bot_username", None)

    conv_reg = (total_regs / total_unique * 100) if total_unique else 0.0
    conv_buy = (total_purch / total_unique * 100) if total_unique else 0.0

    text = (
        "◈ BELLAMY | DEEP LINKS\n\n"
        f"🔗 Links criados: {total}\n"
        f"🟢 Ativos: {active}\n\n"
        f"👁️ Cliques totais: {total_clicks}\n"
        f"👥 Usuários únicos: {total_unique}\n"
        f"📝 Cadastros originados: {total_regs}\n"
        f"🛒 Compras originadas: {total_purch}\n"
        f"💰 Receita atribuída: R$ {total_rev:.2f}\n\n"
        f"📈 Conversão em cadastro: {conv_reg:.1f}%\n"
        f"📈 Conversão em compra: {conv_buy:.1f}%\n\n"
        f"🤖 Bot: @{bot_username if bot_username else 'não configurado'}\n"
    )
    if not bot_username:
        text += "\n⚠️ Configure o username do bot em Configurações para gerar links válidos."

    text += "\n\n👇 Selecione uma opção:"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Criar deep link", callback_data="dl_new")],
        [InlineKeyboardButton(text=f"📋 Listar todos ({total})", callback_data="dl_list_all_0")],
        [InlineKeyboardButton(text="🟢 Apenas ativos", callback_data="dl_list_active_0")],
        [InlineKeyboardButton(text="🔴 Desativados", callback_data="dl_list_inactive_0")],
        [InlineKeyboardButton(text="🔎 Buscar link", callback_data="dl_search")],
        [InlineKeyboardButton(text="📜 Histórico de visitas", callback_data="dl_visits_0")],
        [InlineKeyboardButton(text="📊 Estatísticas comparativas", callback_data="dl_stats")],
        [InlineKeyboardButton(text="📥 Exportar relatório", callback_data="dl_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ LISTAGEM ============
@router.callback_query(F.data.startswith("dl_list_"))
async def list_links(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    parts = cb.data.split("_")
    ftype = parts[2]
    page = int(parts[3])

    async with AsyncSessionLocal() as s:
        stmt = select(DeepLink)
        cnt = select(func.count(DeepLink.id))
        if ftype == "active":
            stmt = stmt.where(DeepLink.is_active == True)
            cnt = cnt.where(DeepLink.is_active == True)
        elif ftype == "inactive":
            stmt = stmt.where(DeepLink.is_active == False)
            cnt = cnt.where(DeepLink.is_active == False)

        total = (await s.execute(cnt)).scalar() or 0
        stmt = stmt.order_by(desc(DeepLink.created_at)).offset(page * PAGE_SIZE).limit(PAGE_SIZE)
        links = (await s.execute(stmt)).scalars().all()

    titles = {
        "all": "TODOS OS DEEP LINKS",
        "active": "DEEP LINKS ATIVOS",
        "inactive": "DEEP LINKS DESATIVADOS",
    }
    text = f"◈ BELLAMY | {titles.get(ftype, ftype.upper())}\n\n"
    text += f"Total: {total} | Página {page + 1}\n\n"

    buttons = []
    if not links:
        text += "Nenhum deep link encontrado."
    else:
        for l in links:
            icon, _ = link_status(l)
            dest = DEST_LABEL.get(l.destination_type, l.destination_type)
            text += (
                f"{icon} <b>{l.name}</b> — <code>{l.code}</code>\n"
                f"   {dest} | 👁️ {l.clicks} | 👥 {l.unique_users} | "
                f"📝 {l.registrations} | 🛒 {l.purchases}\n\n"
            )
            buttons.append([InlineKeyboardButton(
                text=f"{icon} {l.name[:30]} — {l.clicks} cliques",
                callback_data=f"dl_view_{l.id}",
            )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"dl_list_{ftype}_{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"dl_list_{ftype}_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_deeplinks")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============ CRIAR DEEP LINK ============
@router.callback_query(F.data == "dl_new")
async def new_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(DS.new_name)
    await state.update_data(new={})
    await cb.message.answer(
        "🔗 CRIAR NOVO DEEP LINK (1/3)\n\n"
        "Informe o <b>nome interno</b> do link (para identificação):\n\n"
        "Ex.: <i>Campanha de Natal</i>, <i>Divulgação Instagram</i>, <i>Parceria XYZ</i>"
    )
    await cb.answer()


@router.message(DS.new_name)
async def new_name(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    name = m.text.strip() if m.text else ""
    if len(name) < 2 or len(name) > 100:
        await m.answer("❌ O nome deve ter entre 2 e 100 caracteres.")
        return

    data = await state.get_data()
    data["new"]["name"] = name
    await state.update_data(new=data["new"])
    await state.set_state(None)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Menu principal", callback_data="dl_dest_menu")],
        [InlineKeyboardButton(text="🛍️ Catálogo", callback_data="dl_dest_catalog")],
        [InlineKeyboardButton(text="📦 Produto específico", callback_data="dl_dest_product")],
        [InlineKeyboardButton(text="🤝 Afiliados", callback_data="dl_dest_affiliate")],
        [InlineKeyboardButton(text="💎 Adicionar saldo", callback_data="dl_dest_recharge")],
        [InlineKeyboardButton(text="📱 Mini App", callback_data="dl_dest_miniapp")],
        [InlineKeyboardButton(text="🎯 Campanha personalizada", callback_data="dl_dest_campaign")],
        [InlineKeyboardButton(text="❌ Cancelar", callback_data="dl_cancel_new")],
    ])
    await m.answer(
        f"🔗 CRIAR NOVO DEEP LINK (2/3)\n\n"
        f"Nome: <b>{name}</b>\n\n"
        f"Selecione o <b>destino</b> do link:",
        reply_markup=kb,
    )


@router.callback_query(F.data == "dl_cancel_new")
async def cancel_new(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Criação de deep link cancelada.")
    await cb.answer()


@router.callback_query(F.data.startswith("dl_dest_"))
async def new_destination(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    dest = cb.data.replace("dl_dest_", "")
    if dest not in DEST_LABEL:
        await cb.answer("Destino inválido.")
        return

    data = await state.get_data()
    data["new"]["destination_type"] = dest
    await state.update_data(new=data["new"])

    if dest == "product":
        await cb.message.edit_text(
            "🔗 CRIAR NOVO DEEP LINK (2.1/3)\n\n"
            "Envie o <b>ID do produto</b> que o link deve abrir:"
        )
        await state.set_state(DS.new_destination_value)
        await cb.answer()
        return

    if dest == "campaign":
        await cb.message.edit_text(
            "🔗 CRIAR NOVO DEEP LINK (2.1/3)\n\n"
            "Informe o <b>identificador da campanha</b> "
            "(ex.: <code>instagram_nov2026</code>):"
        )
        await state.set_state(DS.new_campaign)
        await cb.answer()
        return

    # Outros destinos não precisam de valor
    data["new"]["destination_value"] = None
    await state.update_data(new=data["new"])
    await _finalize_create(cb, state)


@router.message(DS.new_destination_value)
async def new_product_id(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if not raw.isdigit():
        await m.answer("❌ Envie apenas o número do ID do produto.")
        return
    pid = int(raw)
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
    if not p:
        await m.answer(f"❌ Produto #{pid} não encontrado.")
        return

    data = await state.get_data()
    data["new"]["destination_value"] = str(pid)
    await state.update_data(new=data["new"])
    await _finalize_create(m, state)


@router.message(DS.new_campaign)
async def new_campaign_val(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    val = m.text.strip() if m.text else ""
    if len(val) < 2 or len(val) > 64:
        await m.answer("❌ Use entre 2 e 64 caracteres.")
        return
    if not all(c.isalnum() or c in "-_" for c in val):
        await m.answer("❌ Use apenas letras, números, hífen e underline.")
        return
    data = await state.get_data()
    data["new"]["destination_value"] = val
    data["new"]["campaign"] = val
    await state.update_data(new=data["new"])
    await _finalize_create(m, state)


async def _finalize_create(event, state: FSMContext):
    data = await state.get_data()
    n = data.get("new", {})
    await state.clear()

    if not n.get("name") or not n.get("destination_type"):
        msg = "❌ Dados incompletos. Operação cancelada."
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(msg)
            await event.answer()
        else:
            await event.answer(msg)
        return

    # Gerar código único
    async with AsyncSessionLocal() as s:
        for _ in range(10):
            code = gen_code(8)
            exists = (await s.execute(
                select(DeepLink).where(DeepLink.code == code)
            )).scalar_one_or_none()
            if not exists:
                break
        else:
            code = gen_code(12)

        user_id = event.from_user.id
        link = DeepLink(
            name=n["name"],
            code=code,
            destination_type=n["destination_type"],
            destination_value=n.get("destination_value"),
            campaign=n.get("campaign"),
            is_active=True,
            created_by=user_id,
        )
        s.add(link)
        await s.commit()
        await s.refresh(link)
        lid = link.id

    await log_admin(user_id, "deeplink_create", {
        "deep_link_id": lid, "code": code, "destination": n["destination_type"],
    })

    settings = await get_settings()
    bot_username = getattr(settings, "bot_username", None)
    url = build_link(bot_username, code)

    dest_label = DEST_LABEL.get(n["destination_type"], n["destination_type"])
    extra = ""
    if n.get("destination_value"):
        if n["destination_type"] == "product":
            extra = f"\n📦 Produto ID: {n['destination_value']}"
        else:
            extra = f"\n🎯 Identificador: <code>{n['destination_value']}</code>"

    text = (
        f"✅ <b>DEEP LINK CRIADO</b>\n\n"
        f"🔗 Nome: {n['name']}\n"
        f"📌 Código: <code>{code}</code>\n"
        f"🎯 Destino: {dest_label}{extra}\n\n"
        f"🌐 <b>URL do link</b>\n"
        f"<code>{url}</code>\n\n"
        f"📋 <b>Payload do /start</b>\n"
        f"<code>dl_{code}</code>\n"
        f"({len('dl_' + code)} de 64 caracteres permitidos)"
    )
    if not bot_username:
        text += "\n\n⚠️ Configure o username do bot em Configurações para gerar a URL completa."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Copiar URL", callback_data=f"dl_copy_{lid}")],
        [InlineKeyboardButton(text="➕ Criar outro", callback_data="dl_new")],
        [InlineKeyboardButton(text="👁️ Ver link", callback_data=f"dl_view_{lid}")],
        [InlineKeyboardButton(text="📋 Listar links", callback_data="dl_list_all_0")],
        [InlineKeyboardButton(text="↩️ Menu de deep links", callback_data="admin_deeplinks")],
    ])

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb)


# ============ DETALHE DO LINK ============
async def render_link(lid: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as s:
        l = await s.get(DeepLink, lid)
        if not l:
            return (
                "❌ Deep link não encontrado.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_deeplinks")]
                ]),
            )
        today = datetime.utcnow().date()
        visits_today = (await s.execute(
            select(func.count(DeepLinkVisit.id))
            .where(DeepLinkVisit.deep_link_id == lid)
            .where(DeepLinkVisit.visited_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0
        last_visit = (await s.execute(
            select(DeepLinkVisit)
            .where(DeepLinkVisit.deep_link_id == lid)
            .order_by(desc(DeepLinkVisit.visited_at))
            .limit(1)
        )).scalar_one_or_none()

    settings = await get_settings()
    bot_username = getattr(settings, "bot_username", None)
    url = build_link(bot_username, l.code)

    icon, label = link_status(l)
    dest = DEST_LABEL.get(l.destination_type, l.destination_type)

    conv_reg = (l.registrations / l.unique_users * 100) if l.unique_users else 0.0
    conv_buy = (l.purchases / l.unique_users * 100) if l.unique_users else 0.0
    rev_per_user = (l.revenue / l.unique_users) if l.unique_users else 0.0

    text = (
        f"◈ BELLAMY | DEEP LINK #{l.id}\n\n"
        f"🔗 Nome: <b>{l.name}</b>\n"
        f"📌 Status: {icon} {label}\n"
        f"🔑 Código: <code>{l.code}</code>\n"
        f"🎯 Destino: {dest}\n"
    )
    if l.destination_value:
        text += f"📎 Valor: <code>{l.destination_value}</code>\n"
    if l.campaign:
        text += f"🎯 Campanha: <code>{l.campaign}</code>\n"
    text += f"📅 Criado em: {l.created_at.strftime('%d/%m/%Y %H:%M')}\n"
    if l.created_by:
        text += f"👤 Criado por: Admin <code>{l.created_by}</code>\n"

    text += (
        f"\n🌐 <b>URL</b>\n<code>{url}</code>\n\n"
        f"📋 <b>Payload</b>\n<code>dl_{l.code}</code>\n\n"
        f"📊 <b>Estatísticas</b>\n"
        f"👁️ Cliques: {l.clicks}\n"
        f"👥 Usuários únicos: {l.unique_users}\n"
        f"📝 Cadastros: {l.registrations}\n"
        f"🛒 Compras: {l.purchases}\n"
        f"💰 Receita: R$ {l.revenue:.2f}\n"
        f"📅 Visitas hoje: {visits_today}\n"
    )
    if last_visit:
        text += f"🕒 Última visita: {last_visit.visited_at.strftime('%d/%m/%Y %H:%M')}\n"

    text += (
        f"\n📈 Conversão cadastro: {conv_reg:.1f}%\n"
        f"📈 Conversão compra: {conv_buy:.1f}%\n"
        f"💎 Receita por usuário: R$ {rev_per_user:.2f}"
    )

    buttons = [
        [InlineKeyboardButton(text="📋 Copiar URL", callback_data=f"dl_copy_{l.id}")],
        [InlineKeyboardButton(text="📊 Ver estatísticas detalhadas", callback_data=f"dl_stats_link_{l.id}")],
        [InlineKeyboardButton(text="📜 Ver visitas", callback_data=f"dl_visits_link_{l.id}_0")],
        [InlineKeyboardButton(text="✏️ Editar nome", callback_data=f"dl_edit_{l.id}_name")],
        [InlineKeyboardButton(text="✏️ Editar destino", callback_data=f"dl_edit_{l.id}_dest")],
        [InlineKeyboardButton(
            text=("🔴 Desativar" if l.is_active else "🟢 Ativar"),
            callback_data=f"dl_toggle_{l.id}")],
        [InlineKeyboardButton(text="🔄 Resetar estatísticas", callback_data=f"dl_reset_{l.id}")],
        [InlineKeyboardButton(text="🗑️ Excluir link", callback_data=f"dl_delete_{l.id}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="dl_list_all_0")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("dl_view_"))
async def view_link(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    lid = int(cb.data.split("_")[-1])
    text, kb = await render_link(lid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============ COPIAR URL ============
@router.callback_query(F.data.startswith("dl_copy_"))
async def copy_url(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    lid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        l = await s.get(DeepLink, lid)
        if not l:
            await cb.answer("Link não encontrado.", show_alert=True)
            return

    settings = await get_settings()
    bot_username = getattr(settings, "bot_username", None)
    url = build_link(bot_username, l.code)

    await cb.message.answer(
        f"📋 <b>URL do link</b>\n\n<code>{url}</code>\n\n"
        f"Toque no texto acima para copiar.",
    )
    await cb.answer("URL enviada.")


# ============ EDITAR NOME ============
@router.callback_query(F.data.startswith("dl_edit_"))
async def edit_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    parts = cb.data.split("_")
    lid = int(parts[2])
    field = parts[3]

    if field == "name":
        await state.update_data(edit_link_id=lid, edit_field="name")
        await state.set_state(DS.edit_name)
        await cb.message.answer("✏️ Envie o novo nome do deep link:")
        await cb.answer()
        return

    if field == "dest":
        await state.update_data(edit_link_id=lid, edit_field="dest")
        await state.set_state(None)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Menu principal", callback_data=f"dl_editdest_menu_{lid}")],
            [InlineKeyboardButton(text="🛍️ Catálogo", callback_data=f"dl_editdest_catalog_{lid}")],
            [InlineKeyboardButton(text="📦 Produto específico", callback_data=f"dl_editdest_product_{lid}")],
            [InlineKeyboardButton(text="🤝 Afiliados", callback_data=f"dl_editdest_affiliate_{lid}")],
            [InlineKeyboardButton(text="💎 Adicionar saldo", callback_data=f"dl_editdest_recharge_{lid}")],
            [InlineKeyboardButton(text="📱 Mini App", callback_data=f"dl_editdest_miniapp_{lid}")],
            [InlineKeyboardButton(text="🎯 Campanha", callback_data=f"dl_editdest_campaign_{lid}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"dl_view_{lid}")],
        ])
        await cb.message.edit_text(
            f"✏️ EDITAR DESTINO\n\nSelecione o novo destino do link #{lid}:",
            reply_markup=kb,
        )
        await cb.answer()


@router.message(DS.edit_name)
async def edit_name_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    name = m.text.strip() if m.text else ""
    if len(name) < 2 or len(name) > 100:
        await m.answer("❌ Nome inválido (2-100 caracteres).")
        return

    data = await state.get_data()
    lid = data.get("edit_link_id")
    if not lid:
        await state.clear()
        return

    async with AsyncSessionLocal() as s:
        l = await s.get(DeepLink, lid)
        if not l:
            await state.clear()
            await m.answer("❌ Link não encontrado.")
            return
        old_name = l.name
        l.name = name
        await s.commit()

    await log_admin(m.from_user.id, "deeplink_edit_name", {
        "deep_link_id": lid, "old": old_name, "new": name,
    })
    await state.clear()

    text, kb = await render_link(lid)
    await m.answer(f"✅ Nome atualizado: <b>{name}</b>")
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("dl_editdest_"))
async def edit_dest(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    parts = cb.data.split("_")
    dest_type = parts[2]
    lid = int(parts[3])
    if dest_type not in DEST_LABEL:
        await cb.answer("Destino inválido.")
        return

    if dest_type == "product":
        await state.update_data(edit_link_id=lid, edit_dest_type="product")
        await state.set_state(DS.new_destination_value)
        await cb.message.answer("✏️ Envie o ID do novo produto:")
        await cb.answer()
        return

    if dest_type == "campaign":
        await state.update_data(edit_link_id=lid, edit_dest_type="campaign")
        await state.set_state(DS.new_campaign)
        await cb.message.answer("✏️ Informe o novo identificador da campanha:")
        await cb.answer()
        return

    async with AsyncSessionLocal() as s:
        l = await s.get(DeepLink, lid)
        if not l:
            await cb.answer("Link não encontrado.", show_alert=True)
            return
        old_dest = l.destination_type
        l.destination_type = dest_type
        l.destination_value = None
        l.campaign = None
        await s.commit()

    await log_admin(cb.from_user.id, "deeplink_edit_destination", {
        "deep_link_id": lid, "old": old_dest, "new": dest_type,
    })

    text, kb = await render_link(lid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Destino atualizado.")


# Intercepta edição de produto/campanha (reaproveitando os estados anteriores)
@router.message(DS.new_destination_value)
async def edit_or_create_product(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    edit_lid = data.get("edit_link_id")

    raw = m.text.strip() if m.text else ""
    if not raw.isdigit():
        await m.answer("❌ Envie apenas o número do ID do produto.")
        return
    pid = int(raw)
    async with AsyncSessionLocal() as s:
        p = await s.get(Product, pid)
    if not p:
        await m.answer(f"❌ Produto #{pid} não encontrado.")
        return

    if edit_lid:
        async with AsyncSessionLocal() as s:
            l = await s.get(DeepLink, edit_lid)
            l.destination_type = "product"
            l.destination_value = str(pid)
            l.campaign = None
            await s.commit()
        await log_admin(m.from_user.id, "deeplink_edit_destination", {
            "deep_link_id": edit_lid, "new": "product", "value": str(pid),
        })
        await state.clear()
        text, kb = await render_link(edit_lid)
        await m.answer("✅ Destino atualizado.")
        await m.answer(text, reply_markup=kb)
        return

    data["new"]["destination_value"] = str(pid)
    await state.update_data(new=data["new"])
    await _finalize_create(m, state)


@router.message(DS.new_campaign)
async def edit_or_create_campaign(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    edit_lid = data.get("edit_link_id")

    val = m.text.strip() if m.text else ""
    if len(val) < 2 or len(val) > 64:
        await m.answer("❌ Use entre 2 e 64 caracteres.")
        return
    if not all(c.isalnum() or c in "-_" for c in val):
        await m.answer("❌ Use apenas letras, números, hífen e underline.")
        return

    if edit_lid:
        async with AsyncSessionLocal() as s:
            l = await s.get(DeepLink, edit_lid)
            l.destination_type = "campaign"
            l.destination_value = val
            l.campaign = val
            await s.commit()
        await log_admin(m.from_user.id, "deeplink_edit_destination", {
            "deep_link_id": edit_lid, "new": "campaign", "value": val,
        })
        await state.clear()
        text, kb = await render_link(edit_lid)
        await m.answer("✅ Destino atualizado.")
        await m.answer(text, reply_markup=kb)
        return

    data["new"]["destination_value"] = val
    data["new"]["campaign"] = val
    await state.update_data(new=data["new"])
    await _finalize_create(m, state)


# ============ TOGGLE / RESET / DELETE ============
@router.callback_query(F.data.startswith("dl_toggle_"))
async def toggle_link(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    lid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        l = await s.get(DeepLink, lid)
        if not l:
            await cb.answer("Link não encontrado.", show_alert=True)
            return
        l.is_active = not l.is_active
        new = l.is_active
        await s.commit()
    await log_admin(cb.from_user.id, "deeplink_toggle", {"deep_link_id": lid, "new": new})
    text, kb = await render_link(lid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Status atualizado.")


@router.callback_query(F.data.startswith("dl_reset_"))
async def reset_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    lid = int(cb.data.split("_")[-1])
    await cb.message.edit_text(
        f"🔄 RESETAR ESTATÍSTICAS\n\n"
        f"Todos os contadores do link #{lid} serão zerados.\n"
        f"O histórico de visitas permanecerá acessível.\n\n"
        f"Confirmar?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Sim, resetar", callback_data=f"dl_reset_ok_{lid}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"dl_view_{lid}")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("dl_reset_ok_"))
async def reset_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    lid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        l = await s.get(DeepLink, lid)
        if not l:
            await cb.answer("Link não encontrado.", show_alert=True)
            return
        l.clicks = 0
        l.unique_users = 0
        l.registrations = 0
        l.purchases = 0
        l.revenue = 0.0
        await s.commit()
    await log_admin(cb.from_user.id, "deeplink_reset_stats", {"deep_link_id": lid})
    text, kb = await render_link(lid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("✅ Estatísticas resetadas.")


@router.callback_query(F.data.startswith("dl_delete_"))
async def delete_prompt(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    lid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        l = await s.get(DeepLink, lid)
        if not l:
            await cb.answer("Link não encontrado.", show_alert=True)
            return
    await cb.message.edit_text(
        f"🗑️ EXCLUIR DEEP LINK\n\n"
        f"🔗 <b>{l.name}</b> (<code>{l.code}</code>)\n\n"
        f"⚠️ O link deixará de funcionar imediatamente.\n"
        f"O histórico de visitas será mantido.\n\n"
        f"Confirmar exclusão?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Sim, excluir", callback_data=f"dl_delete_ok_{lid}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"dl_view_{lid}")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("dl_delete_ok_"))
async def delete_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    lid = int(cb.data.split("_")[-1])
    async with AsyncSessionLocal() as s:
        l = await s.get(DeepLink, lid)
        if not l:
            await cb.answer("Link não encontrado.", show_alert=True)
            return
        name = l.name
        code = l.code
        await s.delete(l)
        await s.commit()
    await log_admin(cb.from_user.id, "deeplink_delete", {
        "deep_link_id": lid, "code": code,
    })
    await cb.message.edit_text(
        f"✅ Deep link <b>{name}</b> (<code>{code}</code>) excluído.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Listar links", callback_data="dl_list_all_0")],
            [InlineKeyboardButton(text="↩️ Menu de deep links", callback_data="admin_deeplinks")],
        ]),
    )
    await cb.answer()


# ============ BUSCA ============
@router.callback_query(F.data == "dl_search")
async def search_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(DS.search)
    await cb.message.answer(
        "🔎 BUSCAR DEEP LINK\n\n"
        "Envie o nome, código ou campanha:\n\n"
        "Digite /cancelar para abortar."
    )
    await cb.answer()


@router.message(DS.search)
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
        stmt = (
            select(DeepLink)
            .where(or_(
                DeepLink.name.ilike(f"%{q}%"),
                DeepLink.code.ilike(f"%{q}%"),
                DeepLink.campaign.ilike(f"%{q}%"),
            ))
            .order_by(desc(DeepLink.created_at))
            .limit(30)
        )
        links = (await s.execute(stmt)).scalars().all()

    if not links:
        await m.answer("❌ Nenhum link encontrado.")
        return

    text = f"🔎 RESULTADOS: {len(links)} link(s)\n\n"
    buttons = []
    for l in links:
        icon, _ = link_status(l)
        dest = DEST_LABEL.get(l.destination_type, l.destination_type)
        text += f"{icon} <b>{l.name}</b> — <code>{l.code}</code> — {dest}\n"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {l.name[:30]}",
            callback_data=f"dl_view_{l.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_deeplinks")])
    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ============ HISTÓRICO DE VISITAS ============
@router.callback_query(F.data.startswith("dl_visits_"))
async def visits_global(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    data_str = cb.data.replace("dl_visits_", "")

    if data_str.startswith("link_"):
        parts = data_str.split("_")
        lid = int(parts[1])
        page = int(parts[2])
        await _render_visits(cb, page, link_id=lid)
        return

    page = int(data_str)
    await _render_visits(cb, page, link_id=None)


async def _render_visits(cb: CallbackQuery, page: int, link_id: int | None):
    async with AsyncSessionLocal() as s:
        stmt = select(DeepLinkVisit)
        cnt = select(func.count(DeepLinkVisit.id))
        if link_id:
            stmt = stmt.where(DeepLinkVisit.deep_link_id == link_id)
            cnt = cnt.where(DeepLinkVisit.deep_link_id == link_id)
        total = (await s.execute(cnt)).scalar() or 0
        stmt = stmt.order_by(desc(DeepLinkVisit.visited_at)).offset(page * VISIT_PAGE_SIZE).limit(VISIT_PAGE_SIZE)
        visits = (await s.execute(stmt)).scalars().all()

        users, links = {}, {}
        if visits:
            uids = list({v.user_id for v in visits})
            lids = list({v.deep_link_id for v in visits})
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}
            l = (await s.execute(select(DeepLink).where(DeepLink.id.in_(lids)))).scalars().all()
            links = {x.id: x for x in l}

    title = "HISTÓRICO DE VISITAS"
    if link_id:
        dl = links.get(link_id)
        if dl:
            title = f"VISITAS — {dl.name}"

    text = f"📜 {title}\n\nTotal: {total} | Página {page + 1}\n\n"

    if not visits:
        text += "Nenhuma visita registrada."
    else:
        for v in visits:
            usr = users.get(v.user_id)
            uname = (usr.full_name if usr else f"User {v.user_id}")[:22]
            cpn = links.get(v.deep_link_id)
            code = cpn.code if cpn else f"#{v.deep_link_id}"
            flags = []
            if v.is_new_user:
                flags.append("🆕 novo")
            if v.converted:
                flags.append("🛒 converteu")
            flag_str = (" | " + " | ".join(flags)) if flags else ""
            text += (
                f"👤 {uname} (<code>{v.user_id}</code>)\n"
                f"   🔗 <code>{code}</code> — {v.visited_at.strftime('%d/%m %H:%M')}{flag_str}\n\n"
            )

    nav = []
    if link_id:
        prefix = f"dl_visits_link_{link_id}"
        back_cb = f"dl_view_{link_id}"
    else:
        prefix = "dl_visits"
        back_cb = "admin_deeplinks"

    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"{prefix}_{page - 1}"
        ))
    if (page + 1) * VISIT_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"{prefix}_{page + 1}"
        ))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=back_cb)])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("dl_visits_link_"))
async def visits_by_link(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    parts = cb.data.split("_")
    lid = int(parts[3])
    page = int(parts[4])
    await _render_visits(cb, page, link_id=lid)


# ============ ESTATÍSTICAS ============
@router.callback_query(F.data == "dl_stats")
async def stats_global(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(DeepLink.id)))).scalar() or 0
        total_clicks = (await s.execute(
            select(func.coalesce(func.sum(DeepLink.clicks), 0))
        )).scalar() or 0
        total_unique = (await s.execute(
            select(func.coalesce(func.sum(DeepLink.unique_users), 0))
        )).scalar() or 0
        total_regs = (await s.execute(
            select(func.coalesce(func.sum(DeepLink.registrations), 0))
        )).scalar() or 0
        total_purch = (await s.execute(
            select(func.coalesce(func.sum(DeepLink.purchases), 0))
        )).scalar() or 0
        total_rev = (await s.execute(
            select(func.coalesce(func.sum(DeepLink.revenue), 0))
        )).scalar() or 0.0

        top_links = (await s.execute(
            select(DeepLink).order_by(desc(DeepLink.clicks)).limit(5)
        )).scalars().all()
        top_rev = (await s.execute(
            select(DeepLink).where(DeepLink.revenue > 0).order_by(desc(DeepLink.revenue)).limit(5)
        )).scalars().all()

        # Distribuição por destino
        dist = (await s.execute(
            select(DeepLink.destination_type, func.count(DeepLink.id).label("cnt"))
            .group_by(DeepLink.destination_type)
            .order_by(desc("cnt"))
        )).all()

        today = datetime.utcnow().date()
        visits_today = (await s.execute(
            select(func.count(DeepLinkVisit.id))
            .where(DeepLinkVisit.visited_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0
        unique_today = (await s.execute(
            select(func.count(func.distinct(DeepLinkVisit.user_id)))
            .where(DeepLinkVisit.visited_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0

    conv_reg = (total_regs / total_unique * 100) if total_unique else 0.0
    conv_buy = (total_purch / total_unique * 100) if total_unique else 0.0
    avg_rev = (total_rev / total_purch) if total_purch else 0.0

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DE DEEP LINKS\n\n"
        f"🔗 Total de links: {total}\n"
        f"👁️ Cliques: {total_clicks}\n"
        f"👥 Usuários únicos: {total_unique}\n"
        f"📝 Cadastros: {total_regs}\n"
        f"🛒 Compras: {total_purch}\n"
        f"💰 Receita total: R$ {total_rev:.2f}\n\n"
        f"📈 Conversão cadastro: {conv_reg:.1f}%\n"
        f"📈 Conversão compra: {conv_buy:.1f}%\n"
        f"💎 Ticket médio: R$ {avg_rev:.2f}\n\n"
        f"📅 Hoje: {visits_today} visitas | {unique_today} únicos\n\n"
    )

    if top_links:
        text += "🏆 TOP 5 POR CLIQUES\n"
        for i, l in enumerate(top_links, 1):
            text += f"{i}. {l.name[:25]} — {l.clicks} cliques | {l.unique_users} únicos\n"
        text += "\n"

    if top_rev:
        text += "💰 TOP 5 POR RECEITA\n"
        for i, l in enumerate(top_rev, 1):
            text += f"{i}. {l.name[:25]} — R$ {l.revenue:.2f} | {l.purchases} compras\n"
        text += "\n"

    if dist:
        text += "🎯 DISTRIBUIÇÃO POR DESTINO\n"
        for dest_type, cnt in dist:
            label = DEST_LABEL.get(dest_type, dest_type)
            text += f"• {label}: {cnt} link(s)\n"

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="dl_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_deeplinks")],
    ]))
    await cb.answer()


@router.callback_query(F.data.startswith("dl_stats_link_"))
async def stats_by_link(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    lid = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        l = await s.get(DeepLink, lid)
        if not l:
            await cb.answer("Link não encontrado.", show_alert=True)
            return

        last_7 = datetime.utcnow() - __import__("datetime").timedelta(days=7)
        last_30 = datetime.utcnow() - __import__("datetime").timedelta(days=30)

        visits_7 = (await s.execute(
            select(func.count(DeepLinkVisit.id))
            .where(DeepLinkVisit.deep_link_id == lid)
            .where(DeepLinkVisit.visited_at >= last_7)
        )).scalar() or 0
        visits_30 = (await s.execute(
            select(func.count(DeepLinkVisit.id))
            .where(DeepLinkVisit.deep_link_id == lid)
            .where(DeepLinkVisit.visited_at >= last_30)
        )).scalar() or 0
        regs_from_link = (await s.execute(
            select(func.count(DeepLinkVisit.id))
            .where(DeepLinkVisit.deep_link_id == lid)
            .where(DeepLinkVisit.is_new_user == True)
        )).scalar() or 0
        converted = (await s.execute(
            select(func.count(DeepLinkVisit.id))
            .where(DeepLinkVisit.deep_link_id == lid)
            .where(DeepLinkVisit.converted == True)
        )).scalar() or 0

    conv_reg = (l.registrations / l.unique_users * 100) if l.unique_users else 0.0
    conv_buy = (l.purchases / l.unique_users * 100) if l.unique_users else 0.0
    avg_rev = (l.revenue / l.purchases) if l.purchases else 0.0

    text = (
        f"◈ BELLAMY | ESTATÍSTICAS — {l.name}\n\n"
        f"🔑 <code>{l.code}</code>\n\n"
        f"📊 TOTAIS\n"
        f"👁️ Cliques: {l.clicks}\n"
        f"👥 Usuários únicos: {l.unique_users}\n"
        f"📝 Cadastros: {l.registrations}\n"
        f"🛒 Compras: {l.purchases}\n"
        f"💰 Receita: R$ {l.revenue:.2f}\n\n"
        f"📅 Últimos 7 dias: {visits_7} visitas\n"
        f"📅 Últimos 30 dias: {visits_30} visitas\n"
        f"🆕 Novos usuários: {regs_from_link}\n"
        f"✅ Convertidos: {converted}\n\n"
        f"📈 Conversão cadastro: {conv_reg:.1f}%\n"
        f"📈 Conversão compra: {conv_buy:.1f}%\n"
        f"💎 Ticket médio: R$ {avg_rev:.2f}"
    )

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Ver visitas", callback_data=f"dl_visits_link_{lid}_0")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"dl_view_{lid}")],
    ]))
    await cb.answer()


# ============ EXPORTAÇÃO ============
@router.callback_query(F.data == "dl_export")
async def export_report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        links = (await s.execute(
            select(DeepLink).order_by(desc(DeepLink.clicks))
        )).scalars().all()

        recent_visits = (await s.execute(
            select(DeepLinkVisit).order_by(desc(DeepLinkVisit.visited_at)).limit(500)
        )).scalars().all()

        users = {}
        uids = list({v.user_id for v in recent_visits})
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}

        links_map = {l.id: l for l in links}

    lines = []
    lines.append("═" * 70)
    lines.append("BELLAMY | RELATÓRIO DE DEEP LINKS")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append(f"Total de links: {len(links)}")
    lines.append(f"Visitas (últimas 500): {len(recent_visits)}")
    lines.append("═" * 70)
    lines.append("")

    lines.append("─" * 70)
    lines.append("LINKS")
    lines.append("─" * 70)
    for l in links:
        dest = DEST_LABEL.get(l.destination_type, l.destination_type)
        status = "Ativo" if l.is_active else "Desativado"
        lines.append(
            f"#{l.id:<4} {l.name[:30]:<30} | {l.code:<12} | "
            f"{dest:<20} | {status:<10}"
        )
        lines.append(
            f"      cliques: {l.clicks} | únicos: {l.unique_users} | "
            f"cadastros: {l.registrations} | compras: {l.purchases} | "
            f"receita: R$ {l.revenue:.2f}"
        )
    lines.append("")

    lines.append("─" * 70)
    lines.append("ÚLTIMAS 500 VISITAS")
    lines.append("─" * 70)
    for v in recent_visits:
        usr = users.get(v.user_id)
        uname = usr.full_name if usr else "Desconhecido"
        uhandle = f"@{usr.username}" if usr and usr.username else "—"
        cpn = links_map.get(v.deep_link_id)
        code = cpn.code if cpn else f"#{v.deep_link_id}"
        flags = []
        if v.is_new_user:
            flags.append("novo")
        if v.converted:
            flags.append("converteu")
        flag_str = ",".join(flags) if flags else "-"
        lines.append(
            f"{v.visited_at.strftime('%d/%m/%Y %H:%M')} | {code:<12} | "
            f"{uname[:20]:<20} ({uhandle[:15]:<15}) | {flag_str}"
        )

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"deeplinks_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption=(
            f"📊 <b>Relatório de Deep Links</b>\n\n"
            f"🔗 Links: {len(links)}\n"
            f"👁️ Visitas (últimas 500): {len(recent_visits)}"
        ),
    )
    await cb.answer("Relatório gerado.")


# ============================================================
# HELPERS PARA O BOT DO CLIENTE
# ============================================================
async def register_deeplink_visit(code: str, user_id: int, is_new_user: bool = False) -> int | None:
    """
    Chame no /start quando o payload começar com 'dl_'.
    Retorna o ID do deep link, ou None se inválido/inativo.
    """
    async with AsyncSessionLocal() as s:
        l = (await s.execute(
            select(DeepLink).where(DeepLink.code == code)
        )).scalar_one_or_none()
        if not l or not l.is_active:
            return None

        already = (await s.execute(
            select(DeepLinkVisit)
            .where(DeepLinkVisit.deep_link_id == l.id)
            .where(DeepLinkVisit.user_id == user_id)
            .limit(1)
        )).scalar_one_or_none()

        s.add(DeepLinkVisit(
            deep_link_id=l.id,
            user_id=user_id,
            is_new_user=is_new_user,
            converted=False,
        ))
        l.clicks += 1
        if not already:
            l.unique_users += 1
        if is_new_user:
            l.registrations += 1
        await s.commit()
        return l.id


async def register_deeplink_conversion(
    deep_link_id: int, user_id: int, purchase_amount: float,
) -> None:
    """Chame quando uma compra for confirmada para um usuário originado de um deep link."""
    async with AsyncSessionLocal() as s:
        l = await s.get(DeepLink, deep_link_id)
        if not l:
            return
        l.purchases += 1
        l.revenue += float(purchase_amount)

        visit = (await s.execute(
            select(DeepLinkVisit)
            .where(DeepLinkVisit.deep_link_id == deep_link_id)
            .where(DeepLinkVisit.user_id == user_id)
            .where(DeepLinkVisit.converted == False)
            .limit(1)
        )).scalar_one_or_none()
        if visit:
            visit.converted = True

        await s.commit()
