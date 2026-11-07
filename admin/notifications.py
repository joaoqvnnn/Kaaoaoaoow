from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, or_
from datetime import datetime, timedelta

from bot import dp, bot
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    NotificationSetting, NotificationLog, User, Product, Order,
    Payment, AdminLog, BotSettings,
)
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

PAGE_SIZE = 12
LOG_PAGE_SIZE = 15


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class NS(StatesGroup):
    set_message = State()
    set_target_channel = State()
    set_target_user = State()
    set_image = State()
    test_event = State()


EVENTS = {
    "new_user": {
        "label": "Novo usuário",
        "icon": "👤",
        "default_target": "admins",
        "default_template": (
            "👤 <b>NOVO USUÁRIO</b>\n\n"
            "Nome: {nome}\n"
            "Username: @{username}\n"
            "ID: <code>{user_id}</code>\n"
            "Data: {data}"
        ),
    },
    "new_purchase": {
        "label": "Nova compra",
        "icon": "🛒",
        "default_target": "admins",
        "default_template": (
            "🛒 <b>NOVA COMPRA</b>\n\n"
            "Cliente: {nome}\n"
            "ID: <code>{user_id}</code>\n"
            "Produto: {produto}\n"
            "Quantidade: {quantidade}\n"
            "Valor: R$ {valor}\n"
            "Pedido: #{pedido_id}\n"
            "Data: {data}"
        ),
    },
    "cancel_purchase": {
        "label": "Compra cancelada",
        "icon": "❌",
        "default_target": "admins",
        "default_template": (
            "❌ <b>COMPRA CANCELADA</b>\n\n"
            "Cliente: {nome}\n"
            "ID: <code>{user_id}</code>\n"
            "Pedido: #{pedido_id}\n"
            "Valor: R$ {valor}\n"
            "Motivo: {motivo}\n"
            "Data: {data}"
        ),
    },
    "new_recharge": {
        "label": "Nova recarga",
        "icon": "💎",
        "default_target": "admins",
        "default_template": (
            "💎 <b>NOVA RECARGA</b>\n\n"
            "Cliente: {nome}\n"
            "ID: <code>{user_id}</code>\n"
            "Valor: R$ {valor}\n"
            "Bônus: R$ {bonus}\n"
            "Recarga: #{recarga_id}\n"
            "Data: {data}"
        ),
    },
    "pix_confirmed": {
        "label": "PIX confirmado",
        "icon": "✅",
        "default_target": "admins",
        "default_template": (
            "✅ <b>PIX CONFIRMADO</b>\n\n"
            "Cliente: {nome}\n"
            "ID: <code>{user_id}</code>\n"
            "Valor: R$ {valor}\n"
            "Recarga: #{recarga_id}\n"
            "Data: {data}"
        ),
    },
    "pix_expired": {
        "label": "PIX expirado",
        "icon": "⏱️",
        "default_target": "admins",
        "default_template": (
            "⏱️ <b>PIX EXPIRADO</b>\n\n"
            "Cliente: {nome}\n"
            "ID: <code>{user_id}</code>\n"
            "Valor: R$ {valor}\n"
            "Recarga: #{recarga_id}"
        ),
    },
    "low_stock": {
        "label": "Estoque baixo",
        "icon": "⚠️",
        "default_target": "admins",
        "default_template": (
            "⚠️ <b>ESTOQUE BAIXO</b>\n\n"
            "Produto: {produto}\n"
            "ID: #{produto_id}\n"
            "Estoque atual: {estoque}"
        ),
    },
    "out_of_stock": {
        "label": "Estoque esgotado",
        "icon": "🔴",
        "default_target": "admins",
        "default_template": (
            "🔴 <b>ESTOQUE ESGOTADO</b>\n\n"
            "Produto: {produto}\n"
            "ID: #{produto_id}\n"
            "O produto está sem unidades disponíveis."
        ),
    },
    "restock": {
        "label": "Produto reabastecido",
        "icon": "📦",
        "default_target": "channel_sales",
        "default_template": (
            "📦 <b>PRODUTO REABASTECIDO</b>\n\n"
            "◈ {produto}\n\n"
            "O estoque foi atualizado!\n\n"
            "📦 Unidades disponíveis: {estoque}\n"
            "💰 Valor: R$ {preco}\n"
            "🕒 Atualizado às {data}"
        ),
    },
    "new_affiliate": {
        "label": "Novo afiliado",
        "icon": "🤝",
        "default_target": "admins",
        "default_template": (
            "🤝 <b>NOVO AFILIADO</b>\n\n"
            "Indicador: {nome}\n"
            "ID: <code>{user_id}</code>\n"
            "Indicado: {indicado}\n"
            "Data: {data}"
        ),
    },
    "points_conversion": {
        "label": "Conversão de pontos",
        "icon": "🏆",
        "default_target": "admins",
        "default_template": (
            "🏆 <b>CONVERSÃO DE PONTOS</b>\n\n"
            "Cliente: {nome}\n"
            "ID: <code>{user_id}</code>\n"
            "Pontos: {pontos}\n"
            "Valor creditado: R$ {valor}"
        ),
    },
    "payment_error": {
        "label": "Erro de pagamento",
        "icon": "⚠️",
        "default_target": "admins",
        "default_template": (
            "⚠️ <b>ERRO DE PAGAMENTO</b>\n\n"
            "Cliente: {nome}\n"
            "ID: <code>{user_id}</code>\n"
            "Erro: {erro}\n"
            "Data: {data}"
        ),
    },
    "delivery_error": {
        "label": "Erro de entrega",
        "icon": "⚠️",
        "default_target": "admins",
        "default_template": (
            "⚠️ <b>ERRO DE ENTREGA</b>\n\n"
            "Cliente: {nome}\n"
            "ID: <code>{user_id}</code>\n"
            "Pedido: #{pedido_id}\n"
            "Erro: {erro}"
        ),
    },
    "maintenance": {
        "label": "Sistema em manutenção",
        "icon": "🛠️",
        "default_target": "channel_sales",
        "default_template": (
            "🛠️ <b>MANUTENÇÃO</b>\n\n"
            "O sistema entrará em manutenção.\n"
            "Motivo: {motivo}\n"
            "Data: {data}"
        ),
    },
}


TARGET_LABEL = {
    "admins": "👑 Administradores",
    "channel_sales": "📢 Canal de vendas",
    "channel_restock": "📦 Canal de reposição",
    "channel_support": "💬 Canal de suporte",
    "specific_user": "👤 Usuário específico",
}


# ============================================================
# CONFIG HELPERS
# ============================================================
async def get_or_create_setting(event_key: str) -> NotificationSetting:
    async with AsyncSessionLocal() as s:
        setting = (await s.execute(
            select(NotificationSetting).where(NotificationSetting.event_key == event_key)
        )).scalar_one_or_none()
        if setting:
            return setting

        meta = EVENTS[event_key]
        setting = NotificationSetting(
            event_key=event_key,
            label=meta["label"],
            icon=meta["icon"],
            enabled=True,
            target_type=meta["default_target"],
            channel_id=None,
            user_id=None,
            template=meta["default_template"],
            image_url=None,
            send_image=False,
            frequency="immediate",
        )
        s.add(setting)
        await s.commit()
        await s.refresh(setting)
        return setting


async def load_all_settings() -> dict[str, NotificationSetting]:
    async with AsyncSessionLocal() as s:
        existing = (await s.execute(select(NotificationSetting))).scalars().all()
        existing_map = {x.event_key: x for x in existing}

        to_create = []
        for key in EVENTS:
            if key not in existing_map:
                meta = EVENTS[key]
                to_create.append(NotificationSetting(
                    event_key=key,
                    label=meta["label"],
                    icon=meta["icon"],
                    enabled=True,
                    target_type=meta["default_target"],
                    template=meta["default_template"],
                    send_image=False,
                    frequency="immediate",
                ))
        if to_create:
            for x in to_create:
                s.add(x)
            await s.commit()

        all_settings = (await s.execute(select(NotificationSetting))).scalars().all()
        return {x.event_key: x for x in all_settings}


def target_icon(setting: NotificationSetting) -> str:
    if setting.target_type in ("channel_sales", "channel_restock", "channel_support"):
        return "📢"
    if setting.target_type == "specific_user":
        return "👤"
    return "👑"


# ============================================================
# MENU
# ============================================================
@router.callback_query(F.data == "admin_notifications")
async def notifications_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    settings_map = await load_all_settings()
    now = datetime.utcnow()
    today = now.date()
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        total_events = len(settings_map)
        enabled_count = sum(1 for x in settings_map.values() if x.enabled)
        disabled_count = total_events - enabled_count

        logs_total = (await s.execute(
            select(func.count(NotificationLog.id))
        )).scalar() or 0
        logs_today = (await s.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.sent_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0
        logs_month = (await s.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.sent_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0
        logs_failed = (await s.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.success == False)
        )).scalar() or 0

    settings = await get_settings()
    sales_ok = bool(getattr(settings, "sales_channel_id", None))
    restock_ok = bool(getattr(settings, "restock_channel_id", None))

    text = (
        "◈ BELLAMY | NOTIFICAÇÕES AUTOMÁTICAS\n\n"
        f"📋 Eventos configurados: {total_events}\n"
        f"🟢 Ativados: {enabled_count}\n"
        f"🔴 Desativados: {disabled_count}\n\n"
        f"📜 Histórico de envios: {logs_total}\n"
        f"📅 Hoje: {logs_today} | Este mês: {logs_month}\n"
        f"⚠️ Falhas registradas: {logs_failed}\n\n"
        f"📢 Canal de vendas: {'🟢' if sales_ok else '🔴 não configurado'}\n"
        f"📦 Canal de reposição: {'🟢' if restock_ok else '🔴 não configurado'}\n\n"
        "👇 Selecione uma opção:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Listar todos os eventos", callback_data="not_list_0")],
        [InlineKeyboardButton(text="🟢 Apenas ativos", callback_data="not_list_active_0")],
        [InlineKeyboardButton(text="🔴 Apenas desativados", callback_data="not_list_inactive_0")],
        [InlineKeyboardButton(text="📜 Histórico de envios", callback_data="not_logs_0")],
        [InlineKeyboardButton(text="⚠️ Falhas de envio", callback_data="not_logs_failed_0")],
        [InlineKeyboardButton(text="🧪 Enviar notificação de teste", callback_data="not_test")],
        [InlineKeyboardButton(text="📊 Estatísticas", callback_data="not_stats")],
        [InlineKeyboardButton(text="📥 Exportar relatório", callback_data="not_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# LISTAGEM
# ============================================================
@router.callback_query(F.data.startswith("not_list"))
async def list_events(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    data_str = cb.data.replace("not_list", "").strip("_")
    parts = data_str.split("_")
    if parts[0] in ("active", "inactive"):
        ftype = parts[0]
        page = int(parts[1]) if len(parts) > 1 else 0
    else:
        ftype = "all"
        page = int(parts[0]) if parts[0].isdigit() else 0

    settings_map = await load_all_settings()
    all_keys = list(EVENTS.keys())

    if ftype == "active":
        all_keys = [k for k in all_keys if settings_map[k].enabled]
    elif ftype == "inactive":
        all_keys = [k for k in all_keys if not settings_map[k].enabled]

    total = len(all_keys)
    start = page * PAGE_SIZE
    page_keys = all_keys[start:start + PAGE_SIZE]

    titles = {
        "all": "TODOS OS EVENTOS",
        "active": "EVENTOS ATIVOS",
        "inactive": "EVENTOS DESATIVADOS",
    }
    text = f"◈ BELLAMY | {titles[ftype]}\n\n"
    text += f"Total: {total} | Página {page + 1}\n\n"

    buttons = []
    for k in page_keys:
        st = settings_map[k]
        icon = "🟢" if st.enabled else "🔴"
        meta = EVENTS[k]
        tgt = target_icon(st)
        text += (
            f"{icon} {meta['icon']} <b>{st.label}</b>\n"
            f"   {tgt} {TARGET_LABEL.get(st.target_type, st.target_type)}\n\n"
        )
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {meta['icon']} {st.label}",
            callback_data=f"not_view_{k}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"not_list_{ftype}_{page - 1}" if ftype != "all"
            else f"not_list_{page - 1}"
        ))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"not_list_{ftype}_{page + 1}" if ftype != "all"
            else f"not_list_{page + 1}"
        ))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_notifications")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# DETALHE DO EVENTO
# ============================================================
async def render_event(event_key: str) -> tuple[str, InlineKeyboardMarkup]:
    settings_map = await load_all_settings()
    if event_key not in settings_map:
        return (
            "❌ Evento não encontrado.",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_notifications")]
            ]),
        )

    st = settings_map[event_key]
    meta = EVENTS[event_key]
    status = "🟢 Ativado" if st.enabled else "🔴 Desativado"
    tgt_label = TARGET_LABEL.get(st.target_type, st.target_type)

    tgt_detail = ""
    if st.target_type in ("channel_sales", "channel_restock", "channel_support") and st.channel_id:
        tgt_detail = f"\n   ID do canal: <code>{st.channel_id}</code>"
    elif st.target_type == "specific_user" and st.user_id:
        tgt_detail = f"\n   ID do usuário: <code>{st.user_id}</code>"

    template_preview = st.template[:250] + ("..." if len(st.template) > 250 else "")
    image_info = "🟢 Ativada" if st.send_image else "🔴 Desativada"

    async with AsyncSessionLocal() as s:
        total_sent = (await s.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.event_key == event_key)
        )).scalar() or 0
        last_sent = (await s.execute(
            select(NotificationLog)
            .where(NotificationLog.event_key == event_key)
            .order_by(desc(NotificationLog.sent_at))
            .limit(1)
        )).scalar_one_or_none()
        failed = (await s.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.event_key == event_key)
            .where(NotificationLog.success == False)
        )).scalar() or 0

    text = (
        f"◈ BELLAMY | {meta['icon']} {st.label}\n\n"
        f"📌 Status: {status}\n"
        f"🎯 Destinatário: {tgt_label}{tgt_detail}\n"
        f"⏱️ Frequência: {st.frequency}\n"
        f"🖼️ Imagem: {image_info}\n\n"
        f"📜 Envios: {total_sent} | ⚠️ Falhas: {failed}\n"
    )
    if last_sent:
        text += f"🕒 Último envio: {last_sent.sent_at.strftime('%d/%m/%Y %H:%M')}\n"

    text += f"\n💬 <b>Mensagem</b>\n<code>{template_preview}</code>\n\n"
    text += "🔤 <b>Variáveis disponíveis</b>: {nome}, {username}, {user_id}, {data}"

    buttons = [
        [InlineKeyboardButton(
            text=("🔴 Desativar evento" if st.enabled else "🟢 Ativar evento"),
            callback_data=f"not_toggle_{event_key}")],
        [InlineKeyboardButton(
            text="🎯 Alterar destinatário",
            callback_data=f"not_target_{event_key}")],
        [InlineKeyboardButton(
            text="💬 Editar mensagem",
            callback_data=f"not_msg_{event_key}")],
        [InlineKeyboardButton(
            text="🖼️ Configurar imagem",
            callback_data=f"not_image_{event_key}")],
        [InlineKeyboardButton(
            text="⏱️ Alterar frequência",
            callback_data=f"not_freq_{event_key}")],
        [InlineKeyboardButton(
            text="🧪 Enviar teste",
            callback_data=f"not_testevt_{event_key}")],
        [InlineKeyboardButton(
            text="↩️ Restaurar padrão",
            callback_data=f"not_reset_{event_key}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="not_list_0")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("not_view_"))
async def view_event(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("not_view_", "")
    text, kb = await render_event(key)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# TOGGLE
# ============================================================
@router.callback_query(F.data.startswith("not_toggle_"))
async def toggle_event(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("not_toggle_", "")
    st = await get_or_create_setting(key)
    async with AsyncSessionLocal() as s:
        obj = await s.get(NotificationSetting, st.id)
        obj.enabled = not obj.enabled
        new = obj.enabled
        await s.commit()
    await log_admin(cb.from_user.id, "notification_toggle", {
        "event": key, "new": new,
    })
    text, kb = await render_event(key)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Status atualizado.")


# ============================================================
# ALTERAR DESTINATÁRIO
# ============================================================
@router.callback_query(F.data.startswith("not_target_"))
async def target_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("not_target_", "")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 Administradores", callback_data=f"not_settarget_admins_{key}")],
        [InlineKeyboardButton(text="📢 Canal de vendas", callback_data=f"not_settarget_channel_sales_{key}")],
        [InlineKeyboardButton(text="📦 Canal de reposição", callback_data=f"not_settarget_channel_restock_{key}")],
        [InlineKeyboardButton(text="💬 Canal de suporte", callback_data=f"not_settarget_channel_support_{key}")],
        [InlineKeyboardButton(text="👤 Usuário específico", callback_data=f"not_settarget_specific_{key}")],
        [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"not_view_{key}")],
    ])
    await cb.message.edit_text(
        f"🎯 ALTERAR DESTINATÁRIO — {EVENTS[key]['label']}\n\n"
        f"Selecione o tipo de destinatário:",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("not_settarget_"))
async def set_target(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    rest = cb.data.replace("not_settarget_", "")
    parts = rest.split("_", 1)
    ttype = parts[0] if len(parts) == 2 else rest.split("_")[0]
    event_key = parts[-1] if len(parts) == 2 else rest[len(parts[0]) + 1:]

    # Reconstruir corretamente quando target_type tem underline (channel_sales etc.)
    for cand in ("channel_sales", "channel_restock", "channel_support", "admins", "specific"):
        if rest.startswith(f"{cand}_"):
            ttype = cand
            event_key = rest[len(cand) + 1:]
            break

    st = await get_or_create_setting(event_key)

    if ttype == "specific":
        await state.update_data(event_key=event_key)
        await state.set_state(NS.set_target_user)
        await cb.message.answer("👤 Envie o ID do Telegram do usuário destinatário:")
        await cb.answer()
        return

    if ttype in ("channel_sales", "channel_restock", "channel_support"):
        await state.update_data(event_key=event_key, target_type=ttype)
        await state.set_state(NS.set_target_channel)
        await cb.message.answer(
            f"📢 Envie o ID do canal (ex.: <code>-1001234567890</code>)\n\n"
            f"Ou envie /pular para usar o canal configurado no sistema."
        )
        await cb.answer()
        return

    async with AsyncSessionLocal() as s:
        obj = await s.get(NotificationSetting, st.id)
        obj.target_type = ttype
        obj.channel_id = None
        obj.user_id = None
        await s.commit()

    await log_admin(cb.from_user.id, "notification_set_target", {
        "event": event_key, "target_type": ttype,
    })
    text, kb = await render_event(event_key)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Destinatário atualizado.")


@router.message(NS.set_target_channel)
async def save_target_channel(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    event_key = data.get("event_key")
    ttype = data.get("target_type")
    raw = m.text.strip() if m.text else ""

    channel_id = None
    if raw.lower() != "/pular":
        if not raw.startswith("-") or not raw[1:].isdigit():
            await m.answer("❌ ID de canal inválido. Envie /pular para usar o canal padrão.")
            return
        channel_id = raw

    async with AsyncSessionLocal() as s:
        setting = (await s.execute(
            select(NotificationSetting).where(NotificationSetting.event_key == event_key)
        )).scalar_one_or_none()
        if not setting:
            await state.clear()
            await m.answer("❌ Evento não encontrado.")
            return
        setting.target_type = ttype
        setting.channel_id = channel_id
        setting.user_id = None
        await s.commit()

    await log_admin(m.from_user.id, "notification_set_channel", {
        "event": event_key, "channel_id": channel_id,
    })
    await state.clear()
    text, kb = await render_event(event_key)
    await m.answer("✅ Canal configurado.")
    await m.answer(text, reply_markup=kb)


@router.message(NS.set_target_user)
async def save_target_user(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    event_key = data.get("event_key")
    raw = m.text.strip() if m.text else ""

    if not raw.isdigit():
        await m.answer("❌ ID inválido. Envie apenas números.")
        return

    async with AsyncSessionLocal() as s:
        setting = (await s.execute(
            select(NotificationSetting).where(NotificationSetting.event_key == event_key)
        )).scalar_one_or_none()
        if not setting:
            await state.clear()
            await m.answer("❌ Evento não encontrado.")
            return
        setting.target_type = "specific_user"
        setting.user_id = int(raw)
        setting.channel_id = None
        await s.commit()

    await log_admin(m.from_user.id, "notification_set_user", {
        "event": event_key, "user_id": int(raw),
    })
    await state.clear()
    text, kb = await render_event(event_key)
    await m.answer("✅ Usuário destinatário configurado.")
    await m.answer(text, reply_markup=kb)


# ============================================================
# EDITAR MENSAGEM
# ============================================================
@router.callback_query(F.data.startswith("not_msg_"))
async def msg_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("not_msg_", "")
    await state.update_data(event_key=key)
    await state.set_state(NS.set_message)
    await cb.message.answer(
        f"💬 EDITAR MENSAGEM — {EVENTS[key]['label']}\n\n"
        f"Envie o novo texto.\n\n"
        f"Variáveis suportadas: <code>{{nome}}</code>, <code>{{username}}</code>, "
        f"<code>{{user_id}}</code>, <code>{{data}}</code>, <code>{{valor}}</code>, "
        f"<code>{{produto}}</code>, <code>{{estoque}}</code>, <code>{{pedido_id}}</code>, "
        f"<code>{{recarga_id}}</code>."
    )
    await cb.answer()


@router.message(NS.set_message)
async def msg_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    event_key = data.get("event_key")
    raw = m.text.strip() if m.text else ""

    if len(raw) < 5:
        await m.answer("❌ Mensagem muito curta.")
        return
    if len(raw) > 4000:
        await m.answer("❌ Mensagem muito longa (máximo 4000 caracteres).")
        return

    async with AsyncSessionLocal() as s:
        setting = (await s.execute(
            select(NotificationSetting).where(NotificationSetting.event_key == event_key)
        )).scalar_one_or_none()
        if not setting:
            await state.clear()
            await m.answer("❌ Evento não encontrado.")
            return
        setting.template = raw
        await s.commit()

    await log_admin(m.from_user.id, "notification_edit_message", {
        "event": event_key, "preview": raw[:100],
    })
    await state.clear()
    text, kb = await render_event(event_key)
    await m.answer("✅ Mensagem atualizada.")
    await m.answer(text, reply_markup=kb)


# ============================================================
# IMAGEM
# ============================================================
@router.callback_query(F.data.startswith("not_image_"))
async def image_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("not_image_", "")
    st = await get_or_create_setting(key)
    current = "🟢 Ativada" if st.send_image else "🔴 Desativada"
    img_url = st.image_url or "não configurada"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🔴 Desativar imagem" if st.send_image else "🟢 Ativar imagem"),
            callback_data=f"not_imagetoggle_{key}")],
        [InlineKeyboardButton(text="🔗 Alterar URL da imagem", callback_data=f"not_imageurl_{key}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"not_view_{key}")],
    ])
    await cb.message.edit_text(
        f"🖼️ IMAGEM — {EVENTS[key]['label']}\n\n"
        f"Status: {current}\n"
        f"URL atual: <code>{img_url[:200]}</code>",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("not_imagetoggle_"))
async def image_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("not_imagetoggle_", "")
    st = await get_or_create_setting(key)
    async with AsyncSessionLocal() as s:
        obj = await s.get(NotificationSetting, st.id)
        obj.send_image = not obj.send_image
        new = obj.send_image
        await s.commit()
    await log_admin(cb.from_user.id, "notification_image_toggle", {
        "event": key, "new": new,
    })
    await image_menu(cb)


@router.callback_query(F.data.startswith("not_imageurl_"))
async def image_url_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("not_imageurl_", "")
    await state.update_data(event_key=key)
    await state.set_state(NS.set_image)
    await cb.message.answer("🖼️ Envie a URL pública da imagem (ou /limpar para remover):")
    await cb.answer()


@router.message(NS.set_image)
async def image_url_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    event_key = data.get("event_key")
    raw = m.text.strip() if m.text else ""

    if raw.lower() == "/limpar":
        new_url = None
    else:
        if not (raw.startswith("http://") or raw.startswith("https://")):
            await m.answer("❌ URL inválida. Deve começar com http:// ou https://")
            return
        new_url = raw

    async with AsyncSessionLocal() as s:
        setting = (await s.execute(
            select(NotificationSetting).where(NotificationSetting.event_key == event_key)
        )).scalar_one_or_none()
        if not setting:
            await state.clear()
            await m.answer("❌ Evento não encontrado.")
            return
        setting.image_url = new_url
        await s.commit()

    await log_admin(m.from_user.id, "notification_set_image_url", {
        "event": event_key, "url": new_url,
    })
    await state.clear()
    text, kb = await render_event(event_key)
    await m.answer("✅ URL atualizada.")
    await m.answer(text, reply_markup=kb)


# ============================================================
# FREQUÊNCIA
# ============================================================
@router.callback_query(F.data.startswith("not_freq_"))
async def freq_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("not_freq_", "")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Imediata", callback_data=f"not_setfreq_immediate_{key}")],
        [InlineKeyboardButton(text="📅 Diária (resumo)", callback_data=f"not_setfreq_daily_{key}")],
        [InlineKeyboardButton(text="📆 Semanal (resumo)", callback_data=f"not_setfreq_weekly_{key}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"not_view_{key}")],
    ])
    await cb.message.edit_text(
        f"⏱️ FREQUÊNCIA — {EVENTS[key]['label']}",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("not_setfreq_"))
async def set_freq(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("not_setfreq_", "")
    freq = None
    event_key = None
    for cand in ("immediate", "daily", "weekly"):
        if rest.startswith(f"{cand}_"):
            freq = cand
            event_key = rest[len(cand) + 1:]
            break
    if not freq or not event_key:
        await cb.answer("Parâmetro inválido.")
        return

    st = await get_or_create_setting(event_key)
    async with AsyncSessionLocal() as s:
        obj = await s.get(NotificationSetting, st.id)
        obj.frequency = freq
        await s.commit()

    await log_admin(cb.from_user.id, "notification_set_frequency", {
        "event": event_key, "freq": freq,
    })
    text, kb = await render_event(event_key)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Frequência atualizada.")


# ============================================================
# RESTAURAR PADRÃO
# ============================================================
@router.callback_query(F.data.startswith("not_reset_"))
async def reset_event(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("not_reset_", "")
    if key not in EVENTS:
        await cb.answer("Evento inválido.")
        return

    meta = EVENTS[key]
    st = await get_or_create_setting(key)
    async with AsyncSessionLocal() as s:
        obj = await s.get(NotificationSetting, st.id)
        obj.enabled = True
        obj.target_type = meta["default_target"]
        obj.channel_id = None
        obj.user_id = None
        obj.template = meta["default_template"]
        obj.image_url = None
        obj.send_image = False
        obj.frequency = "immediate"
        await s.commit()

    await log_admin(cb.from_user.id, "notification_reset", {"event": key})
    text, kb = await render_event(key)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("✅ Configurações restauradas.")


# ============================================================
# ENVIAR TESTE
# ============================================================
@router.callback_query(F.data.startswith("not_testevt_"))
async def test_event(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    key = cb.data.replace("not_testevt_", "")
    await _send_test(cb.from_user.id, key)
    await cb.answer("🧪 Teste enviado (verifique suas mensagens).", show_alert=True)


@router.callback_query(F.data == "not_test")
async def test_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    buttons = []
    for k, meta in EVENTS.items():
        buttons.append([InlineKeyboardButton(
            text=f"{meta['icon']} {meta['label']}",
            callback_data=f"not_testevt_{k}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_notifications")])
    await cb.message.edit_text(
        "🧪 ENVIAR NOTIFICAÇÃO DE TESTE\n\n"
        "O teste será enviado para VOCÊ (o admin que clicou).\n"
        "Escolha o evento:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


async def _send_test(admin_id: int, event_key: str) -> None:
    settings_map = await load_all_settings()
    if event_key not in settings_map:
        return
    st = settings_map[event_key]
    meta = EVENTS[event_key]

    sample_data = {
        "nome": "João Teste",
        "username": "joao_teste",
        "user_id": str(admin_id),
        "data": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        "valor": "25.00",
        "bonus": "2.50",
        "produto": "Produto Exemplo",
        "produto_id": "1",
        "estoque": "10",
        "preco": "19.90",
        "pedido_id": "123",
        "recarga_id": "456",
        "quantidade": "1",
        "motivo": "Teste",
        "erro": "Mensagem de teste",
        "pontos": "100",
        "indicado": "Maria Teste",
        "indicador_id": "789",
    }

    try:
        rendered = st.template.format(**sample_data)
    except Exception:
        rendered = st.template

    try:
        if st.send_image and st.image_url:
            await bot.send_photo(
                chat_id=admin_id, photo=st.image_url,
                caption=f"🧪 TESTE — {meta['label']}\n\n{rendered}",
            )
        else:
            await bot.send_message(
                admin_id,
                f"🧪 <b>TESTE — {meta['label']}</b>\n\n{rendered}",
            )
    except Exception as e:
        await bot.send_message(
            admin_id,
            f"❌ Erro ao enviar teste: {str(e)[:200]}",
        )


# ============================================================
# HISTÓRICO DE ENVIOS
# ============================================================
@router.callback_query(F.data.startswith("not_logs"))
async def logs_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    data_str = cb.data.replace("not_logs", "").strip("_")
    parts = data_str.split("_")
    if parts[0] == "failed":
        ftype = "failed"
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    else:
        ftype = "all"
        page = int(parts[0]) if parts[0].isdigit() else 0

    async with AsyncSessionLocal() as s:
        stmt = select(NotificationLog)
        cnt = select(func.count(NotificationLog.id))
        if ftype == "failed":
            stmt = stmt.where(NotificationLog.success == False)
            cnt = cnt.where(NotificationLog.success == False)
        total = (await s.execute(cnt)).scalar() or 0
        stmt = stmt.order_by(desc(NotificationLog.sent_at)).offset(page * LOG_PAGE_SIZE).limit(LOG_PAGE_SIZE)
        logs = (await s.execute(stmt)).scalars().all()

    title = "HISTÓRICO DE ENVIOS" if ftype == "all" else "FALHAS DE ENVIO"
    text = f"📜 {title}\n\nTotal: {total} | Página {page + 1}\n\n"

    if not logs:
        text += "Nenhum registro."
    else:
        for log in logs:
            icon = "✅" if log.success else "❌"
            meta = EVENTS.get(log.event_key, {"icon": "•", "label": log.event_key})
            text += (
                f"{icon} {meta['icon']} {meta['label']}\n"
                f"   🎯 <code>{log.target_id}</code>\n"
                f"   🕒 {log.sent_at.strftime('%d/%m/%Y %H:%M')}\n"
            )
            if log.error:
                text += f"   ⚠️ {log.error[:80]}\n"
            text += "\n"

    nav = []
    if page > 0:
        prefix = f"not_logs_failed" if ftype == "failed" else "not_logs"
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}_{page - 1}"))
    if (page + 1) * LOG_PAGE_SIZE < total:
        prefix = f"not_logs_failed" if ftype == "failed" else "not_logs"
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}_{page + 1}"))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_notifications")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# ESTATÍSTICAS
# ============================================================
@router.callback_query(F.data == "not_stats")
async def stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    today = datetime.utcnow().date()
    week_start = today - timedelta(days=7)
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.count(NotificationLog.id))
        )).scalar() or 0
        success = (await s.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.success == True)
        )).scalar() or 0
        failed = total - success

        today_count = (await s.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.sent_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0
        week_count = (await s.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.sent_at >= datetime.combine(week_start, datetime.min.time()))
        )).scalar() or 0
        month_count = (await s.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.sent_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0

        top_events = (await s.execute(
            select(NotificationLog.event_key,
                   func.count(NotificationLog.id).label("cnt"))
            .group_by(NotificationLog.event_key)
            .order_by(desc("cnt"))
            .limit(6)
        )).all()

    success_rate = (success / total * 100) if total else 0.0

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DE NOTIFICAÇÕES\n\n"
        f"📊 Total de envios: {total}\n"
        f"✅ Sucesso: {success}\n"
        f"❌ Falhas: {failed}\n"
        f"📈 Taxa de sucesso: {success_rate:.1f}%\n\n"
        f"📅 Hoje: {today_count}\n"
        f"📆 Últimos 7 dias: {week_count}\n"
        f"🗓️ Este mês: {month_count}\n\n"
    )
    if top_events:
        text += "🔝 EVENTOS MAIS ENVIADOS\n"
        for ek, cnt in top_events:
            meta = EVENTS.get(ek, {"icon": "•", "label": ek})
            text += f"• {meta['icon']} {meta['label']}: {cnt} envio(s)\n"

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="not_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_notifications")],
    ]))
    await cb.answer()


# ============================================================
# EXPORTAÇÃO
# ============================================================
@router.callback_query(F.data == "not_export")
async def export_report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    settings_map = await load_all_settings()
    async with AsyncSessionLocal() as s:
        recent = (await s.execute(
            select(NotificationLog)
            .order_by(desc(NotificationLog.sent_at))
            .limit(500)
        )).scalars().all()

    lines = []
    lines.append("═" * 70)
    lines.append("BELLAMY | RELATÓRIO DE NOTIFICAÇÕES")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append(f"Total de eventos: {len(settings_map)}")
    lines.append(f"Envios (últimos 500): {len(recent)}")
    lines.append("═" * 70)
    lines.append("")
    lines.append("─" * 70)
    lines.append("CONFIGURAÇÃO POR EVENTO")
    lines.append("─" * 70)

    for key, st in settings_map.items():
        meta = EVENTS[key]
        status = "Ativo" if st.enabled else "Desativado"
        tgt = TARGET_LABEL.get(st.target_type, st.target_type)
        lines.append(
            f"{meta['icon']} {st.label:<25} | {status:<10} | "
            f"{tgt:<20} | freq: {st.frequency}"
        )
    lines.append("")
    lines.append("─" * 70)
    lines.append("ÚLTIMOS 500 ENVIOS")
    lines.append("─" * 70)

    for log in recent:
        meta = EVENTS.get(log.event_key, {"icon": "•", "label": log.event_key})
        status = "OK" if log.success else "FALHA"
        lines.append(
            f"{log.sent_at.strftime('%d/%m/%Y %H:%M')} | "
            f"{meta['icon']} {meta['label']:<20} | "
            f"target: {log.target_id:<20} | {status}"
        )
        if log.error:
            lines.append(f"  ⚠️ {log.error[:120]}")

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"notificacoes_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption=(
            f"📊 <b>Relatório de Notificações</b>\n\n"
            f"📋 Eventos: {len(settings_map)}\n"
            f"📜 Envios: {len(recent)}"
        ),
    )
    await cb.answer("Relatório gerado.")


# ============================================================
# HELPERS PARA O RESTO DO SISTEMA
# ============================================================
async def dispatch_notification(event_key: str, variables: dict) -> bool:
    """
    Chame este helper sempre que um evento ocorrer.
    Ex.:
        await dispatch_notification("new_purchase", {
            "nome": user.full_name, "user_id": user.id,
            "produto": product.name, "valor": f"{order.total:.2f}",
            "quantidade": str(order.quantity),
            "pedido_id": str(order.id),
            "data": datetime.utcnow().strftime('%d/%m/%Y %H:%M'),
        })
    """
    if event_key not in EVENTS:
        return False

    settings_map = await load_all_settings()
    st = settings_map.get(event_key)
    if not st or not st.enabled:
        return False
    if st.frequency != "immediate":
        # Frequência diária/semanal: o envio agregado é responsabilidade
        # de um scheduler externo (não implementado neste módulo).
        return False

    try:
        rendered = st.template.format(**variables)
    except KeyError as e:
        rendered = f"{st.template}\n\n(⚠️ variável ausente: {e})"
    except Exception:
        rendered = st.template

    # Resolver destinatários
    targets: list[int] = []
    if st.target_type == "admins":
        targets = list(ADMIN_IDS)
    elif st.target_type == "specific_user" and st.user_id:
        targets = [st.user_id]
    elif st.target_type in ("channel_sales", "channel_restock", "channel_support"):
        if st.channel_id:
            try:
                targets = [int(st.channel_id)]
            except ValueError:
                targets = []
        else:
            bot_settings = await get_settings()
            field_map = {
                "channel_sales": "sales_channel_id",
                "channel_restock": "restock_channel_id",
                "channel_support": "support_channel_id",
            }
            raw = getattr(bot_settings, field_map.get(st.target_type, ""), None)
            if raw:
                try:
                    targets = [int(raw)]
                except (ValueError, TypeError):
                    targets = []

    if not targets:
        return False

    sent_any = False
    for target_id in targets:
        success = True
        error_msg = None
        try:
            if st.send_image and st.image_url:
                await bot.send_photo(
                    chat_id=target_id,
                    photo=st.image_url,
                    caption=rendered,
                )
            else:
                await bot.send_message(target_id, rendered)
            sent_any = True
        except Exception as e:
            success = False
            error_msg = str(e)[:300]

        async with AsyncSessionLocal() as s:
            s.add(NotificationLog(
                event_key=event_key,
                target_id=str(target_id),
                success=success,
                error=error_msg,
            ))
            await s.commit()

    return sent_any
