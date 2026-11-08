from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ChatMemberUpdated,
)
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from datetime import datetime

from bot import dp, bot
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import User, Wallet, CartItem
from settings_cache import get_settings

from admin.customization import (
    get_rendered_message, get_rendered_image, get_or_create_template,
)
from settings_cache import get_settings, get_greeting
from admin.deeplinks import register_deeplink_visit
from admin.affiliates import process_referral_on_start

router = Router()
dp.include_router(router)


# ============================================================
# TRACKING DA MENSAGEM DE BLOQUEIO
# ============================================================
# Guarda o message_id da última tela de bloqueio enviada por usuário.
# Permite editar essa mensagem automaticamente quando o usuário
# entrar no canal/grupo obrigatório (evento chat_member).
_last_block_msg: dict[int, int] = {}


def _store_block_msg(user_id: int, message_id: int):
    _last_block_msg[user_id] = message_id


def _get_block_msg(user_id: int) -> int | None:
    return _last_block_msg.get(user_id)


def _clear_block_msg(user_id: int):
    _last_block_msg.pop(user_id, None)


# ============================================================
# HELPERS
# ============================================================
async def _get_or_create_user(user_id: int, full_name: str, username: str) -> tuple[User, bool]:
    async with AsyncSessionLocal() as s:
        u = await s.get(User, user_id)
        if u:
            changed = False
            if u.full_name != full_name:
                u.full_name = full_name
                changed = True
            if u.username != (username or ""):
                u.username = username or ""
                changed = True
            if changed:
                await s.commit()
            return u, False

        u = User(
            id=user_id,
            full_name=full_name,
            username=username or "",
            is_active=True,
            is_blocked=False,
        )
        s.add(u)
        s.add(Wallet(user_id=user_id, balance=0.0))
        await s.commit()
        await s.refresh(u)
        return u, True


async def _get_balance(user_id: int) -> float:
    async with AsyncSessionLocal() as s:
        w = await s.get(Wallet, user_id)
        return w.balance if w else 0.0


async def _cart_count(user_id: int) -> int:
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(func.count(CartItem.id)).where(CartItem.user_id == user_id)
        )).scalar() or 0


async def _check_single_chat(chat_id: str | None, user_id: int) -> bool:
    """Verifica se o usuário está em UM canal/grupo específico."""
    if not chat_id:
        return True
    try:
        m = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return m.status in ("creator", "administrator", "member", "restricted")
    except Exception:
        return False


async def _check_membership(user_id: int) -> tuple[bool, bool, bool]:
    """
    Retorna (channel_ok, group_ok, needs_check).
    needs_check=False quando nenhum canal/grupo obrigatório está configurado.
    """
    settings = await get_settings()
    ch_id = getattr(settings, "mandatory_channel_id", None)
    gr_id = getattr(settings, "mandatory_group_id", None)

    if not ch_id and not gr_id:
        return True, True, False

    channel_ok = await _check_single_chat(ch_id, user_id)
    group_ok = await _check_single_chat(gr_id, user_id)
    return channel_ok, group_ok, True


def _filter_button(
    kb: InlineKeyboardMarkup | None, cb_data_to_remove: str,
) -> InlineKeyboardMarkup | None:
    if not kb:
        return None
    new_rows = []
    for row in kb.inline_keyboard:
        new_row = [b for b in row if b.callback_data != cb_data_to_remove]
        if new_row:
            new_rows.append(new_row)
    return InlineKeyboardMarkup(inline_keyboard=new_rows) if new_rows else None


async def _build_welcome_message(
    user_id: int,
) -> tuple[str, InlineKeyboardMarkup | None, str | None]:
    """Retorna (texto, teclado, image_url) do menu principal."""
    await get_or_create_template("welcome")

    async with AsyncSessionLocal() as s:
        u = await s.get(User, user_id)
        w = await s.get(Wallet, user_id)

    balance = w.balance if w else 0.0
    greeting = await get_greeting()
    nome = (u.full_name if u and u.full_name else "Cliente")

    text, kb = await get_rendered_message("welcome", {
        "saudacao": greeting,   # ← agora lê do banco
        "nome_usuario": nome,
        "username": (u.username if u and u.username else "sem_username"),
        "telegram_id": str(user_id),
        "saldo": f"{balance:.2f}",
    })
    if not text:
        text = (
            f"☀️ {greeting}, {nome}!\n\n"
            f"💠 BELLAMY | DIGITAL STORE\n\n"
            f"👇 Selecione uma opção no menu."
        )

    cart_items = await _cart_count(user_id)
    if cart_items == 0:
        kb = _filter_button(kb, "show_cart")

    image_url = await get_rendered_image("welcome")
    return text, kb, image_url


async def _send_welcome(message_or_cb, edit: bool = False):
    if isinstance(message_or_cb, CallbackQuery):
        user_id = message_or_cb.from_user.id
        msg = message_or_cb.message
        is_cb = True
    else:
        user_id = message_or_cb.from_user.id
        msg = message_or_cb
        is_cb = False

    text, kb, image_url = await _build_welcome_message(user_id)

    if image_url:
        if is_cb:
            try:
                await msg.delete()
            except Exception:
                pass
        try:
            await msg.answer_photo(photo=image_url, caption=text, reply_markup=kb)
            return
        except Exception:
            pass

    if is_cb and edit:
        try:
            await msg.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass

    await msg.answer(text, reply_markup=kb)


# ============================================================
# BLOQUEIO — construção e envio
# ============================================================
async def _build_block_content() -> tuple[str, InlineKeyboardMarkup]:
    settings = await get_settings()
    ch_id = getattr(settings, "mandatory_channel_id", None)
    gr_id = getattr(settings, "mandatory_group_id", None)

    ch_link = None
    gr_link = None

    if ch_id:
        try:
            chat = await bot.get_chat(ch_id)
            ch_link = chat.invite_link or (
                f"https://t.me/{chat.username}" if chat.username else None
            )
        except Exception:
            pass

    if gr_id:
        try:
            chat = await bot.get_chat(gr_id)
            gr_link = chat.invite_link or (
                f"https://t.me/{chat.username}" if chat.username else None
            )
        except Exception:
            pass

    text = (
        "🔒 <b>ACESSO RESTRITO</b>\n\n"
        "Para utilizar nossos serviços, é necessário participar do nosso "
        "grupo e canal oficial.\n\n"
        "Após entrar em <b>ambos</b>, toque em <b>Verificar participação</b> "
        "para continuar.\n\n"
        "💡 <i>Verificamos automaticamente assim que você entrar.</i>"
    )

    buttons = []
    if ch_link:
        buttons.append([InlineKeyboardButton(text="📢 Entrar no canal", url=ch_link)])
    if gr_link:
        buttons.append([InlineKeyboardButton(text="👥 Entrar no grupo", url=gr_link)])
    buttons.append([
        InlineKeyboardButton(text="✅ Já entrei, verificar", callback_data="member_verify")
    ])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _send_block_screen(target, edit: bool = False):
    text, kb = await _build_block_content()

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
            _store_block_msg(target.from_user.id, target.message.message_id)
            return
        except Exception:
            sent = await target.message.answer(text, reply_markup=kb)
            _store_block_msg(target.from_user.id, sent.message_id)
            return

    sent = await target.answer(text, reply_markup=kb)
    _store_block_msg(target.from_user.id, sent.message_id)


async def _send_block_to_user(user_id: int):
    """Envia o bloqueio diretamente por user_id (usado quando o usuário sai do canal/grupo)."""
    text, kb = await _build_block_content()
    try:
        sent = await bot.send_message(chat_id=user_id, text=text, reply_markup=kb)
        _store_block_msg(user_id, sent.message_id)
    except Exception as e:
        print(f"[membership] erro ao enviar block para {user_id}: {e}")


async def _send_unblocked_menu(user_id: int):
    """
    Desbloqueia o usuário: edita a mensagem de bloqueio existente ou envia
    o menu principal. Chamado quando o usuário entra no canal/grupo (chat_member).
    """
    text, kb, image_url = await _build_welcome_message(user_id)
    block_msg_id = _get_block_msg(user_id)

    # Caso 1: texto → texto, edita a mensagem de bloqueio
    if block_msg_id and not image_url:
        try:
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=block_msg_id,
                text=text,
                reply_markup=kb,
            )
            _clear_block_msg(user_id)
            return
        except Exception:
            pass  # fallback abaixo

    # Caso 2: precisa recriar a mensagem (imagem ou edição falhou)
    if block_msg_id:
        try:
            await bot.delete_message(chat_id=user_id, message_id=block_msg_id)
        except Exception:
            pass

    try:
        if image_url:
            await bot.send_photo(
                chat_id=user_id, photo=image_url,
                caption=text, reply_markup=kb,
            )
        else:
            await bot.send_message(chat_id=user_id, text=text, reply_markup=kb)
    except Exception as e:
        print(f"[membership] erro ao enviar menu para {user_id}: {e}")

    _clear_block_msg(user_id)


# ============================================================
# /start — entrada principal
# ============================================================
@router.message(F.text.startswith("/start"))
async def cmd_start(message: Message, state: FSMContext):
    user = message.from_user
    await state.clear()

    db_user, is_new = await _get_or_create_user(
        user.id, user.full_name or "", user.username or ""
    )

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    cmd = parts[0].split("@")[0] if parts else ""
    if cmd != "/start":
        return

    payload = parts[1].strip() if len(parts) > 1 else ""

    if payload.startswith("dl_"):
        code = payload[3:]
        try:
            await register_deeplink_visit(code, user.id, is_new_user=is_new)
        except Exception as e:
            print(f"[member/start] deeplink erro: {e}")

    elif payload.startswith("ref_"):
        code = payload[4:]
        try:
            await process_referral_on_start(user.id, code)
        except Exception as e:
            print(f"[member/start] referral erro: {e}")

    elif payload.startswith("produto_"):
        try:
            pid = int(payload.replace("produto_", ""))
            await state.update_data(pending_product_id=pid)
        except (ValueError, TypeError):
            pass

    if user.id not in ADMIN_IDS:
        channel_ok, group_ok, needs_check = await _check_membership(user.id)
        if needs_check and not (channel_ok and group_ok):
            await _send_block_screen(message)
            return

    # Já passou — limpa qualquer tracking antigo
    _clear_block_msg(user.id)
    await _send_welcome(message, edit=False)


# ============================================================
# VERIFICAÇÃO MANUAL (botão "Já entrei, verificar")
# ============================================================
@router.callback_query(F.data == "member_verify")
async def cb_verify_membership(cb: CallbackQuery):
    user_id = cb.from_user.id

    if user_id in ADMIN_IDS:
        _clear_block_msg(user_id)
        await _send_welcome(cb, edit=True)
        await cb.answer("✅ Acesso liberado.")
        return

    channel_ok, group_ok, needs_check = await _check_membership(user_id)

    if not needs_check or (channel_ok and group_ok):
        _clear_block_msg(user_id)
        await _send_welcome(cb, edit=True)
        await cb.answer("✅ Verificado! Acesso liberado.")
        return

    missing = []
    if not channel_ok:
        missing.append("canal")
    if not group_ok:
        missing.append("grupo")

    await cb.answer(
        f"❌ Você ainda não entrou no {' e '.join(missing)}. "
        f"Entre e toque em verificar novamente.",
        show_alert=True,
    )


# ============================================================
# VERIFICAÇÃO AUTOMÁTICA (chat_member event)
# ============================================================
@router.chat_member()
async def on_chat_member_updated(event: ChatMemberUpdated):
    """
    Detecta entrada/saída do usuário no canal/grupo obrigatório
    e age automaticamente (§2.3 e §2.4).
    """
    settings = await get_settings()

    if not getattr(settings, "bot_enabled", True):
        return
    if getattr(settings, "maintenance_mode", False):
        return

    mandatory_channel = getattr(settings, "mandatory_channel_id", None)
    mandatory_group = getattr(settings, "mandatory_group_id", None)
    if not mandatory_channel and not mandatory_group:
        return

    chat_id_str = str(event.chat.id)
    is_channel = (chat_id_str == str(mandatory_channel)) if mandatory_channel else False
    is_group = (chat_id_str == str(mandatory_group)) if mandatory_group else False
    if not (is_channel or is_group):
        return

    user = event.from_user
    if not user or user.is_bot:
        return
    if user.id in ADMIN_IDS:
        return

    # Só age em usuários que já interagiram com o bot
    async with AsyncSessionLocal() as s:
        db_user = await s.get(User, user.id)
    if not db_user:
        return

    new_status = event.new_chat_member.status
    joined = ("member", "administrator", "creator", "restricted")
    left = ("left", "kicked")

    if new_status in joined:
        # Confia no evento para ESTE chat, re-checa o outro
        if is_channel:
            channel_ok = True
            group_ok = await _check_single_chat(mandatory_group, user.id) if mandatory_group else True
        else:
            group_ok = True
            channel_ok = await _check_single_chat(mandatory_channel, user.id) if mandatory_channel else True

        if channel_ok and group_ok and _get_block_msg(user.id) is not None:
            await _send_unblocked_menu(user.id)
        return

    if new_status in left:
        # Saiu de um canal/grupo obrigatório — verifica se ainda cumpre tudo
        channel_ok = await _check_single_chat(mandatory_channel, user.id) if mandatory_channel else True
        group_ok = await _check_single_chat(mandatory_group, user.id) if mandatory_group else True

        if not (channel_ok and group_ok):
            await _send_block_to_user(user.id)
        return


# ============================================================
# RETORNO AO MENU
# ============================================================
@router.callback_query(F.data.in_({"main_menu", "nav_main"}))
async def cb_main_menu(cb: CallbackQuery, state: FSMContext):
    await state.update_data(pending_product_id=None)

    if cb.from_user.id not in ADMIN_IDS:
        channel_ok, group_ok, needs_check = await _check_membership(cb.from_user.id)
        if needs_check and not (channel_ok and group_ok):
            await _send_block_screen(cb)
            await cb.answer()
            return

    await _send_welcome(cb, edit=True)
    await cb.answer()


@router.callback_query(F.data == "nav_back")
async def cb_nav_back(cb: CallbackQuery):
    await _send_welcome(cb, edit=True)
    await cb.answer()


@router.callback_query(F.data == "nav_cancel")
async def cb_nav_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _send_welcome(cb, edit=True)
    await cb.answer("Operação cancelada.")
