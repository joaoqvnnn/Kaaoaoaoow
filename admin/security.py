from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, or_, and_
from datetime import datetime, timedelta
import secrets
import string
import hashlib

from bot import dp, bot
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    AdminUser, AdminSession, LoginAttempt, BackupRecord,
    AdminLog, User, BotSettings,
)
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

LOGS_PAGE_SIZE = 15
SESSIONS_PAGE_SIZE = 12
ATTEMPTS_PAGE_SIZE = 15
ADMINS_PAGE_SIZE = 10


# ============================================================
# PERMISSÕES E NÍVEIS
# ============================================================
PERMISSIONS = {
    "manage_admins":       ("👑 Gerenciar administradores",     "owner"),
    "manage_products":     ("📦 Gerenciar produtos",            "product_manager"),
    "manage_stock":        ("🗃️ Gerenciar estoque",             "product_manager"),
    "manage_orders":       ("🛒 Gerenciar pedidos",             "product_manager"),
    "manage_users":        ("👥 Gerenciar usuários",            "admin"),
    "manage_wallets":      ("💎 Gerenciar carteiras",           "financial"),
    "manage_recharges":    ("💳 Gerenciar recargas",            "financial"),
    "manage_coupons":      ("🎟️ Gerenciar cupons",              "financial"),
    "manage_deeplinks":    ("🔗 Gerenciar deep links",          "admin"),
    "manage_notifications":("🔔 Gerenciar notificações",        "admin"),
    "manage_channels":     ("📢 Gerenciar canais",              "admin"),
    "manage_ranking":      ("🏆 Gerenciar ranking",             "admin"),
    "manage_affiliates":   ("🤝 Gerenciar afiliados",           "admin"),
    "manage_customization":("🎨 Personalizar mensagens",        "admin"),
    "manage_miniapp":      ("📱 Gerenciar Mini App",            "admin"),
    "manage_support":      ("💬 Gerenciar suporte",             "support"),
    "manage_security":     ("🛡️ Gerenciar segurança",           "owner"),
    "modify_settings":     ("⚙️ Alterar configurações",         "admin"),
    "toggle_maintenance":  ("🛠️ Ativar/desativar manutenção",   "admin"),
    "view_reports":        ("📊 Ver relatórios",                "support"),
    "view_financial":      ("💰 Ver dados financeiros",         "financial"),
    "export_data":         ("📥 Exportar dados",                "admin"),
}

ROLES = {
    "owner": {
        "label": "👑 Proprietário",
        "description": "Acesso total ao sistema",
        "permissions": set(PERMISSIONS.keys()),
    },
    "admin": {
        "label": "⚙️ Administrador",
        "description": "Gerencia módulos operacionais",
        "permissions": {
            "manage_products", "manage_stock", "manage_orders", "manage_users",
            "manage_wallets", "manage_recharges", "manage_coupons",
            "manage_deeplinks", "manage_notifications", "manage_channels",
            "manage_ranking", "manage_affiliates", "manage_customization",
            "manage_miniapp", "manage_support", "modify_settings",
            "toggle_maintenance", "view_reports", "view_financial", "export_data",
        },
    },
    "product_manager": {
        "label": "📦 Gerente de Produtos",
        "description": "Produtos, estoque e pedidos",
        "permissions": {
            "manage_products", "manage_stock", "manage_orders",
            "manage_coupons", "view_reports", "export_data",
        },
    },
    "financial": {
        "label": "💰 Financeiro",
        "description": "Carteiras, recargas e relatórios financeiros",
        "permissions": {
            "manage_wallets", "manage_recharges", "manage_coupons",
            "manage_orders", "view_financial", "view_reports", "export_data",
        },
    },
    "support": {
        "label": "💬 Atendimento",
        "description": "Consulta de pedidos e suporte, sem acesso financeiro",
        "permissions": {
            "manage_support", "view_reports",
        },
    },
}


def _token() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(48))


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def is_admin(uid: int) -> bool:
    """Compatibilidade retroativa com módulos anteriores."""
    return uid in ADMIN_IDS


async def get_admin(uid: int) -> AdminUser | None:
    async with AsyncSessionLocal() as s:
        return await s.get(AdminUser, uid)


async def has_permission(uid: int, permission: str) -> bool:
    """
    Verifica permissão de um usuário.
    - ADMIN_IDS (env) têm todas as permissões.
    - Owner sempre tem tudo.
    - AdminUser na tabela tem as permissões do role + extras - revogadas.
    """
    if uid in ADMIN_IDS:
        return True
    admin = await get_admin(uid)
    if not admin or not admin.is_active or admin.is_blocked:
        return False
    role = ROLES.get(admin.role)
    if not role:
        return False
    base = set(role["permissions"])
    extra = set(admin.extra_permissions or [])
    revoked = set(admin.revoked_permissions or [])
    return permission in (base | extra) - revoked


async def require_permission(uid: int, permission: str) -> bool:
    ok = await has_permission(uid, permission)
    if not ok:
        await log_login_attempt(uid, "permission_denied", detail=permission)
    return ok


async def log_admin_action(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


async def log_login_attempt(
    user_id: int,
    attempt_type: str,
    success: bool = True,
    detail: str | None = None,
    ip: str | None = None,
):
    async with AsyncSessionLocal() as s:
        s.add(LoginAttempt(
            user_id=user_id,
            attempt_type=attempt_type,
            success=success,
            detail=(detail or "")[:300],
            ip_address=ip,
        ))
        await s.commit()


# ============================================================
# ESTADOS
# ============================================================
class SECS(StatesGroup):
    add_admin_id = State()
    add_admin_role = State()
    remove_admin_confirm = State()
    block_admin_reason = State()
    search_logs = State()
    backup_note = State()
    set_max_attempts = State()
    set_session_hours = State()
    set_lockout_minutes = State()


# ============================================================
# HELPERS DE RENDER
# ============================================================
def permissions_count(admin: AdminUser) -> int:
    role = ROLES.get(admin.role)
    if not role:
        return 0
    base = set(role["permissions"])
    extra = set(admin.extra_permissions or [])
    revoked = set(admin.revoked_permissions or [])
    return len((base | extra) - revoked)


async def ensure_owner_exists() -> AdminUser | None:
    """Garante que existe pelo menos um owner (usa o primeiro ADMIN_IDS)."""
    if not ADMIN_IDS:
        return None
    owner_id = ADMIN_IDS[0]
    async with AsyncSessionLocal() as s:
        admin = await s.get(AdminUser, owner_id)
        if admin:
            return admin
        admin = AdminUser(
            user_id=owner_id,
            role="owner",
            is_active=True,
            is_blocked=False,
        )
        s.add(admin)
        await s.commit()
        await s.refresh(admin)
        return admin


# ============================================================
# MENU PRINCIPAL
# ============================================================
@router.callback_query(F.data == "admin_security")
async def security_menu(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        if not is_admin(cb.from_user.id):
            await cb.answer("⛔ Acesso negado.", show_alert=True)
            return

    await ensure_owner_exists()

    today = datetime.utcnow().date()
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        total_admins = (await s.execute(
            select(func.count(AdminUser.user_id))
        )).scalar() or 0
        active_admins = (await s.execute(
            select(func.count(AdminUser.user_id))
            .where(AdminUser.is_active == True)
            .where(AdminUser.is_blocked == False)
        )).scalar() or 0
        blocked_admins = (await s.execute(
            select(func.count(AdminUser.user_id))
            .where(AdminUser.is_blocked == True)
        )).scalar() or 0

        active_sessions = (await s.execute(
            select(func.count(AdminSession.id))
            .where(AdminSession.is_active == True)
            .where(AdminSession.expires_at > datetime.utcnow())
        )).scalar() or 0

        failed_today = (await s.execute(
            select(func.count(LoginAttempt.id))
            .where(LoginAttempt.success == False)
            .where(LoginAttempt.attempt_type.in_(["login", "permission_denied"]))
            .where(LoginAttempt.created_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0

        logs_today = (await s.execute(
            select(func.count(AdminLog.id))
            .where(AdminLog.created_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0
        logs_month = (await s.execute(
            select(func.count(AdminLog.id))
            .where(AdminLog.created_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0

        backups_count = (await s.execute(
            select(func.count(BackupRecord.id))
        )).scalar() or 0
        last_backup = (await s.execute(
            select(BackupRecord).order_by(desc(BackupRecord.created_at)).limit(1)
        )).scalar_one_or_none()

    settings = await get_settings()
    maintenance = getattr(settings, "maintenance_mode", False)
    bot_on = getattr(settings, "bot_enabled", True)
    require_2fa = getattr(settings, "require_2fa_admins", False)
    max_attempts = getattr(settings, "max_login_attempts", 5)
    lockout_min = getattr(settings, "login_lockout_minutes", 15)

    text = (
        "◈ BELLAMY | SEGURANÇA E ADMINISTRADORES\n\n"
        f"👑 <b>ADMINISTRADORES</b>\n"
        f"📊 Total: {total_admins}\n"
        f"🟢 Ativos: {active_admins}\n"
        f"🔴 Bloqueados: {blocked_admins}\n\n"
        f"🔐 <b>SESSÕES</b>\n"
        f"🟢 Ativas agora: {active_sessions}\n\n"
        f"🛡️ <b>SEGURANÇA</b>\n"
        f"🔑 2FA obrigatório: {'🟢 Sim' if require_2fa else '🔴 Não'}\n"
        f"🔒 Tentativas máximas: {max_attempts}\n"
        f"⏱️ Bloqueio após falhas: {lockout_min} min\n"
        f"⚠️ Falhas hoje: {failed_today}\n\n"
        f"⚙️ <b>SISTEMA</b>\n"
        f"🤖 Bot: {'🟢 Online' if bot_on else '🔴 Offline'}\n"
        f"🛠️ Manutenção: {'🟢 Ativa' if maintenance else '⚪ Inativa'}\n\n"
        f"📋 <b>AUDITORIA</b>\n"
        f"📅 Ações hoje: {logs_today} | Este mês: {logs_month}\n\n"
        f"💾 <b>BACKUPS</b>\n"
        f"📦 Total: {backups_count}\n"
    )
    if last_backup:
        text += f"🕐 Último: {last_backup.created_at.strftime('%d/%m/%Y %H:%M')}\n"

    text += "\n👇 Selecione uma opção:"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 Gerenciar administradores", callback_data="sec_admins_0")],
        [InlineKeyboardButton(text="🎖️ Níveis e permissões", callback_data="sec_roles")],
        [InlineKeyboardButton(text=f"🔐 Sessões ativas ({active_sessions})", callback_data="sec_sessions_0")],
        [InlineKeyboardButton(text=f"⚠️ Tentativas de login ({failed_today} falhas hoje)", callback_data="sec_attempts_0")],
        [InlineKeyboardButton(text="📋 Auditoria de ações", callback_data="sec_logs_0")],
        [InlineKeyboardButton(text="🛠️ Modo manutenção", callback_data="sec_maintenance")],
        [InlineKeyboardButton(text="🔑 Configurações de segurança", callback_data="sec_config")],
        [InlineKeyboardButton(text="💾 Backup do banco", callback_data="sec_backups_0")],
        [InlineKeyboardButton(text="🔒 Bloqueios de emergência", callback_data="sec_lockdown")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# ADMINISTRADORES — LISTA
# ============================================================
@router.callback_query(F.data.startswith("sec_admins_"))
async def list_admins(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(AdminUser.user_id)))).scalar() or 0
        stmt = (
            select(AdminUser)
            .order_by(desc(AdminUser.created_at))
            .offset(page * ADMINS_PAGE_SIZE)
            .limit(ADMINS_PAGE_SIZE)
        )
        admins = (await s.execute(stmt)).scalars().all()

        uids = [a.user_id for a in admins]
        users = {}
        if uids:
            users = {
                x.id: x for x in (await s.execute(
                    select(User).where(User.id.in_(uids))
                )).scalars().all()
            }

    text = f"👑 ADMINISTRADORES\n\nTotal: {total} | Página {page + 1}\n\n"
    buttons = []

    if not admins:
        text += "Nenhum administrador cadastrado."
    else:
        for a in admins:
            u = users.get(a.user_id)
            uname = (u.full_name if u else f"User {a.user_id}")[:22]
            uhandle = f"@{u.username}" if u and u.username else "—"
            role_label = ROLES.get(a.role, {}).get("label", a.role)
            status = "🔴" if a.is_blocked else ("🟢" if a.is_active else "⚪")
            text += (
                f"{status} {uname} ({uhandle})\n"
                f"   {role_label} | {permissions_count(a)} permissões\n\n"
            )
            buttons.append([InlineKeyboardButton(
                text=f"{status} {uname} — {role_label}",
                callback_data=f"sec_admin_view_{a.user_id}",
            )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"sec_admins_{page - 1}"))
    if (page + 1) * ADMINS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"sec_admins_{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="➕ Adicionar administrador", callback_data="sec_admin_add")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_security")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# ADMIN — DETALHE
# ============================================================
async def _render_admin(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as s:
        a = await s.get(AdminUser, user_id)
        if not a:
            return (
                "❌ Administrador não encontrado.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Voltar", callback_data="sec_admins_0")]
                ]),
            )
        user = await s.get(User, user_id)

        actions_count = (await s.execute(
            select(func.count(AdminLog.id)).where(AdminLog.admin_id == user_id)
        )).scalar() or 0
        last_action = (await s.execute(
            select(AdminLog)
            .where(AdminLog.admin_id == user_id)
            .order_by(desc(AdminLog.created_at))
            .limit(1)
        )).scalar_one_or_none()

        sessions_active = (await s.execute(
            select(func.count(AdminSession.id))
            .where(AdminSession.user_id == user_id)
            .where(AdminSession.is_active == True)
            .where(AdminSession.expires_at > datetime.utcnow())
        )).scalar() or 0

    uname = (user.full_name if user else f"User {user_id}")[:30]
    uhandle = f"@{user.username}" if user and user.username else "—"
    role = ROLES.get(a.role, {})
    role_label = role.get("label", a.role)
    role_desc = role.get("description", "")

    status_icon = "🔴 Bloqueado" if a.is_blocked else ("🟢 Ativo" if a.is_active else "⚪ Inativo")
    base_perms = role.get("permissions", set())
    extra = set(a.extra_permissions or [])
    revoked = set(a.revoked_permissions or [])
    effective = (base_perms | extra) - revoked

    text = (
        f"👑 ADMINISTRADOR — {uname}\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💠 Username: {uhandle}\n"
        f"📌 Status: {status_icon}\n"
        f"🎖️ Nível: {role_label}\n"
        f"<i>{role_desc}</i>\n\n"
        f"📅 Adicionado: {a.created_at.strftime('%d/%m/%Y %H:%M')}\n"
        f"🕐 Último acesso: {a.last_login_at.strftime('%d/%m/%Y %H:%M') if a.last_login_at else 'nunca'}\n"
        f"🔐 Sessões ativas: {sessions_active}\n\n"
        f"🔑 2FA: {'🟢 Ativado' if a.twofa_enabled else '🔴 Desativado'}\n"
        f"🎫 Token 2FA: {'configurado' if a.twofa_secret else 'não configurado'}\n\n"
        f"📊 <b>PERMISSÕES EFETIVAS</b> ({len(effective)})\n"
        f"Base: {len(base_perms)} | Extras: {len(extra)} | Revogadas: {len(revoked)}\n\n"
        f"📋 <b>ATIVIDADES</b>\n"
        f"📊 Total de ações: {actions_count}\n"
    )
    if last_action:
        text += f"🕐 Última ação: {last_action.created_at.strftime('%d/%m/%Y %H:%M')} ({last_action.action})\n"

    if a.is_blocked and a.block_reason:
        text += f"\n🚫 Motivo do bloqueio: {a.block_reason}\n"

    buttons = [
        [InlineKeyboardButton(text="🎖️ Alterar nível", callback_data=f"sec_admin_role_{user_id}")],
        [InlineKeyboardButton(text="🔑 Gerenciar permissões", callback_data=f"sec_admin_perms_{user_id}")],
        [InlineKeyboardButton(
            text=("🔴 Bloquear acesso" if not a.is_blocked else "🟢 Desbloquear acesso"),
            callback_data=f"sec_admin_toggleblock_{user_id}")],
        [InlineKeyboardButton(
            text=("🔴 Desativar" if a.is_active else "🟢 Reativar"),
            callback_data=f"sec_admin_toggleactive_{user_id}")],
        [InlineKeyboardButton(
            text=("🔒 Revogar todas as sessões" if sessions_active > 0 else "🔒 Sem sessões"),
            callback_data=f"sec_admin_revoke_{user_id}")],
        [InlineKeyboardButton(text="📋 Ver histórico completo", callback_data=f"sec_admin_logs_{user_id}_0")],
        [InlineKeyboardButton(text="👤 Ver usuário", callback_data=f"usr_view_{user_id}")],
        [InlineKeyboardButton(text="🗑️ Remover administrador", callback_data=f"sec_admin_remove_{user_id}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="sec_admins_0")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("sec_admin_view_"))
async def view_admin(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    uid = int(cb.data.replace("sec_admin_view_", ""))
    text, kb = await _render_admin(uid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# ADICIONAR ADMIN
# ============================================================
@router.callback_query(F.data == "sec_admin_add")
async def add_admin_start(cb: CallbackQuery, state: FSMContext):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    await state.set_state(SECS.add_admin_id)
    await cb.message.answer(
        "➕ ADICIONAR ADMINISTRADOR (1/2)\n\n"
        "Envie o ID do Telegram do usuário que será promovido:"
    )
    await cb.answer()


@router.message(SECS.add_admin_id)
async def add_admin_id(m: Message, state: FSMContext):
    if not await has_permission(m.from_user.id, "manage_admins"):
        return
    raw = m.text.strip() if m.text else ""
    if not raw.isdigit():
        await m.answer("❌ Envie apenas o ID numérico.")
        return
    uid = int(raw)

    async with AsyncSessionLocal() as s:
        user = await s.get(User, uid)
        if not user:
            await m.answer(f"❌ Usuário {uid} não encontrado no banco.")
            return
        existing = await s.get(AdminUser, uid)
        if existing:
            await state.clear()
            await m.answer(f"⚠️ Este usuário já é administrador ({ROLES.get(existing.role, {}).get('label', existing.role)}).")
            text, kb = await _render_admin(uid)
            await m.answer(text, reply_markup=kb)
            return

    await state.update_data(add_admin_id=uid)
    await state.set_state(SECS.add_admin_role)

    buttons = []
    for key, role in ROLES.items():
        if key == "owner":
            continue  # Owner só pode ser criado via env
        buttons.append([InlineKeyboardButton(
            text=f"{role['label']} — {role['description'][:30]}",
            callback_data=f"sec_setrole_{key}",
        )])
    buttons.append([InlineKeyboardButton(text="❌ Cancelar", callback_data="sec_admin_add_cancel")])

    await m.answer(
        f"➕ ADICIONAR ADMINISTRADOR (2/2)\n\n"
        f"👤 Usuário: {user.full_name} (<code>{uid}</code>)\n\n"
        f"Selecione o nível de acesso:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "sec_admin_add_cancel")
async def add_admin_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Operação cancelada.")
    await cb.answer()


@router.callback_query(F.data.startswith("sec_setrole_"))
async def add_admin_role(cb: CallbackQuery, state: FSMContext):
    if not await has_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    role = cb.data.replace("sec_setrole_", "")
    if role not in ROLES or role == "owner":
        await cb.answer("Nível inválido.")
        return

    data = await state.get_data()
    uid = data.get("add_admin_id")
    if not uid:
        await cb.answer("Sessão expirada.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        admin = AdminUser(
            user_id=uid,
            role=role,
            is_active=True,
            is_blocked=False,
            added_by=cb.from_user.id,
        )
        s.add(admin)
        await s.commit()

    await log_admin_action(cb.from_user.id, "admin_add", {
        "user_id": uid, "role": role,
    })
    await state.clear()

    await cb.message.edit_text(
        f"✅ Administrador adicionado.\n\n"
        f"👤 ID: <code>{uid}</code>\n"
        f"🎖️ Nível: {ROLES[role]['label']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁️ Ver admin", callback_data=f"sec_admin_view_{uid}")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="sec_admins_0")],
        ]),
    )
    await cb.answer("Administrador adicionado.")


# ============================================================
# ALTERAR NÍVEL
# ============================================================
@router.callback_query(F.data.startswith("sec_admin_role_"))
async def change_role_menu(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    uid = int(cb.data.replace("sec_admin_role_", ""))
    async with AsyncSessionLocal() as s:
        a = await s.get(AdminUser, uid)
        if not a:
            await cb.answer("Admin não encontrado.", show_alert=True)
            return

    if a.role == "owner":
        await cb.answer("O nível Proprietário não pode ser alterado.", show_alert=True)
        return

    buttons = []
    for key, role in ROLES.items():
        if key == "owner":
            continue
        mark = "☑️" if a.role == key else "⬜"
        buttons.append([InlineKeyboardButton(
            text=f"{mark} {role['label']}",
            callback_data=f"sec_changerole_{uid}_{key}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"sec_admin_view_{uid}")])

    await cb.message.edit_text(
        f"🎖️ ALTERAR NÍVEL — User {uid}\n\n"
        f"Atual: {ROLES.get(a.role, {}).get('label', a.role)}\n\nSelecione:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sec_changerole_"))
async def change_role(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    rest = cb.data.replace("sec_changerole_", "")
    parts = rest.rsplit("_", 1)
    uid = int(parts[0])
    new_role = parts[1]
    if new_role not in ROLES or new_role == "owner":
        await cb.answer("Nível inválido.")
        return

    async with AsyncSessionLocal() as s:
        a = await s.get(AdminUser, uid)
        if not a:
            await cb.answer("Admin não encontrado.", show_alert=True)
            return
        old = a.role
        a.role = new_role
        await s.commit()

    await log_admin_action(cb.from_user.id, "admin_change_role", {
        "user_id": uid, "old": old, "new": new_role,
    })
    text, kb = await _render_admin(uid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Nível atualizado.")


# ============================================================
# GERENCIAR PERMISSÕES INDIVIDUAIS
# ============================================================
@router.callback_query(F.data.startswith("sec_admin_perms_"))
async def perms_menu(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    uid = int(cb.data.replace("sec_admin_perms_", ""))
    async with AsyncSessionLocal() as s:
        a = await s.get(AdminUser, uid)
        if not a:
            await cb.answer("Admin não encontrado.", show_alert=True)
            return

    role = ROLES.get(a.role, {"permissions": set(), "label": a.role})
    base = role["permissions"]
    extra = set(a.extra_permissions or [])
    revoked = set(a.revoked_permissions or [])

    text = (
        f"🔑 PERMISSÕES — User {uid}\n\n"
        f"🎖️ Nível base: {role['label']}\n\n"
        f"🟢 = permitida | 🔴 = revogada | ➕ = extra\n\n"
        f"Toque para alternar individualmente:\n\n"
    )
    buttons = []
    for perm, (label, _) in PERMISSIONS.items():
        is_base = perm in base
        is_extra = perm in extra
        is_revoked = perm in revoked

        if is_revoked:
            status = "🔴"
        elif is_extra:
            status = "➕"
        elif is_base:
            status = "🟢"
        else:
            status = "⚪"

        buttons.append([InlineKeyboardButton(
            text=f"{status} {label}",
            callback_data=f"sec_perm_toggle_{uid}_{perm}",
        )])

    buttons.append([InlineKeyboardButton(text="🔄 Restaurar padrão do nível", callback_data=f"sec_perm_reset_{uid}")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=f"sec_admin_view_{uid}")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("sec_perm_toggle_"))
async def perm_toggle(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    rest = cb.data.replace("sec_perm_toggle_", "")
    parts = rest.split("_", 1)
    uid = int(parts[0])
    perm = parts[1]
    if perm not in PERMISSIONS:
        await cb.answer("Permissão inválida.")
        return

    async with AsyncSessionLocal() as s:
        a = await s.get(AdminUser, uid)
        if not a:
            await cb.answer("Admin não encontrado.", show_alert=True)
            return
        role_perms = ROLES.get(a.role, {}).get("permissions", set())
        extra = set(a.extra_permissions or [])
        revoked = set(a.revoked_permissions or [])

        # Ciclo: base → revogada → extra → base
        if perm in base_perms_safe(role_perms) and perm not in revoked:
            revoked.add(perm)
            extra.discard(perm)
        elif perm in revoked:
            revoked.discard(perm)
            if perm not in role_perms:
                extra.add(perm)
        else:
            # Não está no base e não está em extra/revoked — adiciona como extra
            if perm in extra:
                extra.discard(perm)
            else:
                extra.add(perm)

        a.extra_permissions = list(extra)
        a.revoked_permissions = list(revoked)
        await s.commit()

    await log_admin_action(cb.from_user.id, "admin_toggle_permission", {
        "user_id": uid, "permission": perm,
    })
    await perms_menu(cb)


def base_perms_safe(s):
    return s if s else set()


@router.callback_query(F.data.startswith("sec_perm_reset_"))
async def perm_reset(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    uid = int(cb.data.replace("sec_perm_reset_", ""))
    async with AsyncSessionLocal() as s:
        a = await s.get(AdminUser, uid)
        if not a:
            await cb.answer("Admin não encontrado.", show_alert=True)
            return
        a.extra_permissions = None
        a.revoked_permissions = None
        await s.commit()

    await log_admin_action(cb.from_user.id, "admin_reset_permissions", {"user_id": uid})
    await perms_menu(cb)


# ============================================================
# TOGGLES DE STATUS
# ============================================================
@router.callback_query(F.data.startswith("sec_admin_toggleblock_"))
async def toggle_block(cb: CallbackQuery, state: FSMContext):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    uid = int(cb.data.replace("sec_admin_toggleblock_", ""))

    async with AsyncSessionLocal() as s:
        a = await s.get(AdminUser, uid)
        if not a:
            await cb.answer("Admin não encontrado.", show_alert=True)
            return
        if a.role == "owner":
            await cb.answer("Proprietário não pode ser bloqueado.", show_alert=True)
            return

        if a.is_blocked:
            a.is_blocked = False
            a.block_reason = None
            await s.commit()
            await log_admin_action(cb.from_user.id, "admin_unblock", {"user_id": uid})
            text, kb = await _render_admin(uid)
            await cb.message.edit_text(text, reply_markup=kb)
            await cb.answer("Desbloqueado.")
        else:
            await state.update_data(block_admin_id=uid)
            await state.set_state(SECS.block_admin_reason)
            await cb.message.answer(f"🔴 Bloquear admin {uid}?\n\nEnvie o motivo:")
            await cb.answer()


@router.message(SECS.block_admin_reason)
async def block_reason(m: Message, state: FSMContext):
    if not await has_permission(m.from_user.id, "manage_admins"):
        return
    raw = m.text.strip() if m.text else ""
    if len(raw) < 3:
        await m.answer("❌ Mínimo 3 caracteres.")
        return
    data = await state.get_data()
    uid = data.get("block_admin_id")
    await state.clear()
    if not uid:
        return
    async with AsyncSessionLocal() as s:
        a = await s.get(AdminUser, uid)
        if not a:
            await m.answer("Admin não encontrado.")
            return
        a.is_blocked = True
        a.block_reason = raw[:300]
        await s.commit()
    await log_admin_action(m.from_user.id, "admin_block", {"user_id": uid, "reason": raw[:100]})
    await m.answer(f"✅ Admin {uid} bloqueado.")
    text, kb = await _render_admin(uid)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("sec_admin_toggleactive_"))
async def toggle_active(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    uid = int(cb.data.replace("sec_admin_toggleactive_", ""))
    async with AsyncSessionLocal() as s:
        a = await s.get(AdminUser, uid)
        if not a:
            await cb.answer("Admin não encontrado.", show_alert=True)
            return
        if a.role == "owner":
            await cb.answer("Proprietário não pode ser desativado.", show_alert=True)
            return
        a.is_active = not a.is_active
        await s.commit()
    await log_admin_action(cb.from_user.id, "admin_toggle_active", {"user_id": uid, "new": a.is_active})
    text, kb = await _render_admin(uid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data.startswith("sec_admin_revoke_"))
async def revoke_all_sessions(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    uid = int(cb.data.replace("sec_admin_revoke_", ""))
    async with AsyncSessionLocal() as s:
        sessions = (await s.execute(
            select(AdminSession).where(AdminSession.user_id == uid)
        )).scalars().all()
        count = 0
        for x in sessions:
            if x.is_active:
                x.is_active = False
                count += 1
        await s.commit()
    await log_admin_action(cb.from_user.id, "admin_revoke_sessions", {"user_id": uid, "count": count})
    text, kb = await _render_admin(uid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer(f"{count} sessão(ões) revogada(s).")


@router.callback_query(F.data.startswith("sec_admin_remove_"))
async def remove_admin_prompt(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    uid = int(cb.data.replace("sec_admin_remove_", ""))
    async with AsyncSessionLocal() as s:
        a = await s.get(AdminUser, uid)
        if not a:
            await cb.answer("Admin não encontrado.", show_alert=True)
            return
        if a.role == "owner":
            await cb.answer("Proprietário não pode ser removido.", show_alert=True)
            return

    await cb.message.edit_text(
        f"🗑️ REMOVER ADMINISTRADOR\n\n"
        f"👤 ID: <code>{uid}</code>\n\n"
        f"⚠️ O acesso administrativo será revogado.\n"
        f"O histórico será mantido.\n\nConfirmar remoção?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Sim, remover", callback_data=f"sec_admin_remove_ok_{uid}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"sec_admin_view_{uid}")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sec_admin_remove_ok_"))
async def remove_admin_confirm(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    uid = int(cb.data.replace("sec_admin_remove_ok_", ""))
    async with AsyncSessionLocal() as s:
        a = await s.get(AdminUser, uid)
        if not a or a.role == "owner":
            await cb.answer("Não pode ser removido.", show_alert=True)
            return
        # Revogar sessões
        sessions = (await s.execute(
            select(AdminSession).where(AdminSession.user_id == uid)
        )).scalars().all()
        for x in sessions:
            await s.delete(x)
        await s.delete(a)
        await s.commit()

    await log_admin_action(cb.from_user.id, "admin_remove", {"user_id": uid})
    await cb.message.edit_text(
        f"✅ Administrador {uid} removido.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="sec_admins_0")],
        ]),
    )
    await cb.answer("Removido.")


# ============================================================
# NÍVEIS E PERMISSÕES (VISÃO GERAL)
# ============================================================
@router.callback_query(F.data == "sec_roles")
async def roles_overview(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return

    text = "🎖️ NÍVEIS DE ACESSO\n\n"
    for key, role in ROLES.items():
        perms = role["permissions"]
        text += (
            f"<b>{role['label']}</b>\n"
            f"<i>{role['description']}</i>\n"
            f"📊 {len(perms)} permissões\n\n"
        )

    text += "👇 Veja permissões de um nível:"
    buttons = []
    for key, role in ROLES.items():
        buttons.append([InlineKeyboardButton(
            text=role["label"],
            callback_data=f"sec_role_view_{key}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_security")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("sec_role_view_"))
async def role_view(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    role_key = cb.data.replace("sec_role_view_", "")
    role = ROLES.get(role_key)
    if not role:
        await cb.answer("Nível inválido.")
        return

    text = (
        f"🎖️ {role['label']}\n\n"
        f"<i>{role['description']}</i>\n\n"
        f"🔑 <b>Permissões ({len(role['permissions'])})</b>\n\n"
    )
    for perm in sorted(role["permissions"]):
        label, _ = PERMISSIONS.get(perm, (perm, ""))
        text += f"✅ {label}\n"

    await cb.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="sec_roles")],
        ]),
    )
    await cb.answer()


# ============================================================
# SESSÕES
# ============================================================
@router.callback_query(F.data.startswith("sec_sessions_"))
async def list_sessions(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(AdminSession.id))
        )).scalar() or 0
        stmt = (
            select(AdminSession)
            .order_by(desc(AdminSession.created_at))
            .offset(page * SESSIONS_PAGE_SIZE)
            .limit(SESSIONS_PAGE_SIZE)
        )
        sessions = (await s.execute(stmt)).scalars().all()

        uids = list({s.user_id for s in sessions})
        users = {}
        if uids:
            users = {
                x.id: x for x in (await s.execute(
                    select(User).where(User.id.in_(uids))
                )).scalars().all()
            }

    text = f"🔐 SESSÕES ADMINISTRATIVAS\n\nTotal: {total} | Página {page + 1}\n\n"
    buttons = []
    now = datetime.utcnow()

    if not sessions:
        text += "Nenhuma sessão registrada."
    else:
        for sess in sessions:
            u = users.get(sess.user_id)
            uname = (u.full_name if u else f"User {sess.user_id}")[:22]
            expired = sess.expires_at < now
            active = sess.is_active and not expired
            icon = "🟢" if active else ("⚪" if expired else "🔴")
            text += (
                f"{icon} {uname} (<code>{sess.user_id}</code>)\n"
                f"   {sess.created_at.strftime('%d/%m %H:%M')} → "
                f"{sess.expires_at.strftime('%d/%m %H:%M')}\n\n"
            )
            buttons.append([InlineKeyboardButton(
                text=f"{icon} {uname}",
                callback_data=f"sec_session_view_{sess.id}",
            )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"sec_sessions_{page - 1}"))
    if (page + 1) * SESSIONS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"sec_sessions_{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="🧹 Limpar expiradas", callback_data="sec_sessions_cleanup")])
    buttons.append([InlineKeyboardButton(text="🚫 Revogar todas", callback_data="sec_sessions_revokeall")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_security")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("sec_session_view_"))
async def session_view(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    sid = int(cb.data.replace("sec_session_view_", ""))
    async with AsyncSessionLocal() as s:
        sess = await s.get(AdminSession, sid)
        if not sess:
            await cb.answer("Sessão não encontrada.", show_alert=True)
            return
        user = await s.get(User, sess.user_id)

    uname = (user.full_name if user else f"User {sess.user_id}")[:30]
    expired = sess.expires_at < datetime.utcnow()
    active = sess.is_active and not expired

    text = (
        f"🔐 SESSÃO #{sess.id}\n\n"
        f"👤 {uname}\n"
        f"🆔 <code>{sess.user_id}</code>\n\n"
        f"📌 Status: {'🟢 Ativa' if active else ('⚪ Expirada' if expired else '🔴 Revogada')}\n"
        f"📅 Criada: {sess.created_at.strftime('%d/%m/%Y %H:%M')}\n"
        f"⏱️ Expira: {sess.expires_at.strftime('%d/%m/%Y %H:%M')}\n"
    )
    if sess.last_used_at:
        text += f"🕐 Último uso: {sess.last_used_at.strftime('%d/%m/%Y %H:%M')}\n"
    if sess.ip_address:
        text += f"🌐 IP: <code>{sess.ip_address}</code>\n"
    if sess.user_agent:
        text += f"📱 UA: {sess.user_agent[:60]}\n"

    buttons = []
    if active:
        buttons.append([InlineKeyboardButton(
            text="🚫 Revogar esta sessão",
            callback_data=f"sec_session_revoke_{sid}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="sec_sessions_0")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("sec_session_revoke_"))
async def session_revoke(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    sid = int(cb.data.replace("sec_session_revoke_", ""))
    async with AsyncSessionLocal() as s:
        sess = await s.get(AdminSession, sid)
        if sess:
            sess.is_active = False
            await s.commit()
    await log_admin_action(cb.from_user.id, "admin_session_revoke", {"session_id": sid})
    await cb.answer("Sessão revogada.", show_alert=True)
    await list_sessions(cb)


@router.callback_query(F.data == "sec_sessions_cleanup")
async def sessions_cleanup(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    now = datetime.utcnow()
    async with AsyncSessionLocal() as s:
        expired = (await s.execute(
            select(AdminSession).where(AdminSession.expires_at < now)
        )).scalars().all()
        count = 0
        for x in expired:
            await s.delete(x)
            count += 1
        await s.commit()
    await log_admin_action(cb.from_user.id, "admin_sessions_cleanup", {"removed": count})
    await cb.answer(f"🧹 {count} sessão(ões) removida(s).", show_alert=True)


@router.callback_query(F.data == "sec_sessions_revokeall")
async def sessions_revoke_all(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    await cb.message.edit_text(
        "🚫 REVOGAR TODAS AS SESSÕES\n\n"
        "⚠️ Todos os administradores serão desconectados.\n\n"
        "Confirmar?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Sim, revogar", callback_data="sec_sessions_revokeall_ok")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data="sec_sessions_0")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data == "sec_sessions_revokeall_ok")
async def sessions_revoke_all_ok(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        await s.execute(
            AdminSession.__table__.update().values(is_active=False)
        )
        await s.commit()
    await log_admin_action(cb.from_user.id, "admin_revoke_all_sessions", {})
    await cb.message.edit_text(
        "✅ Todas as sessões foram revogadas.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="sec_sessions_0")],
        ]),
    )
    await cb.answer("Sessões revogadas.")


# ============================================================
# TENTATIVAS DE LOGIN
# ============================================================
@router.callback_query(F.data.startswith("sec_attempts_"))
async def list_attempts(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(LoginAttempt.id))
        )).scalar() or 0
        stmt = (
            select(LoginAttempt)
            .order_by(desc(LoginAttempt.created_at))
            .offset(page * ATTEMPTS_PAGE_SIZE)
            .limit(ATTEMPTS_PAGE_SIZE)
        )
        attempts = (await s.execute(stmt)).scalars().all()

        uids = list({a.user_id for a in attempts})
        users = {}
        if uids:
            users = {
                x.id: x for x in (await s.execute(
                    select(User).where(User.id.in_(uids))
                )).scalars().all()
            }

    text = f"⚠️ TENTATIVAS DE LOGIN\n\nTotal: {total} | Página {page + 1}\n\n"

    if not attempts:
        text += "Nenhuma tentativa registrada."
    else:
        for a in attempts:
            u = users.get(a.user_id)
            uname = (u.full_name if u else f"User {a.user_id}")[:20]
            icon = "✅" if a.success else "❌"
            text += (
                f"{icon} {uname} (<code>{a.user_id}</code>)\n"
                f"   {a.attempt_type} | {a.created_at.strftime('%d/%m %H:%M')}\n"
            )
            if a.detail:
                text += f"   <i>{a.detail[:60]}</i>\n"
            text += "\n"

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"sec_attempts_{page - 1}"))
    if (page + 1) * ATTEMPTS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"sec_attempts_{page + 1}"))
    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🧹 Limpar antigas", callback_data="sec_attempts_cleanup")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_security")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data == "sec_attempts_cleanup")
async def attempts_cleanup(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    cutoff = datetime.utcnow() - timedelta(days=30)
    async with AsyncSessionLocal() as s:
        old = (await s.execute(
            select(LoginAttempt).where(LoginAttempt.created_at < cutoff)
        )).scalars().all()
        count = 0
        for x in old:
            await s.delete(x)
            count += 1
        await s.commit()
    await log_admin_action(cb.from_user.id, "attempts_cleanup", {"removed": count})
    await cb.answer(f"🧹 {count} registro(s) com mais de 30 dias removido(s).", show_alert=True)


# ============================================================
# AUDITORIA / LOGS
# ============================================================
@router.callback_query(F.data.startswith("sec_logs_"))
async def list_logs(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(AdminLog.id)))).scalar() or 0
        stmt = (
            select(AdminLog)
            .order_by(desc(AdminLog.created_at))
            .offset(page * LOGS_PAGE_SIZE)
            .limit(LOGS_PAGE_SIZE)
        )
        logs = (await s.execute(stmt)).scalars().all()

        uids = list({l.admin_id for l in logs})
        users = {}
        if uids:
            users = {
                x.id: x for x in (await s.execute(
                    select(User).where(User.id.in_(uids))
                )).scalars().all()
            }

    text = f"📋 AUDITORIA DE AÇÕES\n\nTotal: {total} | Página {page + 1}\n\n"

    if not logs:
        text += "Nenhuma ação registrada."
    else:
        for l in logs:
            u = users.get(l.admin_id)
            uname = (u.full_name if u else f"Admin {l.admin_id}")[:20]
            text += (
                f"🕐 {l.created_at.strftime('%d/%m %H:%M')} — {uname}\n"
                f"   ⚙️ <code>{l.action}</code>\n"
            )
            if l.details:
                det = str(l.details)[:100]
                text += f"   <i>{det}</i>\n"
            text += "\n"

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"sec_logs_{page - 1}"))
    if (page + 1) * LOGS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"sec_logs_{page + 1}"))
    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🔎 Buscar nos logs", callback_data="sec_logs_search")])
    buttons.append([InlineKeyboardButton(text="📥 Exportar logs", callback_data="sec_logs_export")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_security")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data == "sec_logs_search")
async def logs_search_start(cb: CallbackQuery, state: FSMContext):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    await state.set_state(SECS.search_logs)
    await cb.message.answer(
        "🔎 BUSCAR NOS LOGS\n\n"
        "Envie a palavra-chave (ação, ID de admin, etc):"
    )
    await cb.answer()


@router.message(SECS.search_logs)
async def logs_search_execute(m: Message, state: FSMContext):
    if not await has_permission(m.from_user.id, "manage_security"):
        return
    q = m.text.strip() if m.text else ""
    await state.clear()
    if len(q) < 2:
        await m.answer("❌ Mínimo 2 caracteres.")
        return

    async with AsyncSessionLocal() as s:
        stmt = select(AdminLog).where(
            or_(
                AdminLog.action.ilike(f"%{q}%"),
            )
        ).order_by(desc(AdminLog.created_at)).limit(30)
        logs = (await s.execute(stmt)).scalars().all()

    if not logs:
        await m.answer("❌ Nenhum log encontrado.")
        return

    text = f"🔎 RESULTADOS: {len(logs)}\n\n"
    for l in logs[:15]:
        text += (
            f"🕐 {l.created_at.strftime('%d/%m %H:%M')} — Admin {l.admin_id}\n"
            f"   ⚙️ <code>{l.action}</code>\n\n"
        )

    await m.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="sec_logs_0")],
        ]),
    )


@router.callback_query(F.data == "sec_logs_export")
async def logs_export(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "export_data"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        logs = (await s.execute(
            select(AdminLog).order_by(desc(AdminLog.created_at)).limit(1000)
        )).scalars().all()

    lines = []
    lines.append("═" * 70)
    lines.append("BELLAMY | AUDITORIA DE AÇÕES")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append(f"Total exportado: {len(logs)} (últimas 1000)")
    lines.append("═" * 70)
    lines.append("")

    for l in logs:
        lines.append(f"[{l.created_at.strftime('%d/%m/%Y %H:%M:%S')}] Admin {l.admin_id}")
        lines.append(f"  Ação: {l.action}")
        if l.details:
            lines.append(f"  Detalhes: {l.details}")
        lines.append("")

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(content, filename=f"auditoria_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt")
    await cb.message.answer_document(file, caption=f"📊 <b>Logs de auditoria</b>\n\n{len(logs)} ações")
    await cb.answer("Exportado.")


@router.callback_query(F.data.startswith("sec_admin_logs_"))
async def admin_logs_history(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_admins"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    rest = cb.data.replace("sec_admin_logs_", "")
    parts = rest.rsplit("_", 1)
    uid = int(parts[0])
    page = int(parts[1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(AdminLog.id)).where(AdminLog.admin_id == uid)
        )).scalar() or 0
        stmt = (
            select(AdminLog)
            .where(AdminLog.admin_id == uid)
            .order_by(desc(AdminLog.created_at))
            .offset(page * LOGS_PAGE_SIZE)
            .limit(LOGS_PAGE_SIZE)
        )
        logs = (await s.execute(stmt)).scalars().all()

    text = f"📋 HISTÓRICO DO ADMIN {uid}\n\nTotal: {total} | Página {page + 1}\n\n"
    if not logs:
        text += "Nenhuma ação registrada."
    else:
        for l in logs:
            text += (
                f"🕐 {l.created_at.strftime('%d/%m %H:%M')}\n"
                f"   ⚙️ <code>{l.action}</code>\n\n"
            )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"sec_admin_logs_{uid}_{page - 1}"))
    if (page + 1) * LOGS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"sec_admin_logs_{uid}_{page + 1}"))
    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=f"sec_admin_view_{uid}")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# MODO MANUTENÇÃO
# ============================================================
@router.callback_query(F.data == "sec_maintenance")
async def maintenance_view(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "toggle_maintenance"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    s = await get_settings(force=True)
    maint = getattr(s, "maintenance_mode", False)
    msg = getattr(s, "maintenance_message", "")

    text = (
        "🛠️ MODO MANUTENÇÃO\n\n"
        f"📌 Status: {'🟢 ATIVO' if maint else '⚪ INATIVO'}\n\n"
    )
    if maint:
        text += "⚠️ Usuários comuns veem apenas a mensagem de manutenção.\n"
        text += "👑 Administradores continuam com acesso total.\n\n"
    text += f"💬 <b>Mensagem atual</b>\n<code>{msg[:300]}</code>"

    buttons = [
        [InlineKeyboardButton(
            text=("🟢 Desativar manutenção" if maint else "🔴 Ativar manutenção"),
            callback_data="sec_maintenance_toggle")],
        [InlineKeyboardButton(text="✏️ Editar mensagem", callback_data="sec_maintenance_msg")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_security")],
    ]
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data == "sec_maintenance_toggle")
async def maintenance_toggle(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "toggle_maintenance"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.maintenance_mode = not bs.maintenance_mode
        new = bs.maintenance_mode
        await s.commit()
    invalidate_cache()
    await log_admin_action(cb.from_user.id, "maintenance_toggle", {"new": new})
    await maintenance_view(cb)


@router.callback_query(F.data == "sec_maintenance_msg")
async def maintenance_msg_start(cb: CallbackQuery, state: FSMContext):
    if not await require_permission(cb.from_user.id, "toggle_maintenance"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    await state.update_data(maint_field="maintenance_message")
    await state.set_state(SECS.set_session_hours)
    await cb.message.answer("💬 Envie a nova mensagem de manutenção (10-500 caracteres):")
    await cb.answer()


# ============================================================
# CONFIGURAÇÕES DE SEGURANÇA
# ============================================================
async def config_view() -> tuple[str, InlineKeyboardMarkup]:
    s = await get_settings(force=True)
    require_2fa = getattr(s, "require_2fa_admins", False)
    max_attempts = getattr(s, "max_login_attempts", 5)
    lockout = getattr(s, "login_lockout_minutes", 15)
    session_hours = getattr(s, "admin_session_hours", 24)

    text = (
        "🔑 CONFIGURAÇÕES DE SEGURANÇA\n\n"
        f"🔐 2FA obrigatório: {'🟢 Ativado' if require_2fa else '🔴 Desativado'}\n"
        f"🔒 Tentativas máximas: {max_attempts}\n"
        f"⏱️ Bloqueio após falhas: {lockout} min\n"
        f"🕐 Duração da sessão: {session_hours}h\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar 2FA" if require_2fa else "🟢 Ativar 2FA obrigatório"),
            callback_data="sec_cfg_t_2fa")],
        [InlineKeyboardButton(text=f"🔒 Alterar tentativas máximas ({max_attempts})", callback_data="sec_cfg_attempts")],
        [InlineKeyboardButton(text=f"⏱️ Alterar tempo de bloqueio ({lockout}min)", callback_data="sec_cfg_lockout")],
        [InlineKeyboardButton(text=f"🕐 Duração da sessão ({session_hours}h)", callback_data="sec_cfg_session")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_security")],
    ])
    return text, kb


@router.callback_query(F.data == "sec_config")
async def config(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "sec_cfg_t_2fa")
async def cfg_t_2fa(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        cur = getattr(bs, "require_2fa_admins", False)
        bs.require_2fa_admins = not cur
        await s.commit()
    invalidate_cache()
    await log_admin_action(cb.from_user.id, "security_2fa_toggle", {"new": not cur})
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "sec_cfg_attempts")
async def cfg_attempts_start(cb: CallbackQuery, state: FSMContext):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    await state.set_state(SECS.set_max_attempts)
    await cb.message.answer("🔒 Envie o número máximo de tentativas (3-20):")
    await cb.answer()


@router.message(SECS.set_max_attempts)
async def cfg_attempts_save(m: Message, state: FSMContext):
    if not await has_permission(m.from_user.id, "manage_security"):
        return
    try:
        v = int(m.text.strip())
        if v < 3 or v > 20:
            raise ValueError
    except ValueError:
        await m.answer("❌ 3-20.")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.max_login_attempts = v
        await s.commit()
    invalidate_cache()
    await log_admin_action(m.from_user.id, "security_max_attempts", {"new": v})
    await state.clear()
    text, kb = await config_view()
    await m.answer(f"✅ {v} tentativas.", reply_markup=kb)


@router.callback_query(F.data == "sec_cfg_lockout")
async def cfg_lockout_start(cb: CallbackQuery, state: FSMContext):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    await state.set_state(SECS.set_lockout_minutes)
    await cb.message.answer("⏱️ Envie o tempo de bloqueio em minutos (5-120):")
    await cb.answer()


@router.message(SECS.set_lockout_minutes)
async def cfg_lockout_save(m: Message, state: FSMContext):
    if not await has_permission(m.from_user.id, "manage_security"):
        return
    try:
        v = int(m.text.strip())
        if v < 5 or v > 120:
            raise ValueError
    except ValueError:
        await m.answer("❌ 5-120.")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.login_lockout_minutes = v
        await s.commit()
    invalidate_cache()
    await log_admin_action(m.from_user.id, "security_lockout", {"new": v})
    await state.clear()
    text, kb = await config_view()
    await m.answer(f"✅ {v} minutos.", reply_markup=kb)


@router.callback_query(F.data == "sec_cfg_session")
async def cfg_session_start(cb: CallbackQuery, state: FSMContext):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    await state.update_data(maint_field="admin_session_hours")
    await state.set_state(SECS.set_session_hours)
    await cb.message.answer("🕐 Envie a duração da sessão em horas (1-720):")
    await cb.answer()


@router.message(SECS.set_session_hours)
async def session_hours_save(m: Message, state: FSMContext):
    if not await has_permission(m.from_user.id, "manage_security"):
        return
    data = await state.get_data()
    field = data.get("maint_field")

    raw = m.text.strip() if m.text else ""

    if field == "maintenance_message":
        if len(raw) < 10 or len(raw) > 500:
            await m.answer("❌ 10-500 caracteres.")
            return
        async with AsyncSessionLocal() as s:
            bs = await s.get(BotSettings, 1)
            bs.maintenance_message = raw
            await s.commit()
        invalidate_cache()
        await log_admin_action(m.from_user.id, "maintenance_message_edit", {"len": len(raw)})
        await state.clear()
        await m.answer("✅ Mensagem atualizada.")
        await maintenance_view_cb_from_msg(m, m.from_user.id)
        return

    try:
        v = int(raw)
        if v < 1 or v > 720:
            raise ValueError
    except ValueError:
        await m.answer("❌ 1-720.")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.admin_session_hours = v
        await s.commit()
    invalidate_cache()
    await log_admin_action(m.from_user.id, "security_session_hours", {"new": v})
    await state.clear()
    text, kb = await config_view()
    await m.answer(f"✅ {v} horas.", reply_markup=kb)


async def maintenance_view_cb_from_msg(m: Message, admin_id: int):
    s = await get_settings(force=True)
    maint = getattr(s, "maintenance_mode", False)
    msg = getattr(s, "maintenance_message", "")
    text = (
        "🛠️ MODO MANUTENÇÃO\n\n"
        f"📌 Status: {'🟢 ATIVO' if maint else '⚪ INATIVO'}\n\n"
        f"💬 <b>Mensagem atual</b>\n<code>{msg[:300]}</code>"
    )
    buttons = [
        [InlineKeyboardButton(
            text=("🟢 Desativar manutenção" if maint else "🔴 Ativar manutenção"),
            callback_data="sec_maintenance_toggle")],
        [InlineKeyboardButton(text="✏️ Editar mensagem", callback_data="sec_maintenance_msg")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_security")],
    ]
    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ============================================================
# BACKUPS
# ============================================================
@router.callback_query(F.data.startswith("sec_backups_"))
async def backups_list(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])

    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(BackupRecord.id)))).scalar() or 0
        stmt = (
            select(BackupRecord)
            .order_by(desc(BackupRecord.created_at))
            .offset(page * 10)
            .limit(10)
        )
        backups = (await s.execute(stmt)).scalars().all()

    text = f"💾 BACKUPS DO BANCO\n\nTotal: {total} | Página {page + 1}\n\n"

    if not backups:
        text += "Nenhum backup registrado."
    else:
        for b in backups:
            icon = "✅" if b.success else "❌"
            text += (
                f"{icon} {b.created_at.strftime('%d/%m/%Y %H:%M')}\n"
                f"   📦 Tamanho: {b.size_bytes or 0} bytes\n"
                f"   👤 Admin: {b.admin_id or '—'}\n"
            )
            if b.note:
                text += f"   📝 {b.note[:60]}\n"
            text += "\n"

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"sec_backups_{page - 1}"))
    if (page + 1) * 10 < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"sec_backups_{page + 1}"))
    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="💾 Registrar novo backup", callback_data="sec_backup_new")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_security")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data == "sec_backup_new")
async def backup_new_start(cb: CallbackQuery, state: FSMContext):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    await state.set_state(SECS.backup_note)
    await cb.message.answer(
        "💾 REGISTRAR BACKUP\n\n"
        "Este é um registro de auditoria — o backup real deve ser feito\n"
        "pelo painel do Render (PostgreSQL backups) ou externamente.\n\n"
        "Envie uma nota/descrição:"
    )
    await cb.answer()


@router.message(SECS.backup_note)
async def backup_new_save(m: Message, state: FSMContext):
    if not await has_permission(m.from_user.id, "manage_security"):
        return
    note = m.text.strip() if m.text else ""
    if len(note) < 3:
        await m.answer("❌ Mínimo 3 caracteres.")
        return
    async with AsyncSessionLocal() as s:
        s.add(BackupRecord(
            admin_id=m.from_user.id,
            note=note[:300],
            success=True,
        ))
        await s.commit()
    await log_admin_action(m.from_user.id, "backup_register", {"note": note[:100]})
    await state.clear()
    await m.answer("✅ Backup registrado na auditoria.")


# ============================================================
# LOCKDOWN DE EMERGÊNCIA
# ============================================================
@router.callback_query(F.data == "sec_lockdown")
async def lockdown_view(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return

    s = await get_settings(force=True)
    maint = getattr(s, "maintenance_mode", False)
    bot_on = getattr(s, "bot_enabled", True)

    text = (
        "🔒 BLOQUEIOS DE EMERGÊNCIA\n\n"
        "Use estas ações em caso de incidente de segurança.\n\n"
        f"🤖 Bot: {'🟢 Online' if bot_on else '🔴 Offline'}\n"
        f"🛠️ Manutenção: {'🟢 Ativa' if maint else '⚪ Inativa'}\n\n"
        "⚠️ <b>LOCKDOWN COMPLETO</b>\n"
        "Ativa manutenção + desativa recargas + revoga sessões.\n"
    )

    buttons = [
        [InlineKeyboardButton(text="🤖 Desativar bot (emergência)", callback_data="sec_lockdown_bot")],
        [InlineKeyboardButton(
            text=("🛠️ Desativar manutenção" if maint else "🛠️ Ativar manutenção"),
            callback_data="sec_maintenance_toggle")],
        [InlineKeyboardButton(
            text="🔒 LOCKDOWN COMPLETO",
            callback_data="sec_lockdown_full")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_security")],
    ]
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data == "sec_lockdown_bot")
async def lockdown_bot(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.bot_enabled = not getattr(bs, "bot_enabled", True)
        new = bs.bot_enabled
        await s.commit()
    invalidate_cache()
    await log_admin_action(cb.from_user.id, "lockdown_toggle_bot", {"new": new})
    await lockdown_view(cb)


@router.callback_query(F.data == "sec_lockdown_full")
async def lockdown_full_prompt(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return
    await cb.message.edit_text(
        "🚨 LOCKDOWN COMPLETO\n\n"
        "As seguintes ações serão executadas:\n\n"
        "• 🛠️ Ativar modo manutenção\n"
        "• 💳 Desativar recargas\n"
        "• 🛒 Desativar compras\n"
        "• 🔐 Revogar TODAS as sessões administrativas\n\n"
        "⚠️ O bot ficará inacessível para usuários comuns.\n"
        "Apenas administradores poderão reverter.\n\n"
        "Confirmar lockdown?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚨 SIM, EXECUTAR", callback_data="sec_lockdown_full_ok")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data="sec_lockdown")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data == "sec_lockdown_full_ok")
async def lockdown_full_execute(cb: CallbackQuery):
    if not await require_permission(cb.from_user.id, "manage_security"):
        await cb.answer("⛔ Sem permissão.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.maintenance_mode = True
        bs.recharge_enabled = False
        bs.block_purchases = True
        await s.commit()

        await s.execute(AdminSession.__table__.update().values(is_active=False))
        await s.commit()

    invalidate_cache()
    await log_admin_action(cb.from_user.id, "lockdown_full", {})

    await cb.message.edit_text(
        "🚨 LOCKDOWN EXECUTADO\n\n"
        "O sistema entrou em modo de segurança.\n"
        "Usuários comuns estão bloqueados.\n\n"
        "Use as configurações de manutenção para reverter quando seguro.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_security")],
        ]),
    )
    await cb.answer("Lockdown executado.", show_alert=True)


# ============================================================
# HELPERS PARA O RESTO DO SISTEMA
# ============================================================
async def create_admin_session(
    user_id: int,
    ip: str | None = None,
    ua: str | None = None,
) -> AdminSession | None:
    """Cria uma sessão administrativa para o usuário."""
    s = await get_settings()
    if getattr(s, "require_2fa_admins", False):
        admin = await get_admin(user_id)
        if not admin or not admin.twofa_enabled:
            await log_login_attempt(user_id, "session_blocked", success=False, detail="2fa required")
            return None

    hours = getattr(s, "admin_session_hours", 24)
    token = _token()

    async with AsyncSessionLocal() as sess:
        session = AdminSession(
            user_id=user_id,
            token_hash=_hash(token),
            ip_address=ip,
            user_agent=(ua or "")[:500],
            expires_at=datetime.utcnow() + timedelta(hours=hours),
            is_active=True,
        )
        sess.add(session)
        await sess.commit()
        await sess.refresh(session)

        admin = await sess.get(AdminUser, user_id)
        if admin:
            admin.last_login_at = datetime.utcnow()
            await sess.commit()

    await log_login_attempt(user_id, "login", success=True)
    return session


async def validate_admin_session(token: str) -> AdminSession | None:
    token_hash = _hash(token)
    async with AsyncSessionLocal() as s:
        sess = (await s.execute(
            select(AdminSession).where(AdminSession.token_hash == token_hash)
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


async def check_login_attempts(user_id: int) -> tuple[bool, int]:
    """Retorna (está_bloqueado, minutos_restantes)."""
    s = await get_settings()
    max_attempts = getattr(s, "max_login_attempts", 5)
    lockout_minutes = getattr(s, "login_lockout_minutes", 15)

    cutoff = datetime.utcnow() - timedelta(minutes=lockout_minutes)
    async with AsyncSessionLocal() as sess:
        failed = (await sess.execute(
            select(func.count(LoginAttempt.id))
            .where(LoginAttempt.user_id == user_id)
            .where(LoginAttempt.success == False)
            .where(LoginAttempt.attempt_type.in_(["login", "permission_denied"]))
            .where(LoginAttempt.created_at >= cutoff)
        )).scalar() or 0

    if failed >= max_attempts:
        return True, lockout_minutes
    return False, 0
