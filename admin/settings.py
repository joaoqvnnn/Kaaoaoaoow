from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, desc
from datetime import datetime, timedelta

from bot import dp
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import BotSettings, User, AdminLog
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

class SettingsStates(StatesGroup):
    waiting_maintenance_message = State()
    waiting_support_url = State()
    waiting_user_id_block = State()
    waiting_block_reason = State()
    waiting_block_duration = State()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def log_admin_action(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as session:
        session.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await session.commit()

# ---------- Menu principal de configurações ----------
def settings_menu_keyboard(s: BotSettings) -> InlineKeyboardMarkup:
    bot_status = "🟢 Ligado" if s.bot_enabled else "🔴 Desligado"
    maint_status = "🟢 Ativo" if s.maintenance_mode else "⚪ Inativo"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🤖 Bot: {bot_status}", callback_data="cfg_toggle_bot")],
        [InlineKeyboardButton(text=f"🛠️ Manutenção: {maint_status}", callback_data="cfg_toggle_maintenance")],
        [InlineKeyboardButton(text="📝 Editar mensagem de manutenção", callback_data="cfg_edit_maintenance_msg")],
        [InlineKeyboardButton(text="🔗 Configurar link de suporte", callback_data="cfg_edit_support_url")],
        [InlineKeyboardButton(text="🚫 Bloqueios de módulos", callback_data="cfg_module_blocks")],
        [InlineKeyboardButton(text="👤 Bloquear/Desbloquear usuário", callback_data="cfg_user_block")],
        [InlineKeyboardButton(text="📋 Auditoria", callback_data="cfg_audit_log")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])

async def show_settings_menu(event: Message | CallbackQuery):
    settings = await get_settings(force=True)
    text = (
        "◈ BELLAMY | CONFIGURAÇÕES DO BOT\n\n"
        f"🤖 Bot: {'Ligado' if settings.bot_enabled else 'Desligado'}\n"
        f"🛠️ Manutenção: {'Ativa' if settings.maintenance_mode else 'Inativa'}\n"
        f"🚫 Pedidos: {'Bloqueados' if settings.block_new_orders else 'Liberados'}\n"
        f"💳 Recargas: {'Bloqueadas' if settings.block_new_recharges else 'Liberadas'}\n"
        f"🛒 Compras: {'Bloqueadas' if settings.block_purchases else 'Liberadas'}\n\n"
        "👇 Selecione uma opção:"
    )
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=settings_menu_keyboard(settings))
    else:
        await event.answer(text, reply_markup=settings_menu_keyboard(settings))

@router.callback_query(F.data == "admin_settings")
async def admin_settings_entry(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Acesso negado.", show_alert=True)
        return
    await show_settings_menu(callback)
    await callback.answer()

# ---------- Toggle bot ----------
@router.callback_query(F.data == "cfg_toggle_bot")
async def toggle_bot(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        s = await session.get(BotSettings, 1)
        s.bot_enabled = not s.bot_enabled
        await session.commit()
        await log_admin_action(callback.from_user.id, "toggle_bot", {"new": s.bot_enabled})
    invalidate_cache()
    await callback.answer(f"Bot {'ativado' if s.bot_enabled else 'desativado'}.")
    await show_settings_menu(callback)

# ---------- Toggle manutenção ----------
@router.callback_query(F.data == "cfg_toggle_maintenance")
async def toggle_maintenance(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        s = await session.get(BotSettings, 1)
        s.maintenance_mode = not s.maintenance_mode
        await session.commit()
        await log_admin_action(callback.from_user.id, "toggle_maintenance", {"new": s.maintenance_mode})
    invalidate_cache()
    await callback.answer(f"Manutenção {'ativada' if s.maintenance_mode else 'desativada'}.")
    await show_settings_menu(callback)

# ---------- Editar mensagem de manutenção ----------
@router.callback_query(F.data == "cfg_edit_maintenance_msg")
async def edit_maintenance_msg_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await state.set_state(SettingsStates.waiting_maintenance_message)
    await callback.message.answer("Envie o novo texto da mensagem de manutenção:")
    await callback.answer()

@router.message(SettingsStates.waiting_maintenance_message)
async def edit_maintenance_msg_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        s = await session.get(BotSettings, 1)
        s.maintenance_message = message.text
        await session.commit()
        await log_admin_action(message.from_user.id, "edit_maintenance_message",
                                {"new": message.text[:200]})
    invalidate_cache()
    await state.clear()
    await message.answer("✅ Mensagem de manutenção atualizada.")

# ---------- Editar link de suporte ----------
@router.callback_query(F.data == "cfg_edit_support_url")
async def edit_support_url_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await state.set_state(SettingsStates.waiting_support_url)
    await callback.message.answer("Envie o link de suporte (ex.: https://t.me/seu_suporte):")
    await callback.answer()

@router.message(SettingsStates.waiting_support_url)
async def edit_support_url_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    url = message.text.strip()
    async with AsyncSessionLocal() as session:
        s = await session.get(BotSettings, 1)
        s.support_url = url
        await session.commit()
        await log_admin_action(message.from_user.id, "edit_support_url", {"new": url})
    invalidate_cache()
    await state.clear()
    await message.answer("✅ Link de suporte atualizado.")

# ---------- Bloqueios de módulos ----------
def module_blocks_keyboard(s: BotSettings) -> InlineKeyboardMarkup:
    def toggle(label, key, value):
        return InlineKeyboardButton(
            text=f"{'🔴' if value else '🟢'} {label}",
            callback_data=f"cfg_mod_{key}"
        )
    return InlineKeyboardMarkup(inline_keyboard=[
        [toggle("Pedidos", "orders", s.block_new_orders)],
        [toggle("Recargas", "recharges", s.block_new_recharges)],
        [toggle("Compras", "purchases", s.block_purchases)],
        [toggle("Catálogo", "catalog", s.catalog_enabled)],
        [toggle("Carrinho", "cart", s.cart_enabled)],
        [toggle("Afiliados", "affiliates", s.affiliates_enabled)],
        [toggle("Conversão de pontos", "points", s.points_conversion_enabled)],
        [toggle("Registro de usuários", "registration", s.registration_enabled)],
        [toggle("Consulta de pedidos", "order_query", s.allow_order_query)],
        [toggle("Acesso ao perfil", "profile", s.allow_profile_access)],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_settings")],
    ])

@router.callback_query(F.data == "cfg_module_blocks")
async def module_blocks(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    s = await get_settings(force=True)
    await callback.message.edit_text(
        "◈ BELLAMY | BLOQUEIOS DE MÓDULOS\n\n"
        "🔴 = bloqueado | 🟢 = liberado\n\n"
        "Toque para alternar:",
        reply_markup=module_blocks_keyboard(s),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cfg_mod_"))
async def toggle_module(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    key = callback.data.replace("cfg_mod_", "")
    mapping = {
        "orders": "block_new_orders",
        "recharges": "block_new_recharges",
        "purchases": "block_purchases",
        "catalog": "catalog_enabled",
        "cart": "cart_enabled",
        "affiliates": "affiliates_enabled",
        "points": "points_conversion_enabled",
        "registration": "registration_enabled",
        "order_query": "allow_order_query",
        "profile": "allow_profile_access",
    }
    field = mapping.get(key)
    if not field:
        await callback.answer("Módulo inválido.")
        return
    async with AsyncSessionLocal() as session:
        s = await session.get(BotSettings, 1)
        new_val = not getattr(s, field)
        setattr(s, field, new_val)
        await session.commit()
        await log_admin_action(callback.from_user.id, f"toggle_module_{key}", {"new": new_val})
    invalidate_cache()
    s = await get_settings(force=True)
    await callback.message.edit_reply_markup(reply_markup=module_blocks_keyboard(s))
    await callback.answer("Atualizado.")

# ---------- Bloqueio de usuário ----------
@router.callback_query(F.data == "cfg_user_block")
async def user_block_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await state.set_state(SettingsStates.waiting_user_id_block)
    await callback.message.answer("Envie o ID do Telegram do usuário que deseja bloquear/desbloquear:")
    await callback.answer()

@router.message(SettingsStates.waiting_user_id_block)
async def user_block_receive_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ ID inválido. Envie apenas números.")
        return
    async with AsyncSessionLocal() as session:
        user = await session.get(User, uid)
        if not user:
            await message.answer("❌ Usuário não encontrado.")
            await state.clear()
            return
        status = "🔴 Bloqueado" if user.is_blocked else "🟢 Liberado"
        detail = f"Usuário: {user.full_name} (@{user.username})\nID: {uid}\nStatus: {status}"
        if user.block_reason:
            detail += f"\nMotivo: {user.block_reason}"
    await state.update_data(target_user_id=uid)
    await state.set_state(None)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 Bloquear permanentemente", callback_data="cfg_block_perm")],
        [InlineKeyboardButton(text="⏳ Bloquear por 30 dias", callback_data="cfg_block_temp")],
        [InlineKeyboardButton(text="🔓 Desbloquear", callback_data="cfg_unblock")],
        [InlineKeyboardButton(text="↩️ Cancelar", callback_data="cfg_cancel_block")],
    ])
    await message.answer(detail + "\n\nEscolha uma ação:", reply_markup=kb)

@router.callback_query(F.data == "cfg_block_perm")
async def block_perm(callback: CallbackQuery, state: FSMContext):
    await state.update_data(block_type="perm")
    await state.set_state(SettingsStates.waiting_block_reason)
    await callback.message.answer("Informe o motivo do bloqueio permanente:")
    await callback.answer()

@router.callback_query(F.data == "cfg_block_temp")
async def block_temp(callback: CallbackQuery, state: FSMContext):
    await state.update_data(block_type="temp")
    await state.set_state(SettingsStates.waiting_block_reason)
    await callback.message.answer("Informe o motivo do bloqueio temporário (30 dias):")
    await callback.answer()

@router.message(SettingsStates.waiting_block_reason)
async def block_reason_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    uid = data["target_user_id"]
    block_type = data["block_type"]
    reason = message.text
    async with AsyncSessionLocal() as session:
        user = await session.get(User, uid)
        user.is_blocked = True
        user.block_reason = reason
        user.blocked_by = message.from_user.id
        user.blocked_until = (
            datetime.utcnow() + timedelta(days=30) if block_type == "temp" else None
        )
        await session.commit()
        await log_admin_action(message.from_user.id, "block_user",
                                {"user_id": uid, "type": block_type, "reason": reason})
    await state.clear()
    await message.answer(f"✅ Usuário {uid} bloqueado com sucesso.")

@router.callback_query(F.data == "cfg_unblock")
async def unblock_user(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    uid = data.get("target_user_id")
    if not uid:
        await callback.answer("Sessão expirada.")
        return
    async with AsyncSessionLocal() as session:
        user = await session.get(User, uid)
        if user:
            user.is_blocked = False
            user.blocked_until = None
            user.block_reason = None
            user.blocked_by = None
            await session.commit()
            await log_admin_action(callback.from_user.id, "unblock_user", {"user_id": uid})
    await state.clear()
    await callback.message.answer(f"✅ Usuário {uid} desbloqueado.")
    await callback.answer()

@router.callback_query(F.data == "cfg_cancel_block")
async def cancel_block(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Operação cancelada.")
    await callback.answer()

# ---------- Auditoria ----------
@router.callback_query(F.data == "cfg_audit_log")
async def audit_log(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AdminLog).order_by(desc(AdminLog.created_at)).limit(20)
        )
        logs = result.scalars().all()
    if not logs:
        text = "📋 Nenhuma ação registrada ainda."
    else:
        lines = []
        for log in logs:
            ts = log.created_at.strftime("%d/%m %H:%M")
            lines.append(f"• {ts} — Admin {log.admin_id}: {log.action}")
        text = "📋 ÚLTIMAS AÇÕES ADMINISTRATIVAS\n\n" + "\n".join(lines)
    await callback.message.answer(text)
    await callback.answer()
