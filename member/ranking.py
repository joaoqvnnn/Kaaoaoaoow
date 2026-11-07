from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext

from bot import dp
from settings_cache import get_settings

from admin.ranking import get_public_ranking, CATEGORIES as _ADMIN_CATS
from admin.customization import get_rendered_message

router = Router()
dp.include_router(router)

# Categoria -> (emoji, label)
RANK_CATEGORIES = {
    "services":   ("🛠️", "Serviços"),
    "recharges":  ("💳", "Recargas"),
    "purchases":  ("🛒", "Compras"),
    "balance":    ("💎", "Saldo"),
    "depositors": ("💰", "Depositadores"),
    "referrals":  ("🤝", "Indicações"),
}


def _strip_html(s: str) -> str:
    return s.replace("<b>", "").replace("</b>", "")


def _format_ranking_list(items: list[dict]) -> str:
    if not items:
        return "<i>Nenhum dado disponível no período.</i>"

    lines = []
    for i, item in enumerate(items, 1):
        medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}."))
        label = item.get("label", "—")
        value = item.get("value", "")
        detail = item.get("detail")
        line = f"{medal} {label} — <b>{value}</b>"
        if detail:
            line += f"\n    <i>{detail}</i>"
        lines.append(line)
    return "\n".join(lines)


def _build_keyboard(selected: str) -> InlineKeyboardMarkup:
    """2 colunas, categoria selecionada em ✅, demais em ⬜, Voltar sozinho."""
    keys = list(RANK_CATEGORIES.keys())
    rows = []

    for i in range(0, len(keys), 2):
        row = []
        for k in keys[i:i + 2]:
            icon, label = RANK_CATEGORIES[k]
            check = "✅" if k == selected else "⬜"
            row.append(InlineKeyboardButton(
                text=f"{check} {icon} {label}",
                callback_data=f"mrk_cat_{k}",
            ))
        if row:
            rows.append(row)

    rows.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
# RENDER
# ============================================================
async def _render_ranking(target, category: str, edit: bool = True):
    settings = await get_settings()

    if not getattr(settings, "ranking_enabled", True):
        text = (
            "⚠️ O ranking está temporariamente indisponível.\n\n"
            "Volte mais tarde."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Menu principal", callback_data="main_menu")],
        ])
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=kb)
        else:
            await target.answer(text, reply_markup=kb)
        return

    if category not in RANK_CATEGORIES:
        category = "services"

    icon, label = RANK_CATEGORIES[category]
    items = await get_public_ranking(category)

    header_vars = {
        "tamanho_ranking": str(getattr(settings, "ranking_public_size", 10)),
        "lista_ranking": _format_ranking_list(items),
        "categoria": label,
        "icone_categoria": icon,
    }

    text, _ = await get_rendered_message("ranking", header_vars)
    if not text:
        text = (
            f"🏆 BELLAMY | RANKING\n\n"
            f"📊 Categoria: {icon} {label}\n\n"
            f"{header_vars['lista_ranking']}"
        )

    kb = _build_keyboard(category)

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
        await target.message.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


# ============================================================
# HANDLERS
# ============================================================
@router.callback_query(F.data == "show_ranking")
async def cb_show_ranking(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_ranking(cb, "services")
    await cb.answer()


@router.callback_query(F.data.startswith("mrk_cat_"))
async def cb_ranking_category(cb: CallbackQuery):
    category = cb.data.replace("mrk_cat_", "")
    if category not in RANK_CATEGORIES:
        await cb.answer("Categoria inválida.", show_alert=True)
        return
    await _render_ranking(cb, category)
    await cb.answer()
