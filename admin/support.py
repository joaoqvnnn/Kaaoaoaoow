from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, or_, and_
from datetime import datetime, timedelta

from bot import dp, bot
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    SupportTicket, SupportMessage, SupportAgent, CannedResponse,
    FAQEntry, User, AdminLog, BotSettings,
)
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

TICKETS_PAGE_SIZE = 10
MSGS_PAGE_SIZE = 30


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class SS(StatesGroup):
    reply_text = State()
    reply_media = State()
    canned_new_title = State()
    canned_new_content = State()
    canned_edit = State()
    faq_new_q = State()
    faq_new_a = State()
    faq_edit_q = State()
    faq_edit_a = State()
    agent_add = State()
    search = State()
    ticket_category = State()
    ticket_priority = State()


STATUS_META = {
    "open":         ("🟢", "Aberto"),
    "in_progress":  ("🟡", "Em andamento"),
    "waiting_user": ("🔵", "Aguardando cliente"),
    "resolved":     ("✅", "Resolvido"),
    "closed":       ("⚪", "Fechado"),
}

PRIORITY_META = {
    "low":    ("⬇️", "Baixa"),
    "normal": ("➡️", "Normal"),
    "high":   ("⬆️", "Alta"),
    "urgent": ("🔥", "Urgente"),
}

DEFAULT_CANNED = [
    ("Saudação", "Olá! 👋 Como posso ajudar você hoje?"),
    ("Aguardando", "Estou verificando sua solicitação e já retorno com mais informações. ⏳"),
    ("Pedir dados", "Para te ajudar melhor, poderia me informar: (1) ID do pedido, (2) descrição do problema?"),
    ("Resolvido", "Seu problema foi resolvido! ✅ Posso ajudar em algo mais?"),
    ("Reembolso", "Sobre o reembolso: analisaremos o caso e, se aprovado, o valor será creditado na sua carteira em até 24h."),
    ("Encerrar", "Estou encerrando este atendimento. Se precisar, é só abrir um novo ticket. 💙"),
]


# ============================================================
# MENU
# ============================================================
@router.callback_query(F.data == "admin_support")
async def support_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    today = datetime.utcnow().date()
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        counts = {}
        for st in ("open", "in_progress", "waiting_user", "resolved", "closed"):
            counts[st] = (await s.execute(
                select(func.count(SupportTicket.id)).where(SupportTicket.status == st)
            )).scalar() or 0
        total = sum(counts.values())

        urgent = (await s.execute(
            select(func.count(SupportTicket.id))
            .where(SupportTicket.priority == "urgent")
            .where(SupportTicket.status.in_(["open", "in_progress", "waiting_user"]))
        )).scalar() or 0

        today_new = (await s.execute(
            select(func.count(SupportTicket.id))
            .where(SupportTicket.created_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0
        month_new = (await s.execute(
            select(func.count(SupportTicket.id))
            .where(SupportTicket.created_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0

        # Tempo médio de resposta e resolução
        resolved_tickets = (await s.execute(
            select(SupportTicket)
            .where(SupportTicket.status.in_(["resolved", "closed"]))
            .where(SupportTicket.resolved_at.isnot(None))
            .limit(200)
        )).scalars().all()

        if resolved_tickets:
            avg_resolution = sum(
                (t.resolved_at - t.created_at).total_seconds()
                for t in resolved_tickets
                if t.resolved_at and t.created_at
            ) / len(resolved_tickets) / 3600
        else:
            avg_resolution = 0.0

        # Avaliações
        ratings = (await s.execute(
            select(func.avg(SupportTicket.rating))
            .where(SupportTicket.rating.isnot(None))
        )).scalar() or 0.0

        total_ratings = (await s.execute(
            select(func.count(SupportTicket.id))
            .where(SupportTicket.rating.isnot(None))
        )).scalar() or 0

        agents_count = (await s.execute(
            select(func.count(SupportAgent.user_id)).where(SupportAgent.is_active == True)
        )).scalar() or 0

    settings = await get_settings()
    support_url = getattr(settings, "support_url", None)
    support_enabled = getattr(settings, "support_enabled", True)

    text = (
        "◈ BELLAMY | SUPORTE E ATENDIMENTO\n\n"
        f"📌 Status: {'🟢 Ativo' if support_enabled else '🔴 Desativado'}\n"
        f"🔗 Link de suporte: {support_url or '❌ não configurado'}\n"
        f"👨‍💼 Agentes ativos: {agents_count}\n\n"
        f"🎫 TICKETS\n"
        f"📊 Total: {total}\n"
        f"🟢 Abertos: {counts['open']}\n"
        f"🟡 Em andamento: {counts['in_progress']}\n"
        f"🔵 Aguardando cliente: {counts['waiting_user']}\n"
        f"✅ Resolvidos: {counts['resolved']}\n"
        f"⚪ Fechados: {counts['closed']}\n"
    )
    if urgent > 0:
        text += f"\n🔥 Urgentes em aberto: {urgent}\n"
    text += (
        f"\n📅 Novos hoje: {today_new} | Este mês: {month_new}\n"
        f"⏱️ Tempo médio de resolução: {avg_resolution:.1f}h\n"
        f"⭐ Avaliação média: {ratings:.2f}/5 ({total_ratings} avaliações)\n\n"
        "👇 Selecione uma opção:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🟢 Abertos ({counts['open']})", callback_data="sup_list_open_0")],
        [InlineKeyboardButton(
            text=f"🟡 Em andamento ({counts['in_progress']})", callback_data="sup_list_in_progress_0")],
        [InlineKeyboardButton(
            text=f"🔵 Aguardando cliente ({counts['waiting_user']})", callback_data="sup_list_waiting_user_0")],
        [InlineKeyboardButton(
            text=f"✅ Resolvidos ({counts['resolved']})", callback_data="sup_list_resolved_0")],
        [InlineKeyboardButton(
            text=f"⚪ Fechados ({counts['closed']})", callback_data="sup_list_closed_0")],
        [InlineKeyboardButton(
            text=f"📋 Todos ({total})", callback_data="sup_list_all_0")],
        [InlineKeyboardButton(text="🔥 Urgentes", callback_data="sup_urgent_0")],
        [InlineKeyboardButton(text="🔎 Buscar ticket", callback_data="sup_search")],
        [InlineKeyboardButton(text="💬 Respostas rápidas", callback_data="sup_canned")],
        [InlineKeyboardButton(text="📖 FAQ", callback_data="sup_faq")],
        [InlineKeyboardButton(text="👨‍💼 Agentes", callback_data="sup_agents")],
        [InlineKeyboardButton(text="⚙️ Configurações", callback_data="sup_config")],
        [InlineKeyboardButton(text="📊 Estatísticas", callback_data="sup_stats")],
        [InlineKeyboardButton(text="📥 Exportar relatório", callback_data="sup_export")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# LISTAGEM
# ============================================================
@router.callback_query(F.data.startswith("sup_list_"))
async def list_tickets(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    parts = cb.data.split("_")
    status = parts[2]
    page = int(parts[3])
    await _render_ticket_list(cb, status, page)


@router.callback_query(F.data.startswith("sup_urgent_"))
async def list_urgent(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])
    await _render_ticket_list(cb, "urgent", page)


async def _render_ticket_list(cb: CallbackQuery, filter_type: str, page: int):
    async with AsyncSessionLocal() as s:
        stmt = select(SupportTicket)
        cnt = select(func.count(SupportTicket.id))

        if filter_type == "urgent":
            stmt = stmt.where(SupportTicket.priority == "urgent")
            stmt = stmt.where(SupportTicket.status.in_(["open", "in_progress", "waiting_user"]))
            cnt = cnt.where(SupportTicket.priority == "urgent")
            cnt = cnt.where(SupportTicket.status.in_(["open", "in_progress", "waiting_user"]))
        elif filter_type != "all":
            stmt = stmt.where(SupportTicket.status == filter_type)
            cnt = cnt.where(SupportTicket.status == filter_type)

        total = (await s.execute(cnt)).scalar() or 0
        stmt = (
            stmt.order_by(
                desc(SupportTicket.priority),
                desc(SupportTicket.last_message_at),
            )
            .offset(page * TICKETS_PAGE_SIZE)
            .limit(TICKETS_PAGE_SIZE)
        )
        tickets = (await s.execute(stmt)).scalars().all()

        users = {}
        if tickets:
            uids = list({t.user_id for t in tickets})
            u = (await s.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            users = {x.id: x for x in u}

    titles = {
        "all": "TODOS OS TICKETS",
        "urgent": "🔥 TICKETS URGENTES",
        "open": "TICKETS ABERTOS",
        "in_progress": "TICKETS EM ANDAMENTO",
        "waiting_user": "AGUARDANDO CLIENTE",
        "resolved": "TICKETS RESOLVIDOS",
        "closed": "TICKETS FECHADOS",
    }
    text = f"◈ BELLAMY | {titles.get(filter_type, filter_type.upper())}\n\n"
    text += f"Total: {total} | Página {page + 1}\n\n"

    buttons = []
    if not tickets:
        text += "Nenhum ticket encontrado."
    else:
        now = datetime.utcnow()
        for t in tickets:
            s_icon, s_label = STATUS_META.get(t.status, ("•", t.status))
            p_icon, p_label = PRIORITY_META.get(t.priority, ("•", t.priority))
            u = users.get(t.user_id)
            uname = (u.full_name if u else f"User {t.user_id}")[:20]
            hours = int((now - t.last_message_at).total_seconds() / 3600)
            text += (
                f"{s_icon}{p_icon} #{t.id:05d} — {uname}\n"
                f"   <i>{(t.subject or 'Sem assunto')[:40]}</i>\n"
                f"   {s_label} | {p_label} | {hours}h sem msg\n\n"
            )
            buttons.append([InlineKeyboardButton(
                text=f"{s_icon}{p_icon} #{t.id:05d} — {uname}",
                callback_data=f"sup_view_{t.id}",
            )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"sup_list_{filter_type}_{page - 1}"
            if filter_type != "urgent" else f"sup_urgent_{page - 1}"
        ))
    if (page + 1) * TICKETS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"sup_list_{filter_type}_{page + 1}"
            if filter_type != "urgent" else f"sup_urgent_{page + 1}"
        ))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_support")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# DETALHE DO TICKET
# ============================================================
async def _render_ticket(ticket_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, ticket_id)
        if not t:
            return (
                "❌ Ticket não encontrado.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_support")]
                ]),
            )
        user = await s.get(User, t.user_id)

        msgs = (await s.execute(
            select(SupportMessage)
            .where(SupportMessage.ticket_id == ticket_id)
            .order_by(SupportMessage.created_at)
            .limit(MSGS_PAGE_SIZE)
        )).scalars().all()

        agent_username = None
        if t.assigned_to:
            agent = (await s.execute(
                select(User).where(User.id == t.assigned_to)
            )).scalar_one_or_none()
            if agent:
                agent_username = agent.full_name or f"User {agent.id}"

    s_icon, s_label = STATUS_META.get(t.status, ("•", t.status))
    p_icon, p_label = PRIORITY_META.get(t.priority, ("•", t.priority))
    uname = user.full_name if user else f"User {t.user_id}"
    uhandle = f"@{user.username}" if user and user.username else "—"

    text = (
        f"◈ BELLAMY | TICKET #{t.id:05d}\n\n"
        f"👤 Cliente: {uname}\n"
        f"💠 Username: {uhandle}\n"
        f"🆔 User ID: <code>{t.user_id}</code>\n\n"
        f"📋 Assunto: <b>{t.subject or 'Sem assunto'}</b>\n"
        f"📂 Categoria: {t.category or 'Geral'}\n"
        f"📌 Status: {s_icon} {s_label}\n"
        f"🚩 Prioridade: {p_icon} {p_label}\n"
    )
    if agent_username:
        text += f"👨‍💼 Atribuído a: {agent_username}\n"
    text += (
        f"📅 Criado: {t.created_at.strftime('%d/%m/%Y %H:%M')}\n"
        f"🕒 Última msg: {t.last_message_at.strftime('%d/%m/%Y %H:%M')}\n"
    )
    if t.resolved_at:
        hours = (t.resolved_at - t.created_at).total_seconds() / 3600
        text += f"✅ Resolvido em: {t.resolved_at.strftime('%d/%m/%Y %H:%M')} ({hours:.1f}h)\n"
    if t.rating:
        stars = "⭐" * t.rating
        text += f"\n⭐ Avaliação: {stars} ({t.rating}/5)\n"
        if t.rating_comment:
            text += f"💬 {t.rating_comment[:200]}\n"

    text += "\n━━━━━━━━━━━━━━\n\n📜 <b>Últimas mensagens</b>\n\n"

    if not msgs:
        text += "<i>Nenhuma mensagem ainda.</i>"
    else:
        for m in msgs[-15:]:
            ts = m.created_at.strftime("%d/%m %H:%M")
            if m.sender_type == "user":
                sender = f"👤 {uname[:15]}"
            elif m.sender_type == "agent":
                sender = f"👨‍💼 Agente {m.sender_id or ''}"
            elif m.sender_type == "system":
                sender = "🤖 Sistema"
            else:
                sender = m.sender_type
            if m.is_internal_note:
                sender += " 📝"
            preview = (m.content or "[mídia]")[:200]
            text += f"<b>{sender}</b> — {ts}\n<i>{preview}</i>\n\n"

    buttons = []
    if t.status in ("open", "in_progress", "waiting_user"):
        buttons.append([InlineKeyboardButton(text="💬 Responder", callback_data=f"sup_reply_{ticket_id}")])
        buttons.append([InlineKeyboardButton(text="📝 Nota interna", callback_data=f"sup_note_{ticket_id}")])
        buttons.append([InlineKeyboardButton(
            text="📌 Marcar em andamento" if t.status != "in_progress" else "🔵 Aguardando cliente",
            callback_data=f"sup_nextstatus_{ticket_id}")])
        buttons.append([InlineKeyboardButton(text="✅ Resolver", callback_data=f"sup_resolve_{ticket_id}")])
        buttons.append([InlineKeyboardButton(text="⚪ Fechar sem resolver", callback_data=f"sup_close_{ticket_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="🔄 Reabrir ticket", callback_data=f"sup_reopen_{ticket_id}")])

    buttons.append([InlineKeyboardButton(text="🚩 Alterar prioridade", callback_data=f"sup_setpriority_{ticket_id}")])
    buttons.append([InlineKeyboardButton(text="📂 Alterar categoria", callback_data=f"sup_setcategory_{ticket_id}")])
    buttons.append([InlineKeyboardButton(
        text="👤 Atribuir a agente" if not t.assigned_to else "🔄 Reatribuir",
        callback_data=f"sup_assign_{ticket_id}")])
    buttons.append([InlineKeyboardButton(text="👤 Ver usuário", callback_data=f"usr_view_{t.user_id}")])
    buttons.append([InlineKeyboardButton(text="📜 Ver mensagens completas", callback_data=f"sup_msgs_{ticket_id}_0")])
    buttons.append([InlineKeyboardButton(text="🗑️ Excluir ticket", callback_data=f"sup_delete_{ticket_id}")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=f"sup_list_{t.status}_0")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("sup_view_"))
async def view_ticket(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_view_", ""))
    text, kb = await _render_ticket(tid)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# MENSAGENS COMPLETAS
# ============================================================
@router.callback_query(F.data.startswith("sup_msgs_"))
async def view_messages(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("sup_msgs_", "")
    parts = rest.rsplit("_", 1)
    tid = int(parts[0])
    page = int(parts[1])

    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await cb.answer("Ticket não encontrado.", show_alert=True)
            return
        total = (await s.execute(
            select(func.count(SupportMessage.id))
            .where(SupportMessage.ticket_id == tid)
        )).scalar() or 0
        stmt = (
            select(SupportMessage)
            .where(SupportMessage.ticket_id == tid)
            .order_by(SupportMessage.created_at)
            .offset(page * 15)
            .limit(15)
        )
        msgs = (await s.execute(stmt)).scalars().all()
        user = await s.get(User, t.user_id)

    uname = user.full_name if user else f"User {t.user_id}"
    text = f"📜 MENSAGENS — Ticket #{tid}\n\nTotal: {total} | Página {page + 1}\n\n"

    for m in msgs:
        ts = m.created_at.strftime("%d/%m/%Y %H:%M")
        if m.sender_type == "user":
            sender = f"👤 {uname}"
        elif m.sender_type == "agent":
            sender = f"👨‍💼 Agente"
        elif m.sender_type == "system":
            sender = "🤖 Sistema"
        else:
            sender = m.sender_type
        if m.is_internal_note:
            sender += " 📝 [NOTA INTERNA]"
        text += f"<b>{sender}</b> — {ts}\n"
        if m.content:
            text += f"{m.content[:500]}\n"
        if m.media_type:
            text += f"<i>[{m.media_type}]</i>\n"
        text += "\n"

    buttons = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"sup_msgs_{tid}_{page - 1}"))
    if (page + 1) * 15 < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"sup_msgs_{tid}_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar ao ticket", callback_data=f"sup_view_{tid}")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# RESPONDER / NOTA INTERNA
# ============================================================
@router.callback_query(F.data.startswith("sup_reply_"))
async def reply_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_reply_", ""))
    await state.update_data(ticket_id=tid, internal=False)
    await state.set_state(SS.reply_text)

    # Mostrar respostas rápidas
    async with AsyncSessionLocal() as s:
        canned = (await s.execute(
            select(CannedResponse)
            .where(CannedResponse.is_active == True)
            .order_by(CannedResponse.title)
            .limit(10)
        )).scalars().all()

    buttons = []
    if canned:
        for c in canned:
            buttons.append([InlineKeyboardButton(
                text=f"💬 {c.title[:40]}",
                callback_data=f"sup_usecanned_{tid}_{c.id}",
            )])

    buttons.append([InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"sup_view_{tid}")])

    await cb.message.answer(
        f"💬 RESPONDER TICKET #{tid}\n\n"
        f"Envie a mensagem que será enviada ao cliente.\n\n"
        f"Ou selecione uma resposta rápida:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if canned else None,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sup_note_"))
async def note_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_note_", ""))
    await state.update_data(ticket_id=tid, internal=True)
    await state.set_state(SS.reply_text)
    await cb.message.answer(
        f"📝 NOTA INTERNA — Ticket #{tid}\n\n"
        f"Esta nota será visível apenas para administradores.\n\nEnvie o conteúdo:"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sup_usecanned_"))
async def use_canned(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("sup_usecanned_", "")
    parts = rest.rsplit("_", 1)
    tid = int(parts[0])
    cid = int(parts[1])

    data = await state.get_data()
    internal = data.get("internal", False)

    async with AsyncSessionLocal() as s:
        canned = await s.get(CannedResponse, cid)
        if not canned:
            await cb.answer("Resposta não encontrada.", show_alert=True)
            return
        content = canned.content
        canned.used_count += 1
        await s.commit()

    await state.clear()
    await _send_reply_to_ticket(tid, content, internal, cb.from_user.id)
    await cb.message.edit_text("✅ Resposta rápida enviada.")
    await cb.answer("Enviada.")


@router.message(SS.reply_text)
async def reply_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    content = (m.text or m.caption or "").strip()
    if not content:
        await m.answer("❌ Envie um texto para a resposta.")
        return

    data = await state.get_data()
    tid = data.get("ticket_id")
    internal = data.get("internal", False)
    await state.clear()
    if not tid:
        await m.answer("❌ Sessão expirada.")
        return

    await _send_reply_to_ticket(tid, content, internal, m.from_user.id)
    await m.answer("✅ Resposta enviada." if not internal else "✅ Nota interna registrada.")
    text, kb = await _render_ticket(tid)
    await m.answer(text, reply_markup=kb)


async def _send_reply_to_ticket(ticket_id: int, content: str, internal: bool, admin_id: int):
    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, ticket_id)
        if not t:
            return

        s.add(SupportMessage(
            ticket_id=ticket_id,
            sender_type="agent",
            sender_id=admin_id,
            content=content,
            is_internal_note=internal,
        ))
        t.last_message_at = datetime.utcnow()

        if not internal:
            if t.status == "open":
                t.status = "in_progress"
            elif t.status == "waiting_user":
                t.status = "in_progress"
            t.last_agent_reply_at = datetime.utcnow()

        await s.commit()
        user_id = t.user_id

    if not internal:
        try:
            await bot.send_message(
                user_id,
                f"💬 <b>Resposta do suporte</b> (Ticket #{ticket_id:05d})\n\n{content}",
            )
        except Exception:
            pass

    await log_admin(admin_id, "support_reply" if not internal else "support_internal_note", {
        "ticket_id": ticket_id, "len": len(content),
    })


# ============================================================
# AÇÕES DO TICKET
# ============================================================
@router.callback_query(F.data.startswith("sup_nextstatus_"))
async def next_status(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_nextstatus_", ""))
    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await cb.answer("Ticket não encontrado.", show_alert=True)
            return
        if t.status == "open" or t.status == "waiting_user":
            t.status = "in_progress"
        elif t.status == "in_progress":
            t.status = "waiting_user"
        await s.commit()

    await log_admin(cb.from_user.id, "support_next_status", {"ticket_id": tid, "new": t.status})
    text, kb = await _render_ticket(tid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Status atualizado.")


@router.callback_query(F.data.startswith("sup_resolve_"))
async def resolve_ticket(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_resolve_", ""))
    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await cb.answer("Ticket não encontrado.", show_alert=True)
            return
        t.status = "resolved"
        t.resolved_at = datetime.utcnow()
        await s.commit()
        user_id = t.user_id

    await log_admin(cb.from_user.id, "support_resolve", {"ticket_id": tid})

    try:
        await bot.send_message(
            user_id,
            f"✅ <b>Ticket #{tid:05d} resolvido!</b>\n\n"
            f"Seu atendimento foi concluído.\n"
            f"Por favor, avalie nosso suporte respondendo com uma nota de 1 a 5 ⭐",
        )
    except Exception:
        pass

    text, kb = await _render_ticket(tid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Ticket resolvido.")


@router.callback_query(F.data.startswith("sup_close_"))
async def close_ticket(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_close_", ""))
    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await cb.answer("Ticket não encontrado.", show_alert=True)
            return
        t.status = "closed"
        t.closed_at = datetime.utcnow()
        await s.commit()

    await log_admin(cb.from_user.id, "support_close", {"ticket_id": tid})
    text, kb = await _render_ticket(tid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Ticket fechado.")


@router.callback_query(F.data.startswith("sup_reopen_"))
async def reopen_ticket(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_reopen_", ""))
    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await cb.answer("Ticket não encontrado.", show_alert=True)
            return
        t.status = "in_progress"
        t.resolved_at = None
        t.closed_at = None
        t.last_message_at = datetime.utcnow()
        await s.commit()

    await log_admin(cb.from_user.id, "support_reopen", {"ticket_id": tid})
    text, kb = await _render_ticket(tid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Ticket reaberto.")


# ============================================================
# PRIORIDADE
# ============================================================
@router.callback_query(F.data.startswith("sup_setpriority_"))
async def set_priority_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_setpriority_", ""))
    buttons = []
    for key, (icon, label) in PRIORITY_META.items():
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {label}",
            callback_data=f"sup_priority_{tid}_{key}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"sup_view_{tid}")])
    await cb.message.edit_text(
        f"🚩 ALTERAR PRIORIDADE — Ticket #{tid}\n\nSelecione:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sup_priority_"))
async def set_priority(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("sup_priority_", "")
    parts = rest.rsplit("_", 1)
    tid = int(parts[0])
    priority = parts[1]

    if priority not in PRIORITY_META:
        await cb.answer("Prioridade inválida.")
        return

    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await cb.answer("Ticket não encontrado.", show_alert=True)
            return
        t.priority = priority
        await s.commit()

    await log_admin(cb.from_user.id, "support_set_priority", {"ticket_id": tid, "new": priority})
    text, kb = await _render_ticket(tid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Prioridade atualizada.")


# ============================================================
# CATEGORIA
# ============================================================
DEFAULT_CATEGORIES = [
    "Dúvida",
    "Problema técnico",
    "Pedido",
    "Pagamento",
    "Reembolso",
    "Sugestão",
    "Outro",
]


@router.callback_query(F.data.startswith("sup_setcategory_"))
async def set_category_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_setcategory_", ""))
    buttons = []
    for cat in DEFAULT_CATEGORIES:
        buttons.append([InlineKeyboardButton(
            text=f"📂 {cat}",
            callback_data=f"sup_category_{tid}_{cat[:20]}",
        )])
    buttons.append([InlineKeyboardButton(text="✏️ Personalizada", callback_data=f"sup_customcat_{tid}")])
    buttons.append([InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"sup_view_{tid}")])
    await cb.message.edit_text(
        f"📂 ALTERAR CATEGORIA — Ticket #{tid}\n\nSelecione:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sup_category_"))
async def set_category(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("sup_category_", "")
    parts = rest.split("_", 1)
    tid = int(parts[0])
    category = parts[1]

    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await cb.answer("Ticket não encontrado.", show_alert=True)
            return
        t.category = category
        await s.commit()

    await log_admin(cb.from_user.id, "support_set_category", {"ticket_id": tid, "new": category})
    text, kb = await _render_ticket(tid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Categoria atualizada.")


@router.callback_query(F.data.startswith("sup_customcat_"))
async def custom_category_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_customcat_", ""))
    await state.update_data(ticket_id=tid)
    await state.set_state(SS.ticket_category)
    await cb.message.answer("📂 Envie o nome da categoria (2-50 caracteres):")
    await cb.answer()


@router.message(SS.ticket_category)
async def custom_category_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if len(raw) < 2 or len(raw) > 50:
        await m.answer("❌ 2-50 caracteres.")
        return
    data = await state.get_data()
    tid = data.get("ticket_id")
    await state.clear()
    if not tid:
        return
    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, tid)
        if t:
            t.category = raw
            await s.commit()
    await log_admin(m.from_user.id, "support_set_category", {"ticket_id": tid, "new": raw})
    text, kb = await _render_ticket(tid)
    await m.answer("✅ Categoria atualizada.")
    await m.answer(text, reply_markup=kb)


# ============================================================
# ATRIBUIR AGENTE
# ============================================================
@router.callback_query(F.data.startswith("sup_assign_"))
async def assign_agent_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_assign_", ""))

    async with AsyncSessionLocal() as s:
        agents = (await s.execute(
            select(SupportAgent)
            .where(SupportAgent.is_active == True)
            .order_by(SupportAgent.created_at)
            .limit(20)
        )).scalars().all()

        uids = [a.user_id for a in agents]
        users = {}
        if uids:
            users = {
                x.id: x for x in (await s.execute(
                    select(User).where(User.id.in_(uids))
                )).scalars().all()
            }

    buttons = []
    if agents:
        for a in agents:
            u = users.get(a.user_id)
            uname = (u.full_name if u else f"User {a.user_id}")[:25]
            buttons.append([InlineKeyboardButton(
                text=f"👨‍💼 {uname}",
                callback_data=f"sup_assign_to_{tid}_{a.user_id}",
            )])
    else:
        buttons.append([InlineKeyboardButton(
            text="⚠️ Nenhum agente cadastrado",
            callback_data="sup_agents_add",
        )])
    buttons.append([InlineKeyboardButton(
        text="👤 Atribuir a mim", callback_data=f"sup_assign_to_{tid}_{cb.from_user.id}"
    )])
    buttons.append([InlineKeyboardButton(text="🗑️ Remover atribuição", callback_data=f"sup_unassign_{tid}")])
    buttons.append([InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"sup_view_{tid}")])

    await cb.message.edit_text(
        f"👤 ATRIBUIR TICKET #{tid}\n\nSelecione o agente:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sup_assign_to_"))
async def assign_to(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("sup_assign_to_", "")
    parts = rest.rsplit("_", 1)
    tid = int(parts[0])
    agent_id = int(parts[1])

    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await cb.answer("Ticket não encontrado.", show_alert=True)
            return
        t.assigned_to = agent_id
        await s.commit()

    await log_admin(cb.from_user.id, "support_assign", {"ticket_id": tid, "agent": agent_id})
    text, kb = await _render_ticket(tid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atribuído.")


@router.callback_query(F.data.startswith("sup_unassign_"))
async def unassign(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_unassign_", ""))
    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, tid)
        if t:
            t.assigned_to = None
            await s.commit()
    await log_admin(cb.from_user.id, "support_unassign", {"ticket_id": tid})
    text, kb = await _render_ticket(tid)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atribuição removida.")


# ============================================================
# EXCLUIR TICKET
# ============================================================
@router.callback_query(F.data.startswith("sup_delete_"))
async def delete_prompt(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_delete_", ""))
    await cb.message.edit_text(
        f"🗑️ EXCLUIR TICKET #{tid}\n\n"
        f"⚠️ Esta ação apagará o ticket e todas as suas mensagens.\n"
        f"Não pode ser desfeita.\n\nConfirmar?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Sim, excluir", callback_data=f"sup_delete_ok_{tid}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"sup_view_{tid}")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sup_delete_ok_"))
async def delete_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    tid = int(cb.data.replace("sup_delete_ok_", ""))
    async with AsyncSessionLocal() as s:
        msgs = (await s.execute(
            select(SupportMessage).where(SupportMessage.ticket_id == tid)
        )).scalars().all()
        for m in msgs:
            await s.delete(m)
        t = await s.get(SupportTicket, tid)
        if t:
            await s.delete(t)
        await s.commit()
    await log_admin(cb.from_user.id, "support_delete_ticket", {"ticket_id": tid})
    await cb.message.edit_text(
        f"✅ Ticket #{tid} excluído.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_support")],
        ]),
    )
    await cb.answer("Excluído.")


# ============================================================
# BUSCA
# ============================================================
@router.callback_query(F.data == "sup_search")
async def search_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(SS.search)
    await cb.message.answer(
        "🔎 BUSCAR TICKET\n\n"
        "Envie:\n"
        "• ID do ticket (ex.: 123)\n"
        "• ID do Telegram do usuário\n"
        "• @username\n"
        "• Palavra-chave no assunto\n\n"
        "Digite /cancelar para abortar."
    )
    await cb.answer()


@router.message(SS.search)
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
        tickets = []
        if q.isdigit():
            n = int(q)
            t = await s.get(SupportTicket, n)
            if t:
                tickets = [t]
            else:
                stmt = (
                    select(SupportTicket)
                    .where(SupportTicket.user_id == n)
                    .order_by(desc(SupportTicket.created_at))
                    .limit(20)
                )
                tickets = (await s.execute(stmt)).scalars().all()
        else:
            clean = q.lstrip("@")
            stmt = (
                select(SupportTicket)
                .join(User, User.id == SupportTicket.user_id)
                .where(or_(
                    User.username.ilike(f"%{clean}%"),
                    User.full_name.ilike(f"%{q}%"),
                    SupportTicket.subject.ilike(f"%{q}%"),
                ))
                .order_by(desc(SupportTicket.last_message_at))
                .limit(20)
            )
            tickets = (await s.execute(stmt)).scalars().all()

    if not tickets:
        await m.answer("❌ Nenhum ticket encontrado.")
        return

    text = f"🔎 RESULTADOS: {len(tickets)} ticket(s)\n\n"
    buttons = []
    for t in tickets:
        s_icon, s_label = STATUS_META.get(t.status, ("•", t.status))
        text += f"{s_icon} #{t.id:05d} — {s_label} — {(t.subject or 'Sem assunto')[:40]}\n"
        buttons.append([InlineKeyboardButton(
            text=f"{s_icon} #{t.id:05d} — {(t.subject or 'Sem assunto')[:30]}",
            callback_data=f"sup_view_{t.id}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_support")])

    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ============================================================
# RESPOSTAS RÁPIDAS
# ============================================================
@router.callback_query(F.data == "sup_canned")
async def canned_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        all_canned = (await s.execute(
            select(CannedResponse).order_by(desc(CannedResponse.is_active), CannedResponse.title)
        )).scalars().all()

        # Se vazio, popular com defaults
        if not all_canned:
            for title, content in DEFAULT_CANNED:
                s.add(CannedResponse(title=title, content=content, is_active=True))
            await s.commit()
            all_canned = (await s.execute(
                select(CannedResponse).order_by(CannedResponse.title)
            )).scalars().all()

    text = f"💬 RESPOSTAS RÁPIDAS\n\nTotal: {len(all_canned)}\n\n"
    buttons = []
    for c in all_canned:
        icon = "🟢" if c.is_active else "🔴"
        text += f"{icon} <b>{c.title}</b>\n   <i>{c.content[:80]}</i>\n\n"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {c.title[:40]}",
            callback_data=f"sup_canned_view_{c.id}",
        )])
    buttons.append([InlineKeyboardButton(text="➕ Nova resposta", callback_data="sup_canned_new")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_support")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data == "sup_canned_new")
async def canned_new_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(SS.canned_new_title)
    await cb.message.answer("➕ NOVA RESPOSTA RÁPIDA\n\nInforme o título (ex.: Saudação):")
    await cb.answer()


@router.message(SS.canned_new_title)
async def canned_new_title(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if len(raw) < 2 or len(raw) > 100:
        await m.answer("❌ Título deve ter 2-100 caracteres.")
        return
    await state.update_data(canned_title=raw)
    await state.set_state(SS.canned_new_content)
    await m.answer("Agora envie o conteúdo da resposta:")


@router.message(SS.canned_new_content)
async def canned_new_content(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if len(raw) < 5 or len(raw) > 2000:
        await m.answer("❌ Conteúdo deve ter 5-2000 caracteres.")
        return

    data = await state.get_data()
    title = data.get("canned_title")
    await state.clear()
    if not title:
        return

    async with AsyncSessionLocal() as s:
        s.add(CannedResponse(title=title, content=raw, is_active=True))
        await s.commit()

    await log_admin(m.from_user.id, "support_canned_new", {"title": title})
    await m.answer(f"✅ Resposta rápida <b>{title}</b> criada.")


@router.callback_query(F.data.startswith("sup_canned_view_"))
async def canned_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    cid = int(cb.data.replace("sup_canned_view_", ""))
    async with AsyncSessionLocal() as s:
        c = await s.get(CannedResponse, cid)
        if not c:
            await cb.answer("Resposta não encontrada.", show_alert=True)
            return

    icon = "🟢" if c.is_active else "🔴"
    text = (
        f"💬 RESPOSTA RÁPIDA #{c.id}\n\n"
        f"📌 Status: {icon}\n"
        f"🏷️ Título: <b>{c.title}</b>\n"
        f"📊 Usos: {c.used_count}\n\n"
        f"💬 <b>Conteúdo</b>\n<code>{c.content[:500]}</code>"
    )
    buttons = [
        [InlineKeyboardButton(text="✏️ Editar título", callback_data=f"sup_canned_edit_{c.id}_title")],
        [InlineKeyboardButton(text="✏️ Editar conteúdo", callback_data=f"sup_canned_edit_{c.id}_content")],
        [InlineKeyboardButton(
            text=("🔴 Desativar" if c.is_active else "🟢 Ativar"),
            callback_data=f"sup_canned_toggle_{c.id}")],
        [InlineKeyboardButton(text="🗑️ Excluir", callback_data=f"sup_canned_del_{c.id}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="sup_canned")],
    ]
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("sup_canned_toggle_"))
async def canned_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    cid = int(cb.data.replace("sup_canned_toggle_", ""))
    async with AsyncSessionLocal() as s:
        c = await s.get(CannedResponse, cid)
        if c:
            c.is_active = not c.is_active
            await s.commit()
    await log_admin(cb.from_user.id, "support_canned_toggle", {"id": cid})
    await canned_view(cb)


@router.callback_query(F.data.startswith("sup_canned_edit_"))
async def canned_edit_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("sup_canned_edit_", "")
    parts = rest.rsplit("_", 1)
    cid = int(parts[0])
    field = parts[1]
    await state.update_data(canned_id=cid, canned_field=field)
    await state.set_state(SS.canned_edit)
    await cb.message.answer(f"✏️ Envie o novo {'título' if field == 'title' else 'conteúdo'}:")
    await cb.answer()


@router.message(SS.canned_edit)
async def canned_edit_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    data = await state.get_data()
    cid = data.get("canned_id")
    field = data.get("canned_field")
    await state.clear()
    if not cid or not field:
        return

    if field == "title" and (len(raw) < 2 or len(raw) > 100):
        await m.answer("❌ 2-100 caracteres.")
        return
    if field == "content" and (len(raw) < 5 or len(raw) > 2000):
        await m.answer("❌ 5-2000 caracteres.")
        return

    async with AsyncSessionLocal() as s:
        c = await s.get(CannedResponse, cid)
        if not c:
            await m.answer("❌ Resposta não encontrada.")
            return
        setattr(c, field, raw)
        await s.commit()

    await log_admin(m.from_user.id, "support_canned_edit", {"id": cid, "field": field})
    await m.answer("✅ Atualizado.")


@router.callback_query(F.data.startswith("sup_canned_del_"))
async def canned_delete(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    cid = int(cb.data.replace("sup_canned_del_", ""))
    async with AsyncSessionLocal() as s:
        c = await s.get(CannedResponse, cid)
        if c:
            await s.delete(c)
            await s.commit()
    await log_admin(cb.from_user.id, "support_canned_delete", {"id": cid})
    await cb.message.edit_text(
        "✅ Resposta rápida excluída.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="sup_canned")],
        ]),
    )
    await cb.answer("Excluída.")


# ============================================================
# FAQ
# ============================================================
@router.callback_query(F.data == "sup_faq")
async def faq_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        faqs = (await s.execute(
            select(FAQEntry).order_by(desc(FAQEntry.is_active), FAQEntry.display_order)
        )).scalars().all()

    text = f"📖 GERENCIAR FAQ\n\nTotal: {len(faqs)}\n\n"
    buttons = []
    for f in faqs:
        icon = "🟢" if f.is_active else "🔴"
        text += f"{icon} <b>{f.question[:60]}</b>\n"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {f.question[:40]}",
            callback_data=f"sup_faq_view_{f.id}",
        )])
    buttons.append([InlineKeyboardButton(text="➕ Nova entrada", callback_data="sup_faq_new")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_support")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data == "sup_faq_new")
async def faq_new_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(SS.faq_new_q)
    await cb.message.answer("➕ NOVA FAQ (1/2)\n\nEnvie a pergunta:")
    await cb.answer()


@router.message(SS.faq_new_q)
async def faq_new_q(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if len(raw) < 5 or len(raw) > 300:
        await m.answer("❌ 5-300 caracteres.")
        return
    await state.update_data(faq_q=raw)
    await state.set_state(SS.faq_new_a)
    await m.answer("Agora envie a resposta:")


@router.message(SS.faq_new_a)
async def faq_new_a(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if len(raw) < 5 or len(raw) > 2000:
        await m.answer("❌ 5-2000 caracteres.")
        return

    data = await state.get_data()
    question = data.get("faq_q")
    await state.clear()
    if not question:
        return

    async with AsyncSessionLocal() as s:
        max_order = (await s.execute(
            select(func.coalesce(func.max(FAQEntry.display_order), 0))
        )).scalar() or 0
        s.add(FAQEntry(
            question=question,
            answer=raw,
            is_active=True,
            display_order=max_order + 1,
        ))
        await s.commit()

    await log_admin(m.from_user.id, "support_faq_new", {"q": question[:50]})
    await m.answer("✅ FAQ adicionada.")


@router.callback_query(F.data.startswith("sup_faq_view_"))
async def faq_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    fid = int(cb.data.replace("sup_faq_view_", ""))
    async with AsyncSessionLocal() as s:
        f = await s.get(FAQEntry, fid)
        if not f:
            await cb.answer("FAQ não encontrada.", show_alert=True)
            return

    icon = "🟢" if f.is_active else "🔴"
    text = (
        f"📖 FAQ #{f.id}\n\n"
        f"📌 Status: {icon}\n"
        f"📊 Views: {f.views}\n"
        f"📐 Ordem: {f.display_order}\n\n"
        f"❓ <b>Pergunta</b>\n{f.question}\n\n"
        f"💬 <b>Resposta</b>\n{f.answer[:500]}"
    )
    buttons = [
        [InlineKeyboardButton(text="✏️ Editar pergunta", callback_data=f"sup_faq_edit_{f.id}_q")],
        [InlineKeyboardButton(text="✏️ Editar resposta", callback_data=f"sup_faq_edit_{f.id}_a")],
        [InlineKeyboardButton(
            text=("🔴 Desativar" if f.is_active else "🟢 Ativar"),
            callback_data=f"sup_faq_toggle_{f.id}")],
        [InlineKeyboardButton(text="🗑️ Excluir", callback_data=f"sup_faq_del_{f.id}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="sup_faq")],
    ]
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("sup_faq_toggle_"))
async def faq_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    fid = int(cb.data.replace("sup_faq_toggle_", ""))
    async with AsyncSessionLocal() as s:
        f = await s.get(FAQEntry, fid)
        if f:
            f.is_active = not f.is_active
            await s.commit()
    await log_admin(cb.from_user.id, "support_faq_toggle", {"id": fid})
    await faq_view(cb)


@router.callback_query(F.data.startswith("sup_faq_edit_"))
async def faq_edit_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("sup_faq_edit_", "")
    parts = rest.rsplit("_", 1)
    fid = int(parts[0])
    field = parts[1]
    await state.update_data(faq_id=fid, faq_field=field)
    if field == "q":
        await state.set_state(SS.faq_edit_q)
        await cb.message.answer("✏️ Envie a nova pergunta:")
    else:
        await state.set_state(SS.faq_edit_a)
        await cb.message.answer("✏️ Envie a nova resposta:")
    await cb.answer()


@router.message(SS.faq_edit_q)
async def faq_edit_q(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if len(raw) < 5 or len(raw) > 300:
        await m.answer("❌ 5-300 caracteres.")
        return
    data = await state.get_data()
    fid = data.get("faq_id")
    await state.clear()
    async with AsyncSessionLocal() as s:
        f = await s.get(FAQEntry, fid)
        if f:
            f.question = raw
            await s.commit()
    await log_admin(m.from_user.id, "support_faq_edit_q", {"id": fid})
    await m.answer("✅ Atualizado.")


@router.message(SS.faq_edit_a)
async def faq_edit_a(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if len(raw) < 5 or len(raw) > 2000:
        await m.answer("❌ 5-2000 caracteres.")
        return
    data = await state.get_data()
    fid = data.get("faq_id")
    await state.clear()
    async with AsyncSessionLocal() as s:
        f = await s.get(FAQEntry, fid)
        if f:
            f.answer = raw
            await s.commit()
    await log_admin(m.from_user.id, "support_faq_edit_a", {"id": fid})
    await m.answer("✅ Atualizado.")


@router.callback_query(F.data.startswith("sup_faq_del_"))
async def faq_delete(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    fid = int(cb.data.replace("sup_faq_del_", ""))
    async with AsyncSessionLocal() as s:
        f = await s.get(FAQEntry, fid)
        if f:
            await s.delete(f)
            await s.commit()
    await log_admin(cb.from_user.id, "support_faq_delete", {"id": fid})
    await cb.message.edit_text(
        "✅ FAQ excluída.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="sup_faq")],
        ]),
    )
    await cb.answer("Excluída.")


# ============================================================
# AGENTES
# ============================================================
@router.callback_query(F.data == "sup_agents")
async def agents_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        agents = (await s.execute(
            select(SupportAgent).order_by(desc(SupportAgent.is_active), SupportAgent.created_at)
        )).scalars().all()

        uids = [a.user_id for a in agents]
        users = {}
        if uids:
            users = {
                x.id: x for x in (await s.execute(
                    select(User).where(User.id.in_(uids))
                )).scalars().all()
            }

    text = f"👨‍💼 AGENTES DE SUPORTE\n\nTotal: {len(agents)}\n\n"
    buttons = []
    for a in agents:
        u = users.get(a.user_id)
        uname = (u.full_name if u else f"User {a.user_id}")[:25]
        icon = "🟢" if a.is_active else "🔴"
        text += f"{icon} {uname} — Nível: {a.level}\n"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {uname}",
            callback_data=f"sup_agent_view_{a.user_id}",
        )])
    buttons.append([InlineKeyboardButton(text="➕ Adicionar agente", callback_data="sup_agents_add")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_support")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data == "sup_agents_add")
async def agents_add_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(SS.agent_add)
    await cb.message.answer(
        "➕ ADICIONAR AGENTE\n\nEnvie o ID do Telegram do usuário que deseja promover a agente:"
    )
    await cb.answer()


@router.message(SS.agent_add)
async def agent_add_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if not raw.isdigit():
        await m.answer("❌ Envie apenas o ID numérico.")
        return
    uid = int(raw)
    await state.clear()

    async with AsyncSessionLocal() as s:
        user = await s.get(User, uid)
        if not user:
            await m.answer(f"❌ Usuário {uid} não encontrado no banco.")
            return
        existing = await s.get(SupportAgent, uid)
        if existing:
            existing.is_active = True
            await s.commit()
            await m.answer(f"✅ Agente {uid} reativado.")
            return
        s.add(SupportAgent(user_id=uid, level="agent", is_active=True))
        await s.commit()

    await log_admin(m.from_user.id, "support_agent_add", {"user_id": uid})
    await m.answer(f"✅ {user.full_name} adicionado como agente.")


@router.callback_query(F.data.startswith("sup_agent_view_"))
async def agent_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.replace("sup_agent_view_", ""))
    async with AsyncSessionLocal() as s:
        a = await s.get(SupportAgent, uid)
        if not a:
            await cb.answer("Agente não encontrado.", show_alert=True)
            return
        user = await s.get(User, uid)
        total_handled = (await s.execute(
            select(func.count(SupportTicket.id))
            .where(SupportTicket.assigned_to == uid)
        )).scalar() or 0
        resolved = (await s.execute(
            select(func.count(SupportTicket.id))
            .where(SupportTicket.assigned_to == uid)
            .where(SupportTicket.status.in_(["resolved", "closed"]))
        )).scalar() or 0
        messages_sent = (await s.execute(
            select(func.count(SupportMessage.id))
            .where(SupportMessage.sender_id == uid)
            .where(SupportMessage.sender_type == "agent")
        )).scalar() or 0

    uname = (user.full_name if user else f"User {uid}")[:30]
    uhandle = f"@{user.username}" if user and user.username else "—"
    icon = "🟢" if a.is_active else "🔴"

    text = (
        f"👨‍💼 AGENTE — {uname}\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"💠 Username: {uhandle}\n"
        f"📌 Status: {icon}\n"
        f"🎖️ Nível: {a.level}\n"
        f"📅 Adicionado: {a.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"📊 ESTATÍSTICAS\n"
        f"🎫 Tickets atribuídos: {total_handled}\n"
        f"✅ Resolvidos: {resolved}\n"
        f"💬 Mensagens enviadas: {messages_sent}"
    )

    buttons = [
        [InlineKeyboardButton(
            text=("🔴 Desativar agente" if a.is_active else "🟢 Ativar agente"),
            callback_data=f"sup_agent_toggle_{uid}")],
        [InlineKeyboardButton(text="🎖️ Alterar nível", callback_data=f"sup_agent_level_{uid}")],
        [InlineKeyboardButton(text="🗑️ Remover agente", callback_data=f"sup_agent_del_{uid}")],
        [InlineKeyboardButton(text="👤 Ver usuário", callback_data=f"usr_view_{uid}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="sup_agents")],
    ]
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("sup_agent_toggle_"))
async def agent_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.replace("sup_agent_toggle_", ""))
    async with AsyncSessionLocal() as s:
        a = await s.get(SupportAgent, uid)
        if a:
            a.is_active = not a.is_active
            await s.commit()
    await log_admin(cb.from_user.id, "support_agent_toggle", {"user_id": uid})
    await agent_view(cb)


@router.callback_query(F.data.startswith("sup_agent_level_"))
async def agent_level_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.replace("sup_agent_level_", ""))
    levels = {
        "agent": "🎖️ Agente",
        "senior": "⭐ Sênior",
        "supervisor": "👑 Supervisor",
    }
    buttons = []
    for key, label in levels.items():
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"sup_agent_setlevel_{uid}_{key}"
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"sup_agent_view_{uid}")])
    await cb.message.edit_text(
        f"🎖️ ALTERAR NÍVEL — Agente {uid}\n\nSelecione:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sup_agent_setlevel_"))
async def agent_set_level(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("sup_agent_setlevel_", "")
    parts = rest.rsplit("_", 1)
    uid = int(parts[0])
    level = parts[1]
    async with AsyncSessionLocal() as s:
        a = await s.get(SupportAgent, uid)
        if a:
            a.level = level
            await s.commit()
    await log_admin(cb.from_user.id, "support_agent_level", {"user_id": uid, "level": level})
    await agent_view(cb)


@router.callback_query(F.data.startswith("sup_agent_del_"))
async def agent_delete(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.replace("sup_agent_del_", ""))
    async with AsyncSessionLocal() as s:
        a = await s.get(SupportAgent, uid)
        if a:
            await s.delete(a)
            await s.commit()
    await log_admin(cb.from_user.id, "support_agent_delete", {"user_id": uid})
    await cb.message.edit_text(
        f"✅ Agente {uid} removido.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="sup_agents")],
        ]),
    )
    await cb.answer("Removido.")


# ============================================================
# CONFIGURAÇÕES
# ============================================================
async def config_view() -> tuple[str, InlineKeyboardMarkup]:
    s = await get_settings(force=True)
    enabled = getattr(s, "support_enabled", True)
    support_url = getattr(s, "support_url", None)
    auto_assign = getattr(s, "support_auto_assign", False)
    auto_close_days = getattr(s, "support_auto_close_days", 7)
    welcome_msg = getattr(s, "support_welcome_message", "Olá! Como podemos ajudar? Envie sua mensagem que um atendente responderá em breve.")

    text = (
        "◈ BELLAMY | CONFIGURAÇÕES DE SUPORTE\n\n"
        f"📌 Status: {'🟢 Ativo' if enabled else '🔴 Desativado'}\n"
        f"🔗 Link externo: {support_url or '❌ não configurado'}\n"
        f"🎯 Auto-atribuição: {'🟢 Ativada' if auto_assign else '🔴 Desativada'}\n"
        f"⏱️ Auto-fechar após: {auto_close_days} dias\n\n"
        f"💬 Mensagem de boas-vindas:\n<code>{welcome_msg[:200]}</code>"
    )

    buttons = [
        [InlineKeyboardButton(
            text=("🔴 Desativar" if enabled else "🟢 Ativar"),
            callback_data="sup_cfg_t_enabled")],
        [InlineKeyboardButton(text="✏️ Link externo", callback_data="sup_cfg_url")],
        [InlineKeyboardButton(
            text=("🔴 Desativar auto-atribuição" if auto_assign else "🟢 Ativar auto-atribuição"),
            callback_data="sup_cfg_t_autoassign")],
        [InlineKeyboardButton(text=f"⏱️ Auto-fechar ({auto_close_days} dias)", callback_data="sup_cfg_autoclose")],
        [InlineKeyboardButton(text="💬 Mensagem de boas-vindas", callback_data="sup_cfg_welcome")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_support")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "sup_config")
async def config(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


class SupportCfg(StatesGroup):
    url = State()
    autoclose = State()
    welcome = State()


@router.callback_query(F.data == "sup_cfg_t_enabled")
async def cfg_toggle_enabled(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.support_enabled = not getattr(bs, "support_enabled", True)
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "support_toggle", {})
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "sup_cfg_t_autoassign")
async def cfg_toggle_autoassign(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.support_auto_assign = not getattr(bs, "support_auto_assign", False)
        await s.commit()
    invalidate_cache()
    await log_admin(cb.from_user.id, "support_autoassign_toggle", {})
    text, kb = await config_view()
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


@router.callback_query(F.data == "sup_cfg_url")
async def cfg_url_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(SupportCfg.url)
    await cb.message.answer("🔗 Envie o link de suporte externo (ou /limpar):")
    await cb.answer()


@router.message(SupportCfg.url)
async def cfg_url_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if raw.lower() == "/limpar":
        val = None
    else:
        if not (raw.startswith("http://") or raw.startswith("https://") or raw.startswith("tg://")):
            await m.answer("❌ URL inválida.")
            return
        val = raw
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.support_url = val
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "support_url", {"url": val})
    await state.clear()
    text, kb = await config_view()
    await m.answer("✅ Link atualizado.", reply_markup=kb)


@router.callback_query(F.data == "sup_cfg_autoclose")
async def cfg_autoclose_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(SupportCfg.autoclose)
    await cb.message.answer("⏱️ Envie o número de dias (0 = desativar, 1-30):")
    await cb.answer()


@router.message(SupportCfg.autoclose)
async def cfg_autoclose_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        v = int(m.text.strip())
        if v < 0 or v > 30:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido (0-30).")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.support_auto_close_days = v
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "support_autoclose", {"days": v})
    await state.clear()
    text, kb = await config_view()
    await m.answer(f"✅ Auto-fechar após {v} dias.", reply_markup=kb)


@router.callback_query(F.data == "sup_cfg_welcome")
async def cfg_welcome_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(SupportCfg.welcome)
    await cb.message.answer("💬 Envie a nova mensagem de boas-vindas (5-500 caracteres):")
    await cb.answer()


@router.message(SupportCfg.welcome)
async def cfg_welcome_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if len(raw) < 5 or len(raw) > 500:
        await m.answer("❌ 5-500 caracteres.")
        return
    async with AsyncSessionLocal() as s:
        bs = await s.get(BotSettings, 1)
        bs.support_welcome_message = raw
        await s.commit()
    invalidate_cache()
    await log_admin(m.from_user.id, "support_welcome_msg", {})
    await state.clear()
    text, kb = await config_view()
    await m.answer("✅ Mensagem atualizada.", reply_markup=kb)


# ============================================================
# ESTATÍSTICAS
# ============================================================
@router.callback_query(F.data == "sup_stats")
async def stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    today = datetime.utcnow().date()
    week_start = today - timedelta(days=7)
    month_start = today.replace(day=1)

    async with AsyncSessionLocal() as s:
        total = (await s.execute(select(func.count(SupportTicket.id)))).scalar() or 0
        counts = {}
        for st in ("open", "in_progress", "waiting_user", "resolved", "closed"):
            counts[st] = (await s.execute(
                select(func.count(SupportTicket.id)).where(SupportTicket.status == st)
            )).scalar() or 0

        today_new = (await s.execute(
            select(func.count(SupportTicket.id))
            .where(SupportTicket.created_at >= datetime.combine(today, datetime.min.time()))
        )).scalar() or 0
        week_new = (await s.execute(
            select(func.count(SupportTicket.id))
            .where(SupportTicket.created_at >= datetime.combine(week_start, datetime.min.time()))
        )).scalar() or 0
        month_new = (await s.execute(
            select(func.count(SupportTicket.id))
            .where(SupportTicket.created_at >= datetime.combine(month_start, datetime.min.time()))
        )).scalar() or 0

        ratings_rows = (await s.execute(
            select(SupportTicket.rating, func.count(SupportTicket.id))
            .where(SupportTicket.rating.isnot(None))
            .group_by(SupportTicket.rating)
        )).all()
        ratings_dist = {r: c for r, c in ratings_rows}
        total_ratings = sum(ratings_dist.values())
        avg_rating = (
            sum(r * c for r, c in ratings_dist.items()) / total_ratings
            if total_ratings else 0.0
        )

        top_agents = (await s.execute(
            select(User.full_name, User.username, SupportTicket.assigned_to,
                   func.count(SupportTicket.id).label("cnt"))
            .join(User, User.id == SupportTicket.assigned_to)
            .where(SupportTicket.status.in_(["resolved", "closed"]))
            .group_by(SupportTicket.assigned_to, User.full_name, User.username)
            .order_by(desc("cnt"))
            .limit(5)
        )).all()

        top_categories = (await s.execute(
            select(SupportTicket.category, func.count(SupportTicket.id).label("cnt"))
            .where(SupportTicket.category.isnot(None))
            .group_by(SupportTicket.category)
            .order_by(desc("cnt"))
            .limit(6)
        )).all()

    resolved_rate = (counts["resolved"] + counts["closed"]) / total * 100 if total else 0.0

    text = (
        "◈ BELLAMY | ESTATÍSTICAS DE SUPORTE\n\n"
        f"📊 Total: {total}\n"
        f"🟢 Abertos: {counts['open']}\n"
        f"🟡 Em andamento: {counts['in_progress']}\n"
        f"🔵 Aguardando: {counts['waiting_user']}\n"
        f"✅ Resolvidos: {counts['resolved']}\n"
        f"⚪ Fechados: {counts['closed']}\n\n"
        f"📈 Taxa de resolução: {resolved_rate:.1f}%\n"
        f"📅 Hoje: {today_new} | 7 dias: {week_new} | Mês: {month_new}\n\n"
    )

    if total_ratings > 0:
        text += f"⭐ <b>Avaliações</b> ({total_ratings} total) — média {avg_rating:.2f}/5\n"
        for r in sorted(ratings_dist.keys(), reverse=True):
            text += f"  {'⭐' * r}: {ratings_dist[r]}\n"
        text += "\n"

    if top_agents:
        text += "🏆 TOP AGENTES (resolvidos)\n"
        for i, (name, uname, uid, cnt) in enumerate(top_agents, 1):
            display = name or (f"@{uname}" if uname else f"User {uid}")
            text += f"{i}. {display[:25]} — {cnt}\n"
        text += "\n"

    if top_categories:
        text += "📂 TOP CATEGORIAS\n"
        for cat, cnt in top_categories:
            text += f"• {cat}: {cnt}\n"

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="sup_stats")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_support")],
    ]))
    await cb.answer()


# ============================================================
# EXPORTAÇÃO
# ============================================================
@router.callback_query(F.data == "sup_export")
async def export_report(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        tickets = (await s.execute(
            select(SupportTicket).order_by(desc(SupportTicket.created_at)).limit(300)
        )).scalars().all()

        uids = list({t.user_id for t in tickets})
        users = {}
        if uids:
            users = {
                x.id: x for x in (await s.execute(
                    select(User).where(User.id.in_(uids))
                )).scalars().all()
            }

    lines = []
    lines.append("═" * 70)
    lines.append("BELLAMY | RELATÓRIO DE SUPORTE")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append(f"Tickets exportados: {len(tickets)} (últimos 300)")
    lines.append("═" * 70)
    lines.append("")

    for t in tickets:
        u = users.get(t.user_id)
        uname = u.full_name if u else "Desconhecido"
        uhandle = f"@{u.username}" if u and u.username else "—"
        s_icon, s_label = STATUS_META.get(t.status, ("•", t.status))
        p_icon, p_label = PRIORITY_META.get(t.priority, ("•", t.priority))
        lines.append(f"Ticket #{t.id:05d} — {s_label} / {p_label}")
        lines.append(f"  Cliente: {uname} ({uhandle}) — ID {t.user_id}")
        lines.append(f"  Assunto: {t.subject or '—'}")
        lines.append(f"  Categoria: {t.category or '—'}")
        lines.append(f"  Criado: {t.created_at.strftime('%d/%m/%Y %H:%M')}")
        if t.resolved_at:
            hours = (t.resolved_at - t.created_at).total_seconds() / 3600
            lines.append(f"  Resolvido: {t.resolved_at.strftime('%d/%m/%Y %H:%M')} ({hours:.1f}h)")
        if t.rating:
            lines.append(f"  Avaliação: {t.rating}/5")
        lines.append("")

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"suporte_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption=f"📊 <b>Relatório de Suporte</b>\n\nTotal: {len(tickets)} ticket(s)",
    )
    await cb.answer("Relatório gerado.")


# ============================================================
# HELPERS PARA O BOT DO CLIENTE
# ============================================================
async def create_ticket(
    user_id: int,
    subject: str,
    initial_message: str,
    category: str | None = None,
    priority: str = "normal",
) -> SupportTicket:
    """Cria um novo ticket a partir do bot do cliente."""
    async with AsyncSessionLocal() as s:
        settings = await get_settings()
        auto_assign = getattr(settings, "support_auto_assign", False)

        t = SupportTicket(
            user_id=user_id,
            subject=subject[:255],
            category=category,
            priority=priority,
            status="open",
            last_message_at=datetime.utcnow(),
        )

        if auto_assign:
            agent = (await s.execute(
                select(SupportAgent)
                .where(SupportAgent.is_active == True)
                .order_by(SupportAgent.created_at)
                .limit(1)
            )).scalar_one_or_none()
            if agent:
                t.assigned_to = agent.user_id

        s.add(t)
        await s.commit()
        await s.refresh(t)
        ticket_id = t.id

        s.add(SupportMessage(
            ticket_id=ticket_id,
            sender_type="user",
            sender_id=user_id,
            content=initial_message,
        ))
        await s.commit()

        return t


async def add_user_message(ticket_id: int, user_id: int, content: str) -> bool:
    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, ticket_id)
        if not t or t.user_id != user_id:
            return False
        if t.status == "closed":
            return False
        s.add(SupportMessage(
            ticket_id=ticket_id,
            sender_type="user",
            sender_id=user_id,
            content=content,
        ))
        t.last_message_at = datetime.utcnow()
        if t.status == "waiting_user":
            t.status = "in_progress"
        await s.commit()
        return True


async def rate_ticket(ticket_id: int, user_id: int, rating: int, comment: str | None = None) -> bool:
    if rating < 1 or rating > 5:
        return False
    async with AsyncSessionLocal() as s:
        t = await s.get(SupportTicket, ticket_id)
        if not t or t.user_id != user_id:
            return False
        t.rating = rating
        t.rating_comment = (comment or "")[:500]
        await s.commit()
        return True


async def get_user_tickets(user_id: int, limit: int = 10) -> list[SupportTicket]:
    async with AsyncSessionLocal() as s:
        stmt = (
            select(SupportTicket)
            .where(SupportTicket.user_id == user_id)
            .order_by(desc(SupportTicket.last_message_at))
            .limit(limit)
        )
        return list((await s.execute(stmt)).scalars().all())


async def get_active_faqs() -> list[FAQEntry]:
    async with AsyncSessionLocal() as s:
        stmt = (
            select(FAQEntry)
            .where(FAQEntry.is_active == True)
            .order_by(FAQEntry.display_order)
        )
        return list((await s.execute(stmt)).scalars().all())
