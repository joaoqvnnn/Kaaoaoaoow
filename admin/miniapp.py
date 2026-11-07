from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, or_
from datetime import datetime, timedelta
import re
import secrets
import string

from bot import dp, bot
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    MiniAppSettings, MiniAppSession, MiniAppThemeHistory,
    User, AdminLog, BotSettings,
)
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

HEX_REGEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
SESSION_PAGE_SIZE = 12
HISTORY_PAGE_SIZE = 10


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class MIS(StatesGroup):
    edit_color = State()
    edit_number = State()
    edit_text = State()
    edit_url = State()


COLOR_GROUPS = {
    "brand": {
        "label": "🎨 Cores da marca",
        "fields": {
            "primary_color": ("Primária", "#6C5CE7"),
            "secondary_color": ("Secundária", "#A29BFE"),
            "accent_color": ("Destaque", "#00B894"),
        },
    },
    "surfaces": {
        "label": "🎭 Superfícies",
        "fields": {
            "background_color": ("Fundo", "#0F0F23"),
            "surface_color": ("Superfície/Cards", "#1A1A35"),
            "border_color": ("Bordas", "#2A2A45"),
        },
    },
    "text": {
        "label": "📝 Textos",
        "fields": {
            "text_color": ("Texto principal", "#FFFFFF"),
            "text_secondary_color": ("Texto secundário", "#B0B0C0"),
        },
    },
    "status": {
        "label": "🚦 Status",
        "fields": {
            "success_color": ("Sucesso", "#00B894"),
            "warning_color": ("Atenção", "#FDCB6E"),
            "danger_color": ("Perigo", "#E17055"),
        },
    },
}

MENU_ITEMS = {
    "dashboard": "📊 Dashboard",
    "products": "📦 Produtos",
    "stock": "🗃️ Estoque",
    "orders": "🛒 Pedidos",
    "users": "👥 Usuários",
    "wallets": "💎 Carteiras",
    "payments": "💳 Pagamentos",
    "deeplinks": "🔗 Deep Links",
    "channel": "📢 Canal",
    "notifications": "🔔 Notificações",
    "messages": "💬 Mensagens",
    "buttons": "🔘 Botões",
    "appearance": "🎨 Aparência",
    "settings": "⚙️ Configurações",
    "security": "🛡️ Segurança",
}


# ============================================================
# HELPERS
# ============================================================
async def get_or_create_settings() -> MiniAppSettings:
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        if st:
            return st
        st = MiniAppSettings(
            id=1,
            menu_items_visible={k: True for k in MENU_ITEMS},
        )
        s.add(st)
        await s.commit()
        await s.refresh(st)
        return st


def config_to_dict(st: MiniAppSettings) -> dict:
    return {
        "enabled": st.enabled,
        "app_name": st.app_name,
        "app_short_name": st.app_short_name,
        "app_description": st.app_description,
        "logo_url": st.logo_url,
        "banner_url": st.banner_url,
        "favicon_url": st.favicon_url,
        "primary_color": st.primary_color,
        "secondary_color": st.secondary_color,
        "accent_color": st.accent_color,
        "background_color": st.background_color,
        "surface_color": st.surface_color,
        "text_color": st.text_color,
        "text_secondary_color": st.text_secondary_color,
        "border_color": st.border_color,
        "success_color": st.success_color,
        "warning_color": st.warning_color,
        "danger_color": st.danger_color,
        "theme_mode": st.theme_mode,
        "border_radius": st.border_radius,
        "spacing_unit": st.spacing_unit,
        "animation_enabled": st.animation_enabled,
        "animation_speed": st.animation_speed,
        "font_family": st.font_family,
        "font_size_base": st.font_size_base,
        "font_size_heading": st.font_size_heading,
        "menu_items_visible": st.menu_items_visible or {},
        "card_style": st.card_style,
        "icon_style": st.icon_style,
        "auth_enabled": st.auth_enabled,
        "auth_token_expiry_hours": st.auth_token_expiry_hours,
        "require_2fa": st.require_2fa,
    }


def gen_session_token(length: int = 48) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def _build_miniapp_link() -> str:
    settings = await get_settings()
    username = getattr(settings, "bot_username", None)
    if not username:
        return ""
    return f"https://t.me/{username}?startapp=admin"


# ============================================================
# MENU
# ============================================================
@router.callback_query(F.data == "admin_miniapp")
async def miniapp_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    st = await get_or_create_settings()

    async with AsyncSessionLocal() as s:
        total_sessions = (await s.execute(
            select(func.count(MiniAppSession.id))
        )).scalar() or 0
        active_sessions = (await s.execute(
            select(func.count(MiniAppSession.id))
            .where(MiniAppSession.is_active == True)
            .where(MiniAppSession.expires_at > datetime.utcnow())
        )).scalar() or 0
        history_total = (await s.execute(
            select(func.count(MiniAppThemeHistory.id))
        )).scalar() or 0

    link = await _build_miniapp_link()
    draft_active = st.draft_theme is not None
    menu_visible = sum(1 for v in (st.menu_items_visible or {}).values() if v)

    text = (
        "◈ BELLAMY | MINI APP ADMINISTRATIVA\n\n"
        f"📌 Status: {'🟢 Ativada' if st.enabled else '🔴 Desativada'}\n"
        f"📱 Nome: {st.app_name}\n"
        f"🏷️ Nome curto: {st.app_short_name}\n\n"
        f"🎨 Tema: {st.theme_mode.title()}\n"
        f"🎭 Cor principal: <code>{st.primary_color}</code>\n"
        f"🖼️ Logo: {'🟢 configurado' if st.logo_url else '🔴 sem logo'}\n"
        f"🖼️ Banner: {'🟢 configurado' if st.banner_url else '🔴 sem banner'}\n\n"
        f"📋 Menu: {menu_visible}/{len(MENU_ITEMS)} itens visíveis\n"
        f"📝 Draft pendente: {'🟡 sim' if draft_active else '⚪ não'}\n"
        f"🕐 Última publicação: {st.published_at.strftime('%d/%m/%Y %H:%M') if st.published_at else 'nunca'}\n\n"
        f"🔐 AUTENTICAÇÃO\n"
        f"Status: {'🟢 Ativa' if st.auth_enabled else '🔴 Inativa'}\n"
        f"Expiração: {st.auth_token_expiry_hours}h\n"
        f"2FA: {'🟢 Sim' if st.require_2fa else '🔴 Não'}\n\n"
        f"👥 Sessões: {active_sessions} ativas de {total_sessions} totais\n"
        f"📜 Histórico de temas: {history_total}\n"
    )
    if link:
        text += f"\n🌐 <b>Link da Mini App</b>\n<code>{link}</code>"
    else:
        text += "\n⚠️ Configure o username do bot (Módulo 13) para gerar o link."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Editor visual", callback_data="mia_visual")],
        [InlineKeyboardButton(text="🖼️ Logo e imagens", callback_data="mia_images")],
        [InlineKeyboardButton(text="📋 Menu de navegação", callback_data="mia_menu_items")],
        [InlineKeyboardButton(text="👁️ Preview do tema", callback_data="mia_preview")],
        [InlineKeyboardButton(
            text=f"📝 Ver draft" if draft_active else "📝 Sem draft",
            callback_data="mia_draft")],
        [InlineKeyboardButton(text="💾 Publicar tema", callback_data="mia_publish")],
        [InlineKeyboardButton(text="🔐 Autenticação", callback_data="mia_auth")],
        [InlineKeyboardButton(text="👥 Sessões ativas", callback_data="mia_sessions_0")],
        [InlineKeyboardButton(text="📜 Histórico de temas", callback_data="mia_history_0")],
        [InlineKeyboardButton(
            text=("🔴 Desativar Mini App" if st.enabled else "🟢 Ativar Mini App"),
            callback_data="mia_toggle")],
        [InlineKeyboardButton(text="🔄 Restaurar padrão", callback_data="mia_reset")],
        [InlineKeyboardButton(text="📥 Exportar configuração", callback_data="mia_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# TOGGLE GERAL
# ============================================================
@router.callback_query(F.data == "mia_toggle")
async def toggle_miniapp(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        st.enabled = not st.enabled
        new = st.enabled
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "miniapp_toggle", {"new": new})
    await miniapp_menu(cb)


# ============================================================
# EDITOR VISUAL
# ============================================================
@router.callback_query(F.data == "mia_visual")
async def visual_editor(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()

    text = (
        "◈ BELLAMY | EDITOR VISUAL\n\n"
        f"🎨 Modo de tema: {st.theme_mode.title()}\n"
        f"📐 Border radius: {st.border_radius}px\n"
        f"📏 Espaçamento: {st.spacing_unit}px\n"
        f"✨ Animações: {'🟢 Ativadas' if st.animation_enabled else '🔴 Desativadas'}\n"
        f"⚡ Velocidade: {st.animation_speed}\n"
        f"🔤 Fonte: {st.font_family}\n"
        f"📝 Tamanho base: {st.font_size_base}px\n"
        f"📢 Tamanho título: {st.font_size_heading}px\n"
        f"🃏 Estilo dos cards: {st.card_style}\n"
        f"🎯 Estilo dos ícones: {st.icon_style}\n\n"
        "👇 Selecione uma categoria de cores:"
    )

    buttons = []
    for key, group in COLOR_GROUPS.items():
        buttons.append([InlineKeyboardButton(
            text=group["label"],
            callback_data=f"mia_colorgrp_{key}",
        )])
    buttons.append([InlineKeyboardButton(text="🌗 Modo de tema", callback_data="mia_theme_mode")])
    buttons.append([InlineKeyboardButton(text="🎨 Estilo dos cards", callback_data="mia_card_style")])
    buttons.append([InlineKeyboardButton(text="🎯 Estilo dos ícones", callback_data="mia_icon_style")])
    buttons.append([InlineKeyboardButton(text="🔤 Tipografia", callback_data="mia_typography")])
    buttons.append([InlineKeyboardButton(text="📐 Layout e animações", callback_data="mia_layout")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("mia_colorgrp_"))
async def color_group(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    group_key = cb.data.replace("mia_colorgrp_", "")
    group = COLOR_GROUPS.get(group_key)
    if not group:
        await cb.answer("Grupo inválido.")
        return

    st = await get_or_create_settings()

    lines = [f"◈ BELLAMY | {group['label'].upper()}\n\n"]
    buttons = []
    for field, (label, _) in group["fields"].items():
        val = getattr(st, field, None)
        lines.append(f"{label}: <code>{val}</code>\n")
        buttons.append([InlineKeyboardButton(
            text=f"{label}: {val}",
            callback_data=f"mia_setcolor_{group_key}_{field}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_visual")])

    await cb.message.edit_text(
        "".join(lines) + "\n\n👇 Toque para editar uma cor:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mia_setcolor_"))
async def edit_color_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("mia_setcolor_", "")
    group_key, field = rest.split("_", 1)
    group = COLOR_GROUPS.get(group_key)
    if not group or field not in group["fields"]:
        await cb.answer("Campo inválido.")
        return

    label, _ = group["fields"][field]
    st = await get_or_create_settings()
    current = getattr(st, field)

    await state.update_data(color_field=field, color_group=group_key)
    await state.set_state(MIS.edit_color)
    await cb.message.answer(
        f"🎨 EDITAR COR — {label}\n\n"
        f"Valor atual: <code>{current}</code>\n\n"
        f"Envie o novo valor em hexadecimal:\n"
        f"• <code>#RRGGBB</code> (ex.: #6C5CE7)\n"
        f"• <code>#RRGGBBAA</code> (com transparência)\n"
        f"• <code>#RGB</code> (forma curta)\n\n"
        f"Envie /cancelar para abortar."
    )
    await cb.answer()


@router.message(MIS.edit_color)
async def edit_color_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if raw.lower() == "/cancelar":
        await state.clear()
        await m.answer("Edição cancelada.")
        return
    if not HEX_REGEX.match(raw):
        await m.answer("❌ Cor inválida. Use formato hexadecimal (#RRGGBB).")
        return

    data = await state.get_data()
    field = data.get("color_field")
    group_key = data.get("color_group")
    if not field:
        await state.clear()
        await m.answer("❌ Sessão expirada.")
        return

    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        old = getattr(st, field)
        setattr(st, field, raw.upper())
        await s.commit()

    await log_admin(m.from_user.id, "miniapp_edit_color", {
        "field": field, "old": old, "new": raw.upper(),
    })
    await state.clear()
    await m.answer(f"✅ Cor atualizada: <code>{raw.upper()}</code>")

    # Reabre o grupo
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
    group = COLOR_GROUPS.get(group_key)
    if group:
        lines = [f"◈ BELLAMY | {group['label'].upper()}\n\n"]
        buttons = []
        for f, (label, _) in group["fields"].items():
            val = getattr(st, f, None)
            lines.append(f"{label}: <code>{val}</code>\n")
            buttons.append([InlineKeyboardButton(
                text=f"{label}: {val}",
                callback_data=f"mia_setcolor_{group_key}_{f}",
            )])
        buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_visual")])
        await m.answer(
            "".join(lines) + "\n\n👇 Toque para editar uma cor:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


# ============================================================
# MODO DE TEMA
# ============================================================
@router.callback_query(F.data == "mia_theme_mode")
async def theme_mode_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'☑️' if st.theme_mode == 'auto' else '⬜'} 🤖 Automático (segue Telegram)",
            callback_data="mia_settheme_auto")],
        [InlineKeyboardButton(
            text=f"{'☑️' if st.theme_mode == 'light' else '⬜'} ☀️ Claro",
            callback_data="mia_settheme_light")],
        [InlineKeyboardButton(
            text=f"{'☑️' if st.theme_mode == 'dark' else '⬜'} 🌙 Escuro",
            callback_data="mia_settheme_dark")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_visual")],
    ])
    await cb.message.edit_text(
        f"🌗 MODO DE TEMA\n\nAtual: {st.theme_mode.title()}\n\n"
        f"• <b>Automático</b>: adapta ao tema do Telegram\n"
        f"• <b>Claro</b>: sempre claro\n"
        f"• <b>Escuro</b>: sempre escuro\n\nSelecione:",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mia_settheme_"))
async def set_theme_mode(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    mode = cb.data.replace("mia_settheme_", "")
    if mode not in ("auto", "light", "dark"):
        await cb.answer("Modo inválido.")
        return
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        st.theme_mode = mode
        await s.commit()
    await log_admin(cb.from_user.id, "miniapp_theme_mode", {"new": mode})
    await theme_mode_menu(cb)


@router.callback_query(F.data == "mia_card_style")
async def card_style_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()
    styles = {
        "flat": "⬜ Plano (sem sombra)",
        "rounded": "🔲 Arredondado",
        "elevated": "🔼 Elevado (sombra)",
        "outlined": "📦 Contornado",
    }
    buttons = []
    for key, label in styles.items():
        mark = "☑️" if st.card_style == key else "⬜"
        buttons.append([InlineKeyboardButton(
            text=f"{mark} {label}", callback_data=f"mia_setcard_{key}"
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_visual")])
    await cb.message.edit_text(
        f"🃏 ESTILO DOS CARDS\n\nAtual: {st.card_style}\n\nSelecione:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mia_setcard_"))
async def set_card_style(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    style = cb.data.replace("mia_setcard_", "")
    if style not in ("flat", "rounded", "elevated", "outlined"):
        await cb.answer("Estilo inválido.")
        return
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        st.card_style = style
        await s.commit()
    await log_admin(cb.from_user.id, "miniapp_card_style", {"new": style})
    await card_style_menu(cb)


@router.callback_query(F.data == "mia_icon_style")
async def icon_style_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()
    styles = {
        "filled": "⚫ Preenchido",
        "outlined": "⭕ Contornado",
        "rounded": "🔵 Arredondado",
        "sharp": "🔺 Angular",
    }
    buttons = []
    for key, label in styles.items():
        mark = "☑️" if st.icon_style == key else "⬜"
        buttons.append([InlineKeyboardButton(
            text=f"{mark} {label}", callback_data=f"mia_seticon_{key}"
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_visual")])
    await cb.message.edit_text(
        f"🎯 ESTILO DOS ÍCONES\n\nAtual: {st.icon_style}\n\nSelecione:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mia_seticon_"))
async def set_icon_style(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    style = cb.data.replace("mia_seticon_", "")
    if style not in ("filled", "outlined", "rounded", "sharp"):
        await cb.answer("Estilo inválido.")
        return
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        st.icon_style = style
        await s.commit()
    await log_admin(cb.from_user.id, "miniapp_icon_style", {"new": style})
    await icon_style_menu(cb)


# ============================================================
# TIPOGRAFIA
# ============================================================
@router.callback_query(F.data == "mia_typography")
async def typography_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()
    text = (
        "◈ BELLAMY | TIPOGRAFIA\n\n"
        f"🔤 Fonte: {st.font_family}\n"
        f"📝 Tamanho base: {st.font_size_base}px\n"
        f"📢 Tamanho título: {st.font_size_heading}px\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔤 Alterar fonte", callback_data="mia_setfont")],
        [InlineKeyboardButton(text="📝 Tamanho base", callback_data="mia_setfontsize_base")],
        [InlineKeyboardButton(text="📢 Tamanho título", callback_data="mia_setfontsize_heading")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_visual")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "mia_setfont")
async def set_font_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.update_data(text_field="font_family")
    await state.set_state(MIS.edit_text)
    await cb.message.answer(
        "🔤 FONTE\n\nEnvie o nome da fonte.\n\n"
        "Sugestões:\n"
        "• <code>Inter</code>\n"
        "• <code>Roboto</code>\n"
        "• <code>Poppins</code>\n"
        "• <code>SF Pro Display</code>\n"
        "• <code>Manrope</code>"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mia_setfontsize_"))
async def set_font_size_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    which = cb.data.replace("mia_setfontsize_", "")
    field_map = {"base": "font_size_base", "heading": "font_size_heading"}
    field = field_map.get(which)
    if not field:
        await cb.answer("Campo inválido.")
        return
    await state.update_data(number_field=field, min_val=10, max_val=64)
    await state.set_state(MIS.edit_number)
    await cb.message.answer(
        f"📝 Envie o novo tamanho em pixels (10-64):"
    )
    await cb.answer()


# ============================================================
# LAYOUT E ANIMAÇÕES
# ============================================================
@router.callback_query(F.data == "mia_layout")
async def layout_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()
    text = (
        "◈ BELLAMY | LAYOUT E ANIMAÇÕES\n\n"
        f"📐 Border radius: {st.border_radius}px\n"
        f"📏 Espaçamento: {st.spacing_unit}px\n"
        f"✨ Animações: {'🟢 Ativadas' if st.animation_enabled else '🔴 Desativadas'}\n"
        f"⚡ Velocidade: {st.animation_speed}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📐 Alterar border radius", callback_data="mia_setnum_border_radius")],
        [InlineKeyboardButton(text="📏 Alterar espaçamento", callback_data="mia_setnum_spacing_unit")],
        [InlineKeyboardButton(
            text=("🔴 Desativar animações" if st.animation_enabled else "🟢 Ativar animações"),
            callback_data="mia_toggle_anim")],
        [InlineKeyboardButton(text="⚡ Alterar velocidade", callback_data="mia_anim_speed")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_visual")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("mia_setnum_"))
async def set_number_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    field = cb.data.replace("mia_setnum_", "")
    ranges = {
        "border_radius": (0, 40, "📐 Envie o border radius em pixels (0-40):"),
        "spacing_unit": (2, 32, "📏 Envie o espaçamento em pixels (2-32):"),
    }
    if field not in ranges:
        await cb.answer("Campo inválido.")
        return
    min_v, max_v, prompt = ranges[field]
    await state.update_data(number_field=field, min_val=min_v, max_val=max_v)
    await state.set_state(MIS.edit_number)
    await cb.message.answer(prompt)
    await cb.answer()


@router.message(MIS.edit_number)
async def set_number_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    try:
        val = int(raw)
    except ValueError:
        await m.answer("❌ Valor inválido. Envie um número inteiro.")
        return

    data = await state.get_data()
    field = data.get("number_field")
    min_v = data.get("min_val", 0)
    max_v = data.get("max_val", 1000)
    if not field:
        await state.clear()
        await m.answer("❌ Sessão expirada.")
        return
    if val < min_v or val > max_v:
        await m.answer(f"❌ Valor fora do intervalo ({min_v}-{max_v}).")
        return

    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        old = getattr(st, field)
        setattr(st, field, val)
        await s.commit()

    await log_admin(m.from_user.id, f"miniapp_set_{field}", {"old": old, "new": val})
    await state.clear()
    await m.answer(f"✅ Valor atualizado: {val}")

    # Retorna ao menu adequado
    if field in ("font_size_base", "font_size_heading"):
        st = await get_or_create_settings()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔤 Alterar fonte", callback_data="mia_setfont")],
            [InlineKeyboardButton(text="📝 Tamanho base", callback_data="mia_setfontsize_base")],
            [InlineKeyboardButton(text="📢 Tamanho título", callback_data="mia_setfontsize_heading")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_visual")],
        ])
        await m.answer(
            f"◈ BELLAMY | TIPOGRAFIA\n\n"
            f"🔤 Fonte: {st.font_family}\n"
            f"📝 Tamanho base: {st.font_size_base}px\n"
            f"📢 Tamanho título: {st.font_size_heading}px",
            reply_markup=kb,
        )


@router.message(MIS.edit_text)
async def set_text_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if len(raw) < 2 or len(raw) > 100:
        await m.answer("❌ Valor deve ter 2-100 caracteres.")
        return

    data = await state.get_data()
    field = data.get("text_field")
    if not field:
        await state.clear()
        await m.answer("❌ Sessão expirada.")
        return

    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        setattr(st, field, raw)
        await s.commit()

    await log_admin(m.from_user.id, f"miniapp_set_{field}", {"new": raw})
    await state.clear()
    await m.answer(f"✅ Fonte atualizada: {raw}")

    # Reabre tipografia
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔤 Alterar fonte", callback_data="mia_setfont")],
        [InlineKeyboardButton(text="📝 Tamanho base", callback_data="mia_setfontsize_base")],
        [InlineKeyboardButton(text="📢 Tamanho título", callback_data="mia_setfontsize_heading")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_visual")],
    ])
    await m.answer(
        f"◈ BELLAMY | TIPOGRAFIA\n\n"
        f"🔤 Fonte: {st.font_family}\n"
        f"📝 Tamanho base: {st.font_size_base}px\n"
        f"📢 Tamanho título: {st.font_size_heading}px",
        reply_markup=kb,
    )


@router.callback_query(F.data == "mia_toggle_anim")
async def toggle_animations(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        st.animation_enabled = not st.animation_enabled
        new = st.animation_enabled
        await s.commit()
    await log_admin(cb.from_user.id, "miniapp_toggle_animations", {"new": new})
    await layout_menu(cb)


@router.callback_query(F.data == "mia_anim_speed")
async def anim_speed_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()
    speeds = {
        "slow": "🐢 Lenta",
        "normal": "⚡ Normal",
        "fast": "⚡⚡ Rápida",
    }
    buttons = []
    for key, label in speeds.items():
        mark = "☑️" if st.animation_speed == key else "⬜"
        buttons.append([InlineKeyboardButton(
            text=f"{mark} {label}", callback_data=f"mia_setspeed_{key}"
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_layout")])
    await cb.message.edit_text(
        f"⚡ VELOCIDADE DAS ANIMAÇÕES\n\nAtual: {st.animation_speed}\n\nSelecione:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mia_setspeed_"))
async def set_anim_speed(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    speed = cb.data.replace("mia_setspeed_", "")
    if speed not in ("slow", "normal", "fast"):
        await cb.answer("Velocidade inválida.")
        return
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        st.animation_speed = speed
        await s.commit()
    await log_admin(cb.from_user.id, "miniapp_animation_speed", {"new": speed})
    await anim_speed_menu(cb)


# ============================================================
# LOGO E IMAGENS
# ============================================================
@router.callback_query(F.data == "mia_images")
async def images_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()

    text = (
        "◈ BELLAMY | LOGO E IMAGENS\n\n"
        f"📱 Nome do app: {st.app_name}\n"
        f"🏷️ Nome curto: {st.app_short_name}\n"
        f"📝 Descrição: {st.app_description or '(vazia)'}\n\n"
        f"🖼️ Logo: {st.logo_url or '❌ não configurado'}\n"
        f"🖼️ Banner: {st.banner_url or '❌ não configurado'}\n"
        f"🖼️ Favicon: {st.favicon_url or '❌ não configurado'}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Nome do app", callback_data="mia_set_app_name")],
        [InlineKeyboardButton(text="✏️ Nome curto", callback_data="mia_set_app_short")],
        [InlineKeyboardButton(text="✏️ Descrição", callback_data="mia_set_app_desc")],
        [InlineKeyboardButton(text="🖼️ Logo", callback_data="mia_set_url_logo_url")],
        [InlineKeyboardButton(text="🖼️ Banner", callback_data="mia_set_url_banner_url")],
        [InlineKeyboardButton(text="🖼️ Favicon", callback_data="mia_set_url_favicon_url")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


APP_FIELDS = {
    "mia_set_app_name": ("app_name", "Nome do app (2-64 chars):"),
    "mia_set_app_short": ("app_short_name", "Nome curto (2-20 chars):"),
    "mia_set_app_desc": ("app_description", "Descrição (até 500 chars):"),
}


@router.callback_query(F.data.in_(list(APP_FIELDS.keys())))
async def set_app_field_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    field, prompt = APP_FIELDS[cb.data]
    await state.update_data(text_field=field)
    await state.set_state(MIS.edit_text)
    await cb.message.answer(f"✏️ {prompt}")
    await cb.answer()


# Correção: sobrescreve o handler de texto para reconhecer campos do app também
@router.message(MIS.edit_text)
async def set_text_save_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""

    data = await state.get_data()
    field = data.get("text_field")
    if not field:
        await state.clear()
        await m.answer("❌ Sessão expirada.")
        return

    limits = {
        "app_name": (2, 64),
        "app_short_name": (2, 20),
        "app_description": (0, 500),
        "font_family": (2, 100),
    }
    lo, hi = limits.get(field, (2, 200))
    if len(raw) < lo or len(raw) > hi:
        await m.answer(f"❌ Valor deve ter {lo}-{hi} caracteres.")
        return

    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        setattr(st, field, raw)
        await s.commit()

    await log_admin(m.from_user.id, f"miniapp_set_{field}", {"new": raw[:100]})
    await state.clear()
    await m.answer(f"✅ Campo atualizado.")

    # Volta ao menu apropriado
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)

    if field.startswith("app_"):
        text = (
            "◈ BELLAMY | LOGO E IMAGENS\n\n"
            f"📱 Nome do app: {st.app_name}\n"
            f"🏷️ Nome curto: {st.app_short_name}\n"
            f"📝 Descrição: {st.app_description or '(vazia)'}\n\n"
            f"🖼️ Logo: {st.logo_url or '❌ não configurado'}\n"
            f"🖼️ Banner: {st.banner_url or '❌ não configurado'}\n"
            f"🖼️ Favicon: {st.favicon_url or '❌ não configurado'}\n"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Nome do app", callback_data="mia_set_app_name")],
            [InlineKeyboardButton(text="✏️ Nome curto", callback_data="mia_set_app_short")],
            [InlineKeyboardButton(text="✏️ Descrição", callback_data="mia_set_app_desc")],
            [InlineKeyboardButton(text="🖼️ Logo", callback_data="mia_set_url_logo_url")],
            [InlineKeyboardButton(text="🖼️ Banner", callback_data="mia_set_url_banner_url")],
            [InlineKeyboardButton(text="🖼️ Favicon", callback_data="mia_set_url_favicon_url")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")],
        ])
        await m.answer(text, reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔤 Alterar fonte", callback_data="mia_setfont")],
            [InlineKeyboardButton(text="📝 Tamanho base", callback_data="mia_setfontsize_base")],
            [InlineKeyboardButton(text="📢 Tamanho título", callback_data="mia_setfontsize_heading")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_visual")],
        ])
        await m.answer(
            f"◈ BELLAMY | TIPOGRAFIA\n\n"
            f"🔤 Fonte: {st.font_family}\n"
            f"📝 Tamanho base: {st.font_size_base}px\n"
            f"📢 Tamanho título: {st.font_size_heading}px",
            reply_markup=kb,
        )


# ============================================================
# URLS (logo, banner, favicon)
# ============================================================
URL_FIELDS = {
    "logo_url": "🖼️ LOGO\n\nEnvie a URL pública da imagem (ou /limpar):",
    "banner_url": "🖼️ BANNER\n\nEnvie a URL pública da imagem (ou /limpar):",
    "favicon_url": "🖼️ FAVICON\n\nEnvie a URL pública do ícone (ou /limpar):",
}


@router.callback_query(F.data.startswith("mia_set_url_"))
async def set_url_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    field = cb.data.replace("mia_set_url_", "")
    if field not in URL_FIELDS:
        await cb.answer("Campo inválido.")
        return
    await state.update_data(url_field=field)
    await state.set_state(MIS.edit_url)
    await cb.message.answer(URL_FIELDS[field])
    await cb.answer()


@router.message(MIS.edit_url)
async def set_url_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    data = await state.get_data()
    field = data.get("url_field")
    if not field:
        await state.clear()
        return

    if raw.lower() == "/limpar":
        new_val = None
    else:
        if not (raw.startswith("http://") or raw.startswith("https://")):
            await m.answer("❌ URL inválida. Deve começar com http:// ou https://")
            return
        new_val = raw

    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        setattr(st, field, new_val)
        await s.commit()

    await log_admin(m.from_user.id, f"miniapp_set_{field}", {"url": new_val})
    await state.clear()
    await m.answer("✅ URL atualizada.")

    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
    text = (
        "◈ BELLAMY | LOGO E IMAGENS\n\n"
        f"📱 Nome do app: {st.app_name}\n"
        f"🏷️ Nome curto: {st.app_short_name}\n"
        f"📝 Descrição: {st.app_description or '(vazia)'}\n\n"
        f"🖼️ Logo: {st.logo_url or '❌ não configurado'}\n"
        f"🖼️ Banner: {st.banner_url or '❌ não configurado'}\n"
        f"🖼️ Favicon: {st.favicon_url or '❌ não configurado'}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Nome do app", callback_data="mia_set_app_name")],
        [InlineKeyboardButton(text="✏️ Nome curto", callback_data="mia_set_app_short")],
        [InlineKeyboardButton(text="✏️ Descrição", callback_data="mia_set_app_desc")],
        [InlineKeyboardButton(text="🖼️ Logo", callback_data="mia_set_url_logo_url")],
        [InlineKeyboardButton(text="🖼️ Banner", callback_data="mia_set_url_banner_url")],
        [InlineKeyboardButton(text="🖼️ Favicon", callback_data="mia_set_url_favicon_url")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")],
    ])
    await m.answer(text, reply_markup=kb)


# ============================================================
# MENU DE NAVEGAÇÃO
# ============================================================
@router.callback_query(F.data == "mia_menu_items")
async def menu_items_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()
    visibility = st.menu_items_visible or {}
    visible_count = sum(1 for k in MENU_ITEMS if visibility.get(k, True))

    buttons = []
    for key, label in MENU_ITEMS.items():
        vis = visibility.get(key, True)
        icon = "🟢" if vis else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {label}",
            callback_data=f"mia_mi_toggle_{key}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")])

    await cb.message.edit_text(
        f"◈ BELLAMY | MENU DE NAVEGAÇÃO\n\n"
        f"Itens visíveis: {visible_count}/{len(MENU_ITEMS)}\n\n"
        f"🟢 = visível | 🔴 = oculto\n\n"
        f"Toque para alternar:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mia_mi_toggle_"))
async def toggle_menu_item(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("mia_mi_toggle_", "")
    if key not in MENU_ITEMS:
        await cb.answer("Item inválido.")
        return

    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        vis = dict(st.menu_items_visible or {})
        for k in MENU_ITEMS:
            vis.setdefault(k, True)
        vis[key] = not vis[key]
        st.menu_items_visible = vis
        await s.commit()
        new_val = vis[key]

    await log_admin(cb.from_user.id, "miniapp_toggle_menu_item", {
        "item": key, "new": new_val,
    })

    # Recarrega
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
    visibility = st.menu_items_visible or {}
    visible_count = sum(1 for k in MENU_ITEMS if visibility.get(k, True))
    buttons = []
    for k, label in MENU_ITEMS.items():
        v = visibility.get(k, True)
        buttons.append([InlineKeyboardButton(
            text=f"{'🟢' if v else '🔴'} {label}",
            callback_data=f"mia_mi_toggle_{k}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")])

    await cb.message.edit_text(
        f"◈ BELLAMY | MENU DE NAVEGAÇÃO\n\n"
        f"Itens visíveis: {visible_count}/{len(MENU_ITEMS)}\n\n"
        f"🟢 = visível | 🔴 = oculto\n\n"
        f"Toque para alternar:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer("Atualizado.")


# ============================================================
# PREVIEW
# ============================================================
@router.callback_query(F.data == "mia_preview")
async def preview(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()

    visible_items = [
        MENU_ITEMS[k] for k in MENU_ITEMS
        if (st.menu_items_visible or {}).get(k, True)
    ]

    colors_demo = (
        f"<b>🎨 Preview do Tema</b>\n\n"
        f"<b>📱 {st.app_name}</b>\n"
        f"<i>{st.app_description or ''}</i>\n\n"
        f"<b>Modo:</b> {st.theme_mode.title()}\n"
        f"<b>Card:</b> {st.card_style} | <b>Ícone:</b> {st.icon_style}\n"
        f"<b>Fonte:</b> {st.font_family} ({st.font_size_base}px / {st.font_size_heading}px)\n"
        f"<b>Radius:</b> {st.border_radius}px | <b>Spacing:</b> {st.spacing_unit}px\n"
        f"<b>Animação:</b> {st.animation_speed if st.animation_enabled else 'desativada'}\n\n"
        f"<b>🎨 Cores</b>\n"
        f"• Primária: <code>{st.primary_color}</code>\n"
        f"• Secundária: <code>{st.secondary_color}</code>\n"
        f"• Accent: <code>{st.accent_color}</code>\n"
        f"• Fundo: <code>{st.background_color}</code>\n"
        f"• Surface: <code>{st.surface_color}</code>\n"
        f"• Texto: <code>{st.text_color}</code>\n"
        f"• Texto secundário: <code>{st.text_secondary_color}</code>\n"
        f"• Sucesso: <code>{st.success_color}</code>\n"
        f"• Alerta: <code>{st.warning_color}</code>\n"
        f"• Perigo: <code>{st.danger_color}</code>\n\n"
        f"<b>📋 Menu ({len(visible_items)} itens)</b>\n"
        + "\n".join(f"• {x}" for x in visible_items)
    )

    caption = "👁️ <b>PREVIEW DO TEMA</b>\n\nEsta é uma representação textual do tema atual."
    try:
        if st.banner_url:
            await bot.send_photo(
                cb.from_user.id, photo=st.banner_url,
                caption=caption + "\n\n" + colors_demo,
            )
        else:
            await bot.send_message(cb.from_user.id, caption + "\n\n" + colors_demo)
    except Exception as e:
        await bot.send_message(cb.from_user.id, f"❌ Erro: {str(e)[:200]}")

    await cb.answer("👁️ Preview enviado.")


# ============================================================
# DRAFT E PUBLICAÇÃO
# ============================================================
@router.callback_query(F.data == "mia_draft")
async def draft_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()

    if not st.draft_theme:
        await cb.message.edit_text(
            "📝 NENHUM DRAFT PENDENTE\n\n"
            "Não há um tema em rascunho no momento.\n\n"
            "Ao fazer alterações, você pode salvar como draft antes de publicar.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💾 Salvar tema atual como draft", callback_data="mia_draft_save")],
                [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")],
            ]),
        )
        await cb.answer()
        return

    draft = st.draft_theme
    text = (
        "📝 DRAFT PENDENTE\n\n"
        f"📱 Nome: {draft.get('app_name', '—')}\n"
        f"🎨 Primária: {draft.get('primary_color', '—')}\n"
        f"🌗 Modo: {draft.get('theme_mode', '—')}\n"
        f"📐 Radius: {draft.get('border_radius', '—')}px\n"
        f"🔤 Fonte: {draft.get('font_family', '—')}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Salvar novamente (sobrescrever)", callback_data="mia_draft_save")],
        [InlineKeyboardButton(text="🗑️ Descartar draft", callback_data="mia_draft_discard")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "mia_draft_save")
async def draft_save(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        st.draft_theme = config_to_dict(st)
        await s.commit()

    await log_admin(cb.from_user.id, "miniapp_draft_save", {})
    await cb.answer("💾 Draft salvo.")
    await cb.message.edit_text(
        "✅ DRAFT SALVO\n\nO tema atual foi salvo como draft.\n"
        "Você pode continuar editando ou publicar quando estiver pronto.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💾 Publicar agora", callback_data="mia_publish")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")],
        ]),
    )


@router.callback_query(F.data == "mia_draft_discard")
async def draft_discard(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        st.draft_theme = None
        await s.commit()
    await log_admin(cb.from_user.id, "miniapp_draft_discard", {})
    await cb.message.edit_text(
        "🗑️ Draft descartado.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")],
        ]),
    )
    await cb.answer("Draft descartado.")


@router.callback_query(F.data == "mia_publish")
async def publish_prompt(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()
    has_draft = st.draft_theme is not None
    text = (
        "💾 PUBLICAR TEMA\n\n"
        f"{'📝 Draft pendente detectado.' if has_draft else '📌 Nenhum draft pendente.'}\n\n"
        f"O tema será publicado e ficará ativo para todos os usuários da Mini App.\n\n"
        f"Confirmar publicação?"
    )
    await cb.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Sim, publicar", callback_data="mia_publish_ok")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data="admin_miniapp")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data == "mia_publish_ok")
async def publish_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        old_config = config_to_dict(st)

        # Se tem draft, aplica o draft
        if st.draft_theme:
            for key, val in st.draft_theme.items():
                if hasattr(st, key):
                    setattr(st, key, val)
            st.draft_theme = None

        st.published_at = datetime.utcnow()
        st.published_by = cb.from_user.id

        new_config = config_to_dict(st)

        s.add(MiniAppThemeHistory(
            old_config=old_config,
            new_config=new_config,
            action="publish",
            admin_id=cb.from_user.id,
        ))
        await s.commit()

    await log_admin(cb.from_user.id, "miniapp_publish", {})
    await cb.message.edit_text(
        "✅ TEMA PUBLICADO!\n\nAs alterações estão ativas para todos os usuários.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁️ Ver preview", callback_data="mia_preview")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")],
        ]),
    )
    await cb.answer("Tema publicado.")


# ============================================================
# AUTENTICAÇÃO
# ============================================================
@router.callback_query(F.data == "mia_auth")
async def auth_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    st = await get_or_create_settings()
    text = (
        "◈ BELLAMY | AUTENTICAÇÃO DA MINI APP\n\n"
        f"🔐 Status: {'🟢 Ativa' if st.auth_enabled else '🔴 Inativa'}\n"
        f"⏱️ Expiração do token: {st.auth_token_expiry_hours}h\n"
        f"🔑 2FA: {'🟢 Ativado' if st.require_2fa else '🔴 Desativado'}\n\n"
        f"A autenticação valida a assinatura do Telegram (initData) no servidor\n"
        f"e emite um token de sessão temporário."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar autenticação" if st.auth_enabled else "🟢 Ativar autenticação"),
            callback_data="mia_auth_toggle")],
        [InlineKeyboardButton(
            text=f"⏱️ Alterar expiração ({st.auth_token_expiry_hours}h)",
            callback_data="mia_auth_expiry")],
        [InlineKeyboardButton(
            text=("🔴 Desativar 2FA" if st.require_2fa else "🟢 Ativar 2FA"),
            callback_data="mia_auth_2fa")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "mia_auth_toggle")
async def auth_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        st.auth_enabled = not st.auth_enabled
        new = st.auth_enabled
        await s.commit()
    await log_admin(cb.from_user.id, "miniapp_auth_toggle", {"new": new})
    await auth_menu(cb)


@router.callback_query(F.data == "mia_auth_2fa")
async def auth_2fa_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        st.require_2fa = not st.require_2fa
        new = st.require_2fa
        await s.commit()
    await log_admin(cb.from_user.id, "miniapp_2fa_toggle", {"new": new})
    await auth_menu(cb)


@router.callback_query(F.data == "mia_auth_expiry")
async def auth_expiry_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.update_data(number_field="auth_token_expiry_hours", min_val=1, max_val=720)
    await state.set_state(MIS.edit_number)
    await cb.message.answer("⏱️ Envie a expiração do token em horas (1-720):")
    await cb.answer()


# ============================================================
# SESSÕES
# ============================================================
@router.callback_query(F.data.startswith("mia_sessions_"))
async def sessions_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(MiniAppSession.id))
        )).scalar() or 0
        stmt = (
            select(MiniAppSession)
            .order_by(desc(MiniAppSession.created_at))
            .offset(page * SESSION_PAGE_SIZE)
            .limit(SESSION_PAGE_SIZE)
        )
        sessions = (await s.execute(stmt)).scalars().all()

        uids = list({s.user_id for s in sessions})
        users = {}
        if uids:
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}

    text = f"👥 SESSÕES DA MINI APP\n\nTotal: {total} | Página {page + 1}\n\n"

    buttons = []
    if not sessions:
        text += "Nenhuma sessão registrada."
    else:
        now = datetime.utcnow()
        for sess in sessions:
            u = users.get(sess.user_id)
            uname = (u.full_name if u else f"User {sess.user_id}")[:20]
            expired = sess.expires_at < now
            active = sess.is_active and not expired
            icon = "🟢" if active else ("⚪" if expired else "🔴")
            status = "Ativa" if active else ("Expirada" if expired else "Desativada")
            text += (
                f"{icon} {uname} (<code>{sess.user_id}</code>)\n"
                f"   {status} | {sess.created_at.strftime('%d/%m %H:%M')}\n\n"
            )
            buttons.append([InlineKeyboardButton(
                text=f"{icon} {uname} — {status}",
                callback_data=f"mia_session_view_{sess.id}",
            )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"mia_sessions_{page - 1}"))
    if (page + 1) * SESSION_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"mia_sessions_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🧹 Limpar expiradas", callback_data="mia_sessions_cleanup")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("mia_session_view_"))
async def session_detail(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    sess_id = int(cb.data.replace("mia_session_view_", ""))

    async with AsyncSessionLocal() as s:
        sess = await s.get(MiniAppSession, sess_id)
        if not sess:
            await cb.answer("Sessão não encontrada.", show_alert=True)
            return
        user = await s.get(User, sess.user_id)

    uname = (user.full_name if user else f"User {sess.user_id}")[:30]
    expired = sess.expires_at < datetime.utcnow()
    active = sess.is_active and not expired

    text = (
        f"👤 SESSÃO #{sess.id}\n\n"
        f"👤 Usuário: {uname}\n"
        f"🆔 ID: <code>{sess.user_id}</code>\n\n"
        f"📌 Status: {'🟢 Ativa' if active else ('⚪ Expirada' if expired else '🔴 Desativada')}\n"
        f"📅 Criada: {sess.created_at.strftime('%d/%m/%Y %H:%M')}\n"
        f"⏱️ Expira: {sess.expires_at.strftime('%d/%m/%Y %H:%M')}\n"
    )
    if sess.last_used_at:
        text += f"🕐 Último uso: {sess.last_used_at.strftime('%d/%m/%Y %H:%M')}\n"
    if sess.ip_address:
        text += f"🌐 IP: <code>{sess.ip_address}</code>\n"

    buttons = []
    if active:
        buttons.append([InlineKeyboardButton(
            text="🚫 Revogar sessão",
            callback_data=f"mia_session_revoke_{sess.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_sessions_0")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("mia_session_revoke_"))
async def revoke_session(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    sess_id = int(cb.data.replace("mia_session_revoke_", ""))
    async with AsyncSessionLocal() as s:
        sess = await s.get(MiniAppSession, sess_id)
        if sess:
            sess.is_active = False
            await s.commit()
    await log_admin(cb.from_user.id, "miniapp_revoke_session", {"session_id": sess_id})
    await cb.answer("✅ Sessão revogada.", show_alert=True)
    await sessions_list(cb)


@router.callback_query(F.data == "mia_sessions_cleanup")
async def cleanup_sessions(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    now = datetime.utcnow()
    async with AsyncSessionLocal() as s:
        expired = (await s.execute(
            select(MiniAppSession).where(MiniAppSession.expires_at < now)
        )).scalars().all()
        count = len(expired)
        for x in expired:
            await s.delete(x)
        await s.commit()
    await log_admin(cb.from_user.id, "miniapp_cleanup_sessions", {"removed": count})
    await cb.answer(f"🧹 {count} sessão(ões) removida(s).", show_alert=True)


# ============================================================
# HISTÓRICO DE TEMAS
# ============================================================
@router.callback_query(F.data.startswith("mia_history_"))
async def history_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(MiniAppThemeHistory.id))
        )).scalar() or 0
        stmt = (
            select(MiniAppThemeHistory)
            .order_by(desc(MiniAppThemeHistory.created_at))
            .offset(page * HISTORY_PAGE_SIZE)
            .limit(HISTORY_PAGE_SIZE)
        )
        history = (await s.execute(stmt)).scalars().all()

    text = f"📜 HISTÓRICO DE TEMAS\n\nTotal: {total} | Página {page + 1}\n\n"
    buttons = []
    if not history:
        text += "Nenhuma alteração registrada."
    else:
        for h in history:
            icon = "💾" if h.action == "publish" else ("📝" if h.action == "save_draft" else "🔄")
            text += (
                f"{icon} {h.created_at.strftime('%d/%m/%Y %H:%M')} — {h.action}\n"
                f"   Admin <code>{h.admin_id}</code>\n\n"
            )
            buttons.append([InlineKeyboardButton(
                text=f"{icon} {h.created_at.strftime('%d/%m %H:%M')} — {h.action}",
                callback_data=f"mia_hist_view_{h.id}",
            )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"mia_history_{page - 1}"))
    if (page + 1) * HISTORY_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"mia_history_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("mia_hist_view_"))
async def history_detail(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    hid = int(cb.data.replace("mia_hist_view_", ""))
    async with AsyncSessionLocal() as s:
        h = await s.get(MiniAppThemeHistory, hid)
        if not h:
            await cb.answer("Registro não encontrado.", show_alert=True)
            return

    old = h.old_config or {}
    new = h.new_config or {}
    diffs = []
    keys = set(old.keys()) | set(new.keys())
    for k in keys:
        if old.get(k) != new.get(k):
            diffs.append(f"• <b>{k}</b>\n   {old.get(k)} → {new.get(k)}")

    text = (
        f"📜 HISTÓRICO #{h.id}\n\n"
        f"🕒 {h.created_at.strftime('%d/%m/%Y %H:%M')}\n"
        f"👤 Admin: <code>{h.admin_id}</code>\n"
        f"⚙️ Ação: {h.action}\n\n"
    )
    if diffs:
        text += "📊 <b>Campos alterados</b>\n\n" + "\n".join(diffs[:20])
    else:
        text += "Nenhuma diferença detectada."

    await cb.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Restaurar esta versão", callback_data=f"mia_hist_restore_{h.id}")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="mia_history_0")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mia_hist_restore_"))
async def history_restore(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    hid = int(cb.data.replace("mia_hist_restore_", ""))

    async with AsyncSessionLocal() as s:
        h = await s.get(MiniAppThemeHistory, hid)
        if not h or not h.old_config:
            await cb.answer("Registro inválido.", show_alert=True)
            return

        st = await s.get(MiniAppSettings, 1)
        current = config_to_dict(st)

        for key, val in h.old_config.items():
            if hasattr(st, key):
                setattr(st, key, val)

        s.add(MiniAppThemeHistory(
            old_config=current,
            new_config=h.old_config,
            action="restore",
            admin_id=cb.from_user.id,
        ))
        await s.commit()

    await log_admin(cb.from_user.id, "miniapp_restore_theme", {"history_id": hid})
    await cb.message.edit_text(
        "✅ Versão restaurada.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")],
        ]),
    )
    await cb.answer("Restaurado.")


# ============================================================
# RESET
# ============================================================
@router.callback_query(F.data == "mia_reset")
async def reset_prompt(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await cb.message.edit_text(
        "⚠️ RESTAURAR PADRÃO\n\n"
        "Todas as configurações da Mini App voltarão ao padrão original.\n"
        "O histórico será mantido.\n\n"
        "Confirmar restauração?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Sim, restaurar", callback_data="mia_reset_ok")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data="admin_miniapp")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data == "mia_reset_ok")
async def reset_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        st = await s.get(MiniAppSettings, 1)
        old = config_to_dict(st)

        st.app_name = "BELLAMY ADMIN"
        st.app_short_name = "Bellamy"
        st.app_description = "Painel administrativo Bellamy"
        st.logo_url = None
        st.banner_url = None
        st.favicon_url = None
        st.primary_color = "#6C5CE7"
        st.secondary_color = "#A29BFE"
        st.accent_color = "#00B894"
        st.background_color = "#0F0F23"
        st.surface_color = "#1A1A35"
        st.text_color = "#FFFFFF"
        st.text_secondary_color = "#B0B0C0"
        st.border_color = "#2A2A45"
        st.success_color = "#00B894"
        st.warning_color = "#FDCB6E"
        st.danger_color = "#E17055"
        st.theme_mode = "auto"
        st.border_radius = 12
        st.spacing_unit = 8
        st.animation_enabled = True
        st.animation_speed = "normal"
        st.font_family = "Inter"
        st.font_size_base = 14
        st.font_size_heading = 20
        st.menu_items_visible = {k: True for k in MENU_ITEMS}
        st.card_style = "rounded"
        st.icon_style = "filled"
        st.auth_enabled = True
        st.auth_token_expiry_hours = 24
        st.require_2fa = False
        st.draft_theme = None

        new = config_to_dict(st)
        s.add(MiniAppThemeHistory(
            old_config=old,
            new_config=new,
            action="reset",
            admin_id=cb.from_user.id,
        ))
        await s.commit()

    await log_admin(cb.from_user.id, "miniapp_reset", {})
    await cb.message.edit_text(
        "✅ Configurações restauradas ao padrão.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_miniapp")],
        ]),
    )
    await cb.answer("Restaurado.")


# ============================================================
# EXPORTAÇÃO
# ============================================================
@router.callback_query(F.data == "mia_export")
async def export_config(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    st = await get_or_create_settings()
    cfg = config_to_dict(st)

    async with AsyncSessionLocal() as s:
        sessions_count = (await s.execute(
            select(func.count(MiniAppSession.id))
        )).scalar() or 0
        history_count = (await s.execute(
            select(func.count(MiniAppThemeHistory.id))
        )).scalar() or 0

    import json
    lines = []
    lines.append("═" * 70)
    lines.append("BELLAMY | CONFIGURAÇÃO DA MINI APP")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append("═" * 70)
    lines.append("")
    lines.append("CONFIGURAÇÃO ATUAL")
    lines.append("─" * 70)
    lines.append(json.dumps(cfg, indent=2, ensure_ascii=False, default=str))
    lines.append("")
    lines.append("─" * 70)
    lines.append(f"Sessões registradas: {sessions_count}")
    lines.append(f"Histórico de temas: {history_count}")
    if st.published_at:
        lines.append(f"Última publicação: {st.published_at.strftime('%d/%m/%Y %H:%M')}")
    else:
        lines.append("Última publicação: nunca")

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"miniapp_config_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption="📊 <b>Configuração da Mini App</b>",
    )
    await cb.answer("Exportação gerada.")


# ============================================================
# HELPERS PARA O APP / MINI APP
# ============================================================
async def create_session(user_id: int, ip: str | None = None, ua: str | None = None) -> MiniAppSession:
    st = await get_or_create_settings()
    if not st.auth_enabled:
        raise Exception("Mini App auth disabled")

    token = gen_session_token()
    expires = datetime.utcnow() + timedelta(hours=st.auth_token_expiry_hours)

    async with AsyncSessionLocal() as s:
        sess = MiniAppSession(
            user_id=user_id,
            token=token,
            ip_address=ip,
            user_agent=(ua or "")[:500],
            expires_at=expires,
            is_active=True,
        )
        s.add(sess)
        await s.commit()
        await s.refresh(sess)
        return sess


async def validate_session(token: str) -> MiniAppSession | None:
    async with AsyncSessionLocal() as s:
        sess = (await s.execute(
            select(MiniAppSession).where(MiniAppSession.token == token)
        )).scalar_one_or_none()
        if not sess:
            return None
        if not sess.is_active:
            return None
        if sess.expires_at < datetime.utcnow():
            return None
        sess.last_used_at = datetime.utcnow()
        await s.commit()
        return sess


async def get_public_config() -> dict:
    """Retorna a configuração pública para ser consumida pela Mini App."""
    st = await get_or_create_settings()
    cfg = config_to_dict(st)
    # Remove campos sensíveis
    cfg.pop("draft_theme", None)
    cfg.pop("auth_token_expiry_hours", None)
    cfg.pop("require_2fa", None)
    return cfg
