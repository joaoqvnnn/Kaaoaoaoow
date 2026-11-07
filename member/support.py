from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ForceReply,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime

from bot import dp
from database import AsyncSessionLocal
from models import (
    SupportTicket, SupportMessage, FAQEntry, User,
)
from settings_cache import get_settings

from admin.support import (
    create_ticket, add_user_message, rate_ticket, get_user_tickets,
    get_active_faqs,
    STATUS_META, PRIORITY_META, DEFAULT_CATEGORIES,
)

router = Router()
dp.include_router(router)


class SupportStates(StatesGroup):
    choosing_category = State()
    typing_subject = State()
    typing_message = State()
    replying_ticket = State()
    rating_ticket = State()


# ============================================================
# MENU PRINCIPAL DE SUPORTE
# ============================================================
@router.callback_query(F.data == "show_support")
async def cb_show_support(cb: CallbackQuery, state: FSMContext):
    await state.clear()

    settings = await get_settings()
    enabled = getattr(settings, "support_enabled", True)

    if not enabled:
        await cb.message.edit_text(
            "💬 O suporte está temporariamente indisponível.\n\n"
            "Tente novamente mais tarde.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
            ]),
        )
        await cb.answer()
        return

    external_url = getattr(settings, "support_url", None)

    if external_url:
        text = (
            "💬 <b>SUPORTE</b>\n\n"
            "Fale conosco diretamente pelo canal de atendimento:\n\n"
            f"👉 {external_url}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Abrir atendimento", url=external_url)],
            [InlineKeyboardButton(text="🎫 Ver meus tickets", callback_data="support_my_tickets")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="main_menu")],
        ])
        await cb.message.edit_text(text, reply_markup=kb)
        await cb.answer()
        return

    welcome = getattr(
        settings, "support_welcome_message",
        "Olá! Como podemos ajudar? Você pode abrir um ticket e nossa equipe responderá em breve.",
    )

    text = f"💬 <b>SUPORTE</b>\n\n{welcome}"

    buttons = [
        [InlineKeyboardButton(text="🎫 Abrir ticket", callback_data="support_new_ticket")],
        [InlineKeyboardButton(text="📋 Meus tickets", callback_data="support_my_tickets")],
        [InlineKeyboardButton(text="📖 Perguntas frequentes", callback_data="support_faq")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="main_menu")],
    ]
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# CRIAR TICKET (FSM)
# ============================================================
@router.callback_query(F.data == "support_new_ticket")
async def cb_new_ticket(cb: CallbackQuery, state: FSMContext):
    async with AsyncSessionLocal() as s:
        open_count = 0
        tickets = await s.execute(
            select(SupportTicket)
            .where(SupportTicket.user_id == cb.from_user.id)
            .where(SupportTicket.status.in_(["open", "in_progress", "waiting_user"]))
        )
        open_count = len(tickets.scalars().all())

    if open_count >= 3:
        await cb.answer(
            "Você já possui 3 tickets abertos. Aguarde a resposta antes de abrir outro.",
            show_alert=True,
        )
        return

    await state.set_state(SupportStates.choosing_category)

    buttons = []
    for cat in DEFAULT_CATEGORIES:
        buttons.append([InlineKeyboardButton(
            text=f"📂 {cat}", callback_data=f"support_cat_{cat[:20]}",
        )])
    buttons.append([InlineKeyboardButton(text="❌ Cancelar", callback_data="show_support")])

    await cb.message.edit_text(
        "🎫 <b>NOVO TICKET (1/3)</b>\n\n"
        "Selecione a categoria do seu atendimento:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("support_cat_"))
async def cb_choose_category(cb: CallbackQuery, state: FSMContext):
    cat = cb.data.replace("support_cat_", "")
    await state.update_data(ticket_category=cat)
    await state.set_state(SupportStates.typing_subject)
    await cb.message.answer(
        "🎫 <b>NOVO TICKET (2/3)</b>\n\n"
        "Envie um <b>assunto curto</b> (máx 100 caracteres) resumindo o problema:",
        reply_markup=ForceReply(input_field_placeholder="Assunto"),
    )
    await cb.answer()


@router.message(SupportStates.typing_subject)
async def msg_ticket_subject(m: Message, state: FSMContext):
    text = (m.text or "").strip()
    if len(text) < 3:
        await m.answer("❌ Assunto muito curto (mínimo 3 caracteres).")
        return
    if len(text) > 100:
        await m.answer("❌ Assunto muito longo (máximo 100 caracteres).")
        return

    await state.update_data(ticket_subject=text)
    await state.set_state(SupportStates.typing_message)
    await m.answer(
        "🎫 <b>NOVO TICKET (3/3)</b>\n\n"
        "Agora envie a <b>descrição completa</b> do problema.\n"
        "Inclua ID do pedido ou detalhes que possam ajudar:",
        reply_markup=ForceReply(input_field_placeholder="Descrição"),
    )


@router.message(SupportStates.typing_message)
async def msg_ticket_message(m: Message, state: FSMContext):
    text = (m.text or "").strip()
    if len(text) < 10:
        await m.answer("❌ Descrição muito curta (mínimo 10 caracteres).")
        return
    if len(text) > 3000:
        await m.answer("❌ Descrição muito longa (máximo 3000 caracteres).")
        return

    data = await state.get_data()
    category = data.get("ticket_category")
    subject = data.get("ticket_subject")
    await state.clear()

    try:
        ticket = await create_ticket(
            user_id=m.from_user.id,
            subject=subject,
            initial_message=text,
            category=category,
            priority="normal",
        )
    except Exception as e:
        await m.answer(
            f"❌ Erro ao criar ticket: {str(e)[:200]}\n\n"
            f"Tente novamente ou contate o suporte."
        )
        return

    text_success = (
        f"✅ <b>TICKET CRIADO</b>\n\n"
        f"🎫 Número: <b>#{ticket.id:05d}</b>\n"
        f"📂 Categoria: {category}\n"
        f"📌 Status: 🟢 Aberto\n"
        f"🕒 {ticket.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Nossa equipe responderá em breve. "
        f"Você receberá uma notificação quando houver resposta."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁️ Ver ticket", callback_data=f"support_view_{ticket.id}")],
        [InlineKeyboardButton(text="📋 Meus tickets", callback_data="support_my_tickets")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="main_menu")],
    ])
    await m.answer(text_success, reply_markup=kb)


# ============================================================
# MEUS TICKETS
# ============================================================
@router.callback_query(F.data == "support_my_tickets")
async def cb_my_tickets(cb: CallbackQuery):
    tickets = await get_user_tickets(cb.from_user.id, limit=20)

    if not tickets:
        text = (
            "📋 <b>MEUS TICKETS</b>\n\n"
            "Você ainda não abriu nenhum ticket.\n\n"
            "Precisa de ajuda? Abra um novo ticket!"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎫 Abrir ticket", callback_data="support_new_ticket")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="show_support")],
        ])
        await cb.message.edit_text(text, reply_markup=kb)
        await cb.answer()
        return

    lines = [f"📋 <b>MEUS TICKETS</b>\n"]
    lines.append(f"Total: {len(tickets)}\n")

    buttons = []
    for t in tickets:
        s_icon, s_label = STATUS_META.get(t.status, ("•", t.status))
        p_icon, _ = PRIORITY_META.get(t.priority, ("•", ""))
        subject = (t.subject or "Sem assunto")[:40]
        date = t.created_at.strftime("%d/%m/%Y")

        lines.append(f"{s_icon}{p_icon} <b>#{t.id:05d}</b> — {subject}")
        lines.append(f"   {s_label} | {date}")

        buttons.append([InlineKeyboardButton(
            text=f"{s_icon} #{t.id:05d} — {subject[:24]}",
            callback_data=f"support_view_{t.id}",
        )])

    buttons.append([InlineKeyboardButton(text="🎫 Abrir novo ticket", callback_data="support_new_ticket")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="show_support")])

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


# ============================================================
# VER TICKET
# ============================================================
@router.callback_query(F.data.startswith("support_view_"))
async def cb_view_ticket(cb: CallbackQuery):
    try:
        tid = int(cb.data.replace("support_view_", ""))
    except (ValueError, TypeError):
        await cb.answer("Ticket inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        ticket = await s.get(SupportTicket, tid)
        if not ticket or ticket.user_id != cb.from_user.id:
            await cb.answer("Ticket não encontrado.", show_alert=True)
            return

        msgs = (await s.execute(
            select(SupportMessage)
            .where(SupportMessage.ticket_id == tid)
            .where(SupportMessage.is_internal_note == False)
            .order_by(SupportMessage.created_at)
            .limit(30)
        )).scalars().all()

    s_icon, s_label = STATUS_META.get(ticket.status, ("•", ticket.status))
    p_icon, p_label = PRIORITY_META.get(ticket.priority, ("•", ticket.priority))

    lines = [
        f"🎫 <b>TICKET #{ticket.id:05d}</b>\n",
        f"📌 Status: {s_icon} {s_label}",
        f"🚩 Prioridade: {p_icon} {p_label}",
    ]
    if ticket.category:
        lines.append(f"📂 Categoria: {ticket.category}")
    lines.append(f"📅 Criado: {ticket.created_at.strftime('%d/%m/%Y %H:%M')}")
    if ticket.resolved_at:
        lines.append(f"✅ Resolvido: {ticket.resolved_at.strftime('%d/%m/%Y %H:%M')}")

    if ticket.rating:
        stars = "⭐" * ticket.rating
        lines.append(f"\n⭐ Avaliação: {stars}")

    lines.append("\n━━━━━━━━━━━━━━\n")

    for m in msgs:
        ts = m.created_at.strftime("%d/%m %H:%M")
        if m.sender_type == "user":
            sender = "👤 Você"
        elif m.sender_type == "agent":
            sender = "👨‍💼 Suporte"
        elif m.sender_type == "system":
            sender = "🤖 Sistema"
        else:
            sender = m.sender_type

        content = (m.content or "[mídia]")[:500]
        lines.append(f"<b>{sender}</b> — {ts}\n{content}\n")

    buttons = []
    if ticket.status in ("open", "in_progress", "waiting_user"):
        buttons.append([InlineKeyboardButton(
            text="💬 Responder",
            callback_data=f"support_reply_{tid}",
        )])
    elif ticket.status in ("resolved", "closed") and not ticket.rating:
        buttons.append([InlineKeyboardButton(
            text="⭐ Avaliar atendimento",
            callback_data=f"support_rate_{tid}",
        )])

    buttons.append([InlineKeyboardButton(text="↩️ Meus tickets", callback_data="support_my_tickets")])

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


# ============================================================
# RESPONDER TICKET
# ============================================================
@router.callback_query(F.data.startswith("support_reply_"))
async def cb_reply_ticket(cb: CallbackQuery, state: FSMContext):
    try:
        tid = int(cb.data.replace("support_reply_", ""))
    except (ValueError, TypeError):
        await cb.answer("Ticket inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        ticket = await s.get(SupportTicket, tid)
        if not ticket or ticket.user_id != cb.from_user.id:
            await cb.answer("Ticket não encontrado.", show_alert=True)
            return

    await state.update_data(ticket_id=tid)
    await state.set_state(SupportStates.replying_ticket)
    await cb.message.answer(
        f"💬 <b>RESPONDER TICKET #{tid:05d}</b>\n\n"
        f"Envie sua mensagem (ou /cancelar para sair):",
        reply_markup=ForceReply(input_field_placeholder="Mensagem"),
    )
    await cb.answer()


@router.message(SupportStates.replying_ticket)
async def msg_reply_ticket(m: Message, state: FSMContext):
    text = (m.text or "").strip()

    if text.lower() in ("/cancelar", "cancelar"):
        await state.clear()
        await m.answer("Operação cancelada.")
        return

    if len(text) < 1:
        await m.answer("❌ Envie uma mensagem.")
        return
    if len(text) > 3000:
        await m.answer("❌ Mensagem muito longa (máx 3000 caracteres).")
        return

    data = await state.get_data()
    tid = data.get("ticket_id")
    await state.clear()

    if not tid:
        await m.answer("❌ Sessão expirada.")
        return

    ok = await add_user_message(tid, m.from_user.id, text)
    if not ok:
        await m.answer("❌ Não foi possível responder (ticket fechado ou inválido).")
        return

    await m.answer(
        f"✅ Mensagem enviada.\n\n"
        f"Nossa equipe responderá em breve.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁️ Ver ticket", callback_data=f"support_view_{tid}")],
            [InlineKeyboardButton(text="📋 Meus tickets", callback_data="support_my_tickets")],
        ]),
    )

    # Notifica admin? Se auto_assign estiver configurado, o agente
    # recebe via painel. Uma notificação por DM pode ser adicionada
    # em um patch futuro do admin/support.py.


# ============================================================
# AVALIAR TICKET
# ============================================================
@router.callback_query(F.data.startswith("support_rate_"))
async def cb_rate_ticket(cb: CallbackQuery, state: FSMContext):
    try:
        tid = int(cb.data.replace("support_rate_", ""))
    except (ValueError, TypeError):
        await cb.answer("Ticket inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        ticket = await s.get(SupportTicket, tid)
        if not ticket or ticket.user_id != cb.from_user.id:
            await cb.answer("Ticket não encontrado.", show_alert=True)
            return
        if ticket.rating:
            await cb.answer("Este ticket já foi avaliado.", show_alert=True)
            return
        if ticket.status not in ("resolved", "closed"):
            await cb.answer("Só é possível avaliar tickets finalizados.", show_alert=True)
            return

    await state.update_data(rating_ticket_id=tid)
    await state.set_state(SupportStates.rating_ticket)

    buttons = []
    for star in range(5, 0, -1):
        stars = "⭐" * star
        buttons.append([InlineKeyboardButton(
            text=f"{stars} — {star} estrela{'s' if star > 1 else ''}",
            callback_data=f"support_ratepick_{tid}_{star}",
        )])
    buttons.append([InlineKeyboardButton(text="❌ Cancelar", callback_data=f"support_view_{tid}")])

    await cb.message.edit_text(
        f"⭐ <b>AVALIAR ATENDIMENTO</b>\n\n"
        f"Ticket #{tid:05d}\n\n"
        f"Como foi sua experiência com nosso suporte?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("support_ratepick_"))
async def cb_rate_pick(cb: CallbackQuery, state: FSMContext):
    rest = cb.data.replace("support_ratepick_", "")
    try:
        tid_s, star_s = rest.rsplit("_", 1)
        tid = int(tid_s)
        star = int(star_s)
    except (ValueError, IndexError):
        await cb.answer("Parâmetros inválidos.", show_alert=True)
        return

    if star < 1 or star > 5:
        await cb.answer("Nota inválida.", show_alert=True)
        return

    ok = await rate_ticket(tid, cb.from_user.id, star)
    await state.clear()

    if not ok:
        await cb.answer("Não foi possível avaliar.", show_alert=True)
        return

    stars = "⭐" * star
    await cb.message.edit_text(
        f"✅ <b>OBRIGADO PELA AVALIAÇÃO!</b>\n\n"
        f"Sua nota: {stars} ({star}/5)\n\n"
        f"Sua opinião nos ajuda a melhorar cada vez mais.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
        ]),
    )
    await cb.answer("Avaliação registrada!")


# ============================================================
# FAQ
# ============================================================
@router.callback_query(F.data == "support_faq")
async def cb_support_faq(cb: CallbackQuery):
    faqs = await get_active_faqs()

    if not faqs:
        text = (
            "📖 <b>PERGUNTAS FREQUENTES</b>\n\n"
            "Ainda não temos perguntas cadastradas.\n\n"
            "Se precisar de ajuda, abra um ticket."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎫 Abrir ticket", callback_data="support_new_ticket")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="show_support")],
        ])
        await cb.message.edit_text(text, reply_markup=kb)
        await cb.answer()
        return

    text = (
        f"📖 <b>PERGUNTAS FREQUENTES</b>\n\n"
        f"Selecione uma pergunta para ver a resposta:"
    )
    buttons = []
    for f in faqs[:15]:
        buttons.append([InlineKeyboardButton(
            text=f"❓ {f.question[:50]}",
            callback_data=f"support_faq_view_{f.id}",
        )])
    buttons.append([InlineKeyboardButton(text="🎫 Abrir ticket", callback_data="support_new_ticket")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="show_support")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("support_faq_view_"))
async def cb_faq_view(cb: CallbackQuery):
    try:
        fid = int(cb.data.replace("support_faq_view_", ""))
    except (ValueError, TypeError):
        await cb.answer("FAQ inválida.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        f = await s.get(FAQEntry, fid)
        if not f or not f.is_active:
            await cb.answer("FAQ não encontrada.", show_alert=True)
            return
        f.views = (f.views or 0) + 1
        await s.commit()

    text = (
        f"📖 <b>{f.question}</b>\n\n"
        f"{f.answer}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎫 Abrir ticket", callback_data="support_new_ticket")],
        [InlineKeyboardButton(text="↩️ Voltar às FAQs", callback_data="support_faq")],
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()
