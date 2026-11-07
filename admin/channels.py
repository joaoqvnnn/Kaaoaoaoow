from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc
from datetime import datetime, timedelta

from bot import dp, bot
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    User, Product, Order, BotSettings, AdminLog, ChannelPublication,
)
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

HISTORY_PAGE_SIZE = 12


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class CHS(StatesGroup):
    set_channel = State()
    edit_purchase_template = State()
    edit_purchase_button = State()
    edit_purchase_image = State()
    edit_restock_template = State()
    edit_restock_button = State()
    edit_restock_image = State()
    set_min_stock = State()
    set_min_restock_qty = State()
    set_bot_username = State()


CHANNEL_KEYS = {
    "sales": ("sales_channel_id", "📢", "Canal de vendas", "Compras confirmadas"),
    "restock": ("restock_channel_id", "📦", "Canal de reposição", "Reabastecimentos"),
    "support": ("support_channel_id", "💬", "Canal de suporte", "Atendimento"),
    "novelties": ("novelties_channel_id", "🆕", "Canal de novidades", "Novidades"),
    "mandatory_channel": ("mandatory_channel_id", "📣", "Canal obrigatório", "Verificação de entrada"),
    "mandatory_group": ("mandatory_group_id", "👥", "Grupo obrigatório", "Verificação de entrada"),
}

DEFAULT_PURCHASE_TEMPLATE = (
    "🛍️ | NOVA COMPRA REALIZADA!\n\n"
    "━━━━━━━━━━━━━━\n\n"
    "👤 Cliente: {nome_publico}\n"
    "🆔 ID: {id_mascarado}\n\n"
    "📦 Produto: {nome_produto}\n"
    "🔢 Quantidade: {quantidade}\n"
    "💰 Valor: R$ {valor_total}\n\n"
    "🕒 Horário: {horario_compra}\n\n"
    "━━━━━━━━━━━━━━\n\n"
    "✅ Pedido confirmado com sucesso!"
)

DEFAULT_RESTOCK_TEMPLATE = (
    "📦 | PRODUTO REABASTECIDO\n\n"
    "◈ {nome_produto}\n\n"
    "O estoque foi atualizado!\n\n"
    "📦 Unidades disponíveis: {estoque_atual}\n"
    "💰 Valor: R$ {preco}\n"
    "🕒 Atualizado às {horario}"
)


# ============================================================
# HELPERS
# ============================================================
def _mask_user_id(user_id: int) -> str:
    s = str(user_id)
    if len(s) <= 4:
        return "***" + s
    return "***" + s[-4:]


async def _build_bot_link(payload: str = "") -> str:
    settings = await get_settings()
    username = getattr(settings, "bot_username", None)
    if not username:
        return ""
    if payload:
        return f"https://t.me/{username}?start={payload}"
    return f"https://t.me/{username}"


async def _check_channel_permissions(channel_id: str) -> tuple[bool, str]:
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=channel_id, user_id=me.id)
        if member.status not in ("administrator", "creator"):
            return False, f"Bot não é administrador (status: {member.status})"
        can_post = getattr(member, "can_post_messages", False)
        if not can_post and member.status != "creator":
            return False, "Bot não tem permissão de postar mensagens"
        return True, "Permissões OK"
    except Exception as e:
        return False, f"Erro: {str(e)[:120]}"


# ============================================================
# MENU
# ============================================================
@router.callback_query(F.data == "admin_channels")
async def channels_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    settings = await get_settings()
    today = datetime.utcnow().date()
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        pubs_today = (await s.execute(
            select(func.count(ChannelPublication.id))
            .where(ChannelPublication.published_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0
        pubs_month = (await s.execute(
            select(func.count(ChannelPublication.id))
            .where(ChannelPublication.published_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0
        pubs_total = (await s.execute(
            select(func.count(ChannelPublication.id))
        )).scalar() or 0
        fails = (await s.execute(
            select(func.count(ChannelPublication.id))
            .where(ChannelPublication.success == False)
        )).scalar() or 0

    channels_status = []
    for key, (field, icon, label, desc) in CHANNEL_KEYS.items():
        val = getattr(settings, field, None)
        channels_status.append(f"{icon} {label}: {'🟢' if val else '🔴'}")

    purchase_on = getattr(settings, "purchase_announcement_enabled", False)
    restock_on = getattr(settings, "restock_announcement_enabled", True)
    bot_username = getattr(settings, "bot_username", None)

    text = (
        "◈ BELLAMY | CANAIS E PUBLICAÇÕES\n\n"
        + "\n".join(channels_status) + "\n\n"
        f"🤖 Bot: @{bot_username if bot_username else 'não configurado'}\n\n"
        f"🛍️ Anúncio de compras: {'🟢 Ativado' if purchase_on else '🔴 Desativado'}\n"
        f"📦 Anúncio de reposição: {'🟢 Ativado' if restock_on else '🔴 Desativado'}\n\n"
        f"📜 Publicações\n"
        f"📅 Hoje: {pubs_today} | Este mês: {pubs_month}\n"
        f"📊 Total: {pubs_total} | ⚠️ Falhas: {fails}\n\n"
        "👇 Selecione uma opção:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Configurar canais", callback_data="chn_config_list")],
        [InlineKeyboardButton(text="🛍️ Anúncio de compras", callback_data="chn_purchase")],
        [InlineKeyboardButton(text="📦 Anúncio de reposição", callback_data="chn_restock")],
        [InlineKeyboardButton(text="🧪 Testar permissões", callback_data="chn_test_perms")],
        [InlineKeyboardButton(text="📜 Histórico de publicações", callback_data="chn_history_0")],
        [InlineKeyboardButton(text="📊 Estatísticas", callback_data="chn_stats")],
        [InlineKeyboardButton(text="📥 Exportar relatório", callback_data="chn_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# CONFIGURAR CANAIS
# ============================================================
@router.callback_query(F.data == "chn_config_list")
async def config_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    settings = await get_settings()
    buttons = []
    for key, (field, icon, label, desc) in CHANNEL_KEYS.items():
        val = getattr(settings, field, None)
        status = "🟢" if val else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {icon} {label}",
            callback_data=f"chn_set_{key}",
        )])
    buttons.append([InlineKeyboardButton(text="🤖 Configurar username do bot", callback_data="chn_set_botname")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_channels")])

    text = (
        "◈ BELLAMY | CONFIGURAR CANAIS\n\n"
        "🟢 = configurado | 🔴 = não configurado\n\n"
        "Toque em um canal para configurar:"
    )
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("chn_set_"))
async def set_channel_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    key = cb.data.replace("chn_set_", "")

    if key == "botname":
        await state.set_state(CHS.set_bot_username)
        await cb.message.answer(
            "🤖 CONFIGURAR USERNAME DO BOT\n\n"
            "Envie o username do bot SEM o @ (ex.: <code>bellamy_store_bot</code>):"
        )
        await cb.answer()
        return

    if key not in CHANNEL_KEYS:
        await cb.answer("Canal inválido.")
        return

    field, icon, label, desc = CHANNEL_KEYS[key]
    settings = await get_settings()
    current = getattr(settings, field, None)

    await state.update_data(channel_key=key)
    await state.set_state(CHS.set_channel)

    current_txt = f"<code>{current}</code>" if current else "não configurado"
    await cb.message.answer(
        f"⚙️ CONFIGURAR {icon} {label.upper()}\n\n"
        f"Uso: {desc}\n"
        f"Valor atual: {current_txt}\n\n"
        f"Envie o ID do canal/grupo (ex.: <code>-1001234567890</code>)\n"
        f"Ou /remover para limpar."
    )
    await cb.answer()


@router.message(CHS.set_channel)
async def save_channel(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    key = data.get("channel_key")
    field, icon, label, desc = CHANNEL_KEYS[key]
    raw = m.text.strip() if m.text else ""

    if raw.lower() == "/remover":
        new_val = None
    else:
        if not raw.startswith("-") or not raw[1:].isdigit():
            await m.answer("❌ ID inválido. Deve começar com - e conter apenas números (ex.: -1001234567890).")
            return
        ok, msg = await _check_channel_permissions(raw)
        if not ok:
            await m.answer(
                f"⚠️ Aviso: {msg}\n\n"
                f"Deseja salvar mesmo assim? (o bot pode não conseguir publicar)\n\n"
                f"Use o comando /forcar para salvar sem validação."
            )
            await state.update_data(pending_channel=raw)
            return
        new_val = raw

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        setattr(bs, field, new_val)
        await s.commit()
    invalidate_cache()

    await log_admin(m.from_user.id, f"channel_set_{key}", {"value": new_val})
    await state.clear()

    status = f"✅ {label} configurado: <code>{new_val}</code>" if new_val else f"✅ {label} removido"
    await m.answer(status)


@router.message(F.text == "/forcar")
async def force_save_channel(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    pending = data.get("pending_channel")
    key = data.get("channel_key")
    if not pending or not key:
        return
    field, icon, label, desc = CHANNEL_KEYS[key]

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        setattr(bs, field, pending)
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, f"channel_set_{key}_forced", {"value": pending})
    await state.clear()
    await m.answer(f"✅ {label} configurado (forçado): <code>{pending}</code>")


@router.message(CHS.set_bot_username)
async def save_bot_username(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    raw = raw.lstrip("@")
    if len(raw) < 3 or len(raw) > 64:
        await m.answer("❌ Username inválido (3-64 caracteres).")
        return
    if not all(c.isalnum() or c == "_" for c in raw):
        await m.answer("❌ Username deve conter apenas letras, números e underline.")
        return

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.bot_username = raw
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "set_bot_username", {"value": raw})
    await state.clear()
    await m.answer(
        f"✅ Username do bot configurado: @{raw}\n\n"
        f"Link principal: https://t.me/{raw}"
    )


# ============================================================
# TESTE DE PERMISSÕES
# ============================================================
@router.callback_query(F.data == "chn_test_perms")
async def test_perms(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    settings = await get_settings()
    results = []

    for key, (field, icon, label, desc) in CHANNEL_KEYS.items():
        val = getattr(settings, field, None)
        if not val:
            results.append(f"⚪ {icon} {label}: não configurado")
            continue
        ok, msg = await _check_channel_permissions(val)
        if ok:
            results.append(f"✅ {icon} {label}: {msg}")
        else:
            results.append(f"❌ {icon} {label}: {msg}")

    text = "🧪 TESTE DE PERMISSÕES\n\n" + "\n".join(results)

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Testar novamente", callback_data="chn_test_perms")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_channels")],
    ]))
    await cb.answer()


# ============================================================
# ANÚNCIO DE COMPRAS
# ============================================================
async def purchase_view() -> tuple[str, InlineKeyboardMarkup]:
    settings = await get_settings()
    enabled = getattr(settings, "purchase_announcement_enabled", False)
    show_name = getattr(settings, "purchase_announcement_show_name", True)
    show_username = getattr(settings, "purchase_announcement_show_username", False)
    mask_id = getattr(settings, "purchase_announcement_mask_id", True)
    button_text = getattr(settings, "purchase_announcement_button_text", "🛒 IR PARA A BELLAMY")
    button_url = getattr(settings, "purchase_announcement_button_url", None)
    send_image = getattr(settings, "purchase_announcement_send_image", False)
    image_url = getattr(settings, "purchase_announcement_image_url", None)
    filter_products = getattr(settings, "purchase_announcement_filter_products", False)
    channel_id = getattr(settings, "sales_channel_id", None)
    template = getattr(settings, "purchase_announcement_template", DEFAULT_PURCHASE_TEMPLATE)

    preview = template[:200] + ("..." if len(template) > 200 else "")

    text = (
        "◈ BELLAMY | ANÚNCIO DE COMPRAS\n\n"
        f"📌 Status: {'🟢 Ativado' if enabled else '🔴 Desativado'}\n"
        f"📢 Canal: {'🟢 configurado' if channel_id else '🔴 não configurado'}\n\n"
        f"🔒 PRIVACIDADE\n"
        f"👤 Mostrar nome: {'🟢 sim' if show_name else '🔴 não'}\n"
        f"💠 Mostrar username: {'🟢 sim' if show_username else '🔴 não'}\n"
        f"🆔 Mascarar ID: {'🟢 sim' if mask_id else '🔴 não'}\n\n"
        f"🎨 PERSONALIZAÇÃO\n"
        f"🖼️ Imagem: {'🟢 ativa' if send_image else '🔴 desativada'}\n"
        f"🔘 Botão: {button_text or '(removido)'}\n"
        f"🔗 URL: {button_url or 'auto (link do bot)'}\n"
        f"🎯 Filtrar produtos: {'🟢 sim' if filter_products else '🔴 não'}\n\n"
        f"💬 <b>Preview do template</b>\n<code>{preview}</code>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar" if enabled else "🟢 Ativar"),
            callback_data="chn_purchase_toggle")],
        [InlineKeyboardButton(
            text=f"👤 Nome: {'🟢' if show_name else '🔴'}",
            callback_data="chn_purchase_t_name"),
         InlineKeyboardButton(
            text=f"💠 Username: {'🟢' if show_username else '🔴'}",
            callback_data="chn_purchase_t_uname")],
        [InlineKeyboardButton(
            text=f"🆔 Mascarar ID: {'🟢' if mask_id else '🔴'}",
            callback_data="chn_purchase_t_mask")],
        [InlineKeyboardButton(text="💬 Editar mensagem", callback_data="chn_purchase_edit_msg")],
        [InlineKeyboardButton(text="🔘 Editar botão", callback_data="chn_purchase_edit_btn")],
        [InlineKeyboardButton(text="🖼️ Configurar imagem", callback_data="chn_purchase_image")],
        [InlineKeyboardButton(
            text=f"🎯 Filtrar produtos: {'🟢' if filter_products else '🔴'}",
            callback_data="chn_purchase_t_filter")],
        [InlineKeyboardButton(text="👁️ Enviar preview para mim", callback_data="chn_purchase_preview")],
        [InlineKeyboardButton(text="↩️ Restaurar padrão", callback_data="chn_purchase_reset")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_channels")],
    ])
    return text, kb


@router.callback_query(F.data == "chn_purchase")
async def purchase_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    text, kb = await purchase_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


async def _toggle_field(cb: CallbackQuery, field: str, action_name: str, redirect_fn):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        cur = getattr(bs, field, False)
        setattr(bs, field, not cur)
        new = getattr(bs, field)
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, action_name, {"new": new})
    text, kb = await redirect_fn()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "chn_purchase_toggle")
async def purchase_toggle(cb: CallbackQuery):
    await _toggle_field(cb, "purchase_announcement_enabled", "purchase_announcement_toggle", purchase_view)


@router.callback_query(F.data == "chn_purchase_t_name")
async def purchase_t_name(cb: CallbackQuery):
    await _toggle_field(cb, "purchase_announcement_show_name", "purchase_show_name", purchase_view)


@router.callback_query(F.data == "chn_purchase_t_uname")
async def purchase_t_uname(cb: CallbackQuery):
    await _toggle_field(cb, "purchase_announcement_show_username", "purchase_show_username", purchase_view)


@router.callback_query(F.data == "chn_purchase_t_mask")
async def purchase_t_mask(cb: CallbackQuery):
    await _toggle_field(cb, "purchase_announcement_mask_id", "purchase_mask_id", purchase_view)


@router.callback_query(F.data == "chn_purchase_t_filter")
async def purchase_t_filter(cb: CallbackQuery):
    await _toggle_field(cb, "purchase_announcement_filter_products", "purchase_filter_products", purchase_view)


@router.callback_query(F.data == "chn_purchase_edit_msg")
async def purchase_edit_msg_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CHS.edit_purchase_template)
    await cb.message.answer(
        "💬 EDITAR MENSAGEM DE COMPRA\n\n"
        "Variáveis disponíveis:\n"
        "• <code>{nome_publico}</code> — nome do cliente\n"
        "• <code>{id_mascarado}</code> — ID mascarado\n"
        "• <code>{nome_produto}</code>\n"
        "• <code>{quantidade}</code>\n"
        "• <code>{valor_total}</code>\n"
        "• <code>{pedido_id}</code>\n"
        "• <code>{horario_compra}</code>\n"
        "• <code>{username}</code> (só se ativado)\n\n"
        "Envie a nova mensagem (HTML permitido).\n"
        "Ou /resetar para usar o padrão."
    )
    await cb.answer()


@router.message(CHS.edit_purchase_template)
async def purchase_edit_msg_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if raw.lower() == "/resetar":
        new_val = DEFAULT_PURCHASE_TEMPLATE
    else:
        if len(raw) < 10:
            await m.answer("❌ Mensagem muito curta.")
            return
        if len(raw) > 4000:
            await m.answer("❌ Mensagem muito longa (max 4000).")
            return
        new_val = raw

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.purchase_announcement_template = new_val
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "purchase_template_edit", {"len": len(new_val)})
    await state.clear()
    text, kb = await purchase_view()
    await m.answer("✅ Mensagem atualizada.")
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data == "chn_purchase_edit_btn")
async def purchase_edit_btn_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CHS.edit_purchase_button)
    await cb.message.answer(
        "🔘 EDITAR BOTÃO\n\n"
        "Envie no formato:\n"
        "<code>TEXTO | URL</code>\n\n"
        "Exemplos:\n"
        "<code>🛒 IR PARA A BELLAMY | https://t.me/seu_bot</code>\n"
        "<code>💎 Comprar | https://t.me/seu_bot?start=produto_5</code>\n\n"
        "Envie /limpar para remover o botão."
    )
    await cb.answer()


@router.message(CHS.edit_purchase_button)
async def purchase_edit_btn_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""

    if raw.lower() == "/limpar":
        btn_text = None
        btn_url = None
    else:
        try:
            parts = raw.split("|", 1)
            if len(parts) != 2:
                raise ValueError
            btn_text = parts[0].strip()
            btn_url = parts[1].strip()
            if not btn_text or not btn_url:
                raise ValueError
            if not (btn_url.startswith("http://") or btn_url.startswith("https://") or btn_url.startswith("tg://")):
                await m.answer("❌ URL inválida.")
                return
        except ValueError:
            await m.answer("❌ Formato inválido. Use <code>TEXTO | URL</code>.")
            return

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.purchase_announcement_button_text = btn_text
        bs.purchase_announcement_button_url = btn_url
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "purchase_button_edit", {"text": btn_text, "url": btn_url})
    await state.clear()
    text, kb = await purchase_view()
    await m.answer("✅ Botão atualizado.")
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data == "chn_purchase_image")
async def purchase_image_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    settings = await get_settings()
    send_image = getattr(settings, "purchase_announcement_send_image", False)
    image_url = getattr(settings, "purchase_announcement_image_url", None)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar imagem" if send_image else "🟢 Ativar imagem"),
            callback_data="chn_purchase_t_image")],
        [InlineKeyboardButton(text="🔗 Alterar URL da imagem", callback_data="chn_purchase_set_image")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="chn_purchase")],
    ])
    await cb.message.edit_text(
        f"🖼️ IMAGEM DA PUBLICAÇÃO DE COMPRA\n\n"
        f"Status: {'🟢 Ativada' if send_image else '🔴 Desativada'}\n"
        f"URL atual: <code>{(image_url or 'não configurada')[:200]}</code>",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data == "chn_purchase_t_image")
async def purchase_t_image(cb: CallbackQuery):
    await _toggle_field(cb, "purchase_announcement_send_image", "purchase_t_image", purchase_image_menu)


@router.callback_query(F.data == "chn_purchase_set_image")
async def purchase_set_image_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CHS.edit_purchase_image)
    await cb.message.answer("🖼️ Envie a URL pública da imagem (ou /limpar para remover):")
    await cb.answer()


@router.message(CHS.edit_purchase_image)
async def purchase_set_image_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if raw.lower() == "/limpar":
        new_val = None
    else:
        if not (raw.startswith("http://") or raw.startswith("https://")):
            await m.answer("❌ URL inválida.")
            return
        new_val = raw

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.purchase_announcement_image_url = new_val
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "purchase_set_image", {"url": new_val})
    await state.clear()
    await m.answer("✅ URL atualizada.")
    text, kb = await purchase_view()
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data == "chn_purchase_preview")
async def purchase_preview(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await _send_purchase_preview(cb.from_user.id)
    await cb.answer("👁️ Preview enviado.")


@router.callback_query(F.data == "chn_purchase_reset")
async def purchase_reset(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.purchase_announcement_template = DEFAULT_PURCHASE_TEMPLATE
        bs.purchase_announcement_show_name = True
        bs.purchase_announcement_show_username = False
        bs.purchase_announcement_mask_id = True
        bs.purchase_announcement_button_text = "🛒 IR PARA A BELLAMY"
        bs.purchase_announcement_button_url = None
        bs.purchase_announcement_send_image = False
        bs.purchase_announcement_image_url = None
        bs.purchase_announcement_filter_products = False
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "purchase_reset", {})
    text, kb = await purchase_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("✅ Configurações restauradas.")


# ============================================================
# ANÚNCIO DE REPOSIÇÃO
# ============================================================
async def restock_view() -> tuple[str, InlineKeyboardMarkup]:
    settings = await get_settings()
    enabled = getattr(settings, "restock_announcement_enabled", True)
    min_stock = getattr(settings, "restock_announcement_min_stock", 1)
    min_restock_qty = getattr(settings, "restock_announcement_min_restock_qty", 1)
    button_text = getattr(settings, "restock_announcement_button_text", "🛍️ Acessar produto")
    button_url = getattr(settings, "restock_announcement_button_url", "auto")
    send_image = getattr(settings, "restock_announcement_send_image", False)
    image_url = getattr(settings, "restock_announcement_image_url", None)
    channel_id = getattr(settings, "restock_channel_id", None)
    template = getattr(settings, "restock_announcement_template", DEFAULT_RESTOCK_TEMPLATE)

    preview = template[:200] + ("..." if len(template) > 200 else "")

    text = (
        "◈ BELLAMY | ANÚNCIO DE REPOSIÇÃO\n\n"
        f"📌 Status: {'🟢 Ativado' if enabled else '🔴 Desativado'}\n"
        f"📢 Canal: {'🟢 configurado' if channel_id else '🔴 não configurado'}\n\n"
        f"🎯 REGRAS\n"
        f"⚠️ Estoque mínimo para publicar: {min_stock} unidade(s)\n"
        f"📥 Reposição mínima para publicar: {min_restock_qty} unidade(s)\n\n"
        f"🎨 PERSONALIZAÇÃO\n"
        f"🖼️ Imagem: {'🟢 ativa' if send_image else '🔴 desativada'}\n"
        f"🔘 Botão: {button_text or '(removido)'}\n"
        f"🔗 URL: {button_url or 'auto (deep link do produto)'}\n\n"
        f"💬 <b>Preview do template</b>\n<code>{preview}</code>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar" if enabled else "🟢 Ativar"),
            callback_data="chn_restock_toggle")],
        [InlineKeyboardButton(text="⚠️ Alterar estoque mínimo", callback_data="chn_restock_set_mins")],
        [InlineKeyboardButton(text="📥 Alterar reposição mínima", callback_data="chn_restock_set_minq")],
        [InlineKeyboardButton(text="💬 Editar mensagem", callback_data="chn_restock_edit_msg")],
        [InlineKeyboardButton(text="🔘 Editar botão", callback_data="chn_restock_edit_btn")],
        [InlineKeyboardButton(text="🖼️ Configurar imagem", callback_data="chn_restock_image")],
        [InlineKeyboardButton(text="👁️ Enviar preview para mim", callback_data="chn_restock_preview")],
        [InlineKeyboardButton(text="↩️ Restaurar padrão", callback_data="chn_restock_reset")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_channels")],
    ])
    return text, kb


@router.callback_query(F.data == "chn_restock")
async def restock_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    text, kb = await restock_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "chn_restock_toggle")
async def restock_toggle(cb: CallbackQuery):
    await _toggle_field(cb, "restock_announcement_enabled", "restock_announcement_toggle", restock_view)


@router.callback_query(F.data == "chn_restock_set_mins")
async def restock_set_mins_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CHS.set_min_stock)
    await cb.message.answer("Informe o estoque mínimo para publicar (0 = sempre publicar):")
    await cb.answer()


@router.callback_query(F.data == "chn_restock_set_minq")
async def restock_set_minq_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CHS.set_min_restock_qty)
    await cb.message.answer("Informe a reposição mínima para publicar (0 = sempre publicar):")
    await cb.answer()


@router.message(CHS.set_min_stock)
async def save_min_stock(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        val = int(m.text.strip())
        if val < 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido (0+).")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.restock_announcement_min_stock = val
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "restock_min_stock_set", {"value": val})
    await state.clear()
    text, kb = await restock_view()
    await m.answer(f"✅ Estoque mínimo: {val}.", reply_markup=kb)


@router.message(CHS.set_min_restock_qty)
async def save_min_restock_qty(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        val = int(m.text.strip())
        if val < 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido (0+).")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.restock_announcement_min_restock_qty = val
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "restock_min_qty_set", {"value": val})
    await state.clear()
    text, kb = await restock_view()
    await m.answer(f"✅ Reposição mínima: {val}.", reply_markup=kb)


@router.callback_query(F.data == "chn_restock_edit_msg")
async def restock_edit_msg_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CHS.edit_restock_template)
    await cb.message.answer(
        "💬 EDITAR MENSAGEM DE REPOSIÇÃO\n\n"
        "Variáveis:\n"
        "• <code>{nome_produto}</code>\n"
        "• <code>{estoque_atual}</code>\n"
        "• <code>{preco}</code>\n"
        "• <code>{horario}</code>\n"
        "• <code>{quantidade_adicionada}</code>\n\n"
        "Envie a nova mensagem ou /resetar."
    )
    await cb.answer()


@router.message(CHS.edit_restock_template)
async def restock_edit_msg_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if raw.lower() == "/resetar":
        new_val = DEFAULT_RESTOCK_TEMPLATE
    else:
        if len(raw) < 10:
            await m.answer("❌ Mensagem muito curta.")
            return
        if len(raw) > 4000:
            await m.answer("❌ Mensagem muito longa.")
            return
        new_val = raw

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.restock_announcement_template = new_val
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "restock_template_edit", {"len": len(new_val)})
    await state.clear()
    text, kb = await restock_view()
    await m.answer("✅ Mensagem atualizada.")
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data == "chn_restock_edit_btn")
async def restock_edit_btn_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CHS.edit_restock_button)
    await cb.message.answer(
        "🔘 EDITAR BOTÃO\n\n"
        "Envie no formato:\n"
        "<code>TEXTO | URL</code>\n\n"
        "Ou <code>TEXTO | auto</code> para usar o deep link do produto.\n\n"
        "Envie /limpar para remover o botão."
    )
    await cb.answer()


@router.message(CHS.edit_restock_button)
async def restock_edit_btn_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""

    if raw.lower() == "/limpar":
        btn_text = None
        btn_url = None
    else:
        try:
            parts = raw.split("|", 1)
            if len(parts) != 2:
                raise ValueError
            btn_text = parts[0].strip()
            btn_url = parts[1].strip()
            if not btn_text or not btn_url:
                raise ValueError
            if btn_url.lower() != "auto" and not (
                btn_url.startswith("http://") or btn_url.startswith("https://") or btn_url.startswith("tg://")
            ):
                await m.answer("❌ URL inválida. Use 'auto' ou uma URL válida.")
                return
        except ValueError:
            await m.answer("❌ Formato inválido. Use <code>TEXTO | URL</code>.")
            return

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.restock_announcement_button_text = btn_text
        bs.restock_announcement_button_url = btn_url
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "restock_button_edit", {"text": btn_text, "url": btn_url})
    await state.clear()
    text, kb = await restock_view()
    await m.answer("✅ Botão atualizado.")
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data == "chn_restock_image")
async def restock_image_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    settings = await get_settings()
    send_image = getattr(settings, "restock_announcement_send_image", False)
    image_url = getattr(settings, "restock_announcement_image_url", None)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar imagem" if send_image else "🟢 Ativar imagem"),
            callback_data="chn_restock_t_image")],
        [InlineKeyboardButton(text="🔗 Alterar URL da imagem", callback_data="chn_restock_set_image")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="chn_restock")],
    ])
    await cb.message.edit_text(
        f"🖼️ IMAGEM DA PUBLICAÇÃO DE REPOSIÇÃO\n\n"
        f"Status: {'🟢 Ativada' if send_image else '🔴 Desativada'}\n"
        f"URL atual: <code>{(image_url or 'não configurada')[:200]}</code>",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data == "chn_restock_t_image")
async def restock_t_image(cb: CallbackQuery):
    await _toggle_field(cb, "restock_announcement_send_image", "restock_t_image", restock_image_menu)


@router.callback_query(F.data == "chn_restock_set_image")
async def restock_set_image_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(CHS.edit_restock_image)
    await cb.message.answer("🖼️ Envie a URL pública da imagem (ou /limpar):")
    await cb.answer()


@router.message(CHS.edit_restock_image)
async def restock_set_image_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if raw.lower() == "/limpar":
        new_val = None
    else:
        if not (raw.startswith("http://") or raw.startswith("https://")):
            await m.answer("❌ URL inválida.")
            return
        new_val = raw

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.restock_announcement_image_url = new_val
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "restock_set_image", {"url": new_val})
    await state.clear()
    await m.answer("✅ URL atualizada.")
    text, kb = await restock_view()
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data == "chn_restock_preview")
async def restock_preview(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await _send_restock_preview(cb.from_user.id)
    await cb.answer("👁️ Preview enviado.")


@router.callback_query(F.data == "chn_restock_reset")
async def restock_reset(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.restock_announcement_template = DEFAULT_RESTOCK_TEMPLATE
        bs.restock_announcement_button_text = "🛍️ Acessar produto"
        bs.restock_announcement_button_url = "auto"
        bs.restock_announcement_send_image = False
        bs.restock_announcement_image_url = None
        bs.restock_announcement_min_stock = 1
        bs.restock_announcement_min_restock_qty = 1
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "restock_reset", {})
    text, kb = await restock_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("✅ Configurações restauradas.")


# ============================================================
# PREVIEW HELPERS
# ============================================================
async def _send_purchase_preview(admin_id: int):
    settings = await get_settings()
    show_name = getattr(settings, "purchase_announcement_show_name", True)
    mask_id = getattr(settings, "purchase_announcement_mask_id", True)
    show_username = getattr(settings, "purchase_announcement_show_username", False)
    template = getattr(settings, "purchase_announcement_template", DEFAULT_PURCHASE_TEMPLATE)
    button_text = getattr(settings, "purchase_announcement_button_text", "🛒 IR PARA A BELLAMY")
    button_url = getattr(settings, "purchase_announcement_button_url", None)
    send_image = getattr(settings, "purchase_announcement_send_image", False)
    image_url = getattr(settings, "purchase_announcement_image_url", None)

    if not button_url:
        button_url = await _build_bot_link()

    variables = {
        "nome_publico": "João Teste" if show_name else "Cliente",
        "id_mascarado": _mask_user_id(123456789) if mask_id else "123456789",
        "nome_produto": "Produto Exemplo",
        "quantidade": "1",
        "valor_total": "29.90",
        "pedido_id": "999",
        "horario_compra": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        "username": "@joao_teste" if show_username else "",
    }
    try:
        rendered = template.format(**variables)
    except KeyError as e:
        rendered = f"{template}\n\n(⚠️ variável ausente: {e})"
    except Exception:
        rendered = template

    rendered = f"👁️ <b>PREVIEW — Anúncio de Compra</b>\n\n{rendered}"

    kb = None
    if button_text and button_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=button_text, url=button_url)]
        ])

    try:
        if send_image and image_url:
            await bot.send_photo(chat_id=admin_id, photo=image_url, caption=rendered, reply_markup=kb)
        else:
            await bot.send_message(admin_id, rendered, reply_markup=kb)
    except Exception as e:
        await bot.send_message(admin_id, f"❌ Erro no preview: {str(e)[:200]}")


async def _send_restock_preview(admin_id: int):
    settings = await get_settings()
    template = getattr(settings, "restock_announcement_template", DEFAULT_RESTOCK_TEMPLATE)
    button_text = getattr(settings, "restock_announcement_button_text", "🛍️ Acessar produto")
    button_url = getattr(settings, "restock_announcement_button_url", "auto")
    send_image = getattr(settings, "restock_announcement_send_image", False)
    image_url = getattr(settings, "restock_announcement_image_url", None)

    if not button_url or button_url == "auto":
        button_url = await _build_bot_link("produto_1")
        if not button_url:
            button_url = await _build_bot_link()

    variables = {
        "nome_produto": "Produto Exemplo",
        "estoque_atual": "25",
        "preco": "29.90",
        "horario": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        "quantidade_adicionada": "10",
    }
    try:
        rendered = template.format(**variables)
    except KeyError as e:
        rendered = f"{template}\n\n(⚠️ variável ausente: {e})"
    except Exception:
        rendered = template

    rendered = f"👁️ <b>PREVIEW — Anúncio de Reposição</b>\n\n{rendered}"

    kb = None
    if button_text and button_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=button_text, url=button_url)]
        ])

    try:
        if send_image and image_url:
            await bot.send_photo(chat_id=admin_id, photo=image_url, caption=rendered, reply_markup=kb)
        else:
            await bot.send_message(admin_id, rendered, reply_markup=kb)
    except Exception as e:
        await bot.send_message(admin_id, f"❌ Erro no preview: {str(e)[:200]}")


# ============================================================
# HISTÓRICO
# ============================================================
@router.callback_query(F.data.startswith("chn_history_"))
async def history(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(ChannelPublication.id))
        )).scalar() or 0
        stmt = (
            select(ChannelPublication)
            .order_by(desc(ChannelPublication.published_at))
            .offset(page * HISTORY_PAGE_SIZE)
            .limit(HISTORY_PAGE_SIZE)
        )
        pubs = (await s.execute(stmt)).scalars().all()

    text = f"📜 HISTÓRICO DE PUBLICAÇÕES\n\nTotal: {total} | Página {page + 1}\n\n"

    if not pubs:
        text += "Nenhuma publicação registrada."
    else:
        for p in pubs:
            icon = "✅" if p.success else "❌"
            type_label = "🛍️ Compra" if p.publication_type == "purchase" else "📦 Reposição"
            text += (
                f"{icon} {type_label} — {p.published_at.strftime('%d/%m/%Y %H:%M')}\n"
                f"   📢 <code>{p.channel_id}</code>\n"
            )
            if p.content_preview:
                text += f"   {p.content_preview[:60]}...\n"
            if p.error:
                text += f"   ⚠️ {p.error[:80]}\n"
            text += "\n"

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"chn_history_{page - 1}"))
    if (page + 1) * HISTORY_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"chn_history_{page + 1}"))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_channels")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# ESTATÍSTICAS
# ============================================================
@router.callback_query(F.data == "chn_stats")
async def stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    today = datetime.utcnow().date()
    week_start = today - timedelta(days=7)
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(ChannelPublication.id)))).scalar() or 0
        success = (await s.execute(
            select(func.count(ChannelPublication.id))
            .where(ChannelPublication.success == True)
        )).scalar() or 0
        failed = total - success

        purchases = (await s.execute(
            select(func.count(ChannelPublication.id))
            .where(ChannelPublication.publication_type == "purchase")
        )).scalar() or 0
        restocks = (await s.execute(
            select(func.count(ChannelPublication.id))
            .where(ChannelPublication.publication_type == "restock")
        )).scalar() or 0

        today_count = (await s.execute(
            select(func.count(ChannelPublication.id))
            .where(ChannelPublication.published_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0
        week_count = (await s.execute(
            select(func.count(ChannelPublication.id))
            .where(ChannelPublication.published_at >= datetime.combine(week_start, datetime.min.time()))
        )).scalar() or 0
        month_count = (await s.execute(
            select(func.count(ChannelPublication.id))
            .where(ChannelPublication.published_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0

    success_rate = (success / total * 100) if total else 0.0

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DE PUBLICAÇÕES\n\n"
        f"📊 Total: {total}\n"
        f"✅ Sucesso: {success}\n"
        f"❌ Falhas: {failed}\n"
        f"📈 Taxa de sucesso: {success_rate:.1f}%\n\n"
        f"🛍️ Compras publicadas: {purchases}\n"
        f"📦 Reposições publicadas: {restocks}\n\n"
        f"📅 Hoje: {today_count}\n"
        f"📆 Últimos 7 dias: {week_count}\n"
        f"🗓️ Este mês: {month_count}\n"
    )

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="chn_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_channels")],
    ]))
    await cb.answer()


# ============================================================
# EXPORTAÇÃO
# ============================================================
@router.callback_query(F.data == "chn_export")
async def export_report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        pubs = (await s.execute(
            select(ChannelPublication)
            .order_by(desc(ChannelPublication.published_at))
            .limit(500)
        )).scalars().all()

    settings = await get_settings()

    lines = []
    lines.append("═" * 70)
    lines.append("BELLAMY | RELATÓRIO DE PUBLICAÇÕES EM CANAIS")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append(f"Total: {len(pubs)} publicações (últimas 500)")
    lines.append("═" * 70)
    lines.append("")
    lines.append("─" * 70)
    lines.append("CANAIS CONFIGURADOS")
    lines.append("─" * 70)
    for key, (field, icon, label, desc) in CHANNEL_KEYS.items():
        val = getattr(settings, field, None)
        lines.append(f"{icon} {label}: {val or 'não configurado'}")
    lines.append(f"🤖 Bot username: @{getattr(settings, 'bot_username', None) or 'não configurado'}")
    lines.append("")
    lines.append("─" * 70)
    lines.append("ÚLTIMAS 500 PUBLICAÇÕES")
    lines.append("─" * 70)
    for p in pubs:
        status = "OK" if p.success else "FALHA"
        type_label = "COMPRA" if p.publication_type == "purchase" else "REPOSIÇÃO"
        lines.append(
            f"{p.published_at.strftime('%d/%m/%Y %H:%M')} | "
            f"{type_label:<10} | {p.channel_id:<20} | {status}"
        )
        if p.error:
            lines.append(f"  ⚠️ {p.error[:150]}")

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"publicacoes_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption=f"📊 <b>Relatório de Publicações</b>\n\n📜 Total: {len(pubs)}",
    )
    await cb.answer("Relatório gerado.")


# ============================================================
# HELPERS PARA OUTROS MÓDULOS
# ============================================================
async def publish_purchase_announcement(order_id: int) -> bool:
    """
    Chame em admin/orders.py após confirmar um pedido:
        from admin.channels import publish_purchase_announcement
        await publish_purchase_announcement(order_id)
    """
    settings = await get_settings()

    if not getattr(settings, "purchase_announcement_enabled", False):
        return False

    channel_id = getattr(settings, "sales_channel_id", None)
    if not channel_id:
        return False

    async with AsyncSessionLocal() as s:
        order = await s.get(Order, order_id)
        if not order or order.status != "completed":
            return False
        user = await s.get(User, order.user_id)
        product = await s.get(Product, order.product_id)

    if not user or not product:
        return False

    if getattr(settings, "purchase_announcement_filter_products", False):
        allowed = getattr(settings, "purchase_announcement_product_ids", None) or []
        if isinstance(allowed, list) and allowed and order.product_id not in allowed:
            return False

    show_name = getattr(settings, "purchase_announcement_show_name", True)
    show_username = getattr(settings, "purchase_announcement_show_username", False)
    mask_id = getattr(settings, "purchase_announcement_mask_id", True)

    nome_publico = user.full_name if show_name else "Cliente"
    if show_username and user.username:
        nome_publico = f"{nome_publico} (@{user.username})"

    id_mascarado = _mask_user_id(order.user_id) if mask_id else str(order.user_id)

    variables = {
        "nome_publico": nome_publico,
        "id_mascarado": id_mascarado,
        "nome_produto": product.name,
        "quantidade": str(order.quantity),
        "valor_total": f"{order.total:.2f}",
        "pedido_id": str(order.id),
        "horario_compra": order.created_at.strftime("%d/%m/%Y %H:%M"),
        "username": f"@{user.username}" if user.username else "",
    }

    template = getattr(settings, "purchase_announcement_template", DEFAULT_PURCHASE_TEMPLATE)
    try:
        rendered = template.format(**variables)
    except KeyError:
        rendered = template

    btn_text = getattr(settings, "purchase_announcement_button_text", None)
    btn_url = getattr(settings, "purchase_announcement_button_url", None)
    if btn_text and not btn_url:
        btn_url = await _build_bot_link()
    kb = None
    if btn_text and btn_url:
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=btn_text, url=btn_url)]
            ])
        except Exception:
            kb = None

    send_image = getattr(settings, "purchase_announcement_send_image", False)
    image_url = getattr(settings, "purchase_announcement_image_url", None)

    success = True
    error_msg = None
    message_id = None
    try:
        if send_image and image_url:
            msg = await bot.send_photo(
                chat_id=channel_id, photo=image_url,
                caption=rendered, reply_markup=kb,
            )
        else:
            msg = await bot.send_message(channel_id, rendered, reply_markup=kb)
        message_id = msg.message_id
    except Exception as e:
        success = False
        error_msg = str(e)[:300]

    async with AsyncSessionLocal() as s:
        s.add(ChannelPublication(
            publication_type="purchase",
            reference_id=order_id,
            channel_id=str(channel_id),
            message_id=message_id,
            content_preview=rendered[:500],
            success=success,
            error=error_msg,
        ))
        await s.commit()

    return success


async def publish_restock_announcement(product_id: int, quantity_added: int) -> bool:
    """
    Chame em admin/products.py (restock) e admin/stock.py (bulk) após atualizar estoque:
        from admin.channels import publish_restock_announcement
        await publish_restock_announcement(product_id, quantity_added)
    """
    settings = await get_settings()

    if not getattr(settings, "restock_announcement_enabled", True):
        return False

    channel_id = getattr(settings, "restock_channel_id", None)
    if not channel_id:
        return False

    min_stock = getattr(settings, "restock_announcement_min_stock", 1)
    min_qty = getattr(settings, "restock_announcement_min_restock_qty", 1)

    if quantity_added < min_qty:
        return False

    async with AsyncSessionLocal() as s:
        product = await s.get(Product, product_id)
    if not product:
        return False

    if product.stock < min_stock:
        return False

    variables = {
        "nome_produto": product.name,
        "estoque_atual": str(product.stock),
        "preco": f"{product.price:.2f}",
        "horario": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        "quantidade_adicionada": str(quantity_added),
    }

    template = getattr(settings, "restock_announcement_template", DEFAULT_RESTOCK_TEMPLATE)
    try:
        rendered = template.format(**variables)
    except KeyError:
        rendered = template

    btn_text = getattr(settings, "restock_announcement_button_text", "🛍️ Acessar produto")
    btn_url = getattr(settings, "restock_announcement_button_url", "auto")

    if not btn_url or btn_url == "auto":
        btn_url = await _build_bot_link(f"produto_{product.id}")
        if not btn_url:
            btn_url = await _build_bot_link()

    kb = None
    if btn_text and btn_url:
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=btn_text, url=btn_url)]
            ])
        except Exception:
            kb = None

    send_image = getattr(settings, "restock_announcement_send_image", False)
    image_url = getattr(settings, "restock_announcement_image_url", None)

    success = True
    error_msg = None
    message_id = None
    try:
        if send_image and image_url:
            msg = await bot.send_photo(
                chat_id=channel_id, photo=image_url,
                caption=rendered, reply_markup=kb,
            )
        else:
            msg = await bot.send_message(channel_id, rendered, reply_markup=kb)
        message_id = msg.message_id
    except Exception as e:
        success = False
        error_msg = str(e)[:300]

    async with AsyncSessionLocal() as s:
        s.add(ChannelPublication(
            publication_type="restock",
            reference_id=product_id,
            channel_id=str(channel_id),
            message_id=message_id,
            content_preview=rendered[:500],
            success=success,
            error=error_msg,
        ))
        await s.commit()

    return success
