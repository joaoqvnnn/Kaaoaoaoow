from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc, and_
from datetime import datetime
import re

from bot import dp, bot
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import (
    MessageTemplate, MessageTemplateHistory,
    ButtonConfig, ButtonConfigHistory,
    AdminLog, BotSettings,
)
from settings_cache import get_settings, invalidate_cache

router = Router()
dp.include_router(router)

VAR_REGEX = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def log_admin(admin_id: int, action: str, details: dict | None = None):
    async with AsyncSessionLocal() as s:
        s.add(AdminLog(admin_id=admin_id, action=action, details=details))
        await s.commit()


class MES(StatesGroup):
    edit_title = State()
    edit_content = State()
    edit_image = State()
    edit_extra = State()


class BTS(StatesGroup):
    add_label = State()
    add_emoji = State()
    add_action_type = State()
    add_action_value = State()
    add_row = State()
    add_col = State()
    edit_field = State()


CATEGORIES = {
    "main":     ("🏠", "Principal"),
    "shop":     ("🛒", "Compras"),
    "finance":  ("💰", "Financeiro"),
    "user":     ("👤", "Usuário"),
    "social":   ("🏆", "Social"),
    "channel":  ("📢", "Canal"),
    "system":   ("⚠️", "Sistema"),
    "reminder": ("🔔", "Lembretes"),
}

ACTION_TYPES = {
    "callback": ("🎯 Ação interna", "callback_data do bot"),
    "url":      ("🔗 Link externo", "URL http(s)"),
    "deeplink": ("🎣 Deep Link", "https://t.me/bot?start=..."),
    "miniapp":  ("📱 Mini App", "URL web_app"),
    "nav":      ("↩️ Navegação", "back | main_menu | cancel"),
}


# ============================================================
# DEFAULTS
# ============================================================
DEFAULT_SCREENS = {
    "welcome": {
        "category": "main",
        "title": "Boas-vindas / Menu principal",
        "content": (
            "☀️ {saudacao}, {nome_usuario}!\n\n"
            "💠 BELLAMY | DIGITAL STORE\n\n"
            "Tecnologia, praticidade e agilidade em uma única plataforma.\n\n"
            "⚡ Sistema automatizado\n"
            "💎 Saldo atualizado em tempo real\n"
            "🛒 Catálogo inteligente\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "👤 SEU PERFIL\n\n"
            "➤ Usuário: {username}\n"
            "➤ ID: {telegram_id}\n"
            "➤ Saldo: R$ {saldo}\n\n"
            "👇 Selecione uma opção no menu."
        ),
        "image_url": None,
        "variables": ["saudacao", "nome_usuario", "username", "telegram_id", "saldo"],
        "buttons": [
            {"key": "catalog", "text": "🛍️ Logins | Contas Premium", "action_type": "callback", "action_value": "show_catalog", "row": 0, "col": 0},
            {"key": "profile", "text": "👤 Perfil", "action_type": "callback", "action_value": "show_profile", "row": 0, "col": 1},
            {"key": "add_balance", "text": "💎 Adicionar saldo", "action_type": "callback", "action_value": "show_recharge", "row": 1, "col": 0},
            {"key": "support", "text": "💬 Suporte", "action_type": "callback", "action_value": "show_support", "row": 1, "col": 1},
            {"key": "ranking", "text": "🏆 Ranking", "action_type": "callback", "action_value": "show_ranking", "row": 2, "col": 0},
            {"key": "affiliates", "text": "🤝 Afiliados", "action_type": "callback", "action_value": "show_affiliates", "row": 2, "col": 1},
            {"key": "cart", "text": "🧺 Carrinho", "action_type": "callback", "action_value": "show_cart", "row": 3, "col": 0},
        ],
    },
    "catalog": {
        "category": "shop",
        "title": "Catálogo de produtos",
        "content": (
            "◈ BELLAMY | CONTAS\n\n"
            "Olá, {nome_usuario}!\n\n"
            "💎 Saldo: R$ {saldo}\n\n"
            "⌘ Selecione um produto disponível abaixo."
        ),
        "image_url": None,
        "variables": ["nome_usuario", "saldo"],
        "buttons": [
            {"key": "back", "text": "↩️ Voltar", "action_type": "nav", "action_value": "main_menu", "row": 99, "col": 0},
        ],
    },
    "product_detail": {
        "category": "shop",
        "title": "Detalhes do produto",
        "content": (
            "◈ BELLAMY | PRODUTO\n\n"
            "▸ Produto: {nome_produto}\n"
            "▸ Preço: R$ {preco}\n"
            "▸ Seu saldo: R$ {saldo}\n"
            "▸ Disponibilidade: {estoque}\n\n"
            "⌁ {descricao_produto}\n\n"
            "◈ {quantidade_visualizando} pessoas visualizando este produto."
        ),
        "image_url": None,
        "variables": ["nome_produto", "preco", "saldo", "estoque", "descricao_produto", "quantidade_visualizando"],
        "buttons": [
            {"key": "buy_now", "text": "🛒 Comprar agora", "action_type": "callback", "action_value": "buy_now", "row": 0, "col": 0},
            {"key": "buy_qty", "text": "🔢 Comprar quantidade", "action_type": "callback", "action_value": "buy_qty", "row": 0, "col": 1},
            {"key": "add_cart", "text": "🧺 Adicionar ao carrinho", "action_type": "callback", "action_value": "add_cart", "row": 1, "col": 0},
            {"key": "add_balance", "text": "💎 Adicionar saldo", "action_type": "callback", "action_value": "show_recharge", "row": 1, "col": 1},
            {"key": "back", "text": "↩️ Voltar", "action_type": "callback", "action_value": "show_catalog", "row": 2, "col": 0},
        ],
    },
    "purchase_confirm": {
        "category": "shop",
        "title": "Confirmação de compra",
        "content": (
            "🛍️ CONFIRMAÇÃO DA COMPRA\n\n"
            "Seu pedido está pronto para confirmação!\n\n"
            "◈ Produto: {nome_produto}\n"
            "◈ Quantidade: {quantidade}\n"
            "◈ Valor unitário: R$ {preco}\n"
            "◈ Total: R$ {valor_total}\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "💎 Saldo atual: R$ {saldo_atual}\n"
            "💰 Saldo após a compra: R$ {saldo_final}\n\n"
            "Deseja confirmar seu pedido?"
        ),
        "image_url": None,
        "variables": ["nome_produto", "quantidade", "preco", "valor_total", "saldo_atual", "saldo_final"],
        "buttons": [
            {"key": "confirm", "text": "✅ Confirmar compra", "action_type": "callback", "action_value": "confirm_purchase", "row": 0, "col": 0},
            {"key": "cancel", "text": "↩️ Cancelar", "action_type": "callback", "action_value": "cancel_purchase", "row": 0, "col": 1},
        ],
    },
    "purchase_done": {
        "category": "shop",
        "title": "Compra concluída",
        "content": (
            "✅ COMPRA CONCLUÍDA!\n\n"
            "Seu pedido foi confirmado e seus dados de acesso estão disponíveis.\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "📦 Produto: {nome_produto}\n"
            "🆔 Pedido: #{pedido_id}\n"
            "📅 Data: {data_compra}\n\n"
            "🔐 DADOS DE ACESSO\n\n"
            "📧 E-mail: {email_borrado}\n"
            "🔑 Senha: {senha_borrada}"
        ),
        "image_url": None,
        "variables": ["nome_produto", "pedido_id", "data_compra", "email_borrado", "senha_borrada"],
        "buttons": [
            {"key": "show_creds", "text": "🔓 Ativar visualização", "action_type": "callback", "action_value": "toggle_creds", "row": 0, "col": 0},
            {"key": "activate", "text": "🌐 Ativar diretamente", "action_type": "callback", "action_value": "activate_link", "row": 0, "col": 1},
            {"key": "main", "text": "🏠 Menu principal", "action_type": "nav", "action_value": "main_menu", "row": 1, "col": 0},
        ],
    },
    "insufficient_balance": {
        "category": "shop",
        "title": "Saldo insuficiente",
        "content": (
            "⚠️ SALDO INSUFICIENTE\n\n"
            "Não foi possível continuar com sua compra.\n\n"
            "💰 Valor que falta: R$ {valor_faltante}\n\n"
            "Adicione saldo à sua carteira e tente novamente."
        ),
        "image_url": None,
        "variables": ["valor_faltante"],
        "buttons": [
            {"key": "add_balance", "text": "💎 Adicionar saldo", "action_type": "callback", "action_value": "show_recharge", "row": 0, "col": 0},
            {"key": "back", "text": "↩️ Voltar", "action_type": "nav", "action_value": "back", "row": 0, "col": 1},
        ],
    },
    "cart": {
        "category": "shop",
        "title": "Meu carrinho",
        "content": (
            "◈ BELLAMY | MEU CARRINHO\n\n"
            "👤 {nome_usuario} ({telegram_id})\n\n"
            "{lista_produtos}\n\n"
            "📦 Quantidade de itens: {total_itens}\n"
            "💰 Total: R$ {valor_total}\n"
            "💎 Saldo: R$ {saldo}"
        ),
        "image_url": None,
        "variables": ["nome_usuario", "telegram_id", "lista_produtos", "total_itens", "valor_total", "saldo"],
        "buttons": [
            {"key": "buy", "text": "🛒 Comprar", "action_type": "callback", "action_value": "cart_buy", "row": 0, "col": 0},
            {"key": "clear", "text": "🗑️ Limpar carrinho", "action_type": "callback", "action_value": "cart_clear", "row": 0, "col": 1},
            {"key": "back", "text": "↩️ Cancelar e voltar", "action_type": "nav", "action_value": "main_menu", "row": 1, "col": 0},
        ],
    },
    "recharge_menu": {
        "category": "finance",
        "title": "Menu de recarga",
        "content": (
            "💳 Selecione a opção desejada para adicionar saldo via PIX!\n\n"
            "🔹 Adicione crédito de forma rápida e segura com PIX.\n"
            "💸 Escolha o valor e faça o pagamento."
        ),
        "image_url": None,
        "variables": [],
        "buttons": [
            {"key": "pix", "text": "💠 PIX automático", "action_type": "callback", "action_value": "start_pix", "row": 0, "col": 0},
            {"key": "back", "text": "↩️ Voltar", "action_type": "nav", "action_value": "back", "row": 0, "col": 1},
        ],
    },
    "pix_charge": {
        "category": "finance",
        "title": "Cobrança PIX",
        "content": (
            "💰 COMPRAR SALDO COM PIX AUTOMÁTICO\n\n"
            "⏱ Expira em: {tempo_expiracao}\n"
            "💰 Valor: R$ {valor}\n"
            "✨ ID da compra: {transacao_id}\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "💎 PIX COPIA E COLA\n\n"
            "{codigo_pix}\n\n"
            "🇧🇷 Após a confirmação do pagamento, seu saldo será atualizado automaticamente."
        ),
        "image_url": None,
        "variables": ["tempo_expiracao", "valor", "transacao_id", "codigo_pix"],
        "buttons": [
            {"key": "check", "text": "⏳ Aguardando pagamento", "action_type": "callback", "action_value": "check_payment", "row": 0, "col": 0},
            {"key": "copy", "text": "📋 Copiar PIX", "action_type": "callback", "action_value": "copy_pix", "row": 0, "col": 1},
            {"key": "cancel", "text": "↩️ Cancelar recarga", "action_type": "callback", "action_value": "cancel_recharge", "row": 1, "col": 0},
        ],
    },
    "recharge_confirmed": {
        "category": "finance",
        "title": "Recarga confirmada",
        "content": (
            "✅ RECARGA CONFIRMADA!\n\n"
            "Seu pagamento foi identificado.\n\n"
            "💎 Valor recebido: R$ {valor_creditado}\n"
            "🎁 Bônus: R$ {bonus_creditado}\n"
            "💰 Novo saldo: R$ {novo_saldo}"
        ),
        "image_url": None,
        "variables": ["valor_creditado", "bonus_creditado", "novo_saldo"],
        "buttons": [
            {"key": "main", "text": "🏠 Menu principal", "action_type": "nav", "action_value": "main_menu", "row": 0, "col": 0},
        ],
    },
    "profile": {
        "category": "user",
        "title": "Perfil do usuário",
        "content": (
            "◈ BELLAMY | MEU PERFIL\n\n"
            "👤 {nome_usuario}\n"
            "💠 @{username}\n"
            "🆔 {telegram_id}\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "💎 CARTEIRA\n\n"
            "Saldo disponível: R$ {saldo}\n\n"
            "🛒 ATIVIDADES\n\n"
            "Compras realizadas: {total_compras}\n"
            "Total em recargas: R$ {total_recargas}\n"
            "Gift Cards resgatados: R$ {total_gifts}\n\n"
            "⌁ INDICAÇÕES\n\n"
            "Pontos: {pontos_indicacao}\n"
            "Indicados: {total_indicados}\n\n"
            "🔗 Seu link:\n\n"
            "{link_indicacao}"
        ),
        "image_url": None,
        "variables": [
            "nome_usuario", "username", "telegram_id", "saldo",
            "total_compras", "total_recargas", "total_gifts",
            "pontos_indicacao", "total_indicados", "link_indicacao",
        ],
        "buttons": [
            {"key": "history", "text": "📄 Histórico de movimentações", "action_type": "callback", "action_value": "show_history", "row": 0, "col": 0},
            {"key": "subs", "text": "📺 Minhas assinaturas", "action_type": "callback", "action_value": "show_subscriptions", "row": 0, "col": 1},
            {"key": "back", "text": "↩️ Voltar", "action_type": "nav", "action_value": "main_menu", "row": 1, "col": 0},
        ],
    },
    "history": {
        "category": "user",
        "title": "Histórico de movimentações",
        "content": (
            "◈ BELLAMY | HISTÓRICO\n\n"
            "Confira suas movimentações.\n\n"
            "💰 Compras: {total_compras}\n"
            "💎 Recargas: R$ {total_recargas}\n"
            "🎁 Gift Cards: R$ {total_gifts}\n"
            "💸 Saques: R$ {total_saques}"
        ),
        "image_url": None,
        "variables": ["total_compras", "total_recargas", "total_gifts", "total_saques"],
        "buttons": [
            {"key": "pdf", "text": "📄 Gerar histórico em PDF", "action_type": "callback", "action_value": "generate_history_pdf", "row": 0, "col": 0},
            {"key": "back", "text": "↩️ Voltar ao perfil", "action_type": "callback", "action_value": "show_profile", "row": 1, "col": 0},
        ],
    },
    "subscriptions": {
        "category": "user",
        "title": "Minhas assinaturas",
        "content": (
            "◈ BELLAMY | MINHAS ASSINATURAS\n\n"
            "📦 Assinaturas cadastradas: {total_assinaturas}\n"
            "🟢 Ativas: {assinaturas_ativas}\n"
            "⚪ Finalizadas: {assinaturas_finalizadas}"
        ),
        "image_url": None,
        "variables": ["total_assinaturas", "assinaturas_ativas", "assinaturas_finalizadas"],
        "buttons": [
            {"key": "back", "text": "↩️ Voltar ao perfil", "action_type": "callback", "action_value": "show_profile", "row": 99, "col": 0},
        ],
    },
    "ranking": {
        "category": "social",
        "title": "Ranking",
        "content": (
            "🏆 BELLAMY | RANKING\n\n"
            "Confira o TOP {tamanho_ranking} da categoria selecionada.\n\n"
            "{lista_ranking}"
        ),
        "image_url": None,
        "variables": ["tamanho_ranking", "lista_ranking"],
        "buttons": [
            {"key": "cat_services", "text": "🛠️ Serviços", "action_type": "callback", "action_value": "rank_services", "row": 0, "col": 0},
            {"key": "cat_recharges", "text": "💳 Recargas", "action_type": "callback", "action_value": "rank_recharges", "row": 0, "col": 1},
            {"key": "cat_purchases", "text": "🛒 Compras", "action_type": "callback", "action_value": "rank_purchases", "row": 1, "col": 0},
            {"key": "cat_balance", "text": "💎 Saldo", "action_type": "callback", "action_value": "rank_balance", "row": 1, "col": 1},
            {"key": "cat_depositors", "text": "💰 Depositadores", "action_type": "callback", "action_value": "rank_depositors", "row": 2, "col": 0},
            {"key": "cat_referrals", "text": "🤝 Indicações", "action_type": "callback", "action_value": "rank_referrals", "row": 2, "col": 1},
            {"key": "back", "text": "↩️ Voltar", "action_type": "nav", "action_value": "main_menu", "row": 3, "col": 0},
        ],
    },
    "affiliates": {
        "category": "social",
        "title": "Afiliados",
        "content": (
            "◈ BELLAMY | AFILIADOS\n\n"
            "Convide pessoas e acumule recompensas através das suas indicações.\n\n"
            "🏆 Pontos acumulados: {pontos}\n"
            "💎 Saldo disponível para resgate: R$ {valor_resgate}\n"
            "👥 Pessoas indicadas: {total_indicados}\n"
            "✅ Indicações confirmadas: {indicacoes_confirmadas}\n\n"
            "🔗 Seu link exclusivo:\n\n"
            "{link_indicacao}"
        ),
        "image_url": None,
        "variables": ["pontos", "valor_resgate", "total_indicados", "indicacoes_confirmadas", "link_indicacao"],
        "buttons": [
            {"key": "convert", "text": "💎 Trocar pontos por saldo", "action_type": "callback", "action_value": "convert_points", "row": 0, "col": 0},
            {"key": "my_refs", "text": "👥 Meus indicados", "action_type": "callback", "action_value": "show_my_refs", "row": 0, "col": 1},
            {"key": "performance", "text": "📊 Meu desempenho", "action_type": "callback", "action_value": "show_performance", "row": 1, "col": 0},
            {"key": "rewards", "text": "📜 Histórico de recompensas", "action_type": "callback", "action_value": "show_rewards", "row": 1, "col": 1},
            {"key": "how", "text": "📖 Como funciona", "action_type": "callback", "action_value": "show_how_it_works", "row": 2, "col": 0},
            {"key": "back", "text": "↩️ Voltar", "action_type": "nav", "action_value": "main_menu", "row": 3, "col": 0},
        ],
    },
    "reminder": {
        "category": "reminder",
        "title": "Lembrete de produto",
        "content": (
            "👀 AINDA INTERESSADO?\n\n"
            "Você estava conferindo um dos nossos produtos.\n\n"
            "📦 Produto: {nome_produto}\n"
            "💰 Valor: R$ {preco}\n\n"
            "Ele ainda está disponível no catálogo."
        ),
        "image_url": None,
        "variables": ["nome_produto", "preco"],
        "buttons": [
            {"key": "back_product", "text": "🛍️ Voltar ao produto", "action_type": "callback", "action_value": "open_product", "row": 0, "col": 0},
            {"key": "main", "text": "🏠 Menu principal", "action_type": "nav", "action_value": "main_menu", "row": 0, "col": 1},
        ],
    },
    "purchase_announcement": {
        "category": "channel",
        "title": "Anúncio de compra (canal)",
        "content": (
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
        ),
        "image_url": None,
        "variables": ["nome_publico", "id_mascarado", "nome_produto", "quantidade", "valor_total", "horario_compra"],
        "buttons": [
            {"key": "go_bot", "text": "🛒 IR PARA A BELLAMY", "action_type": "deeplink", "action_value": "auto", "row": 0, "col": 0},
        ],
    },
    "restock_announcement": {
        "category": "channel",
        "title": "Anúncio de reposição (canal)",
        "content": (
            "📦 | PRODUTO REABASTECIDO\n\n"
            "◈ {nome_produto}\n\n"
            "O estoque foi atualizado!\n\n"
            "📦 Unidades disponíveis: {estoque_atual}\n"
            "💰 Valor: R$ {preco}\n"
            "🕒 Atualizado às {horario}"
        ),
        "image_url": None,
        "variables": ["nome_produto", "estoque_atual", "preco", "horario"],
        "buttons": [
            {"key": "open", "text": "🛍️ Acessar produto", "action_type": "deeplink", "action_value": "auto", "row": 0, "col": 0},
        ],
    },
    "maintenance": {
        "category": "system",
        "title": "Modo manutenção",
        "content": (
            "🛠️ BELLAMY EM MANUTENÇÃO\n\n"
            "Estamos realizando melhorias em nossa plataforma.\n\n"
            "Voltaremos em breve.\n\n"
            "Agradecemos sua compreensão."
        ),
        "image_url": None,
        "variables": [],
        "buttons": [],
    },
    "blocked": {
        "category": "system",
        "title": "Usuário bloqueado",
        "content": (
            "🚫 SUA CONTA ESTÁ BLOQUEADA.\n\n"
            "Motivo: {motivo}\n\n"
            "Se acredita que isso é um engano, entre em contato com o suporte."
        ),
        "image_url": None,
        "variables": ["motivo"],
        "buttons": [],
    },
}


# ============================================================
# HELPERS
# ============================================================
def extract_variables(text: str) -> set[str]:
    return set(VAR_REGEX.findall(text))


async def get_or_create_template(screen_key: str) -> MessageTemplate:
    async with AsyncSessionLocal() as s:
        tpl = (await s.execute(
            select(MessageTemplate).where(MessageTemplate.screen_key == screen_key)
        )).scalar_one_or_none()
        if tpl:
            return tpl

        default = DEFAULT_SCREENS.get(screen_key)
        if not default:
            raise ValueError(f"Unknown screen: {screen_key}")

        tpl = MessageTemplate(
            screen_key=screen_key,
            title=default["title"],
            content=default["content"],
            image_url=default.get("image_url"),
            category=default["category"],
            variables=default["variables"],
            is_active=True,
        )
        s.add(tpl)
        await s.commit()
        await s.refresh(tpl)
        return tpl


async def get_or_create_buttons(screen_key: str) -> list[ButtonConfig]:
    async with AsyncSessionLocal() as s:
        existing = (await s.execute(
            select(ButtonConfig)
            .where(ButtonConfig.screen_key == screen_key)
            .order_by(ButtonConfig.row_order, ButtonConfig.col_order)
        )).scalars().all()
        if existing:
            return list(existing)

        default = DEFAULT_SCREENS.get(screen_key)
        if not default:
            return []

        new_buttons = []
        for b in default.get("buttons", []):
            btn = ButtonConfig(
                screen_key=screen_key,
                button_key=b["key"],
                label=b["text"],
                emoji=None,
                action_type=b["action_type"],
                action_value=b.get("action_value"),
                row_order=b["row"],
                col_order=b["col"],
                is_visible=True,
                admin_only=False,
                requires_confirmation=False,
            )
            s.add(btn)
            new_buttons.append(btn)
        await s.commit()

        return list((await s.execute(
            select(ButtonConfig)
            .where(ButtonConfig.screen_key == screen_key)
            .order_by(ButtonConfig.row_order, ButtonConfig.col_order)
        )).scalars().all())


async def ensure_all_defaults():
    for key in DEFAULT_SCREENS:
        await get_or_create_template(key)
        await get_or_create_buttons(key)


def validate_variables(content: str, allowed: list[str]) -> tuple[bool, list[str]]:
    used = extract_variables(content)
    allowed_set = set(allowed)
    invalid = [v for v in used if v not in allowed_set]
    return (len(invalid) == 0, invalid)


def build_buttons_keyboard(buttons: list[ButtonConfig], bot_link: str = "") -> InlineKeyboardMarkup | None:
    if not buttons:
        return None

    visible = [b for b in buttons if b.is_visible]
    if not visible:
        return None

    rows: dict[int, list[ButtonConfig]] = {}
    for b in visible:
        rows.setdefault(b.row_order, []).append(b)

    keyboard = []
    for row_idx in sorted(rows.keys()):
        row = sorted(rows[row_idx], key=lambda x: x.col_order)
        tg_row = []
        for b in row:
            label = f"{b.emoji} {b.label}".strip() if b.emoji else b.label
            if b.action_type == "callback":
                tg_row.append(InlineKeyboardButton(text=label, callback_data=b.action_value or "noop"))
            elif b.action_type == "url":
                tg_row.append(InlineKeyboardButton(text=label, url=b.action_value or "https://t.me"))
            elif b.action_type == "deeplink":
                url = bot_link if (b.action_value or "").lower() == "auto" else (b.action_value or bot_link)
                tg_row.append(InlineKeyboardButton(text=label, url=url or "https://t.me"))
            elif b.action_type == "miniapp":
                tg_row.append(InlineKeyboardButton(text=label, url=b.action_value or "https://t.me"))
            elif b.action_type == "nav":
                cb = {"back": "nav_back", "main_menu": "nav_main", "cancel": "nav_cancel"}.get(
                    (b.action_value or "").lower(), "nav_back"
                )
                tg_row.append(InlineKeyboardButton(text=label, callback_data=cb))
        if tg_row:
            keyboard.append(tg_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None


async def _render_preview(screen_key: str, admin_id: int):
    tpl = await get_or_create_template(screen_key)
    buttons = await get_or_create_buttons(screen_key)

    sample = {
        "saudacao": "Bom dia", "nome_usuario": "João", "username": "joao_test",
        "telegram_id": str(admin_id), "saldo": "150.00",
        "nome_produto": "Produto Exemplo", "preco": "29.90", "estoque": "25",
        "descricao_produto": "Descrição do produto exemplo.", "quantidade_visualizando": "3",
        "quantidade": "1", "valor_total": "29.90", "saldo_atual": "150.00", "saldo_final": "120.10",
        "pedido_id": "999", "data_compra": datetime.utcnow().strftime("%d/%m/%Y"),
        "email_borrado": "•••••••••", "senha_borrada": "••••••••••",
        "valor_faltante": "50.00", "lista_produtos": "• Produto 1 x1\n• Produto 2 x2",
        "total_itens": "3", "tempo_expiracao": "30 minutos",
        "valor": "100.00", "transacao_id": "123456", "codigo_pix": "00020126...",
        "valor_creditado": "100.00", "bonus_creditado": "5.00", "novo_saldo": "255.00",
        "total_compras": "5", "total_recargas": "300.00", "total_gifts": "0.00",
        "total_saques": "0.00", "pontos_indicacao": "50", "total_indicados": "3",
        "link_indicacao": "https://t.me/bot?start=ref_ABC123",
        "total_assinaturas": "2", "assinaturas_ativas": "1", "assinaturas_finalizadas": "1",
        "tamanho_ranking": "10", "lista_ranking": "🥇 User 1 — 100\n🥈 User 2 — 50",
        "pontos": "100", "valor_resgate": "10.00", "indicacoes_confirmadas": "2",
        "nome_publico": "João S.", "id_mascarado": "***6789",
        "horario_compra": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        "estoque_atual": "25", "horario": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        "motivo": "Violação dos termos",
    }

    try:
        rendered = tpl.content.format(**{k: sample.get(k, f"{{{k}}}") for k in extract_variables(tpl.content)})
    except Exception as e:
        rendered = f"{tpl.content}\n\n(⚠️ erro de formatação: {e})"

    settings = await get_settings()
    bot_username = getattr(settings, "bot_username", None)
    bot_link = f"https://t.me/{bot_username}" if bot_username else ""

    kb = build_buttons_keyboard(buttons, bot_link=bot_link)

    try:
        if tpl.image_url:
            await bot.send_photo(admin_id, photo=tpl.image_url, caption=rendered, reply_markup=kb)
        else:
            await bot.send_message(admin_id, rendered, reply_markup=kb)
    except Exception as e:
        await bot.send_message(admin_id, f"❌ Erro no preview: {str(e)[:200]}")


# ============================================================
# MENU
# ============================================================
@router.callback_query(F.data == "admin_customization")
async def customization_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Acesso negado.", show_alert=True)
        return

    await ensure_all_defaults()

    async with AsyncSessionLocal() as s:
        total_screens = (await s.execute(
            select(func.count(MessageTemplate.id))
        )).scalar() or 0
        active_screens = (await s.execute(
            select(func.count(MessageTemplate.id)).where(MessageTemplate.is_active == True)
        )).scalar() or 0
        total_buttons = (await s.execute(
            select(func.count(ButtonConfig.id))
        )).scalar() or 0
        visible_buttons = (await s.execute(
            select(func.count(ButtonConfig.id)).where(ButtonConfig.is_visible == True)
        )).scalar() or 0

        cat_counts = {}
        rows = (await s.execute(
            select(MessageTemplate.category, func.count(MessageTemplate.id))
            .group_by(MessageTemplate.category)
        )).all()
        for cat, cnt in rows:
            cat_counts[cat] = cnt

    text = (
        "◈ BELLAMY | PERSONALIZAÇÃO\n\n"
        "Editor universal de mensagens e botões.\n\n"
        f"💬 Telas cadastradas: {total_screens}\n"
        f"🟢 Ativas: {active_screens}\n"
        f"🔘 Botões totais: {total_buttons}\n"
        f"👁️ Visíveis: {visible_buttons}\n\n"
        "👇 Selecione uma categoria:"
    )

    buttons = []
    for cat_key, (icon, label) in CATEGORIES.items():
        cnt = cat_counts.get(cat_key, 0)
        if cnt > 0:
            buttons.append([InlineKeyboardButton(
                text=f"{icon} {label} ({cnt})",
                callback_data=f"mes_cat_{cat_key}_0",
            )])

    buttons.append([InlineKeyboardButton(text="📋 Todas as telas", callback_data="mes_all_0")])
    buttons.append([InlineKeyboardButton(text="👁️ Preview rápido", callback_data="mes_quick_preview")])
    buttons.append([InlineKeyboardButton(text="🔄 Restaurar todos os padrões", callback_data="mes_reset_all")])
    buttons.append([InlineKeyboardButton(text="📥 Exportar configurações", callback_data="mes_export")])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_refresh")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# LISTAGEM POR CATEGORIA / TODAS
# ============================================================
@router.callback_query(F.data.startswith("mes_cat_"))
async def list_by_category(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    parts = cb.data.split("_")
    cat = parts[2]
    page = int(parts[3])
    await _list_screens(cb, page, category=cat)


@router.callback_query(F.data.startswith("mes_all_"))
async def list_all(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split("_")[-1])
    await _list_screens(cb, page, category=None)


async def _list_screens(cb: CallbackQuery, page: int, category: str | None):
    page_size = 12
    async with AsyncSessionLocal() as s:
        stmt = select(MessageTemplate)
        cnt = select(func.count(MessageTemplate.id))
        if category:
            stmt = stmt.where(MessageTemplate.category == category)
            cnt = cnt.where(MessageTemplate.category == category)
        total = (await s.execute(cnt)).scalar() or 0
        stmt = stmt.order_by(MessageTemplate.category, MessageTemplate.screen_key).offset(page * page_size).limit(page_size)
        templates = (await s.execute(stmt)).scalars().all()

    if category:
        icon, label = CATEGORIES.get(category, ("•", category))
        title = f"{icon} {label}"
    else:
        title = "TODAS AS TELAS"

    text = f"◈ BELLAMY | {title}\n\nTotal: {total} | Página {page + 1}\n\n"

    buttons = []
    for t in templates:
        status = "🟢" if t.is_active else "🔴"
        cat_icon, _ = CATEGORIES.get(t.category, ("•", ""))
        text += f"{status} {cat_icon} <b>{t.title}</b>\n   <code>{t.screen_key}</code>\n\n"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {cat_icon} {t.title[:35]}",
            callback_data=f"mes_view_{t.screen_key}",
        )])

    nav = []
    prefix = f"mes_cat_{category}" if category else "mes_all"
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}_{page - 1}"))
    if (page + 1) * page_size < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}_{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_customization")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


# ============================================================
# TELA DE DETALHE
# ============================================================
async def _screen_detail(screen_key: str) -> tuple[str, InlineKeyboardMarkup]:
    tpl = await get_or_create_template(screen_key)
    buttons = await get_or_create_buttons(screen_key)

    cat_icon, cat_label = CATEGORIES.get(tpl.category, ("•", tpl.category))
    content_preview = tpl.content[:300] + ("..." if len(tpl.content) > 300 else "")

    text = (
        f"◈ BELLAMY | TELA: {tpl.title}\n\n"
        f"🔑 <code>{screen_key}</code>\n"
        f"📂 Categoria: {cat_icon} {cat_label}\n"
        f"📌 Status: {'🟢 Ativa' if tpl.is_active else '🔴 Desativada'}\n"
        f"🖼️ Imagem: {'🟢 ' + tpl.image_url[:50] if tpl.image_url else '🔴 Nenhuma'}\n\n"
        f"🔤 Variáveis permitidas ({len(tpl.variables)}):\n"
        f"<code>{', '.join('{' + v + '}' for v in tpl.variables) or '(nenhuma)'}</code>\n\n"
        f"💬 <b>Mensagem atual</b>\n<code>{content_preview}</code>\n\n"
        f"🔘 Botões: {len(buttons)} total | {sum(1 for b in buttons if b.is_visible)} visíveis\n"
        f"🕐 Última atualização: {tpl.updated_at.strftime('%d/%m/%Y %H:%M')}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Editar título", callback_data=f"mes_edit_title_{screen_key}")],
        [InlineKeyboardButton(text="💬 Editar mensagem", callback_data=f"mes_edit_content_{screen_key}")],
        [InlineKeyboardButton(text="🖼️ Configurar imagem", callback_data=f"mes_edit_image_{screen_key}")],
        [InlineKeyboardButton(text="🔘 Gerenciar botões", callback_data=f"mes_btn_list_{screen_key}")],
        [InlineKeyboardButton(text="➕ Adicionar botão", callback_data=f"mes_btn_add_{screen_key}")],
        [InlineKeyboardButton(text="👁️ Preview para mim", callback_data=f"mes_preview_{screen_key}")],
        [InlineKeyboardButton(
            text=("🔴 Desativar tela" if tpl.is_active else "🟢 Ativar tela"),
            callback_data=f"mes_toggle_{screen_key}")],
        [InlineKeyboardButton(text="📜 Histórico", callback_data=f"mes_hist_{screen_key}_0")],
        [InlineKeyboardButton(text="↩️ Restaurar padrão", callback_data=f"mes_reset_{screen_key}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_customization")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("mes_view_"))
async def view_screen(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    screen_key = cb.data.replace("mes_view_", "")
    try:
        text, kb = await _screen_detail(screen_key)
    except ValueError:
        await cb.answer("Tela inválida.", show_alert=True)
        return
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# EDITAR TÍTULO / CONTEÚDO / IMAGEM
# ============================================================
@router.callback_query(F.data.startswith("mes_edit_title_"))
async def edit_title_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    screen_key = cb.data.replace("mes_edit_title_", "")
    tpl = await get_or_create_template(screen_key)
    await state.update_data(screen_key=screen_key)
    await state.set_state(MES.edit_title)
    await cb.message.answer(
        f"✏️ EDITAR TÍTULO\n\nTela: <code>{screen_key}</code>\n"
        f"Título atual: <b>{tpl.title}</b>\n\nEnvie o novo título:"
    )
    await cb.answer()


@router.message(MES.edit_title)
async def edit_title_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    screen_key = data.get("screen_key")
    new_title = m.text.strip() if m.text else ""
    if len(new_title) < 2 or len(new_title) > 120:
        await m.answer("❌ Título deve ter 2-120 caracteres.")
        return

    async with AsyncSessionLocal() as s:
        tpl = (await s.execute(
            select(MessageTemplate).where(MessageTemplate.screen_key == screen_key)
        )).scalar_one_or_none()
        if not tpl:
            await state.clear()
            await m.answer("❌ Tela não encontrada.")
            return
        old = tpl.title
        tpl.title = new_title
        tpl.updated_by = m.from_user.id
        await s.commit()

    await log_admin(m.from_user.id, "customization_edit_title", {
        "screen": screen_key, "old": old, "new": new_title,
    })
    await state.clear()
    await m.answer("✅ Título atualizado.")
    text, kb = await _screen_detail(screen_key)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("mes_edit_content_"))
async def edit_content_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    screen_key = cb.data.replace("mes_edit_content_", "")
    tpl = await get_or_create_template(screen_key)
    await state.update_data(screen_key=screen_key)
    await state.set_state(MES.edit_content)

    vars_str = ", ".join("{" + v + "}" for v in tpl.variables) or "(nenhuma)"
    await cb.message.answer(
        f"💬 EDITAR MENSAGEM\n\n"
        f"Tela: <code>{screen_key}</code>\n\n"
        f"🔤 Variáveis permitidas:\n<code>{vars_str}</code>\n\n"
        f"Envie o novo conteúdo (HTML permitido).\n"
        f"Use /resetar para voltar ao padrão."
    )
    await cb.answer()


@router.message(MES.edit_content)
async def edit_content_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    screen_key = data.get("screen_key")
    raw = m.text.strip() if m.text else ""

    async with AsyncSessionLocal() as s:
        tpl = (await s.execute(
            select(MessageTemplate).where(MessageTemplate.screen_key == screen_key)
        )).scalar_one_or_none()
        if not tpl:
            await state.clear()
            await m.answer("❌ Tela não encontrada.")
            return

        if raw.lower() == "/resetar":
            new_content = DEFAULT_SCREENS[screen_key]["content"]
        else:
            if len(raw) < 5 or len(raw) > 4000:
                await m.answer("❌ Conteúdo deve ter entre 5 e 4000 caracteres.")
                return
            ok, invalid = validate_variables(raw, tpl.variables)
            if not ok:
                await m.answer(
                    f"❌ Variáveis não permitidas: <code>{', '.join('{' + v + '}' for v in invalid)}</code>\n\n"
                    f"Permitidas: <code>{', '.join('{' + v + '}' for v in tpl.variables) or 'nenhuma'}</code>"
                )
                return
            new_content = raw

        s.add(MessageTemplateHistory(
            template_id=tpl.id,
            old_content=tpl.content,
            new_content=new_content,
            admin_id=m.from_user.id,
        ))
        tpl.content = new_content
        tpl.updated_by = m.from_user.id
        await s.commit()

    await log_admin(m.from_user.id, "customization_edit_content", {
        "screen": screen_key, "len": len(new_content),
    })
    await state.clear()
    await m.answer("✅ Mensagem atualizada.")
    text, kb = await _screen_detail(screen_key)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("mes_edit_image_"))
async def edit_image_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    screen_key = cb.data.replace("mes_edit_image_", "")
    tpl = await get_or_create_template(screen_key)
    await state.update_data(screen_key=screen_key)
    await state.set_state(MES.edit_image)
    current = tpl.image_url or "nenhuma"
    await cb.message.answer(
        f"🖼️ EDITAR IMAGEM\n\nTela: <code>{screen_key}</code>\n"
        f"Atual: <code>{current[:200]}</code>\n\n"
        f"Envie a URL pública ou /limpar para remover."
    )
    await cb.answer()


@router.message(MES.edit_image)
async def edit_image_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    screen_key = data.get("screen_key")
    raw = m.text.strip() if m.text else ""

    if raw.lower() == "/limpar":
        new_url = None
    else:
        if not (raw.startswith("http://") or raw.startswith("https://")):
            await m.answer("❌ URL inválida.")
            return
        new_url = raw

    async with AsyncSessionLocal() as s:
        tpl = (await s.execute(
            select(MessageTemplate).where(MessageTemplate.screen_key == screen_key)
        )).scalar_one_or_none()
        if not tpl:
            await state.clear()
            await m.answer("❌ Tela não encontrada.")
            return
        tpl.image_url = new_url
        tpl.updated_by = m.from_user.id
        await s.commit()

    await log_admin(m.from_user.id, "customization_edit_image", {
        "screen": screen_key, "url": new_url,
    })
    await state.clear()
    await m.answer("✅ Imagem atualizada.")
    text, kb = await _screen_detail(screen_key)
    await m.answer(text, reply_markup=kb)


# ============================================================
# BOTÕES — LISTAGEM
# ============================================================
@router.callback_query(F.data.startswith("mes_btn_list_"))
async def btn_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    screen_key = cb.data.replace("mes_btn_list_", "")
    buttons = await get_or_create_buttons(screen_key)

    if not buttons:
        text = f"🔘 BOTÕES — <code>{screen_key}</code>\n\nNenhum botão cadastrado."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Adicionar botão", callback_data=f"mes_btn_add_{screen_key}")],
            [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"mes_view_{screen_key}")],
        ])
        await cb.message.edit_text(text, reply_markup=kb)
        await cb.answer()
        return

    rows: dict[int, list] = {}
    for b in buttons:
        rows.setdefault(b.row_order, []).append(b)

    lines = [f"🔘 BOTÕES — <code>{screen_key}</code>\n\n"]
    for row_idx in sorted(rows.keys()):
        row = sorted(rows[row_idx], key=lambda x: x.col_order)
        preview = " ".join(
            f"[{'🟢' if b.is_visible else '🔴'} {b.emoji or ''} {b.label}]" for b in row
        )
        lines.append(f"Linha {row_idx}: {preview}\n")

    text = "".join(lines)

    buttons_kb = []
    for b in buttons:
        status = "🟢" if b.is_visible else "🔴"
        buttons_kb.append([InlineKeyboardButton(
            text=f"{status} {b.emoji or ''} {b.label[:28]}",
            callback_data=f"mes_btn_view_{b.id}",
        )])
    buttons_kb.append([InlineKeyboardButton(text="➕ Adicionar botão", callback_data=f"mes_btn_add_{screen_key}")])
    buttons_kb.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=f"mes_view_{screen_key}")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons_kb))
    await cb.answer()


# ============================================================
# BOTÕES — DETALHE
# ============================================================
async def _btn_detail(btn_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as s:
        b = await s.get(ButtonConfig, btn_id)
        if not b:
            return (
                "❌ Botão não encontrado.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_customization")]
                ]),
            )

    action_label = ACTION_TYPES.get(b.action_type, ("•", b.action_type))[0]

    text = (
        f"🔘 BOTÃO #{b.id}\n\n"
        f"🏠 Tela: <code>{b.screen_key}</code>\n"
        f"🔑 Chave: <code>{b.button_key}</code>\n\n"
        f"🏷️ Texto: {b.label}\n"
        f"😀 Emoji: {b.emoji or '—'}\n"
        f"🎯 Ação: {action_label}\n"
        f"💬 Valor: <code>{(b.action_value or '—')[:120]}</code>\n\n"
        f"📐 Layout: linha {b.row_order}, coluna {b.col_order}\n"
        f"👁️ Visível: {'🟢 sim' if b.is_visible else '🔴 não'}\n"
        f"👑 Só admin: {'🟢 sim' if b.admin_only else '🔴 não'}\n"
        f"⚠️ Confirmação: {'🟢 sim' if b.requires_confirmation else '🔴 não'}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏷️ Editar texto", callback_data=f"mes_btn_edit_{b.id}_label")],
        [InlineKeyboardButton(text="😀 Editar emoji", callback_data=f"mes_btn_edit_{b.id}_emoji")],
        [InlineKeyboardButton(text="🎯 Editar ação", callback_data=f"mes_btn_action_{b.id}")],
        [InlineKeyboardButton(text="📐 Editar posição", callback_data=f"mes_btn_edit_{b.id}_pos")],
        [InlineKeyboardButton(
            text=("🔴 Ocultar" if b.is_visible else "🟢 Mostrar"),
            callback_data=f"mes_btn_toggle_vis_{b.id}")],
        [InlineKeyboardButton(
            text=("🔴 Não exigir confirmação" if b.requires_confirmation else "🟢 Exigir confirmação"),
            callback_data=f"mes_btn_toggle_conf_{b.id}")],
        [InlineKeyboardButton(
            text=("🔴 Ocultar de admin" if b.admin_only else "🟢 Restringir a admin"),
            callback_data=f"mes_btn_toggle_admin_{b.id}")],
        [InlineKeyboardButton(text="🗑️ Excluir botão", callback_data=f"mes_btn_del_{b.id}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"mes_btn_list_{b.screen_key}")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("mes_btn_view_"))
async def btn_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    btn_id = int(cb.data.replace("mes_btn_view_", ""))
    text, kb = await _btn_detail(btn_id)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ============================================================
# BOTÕES — EDITAR CAMPOS
# ============================================================
@router.callback_query(F.data.startswith("mes_btn_edit_"))
async def btn_edit_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    rest = cb.data.replace("mes_btn_edit_", "")
    parts = rest.split("_", 1)
    btn_id = int(parts[0])
    field = parts[1]

    async with AsyncSessionLocal() as s:
        b = await s.get(ButtonConfig, btn_id)
        if not b:
            await cb.answer("Botão não encontrado.", show_alert=True)
            return

    await state.update_data(btn_id=btn_id, btn_field=field)
    await state.set_state(BTS.edit_field)

    prompts = {
        "label": "Envie o novo texto do botão (2-64 caracteres):",
        "emoji": "Envie o emoji do botão (ou /limpar para remover):",
        "pos": (
            f"Envie a nova posição como <code>LINHA,COLUNA</code>.\n"
            f"Atual: linha {b.row_order}, coluna {b.col_order}.\n"
            f"Ex.: <code>0,0</code> (primeiro), <code>2,1</code> (linha 3, coluna 2)."
        ),
    }
    await cb.message.answer(prompts.get(field, "Envie o novo valor:"))
    await cb.answer()


@router.message(BTS.edit_field)
async def btn_edit_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    btn_id = data.get("btn_id")
    field = data.get("btn_field")
    raw = m.text.strip() if m.text else ""

    async with AsyncSessionLocal() as s:
        b = await s.get(ButtonConfig, btn_id)
        if not b:
            await state.clear()
            await m.answer("❌ Botão não encontrado.")
            return

        if field == "label":
            if len(raw) < 2 or len(raw) > 64:
                await m.answer("❌ Texto deve ter 2-64 caracteres.")
                return
            b.label = raw
        elif field == "emoji":
            if raw.lower() == "/limpar":
                b.emoji = None
            else:
                b.emoji = raw[:8]
        elif field == "pos":
            try:
                r, c = raw.split(",")
                row = int(r.strip())
                col = int(c.strip())
                if row < 0 or col < 0:
                    raise ValueError
            except Exception:
                await m.answer("❌ Formato inválido. Use <code>LINHA,COLUNA</code>.")
                return
            b.row_order = row
            b.col_order = col
        else:
            await state.clear()
            await m.answer("❌ Campo inválido.")
            return

        await s.commit()

    await log_admin(m.from_user.id, f"customization_btn_edit_{field}", {
        "btn_id": btn_id, "value": raw[:100],
    })
    await state.clear()
    await m.answer("✅ Atualizado.")
    text, kb = await _btn_detail(btn_id)
    await m.answer(text, reply_markup=kb)


# ============================================================
# BOTÕES — EDITAR AÇÃO (tipo + valor)
# ============================================================
@router.callback_query(F.data.startswith("mes_btn_action_"))
async def btn_action_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    btn_id = int(cb.data.replace("mes_btn_action_", ""))
    buttons = []
    for key, (label, desc) in ACTION_TYPES.items():
        buttons.append([InlineKeyboardButton(
            text=f"{label} ({desc})",
            callback_data=f"mes_btn_settype_{btn_id}_{key}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"mes_btn_view_{btn_id}")])
    await cb.message.edit_text(
        "🎯 TIPO DE AÇÃO DO BOTÃO\n\nSelecione:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mes_btn_settype_"))
async def btn_set_type(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("mes_btn_settype_", "")
    parts = rest.split("_", 1)
    btn_id = int(parts[0])
    action_type = parts[1]

    if action_type not in ACTION_TYPES:
        await cb.answer("Tipo inválido.")
        return

    async with AsyncSessionLocal() as s:
        b = await s.get(ButtonConfig, btn_id)
        if not b:
            await cb.answer("Botão não encontrado.", show_alert=True)
            return
        b.action_type = action_type
        await s.commit()

    await log_admin(cb.from_user.id, "customization_btn_set_action_type", {
        "btn_id": btn_id, "type": action_type,
    })
    await cb.answer(f"Tipo alterado para {ACTION_TYPES[action_type][0]}.")

    # Abre o editor de valor automaticamente
    await _start_action_value(cb.message, state, btn_id, action_type, from_callback=True)


async def _start_action_value(target_message, state: FSMContext, btn_id: int, action_type: str, from_callback=False):
    prompts = {
        "callback": "Envie o <code>callback_data</code> (ex.: <code>show_catalog</code>):",
        "url": "Envie a URL completa (https://...):",
        "deeplink": "Envie a URL do Deep Link ou <code>auto</code> para usar o link principal do bot:",
        "miniapp": "Envie a URL da Mini App (https://...):",
        "nav": "Envie a ação de navegação: <code>back</code>, <code>main_menu</code> ou <code>cancel</code>:",
    }
    await state.update_data(btn_id=btn_id, btn_action_type=action_type)
    await state.set_state(BTS.add_action_value)
    await target_message.answer(prompts.get(action_type, "Envie o valor:"))


@router.callback_query(F.data.startswith("mes_btn_setval_"))
async def btn_set_value_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    btn_id = int(cb.data.replace("mes_btn_setval_", ""))
    async with AsyncSessionLocal() as s:
        b = await s.get(ButtonConfig, btn_id)
        if not b:
            await cb.answer("Botão não encontrado.", show_alert=True)
            return
        atype = b.action_type
    await _start_action_value(cb.message, state, btn_id, atype)
    await cb.answer()


@router.message(BTS.add_action_value)
async def btn_action_value_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    btn_id = data.get("btn_id")
    atype = data.get("btn_action_type")
    raw = m.text.strip() if m.text else ""

    if len(raw) < 1 or len(raw) > 500:
        await m.answer("❌ Valor inválido (1-500 caracteres).")
        return

    if atype in ("url", "miniapp") and not (raw.startswith("http://") or raw.startswith("https://")):
        await m.answer("❌ URL deve começar com http:// ou https://")
        return
    if atype == "deeplink":
        if raw.lower() != "auto" and not raw.startswith("https://t.me/"):
            await m.answer("❌ Use <code>auto</code> ou uma URL <code>https://t.me/...</code>")
            return
    if atype == "nav":
        if raw.lower() not in ("back", "main_menu", "cancel"):
            await m.answer("❌ Use <code>back</code>, <code>main_menu</code> ou <code>cancel</code>.")
            return
        raw = raw.lower()

    async with AsyncSessionLocal() as s:
        b = await s.get(ButtonConfig, btn_id)
        if not b:
            await state.clear()
            await m.answer("❌ Botão não encontrado.")
            return
        b.action_value = raw
        await s.commit()

    await log_admin(m.from_user.id, "customization_btn_set_action_value", {
        "btn_id": btn_id, "value": raw[:100],
    })
    await state.clear()
    await m.answer("✅ Valor atualizado.")
    text, kb = await _btn_detail(btn_id)
    await m.answer(text, reply_markup=kb)


# ============================================================
# BOTÕES — TOGGLES
# ============================================================
@router.callback_query(F.data.startswith("mes_btn_toggle_"))
async def btn_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("mes_btn_toggle_", "")
    parts = rest.split("_", 1)
    field_prefix = parts[0]   # vis | conf | admin
    btn_id = int(parts[1])

    field_map = {"vis": "is_visible", "conf": "requires_confirmation", "admin": "admin_only"}
    field = field_map.get(field_prefix)
    if not field:
        await cb.answer("Campo inválido.")
        return

    async with AsyncSessionLocal() as s:
        b = await s.get(ButtonConfig, btn_id)
        if not b:
            await cb.answer("Botão não encontrado.", show_alert=True)
            return
        cur = getattr(b, field)
        setattr(b, field, not cur)
        new = getattr(b, field)
        await s.commit()

    await log_admin(cb.from_user.id, f"customization_btn_toggle_{field_prefix}", {
        "btn_id": btn_id, "new": new,
    })
    text, kb = await _btn_detail(btn_id)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Atualizado.")


# ============================================================
# BOTÕES — EXCLUIR
# ============================================================
@router.callback_query(F.data.startswith("mes_btn_del_"))
async def btn_delete_prompt(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    btn_id = int(cb.data.replace("mes_btn_del_", ""))
    async with AsyncSessionLocal() as s:
        b = await s.get(ButtonConfig, btn_id)
        if not b:
            await cb.answer("Botão não encontrado.", show_alert=True)
            return
        screen_key = b.screen_key
        label = b.label

    await cb.message.edit_text(
        f"🗑️ EXCLUIR BOTÃO\n\n"
        f"Tela: <code>{screen_key}</code>\n"
        f"Texto: <b>{label}</b>\n\n"
        f"Confirmar exclusão?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Sim, excluir", callback_data=f"mes_btn_delok_{btn_id}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"mes_btn_view_{btn_id}")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mes_btn_delok_"))
async def btn_delete_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    btn_id = int(cb.data.replace("mes_btn_delok_", ""))
    async with AsyncSessionLocal() as s:
        b = await s.get(ButtonConfig, btn_id)
        if not b:
            await cb.answer("Botão não encontrado.", show_alert=True)
            return
        screen_key = b.screen_key
        label = b.label
        await s.delete(b)
        await s.commit()

    await log_admin(cb.from_user.id, "customization_btn_delete", {
        "btn_id": btn_id, "screen": screen_key, "label": label,
    })
    await cb.message.edit_text(
        f"✅ Botão <b>{label}</b> excluído.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔘 Ver botões da tela", callback_data=f"mes_btn_list_{screen_key}")],
            [InlineKeyboardButton(text="↩️ Voltar à tela", callback_data=f"mes_view_{screen_key}")],
        ]),
    )
    await cb.answer()


# ============================================================
# BOTÕES — ADICIONAR (FSM)
# ============================================================
@router.callback_query(F.data.startswith("mes_btn_add_"))
async def btn_add_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    screen_key = cb.data.replace("mes_btn_add_", "")
    await state.update_data(screen_key=screen_key, new_btn={})
    await state.set_state(BTS.add_label)
    await cb.message.answer(
        f"➕ NOVO BOTÃO (1/5)\n\nTela: <code>{screen_key}</code>\n\n"
        f"Informe o <b>texto</b> do botão (2-64 caracteres):"
    )
    await cb.answer()


@router.message(BTS.add_label)
async def btn_add_label(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    if len(raw) < 2 or len(raw) > 64:
        await m.answer("❌ Texto deve ter 2-64 caracteres.")
        return
    data = await state.get_data()
    data["new_btn"]["label"] = raw
    await state.update_data(new_btn=data["new_btn"])
    await state.set_state(BTS.add_emoji)
    await m.answer(
        "➕ NOVO BOTÃO (2/5)\n\n"
        "Envie um <b>emoji</b> opcional ou /pular:"
    )


@router.message(BTS.add_emoji)
async def btn_add_emoji(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    data = await state.get_data()
    data["new_btn"]["emoji"] = None if raw.lower() == "/pular" else raw[:8]
    await state.update_data(new_btn=data["new_btn"])
    await state.set_state(BTS.add_action_type)

    buttons = []
    for key, (label, desc) in ACTION_TYPES.items():
        buttons.append([InlineKeyboardButton(
            text=f"{label} — {desc}",
            callback_data=f"mes_btnaddtype_{key}",
        )])
    buttons.append([InlineKeyboardButton(text="❌ Cancelar", callback_data="mes_btnadd_cancel")])
    await m.answer(
        "➕ NOVO BOTÃO (3/5)\n\nSelecione o tipo de ação:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("mes_btnaddtype_"))
async def btn_add_type(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    atype = cb.data.replace("mes_btnaddtype_", "")
    if atype not in ACTION_TYPES:
        await cb.answer("Tipo inválido.")
        return
    data = await state.get_data()
    data["new_btn"]["action_type"] = atype
    await state.update_data(new_btn=data["new_btn"])
    await state.set_state(BTS.add_action_value)

    prompts = {
        "callback": "Envie o callback_data (ex.: show_catalog):",
        "url": "Envie a URL completa (https://...):",
        "deeplink": "Envie a URL do deep link ou <code>auto</code>:",
        "miniapp": "Envie a URL da Mini App:",
        "nav": "Envie: <code>back</code>, <code>main_menu</code> ou <code>cancel</code>.",
    }
    await cb.message.answer(
        f"➕ NOVO BOTÃO (4/5)\n\n{prompts.get(atype, 'Envie o valor:')}"
    )
    await cb.answer()


@router.message(BTS.add_action_value)
async def btn_add_action_value(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip() if m.text else ""
    data = await state.get_data()
    atype = data["new_btn"].get("action_type")

    if atype in ("url", "miniapp") and not (raw.startswith("http://") or raw.startswith("https://")):
        await m.answer("❌ URL deve começar com http:// ou https://")
        return
    if atype == "deeplink" and raw.lower() != "auto" and not raw.startswith("https://t.me/"):
        await m.answer("❌ Use <code>auto</code> ou URL <code>https://t.me/...</code>")
        return
    if atype == "nav" and raw.lower() not in ("back", "main_menu", "cancel"):
        await m.answer("❌ Use <code>back</code>, <code>main_menu</code> ou <code>cancel</code>.")
        return

    if atype == "nav":
        raw = raw.lower()

    data["new_btn"]["action_value"] = raw
    await state.update_data(new_btn=data["new_btn"])
    await state.set_state(BTS.add_row)
    await m.answer(
        "➕ NOVO BOTÃO (5/5)\n\n"
        "Informe a <b>linha</b> do botão (0 = primeira linha):"
    )


@router.message(BTS.add_row)
async def btn_add_row(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        row = int(m.text.strip())
        if row < 0 or row > 99:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido (0-99).")
        return
    data = await state.get_data()
    data["new_btn"]["row"] = row
    await state.update_data(new_btn=data["new_btn"])
    await state.set_state(BTS.add_col)
    await m.answer("➕ Último passo\n\nInforme a <b>coluna</b> (0 = primeira):")


@router.message(BTS.add_col)
async def btn_add_col(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        col = int(m.text.strip())
        if col < 0 or col > 10:
            raise ValueError
    except ValueError:
        await m.answer("❌ Valor inválido (0-10).")
        return
    data = await state.get_data()
    data["new_btn"]["col"] = col
    n = data["new_btn"]
    screen_key = data.get("screen_key")
    await state.clear()

    # Gerar button_key único
    async with AsyncSessionLocal() as s:
        base_key = f"custom_{int(datetime.utcnow().timestamp())}"
        btn = ButtonConfig(
            screen_key=screen_key,
            button_key=base_key,
            label=n["label"],
            emoji=n.get("emoji"),
            action_type=n["action_type"],
            action_value=n.get("action_value"),
            row_order=n["row"],
            col_order=n["col"],
            is_visible=True,
            admin_only=False,
            requires_confirmation=False,
        )
        s.add(btn)
        await s.commit()
        await s.refresh(btn)
        btn_id = btn.id

    await log_admin(m.from_user.id, "customization_btn_add", {
        "btn_id": btn_id, "screen": screen_key, "label": n["label"],
    })
    await m.answer("✅ Botão adicionado com sucesso.")
    text, kb = await _btn_detail(btn_id)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data == "mes_btnadd_cancel")
async def btn_add_cancel(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    screen_key = data.get("screen_key", "")
    await state.clear()
    await cb.message.edit_text(
        "Adição cancelada.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"mes_btn_list_{screen_key}")],
        ]),
    )
    await cb.answer()


# ============================================================
# TOGGLE TELA / RESET / PREVIEW / HISTÓRICO
# ============================================================
@router.callback_query(F.data.startswith("mes_toggle_"))
async def toggle_screen(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    screen_key = cb.data.replace("mes_toggle_", "")
    async with AsyncSessionLocal() as s:
        tpl = (await s.execute(
            select(MessageTemplate).where(MessageTemplate.screen_key == screen_key)
        )).scalar_one_or_none()
        if not tpl:
            await cb.answer("Tela não encontrada.", show_alert=True)
            return
        tpl.is_active = not tpl.is_active
        new = tpl.is_active
        await s.commit()
    await log_admin(cb.from_user.id, "customization_toggle_screen", {"screen": screen_key, "new": new})
    text, kb = await _screen_detail(screen_key)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Status atualizado.")


@router.callback_query(F.data.startswith("mes_preview_"))
async def preview_screen(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    screen_key = cb.data.replace("mes_preview_", "")
    try:
        await _render_preview(screen_key, cb.from_user.id)
        await cb.answer("👁️ Preview enviado.")
    except Exception as e:
        await cb.answer(f"Erro: {str(e)[:100]}", show_alert=True)


@router.callback_query(F.data == "mes_quick_preview")
async def quick_preview(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    buttons = []
    for key, meta in DEFAULT_SCREENS.items():
        buttons.append([InlineKeyboardButton(
            text=f"👁️ {meta['title'][:35]}",
            callback_data=f"mes_preview_{key}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_customization")])
    await cb.message.edit_text(
        "👁️ PREVIEW RÁPIDO\n\nSelecione uma tela para visualizar:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mes_reset_"))
async def reset_screen_prompt(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    if cb.data == "mes_reset_all":
        await cb.message.edit_text(
            "⚠️ RESTAURAR TODOS OS PADRÕES\n\n"
            "Todas as mensagens e botões serão restaurados ao padrão original.\n"
            "O histórico será mantido.\n\n"
            "Confirmar?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Sim, restaurar tudo", callback_data="mes_reset_all_ok")],
                [InlineKeyboardButton(text="↩️ Cancelar", callback_data="admin_customization")],
            ]),
        )
        await cb.answer()
        return

    screen_key = cb.data.replace("mes_reset_", "")
    await cb.message.edit_text(
        f"⚠️ RESTAURAR PADRÃO\n\nTela: <code>{screen_key}</code>\n\n"
        f"A mensagem e os botões voltarão ao padrão original.\n"
        f"O histórico será mantido.\n\nConfirmar?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Sim", callback_data=f"mes_reset_ok_{screen_key}")],
            [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"mes_view_{screen_key}")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("mes_reset_ok_"))
async def reset_screen_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    screen_key = cb.data.replace("mes_reset_ok_", "")
    default = DEFAULT_SCREENS.get(screen_key)
    if not default:
        await cb.answer("Tela inválida.", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        tpl = (await s.execute(
            select(MessageTemplate).where(MessageTemplate.screen_key == screen_key)
        )).scalar_one_or_none()
        if tpl:
            s.add(MessageTemplateHistory(
                template_id=tpl.id,
                old_content=tpl.content,
                new_content=default["content"],
                admin_id=cb.from_user.id,
            ))
            tpl.content = default["content"]
            tpl.title = default["title"]
            tpl.image_url = default.get("image_url")
            tpl.updated_by = cb.from_user.id

        existing_btns = (await s.execute(
            select(ButtonConfig).where(ButtonConfig.screen_key == screen_key)
        )).scalars().all()
        for b in existing_btns:
            await s.delete(b)

        for b in default.get("buttons", []):
            s.add(ButtonConfig(
                screen_key=screen_key,
                button_key=b["key"],
                label=b["text"],
                emoji=None,
                action_type=b["action_type"],
                action_value=b.get("action_value"),
                row_order=b["row"],
                col_order=b["col"],
                is_visible=True,
                admin_only=False,
                requires_confirmation=False,
            ))
        await s.commit()

    await log_admin(cb.from_user.id, "customization_reset_screen", {"screen": screen_key})
    text, kb = await _screen_detail(screen_key)
    await cb.message.edit_text("✅ Restaurado ao padrão.\n\n" + text, reply_markup=kb)
    await cb.answer("Restaurado.")


@router.callback_query(F.data == "mes_reset_all_ok")
async def reset_all_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await _reset_all()
    await log_admin(cb.from_user.id, "customization_reset_all", {})
    await cb.message.edit_text(
        "✅ Todas as telas e botões foram restaurados ao padrão.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Voltar", callback_data="admin_customization")],
        ]),
    )
    await cb.answer("Restaurado.")


async def _reset_all():
    async with AsyncSessionLocal() as s:
        all_tpls = (await s.execute(select(MessageTemplate))).scalars().all()
        for tpl in all_tpls:
            if tpl.screen_key in DEFAULT_SCREENS:
                default = DEFAULT_SCREENS[tpl.screen_key]
                tpl.content = default["content"]
                tpl.title = default["title"]
                tpl.image_url = default.get("image_url")

        all_btns = (await s.execute(select(ButtonConfig))).scalars().all()
        for b in all_btns:
            await s.delete(b)

        for screen_key, default in DEFAULT_SCREENS.items():
            for b in default.get("buttons", []):
                s.add(ButtonConfig(
                    screen_key=screen_key,
                    button_key=b["key"],
                    label=b["text"],
                    emoji=None,
                    action_type=b["action_type"],
                    action_value=b.get("action_value"),
                    row_order=b["row"],
                    col_order=b["col"],
                    is_visible=True,
                    admin_only=False,
                    requires_confirmation=False,
                ))
        await s.commit()


# ============================================================
# HISTÓRICO
# ============================================================
@router.callback_query(F.data.startswith("mes_hist_"))
async def history_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rest = cb.data.replace("mes_hist_", "")
    parts = rest.rsplit("_", 1)
    screen_key = parts[0]
    page = int(parts[1])
    page_size = 8

    async with AsyncSessionLocal() as s:
        tpl = (await s.execute(
            select(MessageTemplate).where(MessageTemplate.screen_key == screen_key)
        )).scalar_one_or_none()
        if not tpl:
            await cb.answer("Tela não encontrada.", show_alert=True)
            return
        total = (await s.execute(
            select(func.count(MessageTemplateHistory.id))
            .where(MessageTemplateHistory.template_id == tpl.id)
        )).scalar() or 0
        stmt = (
            select(MessageTemplateHistory)
            .where(MessageTemplateHistory.template_id == tpl.id)
            .order_by(desc(MessageTemplateHistory.edited_at))
            .offset(page * page_size)
            .limit(page_size)
        )
        history = (await s.execute(stmt)).scalars().all()

    text = f"📜 HISTÓRICO — <code>{screen_key}</code>\n\nTotal: {total} | Página {page + 1}\n\n"
    if not history:
        text += "Nenhuma alteração registrada."
    else:
        for h in history:
            text += (
                f"🕒 {h.edited_at.strftime('%d/%m/%Y %H:%M')} | Admin <code>{h.admin_id}</code>\n"
                f"   De: {len(h.old_content)} caracteres → Para: {len(h.new_content)} caracteres\n\n"
            )

    buttons = []
    for h in history:
        buttons.append([InlineKeyboardButton(
            text=f"👁️ Ver versão de {h.edited_at.strftime('%d/%m %H:%M')}",
            callback_data=f"mes_histver_{h.id}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=f"mes_hist_{screen_key}_{page - 1}"
        ))
    if (page + 1) * page_size < total:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=f"mes_hist_{screen_key}_{page + 1}"
        ))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Voltar", callback_data=f"mes_view_{screen_key}")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("mes_histver_"))
async def history_version(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    hist_id = int(cb.data.replace("mes_histver_", ""))
    async with AsyncSessionLocal() as s:
        h = await s.get(MessageTemplateHistory, hist_id)
        if not h:
            await cb.answer("Registro não encontrado.", show_alert=True)
            return
        tpl = await s.get(MessageTemplate, h.template_id)

    old_preview = h.old_content[:600] + ("..." if len(h.old_content) > 600 else "")
    new_preview = h.new_content[:600] + ("..." if len(h.new_content) > 600 else "")

    text = (
        f"📜 VERSÃO DE {h.edited_at.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"👤 Admin: <code>{h.admin_id}</code>\n\n"
        f"📄 <b>Versão anterior</b> ({len(h.old_content)} chars)\n"
        f"<code>{old_preview}</code>\n\n"
        f"📄 <b>Nova versão</b> ({len(h.new_content)} chars)\n"
        f"<code>{new_preview}</code>"
    )

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Restaurar versão anterior", callback_data=f"mes_restore_{hist_id}")],
        [InlineKeyboardButton(text="↩️ Voltar", callback_data=f"mes_hist_{tpl.screen_key}_0")],
    ]))
    await cb.answer()


@router.callback_query(F.data.startswith("mes_restore_"))
async def restore_version(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    hist_id = int(cb.data.replace("mes_restore_", ""))
    async with AsyncSessionLocal() as s:
        h = await s.get(MessageTemplateHistory, hist_id)
        if not h:
            await cb.answer("Registro não encontrado.", show_alert=True)
            return
        tpl = await s.get(MessageTemplate, h.template_id)
        if not tpl:
            await cb.answer("Tela não encontrada.", show_alert=True)
            return

        s.add(MessageTemplateHistory(
            template_id=tpl.id,
            old_content=tpl.content,
            new_content=h.old_content,
            admin_id=cb.from_user.id,
        ))
        tpl.content = h.old_content
        tpl.updated_by = cb.from_user.id
        await s.commit()
        screen_key = tpl.screen_key

    await log_admin(cb.from_user.id, "customization_restore_version", {
        "screen": screen_key, "history_id": hist_id,
    })
    text, kb = await _screen_detail(screen_key)
    await cb.message.edit_text("✅ Versão restaurada.\n\n" + text, reply_markup=kb)
    await cb.answer("Restaurado.")


# ============================================================
# EXPORTAÇÃO
# ============================================================
@router.callback_query(F.data == "mes_export")
async def export_config(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return

    async with AsyncSessionLocal() as s:
        templates = (await s.execute(
            select(MessageTemplate).order_by(MessageTemplate.category, MessageTemplate.screen_key)
        )).scalars().all()
        buttons = (await s.execute(
            select(ButtonConfig).order_by(ButtonConfig.screen_key, ButtonConfig.row_order, ButtonConfig.col_order)
        )).scalars().all()

    lines = []
    lines.append("═" * 70)
    lines.append("BELLAMY | EXPORTAÇÃO DE PERSONALIZAÇÃO")
    lines.append(f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')} UTC")
    lines.append("═" * 70)
    lines.append("")

    for tpl in templates:
        lines.append("─" * 70)
        lines.append(f"TELA: {tpl.screen_key}")
        lines.append(f"Título: {tpl.title}")
        lines.append(f"Categoria: {tpl.category}")
        lines.append(f"Ativa: {'sim' if tpl.is_active else 'não'}")
        if tpl.image_url:
            lines.append(f"Imagem: {tpl.image_url}")
        lines.append(f"Variáveis: {', '.join(tpl.variables) or '(nenhuma)'}")
        lines.append("")
        lines.append("Conteúdo:")
        lines.append(tpl.content)
        lines.append("")
        lines.append("Botões:")
        for b in buttons:
            if b.screen_key == tpl.screen_key:
                lines.append(
                    f"  [L{b.row_order}C{b.col_order}] "
                    f"{b.emoji or ''} {b.label} → "
                    f"{b.action_type}:{b.action_value} "
                    f"({'visível' if b.is_visible else 'oculto'})"
                )
        lines.append("")

    content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        content,
        filename=f"personalizacao_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt",
    )
    await cb.message.answer_document(
        file,
        caption=(
            f"📊 <b>Exportação de Personalização</b>\n\n"
            f"💬 Telas: {len(templates)}\n"
            f"🔘 Botões: {len(buttons)}"
        ),
    )
    await cb.answer("Exportação gerada.")


# ============================================================
# HELPERS PARA O BOT DO CLIENTE
# ============================================================
async def get_rendered_message(screen_key: str, variables: dict) -> tuple[str | None, InlineKeyboardMarkup | None]:
    """
    Retorna o conteúdo renderizado + teclado da tela.
    Retorna (None, None) se a tela estiver desativada.

    Ex.:
        text, kb = await get_rendered_message("welcome", {
            "saudacao": "Bom dia", "nome_usuario": user.full_name, ...
        })
    """
    async with AsyncSessionLocal() as s:
        tpl = (await s.execute(
            select(MessageTemplate).where(MessageTemplate.screen_key == screen_key)
        )).scalar_one_or_none()
        if not tpl or not tpl.is_active:
            return (None, None)

        buttons = (await s.execute(
            select(ButtonConfig)
            .where(ButtonConfig.screen_key == screen_key)
            .where(ButtonConfig.is_visible == True)
            .order_by(ButtonConfig.row_order, ButtonConfig.col_order)
        )).scalars().all()

    try:
        rendered = tpl.content.format(**{k: variables.get(k, f"{{{k}}}") for k in extract_variables(tpl.content)})
    except Exception:
        rendered = tpl.content

    settings = await get_settings()
    bot_username = getattr(settings, "bot_username", None)
    bot_link = f"https://t.me/{bot_username}" if bot_username else ""

    kb = build_buttons_keyboard(list(buttons), bot_link=bot_link)
    return (rendered, kb)


async def get_rendered_image(screen_key: str) -> str | None:
    async with AsyncSessionLocal() as s:
        tpl = (await s.execute(
            select(MessageTemplate).where(MessageTemplate.screen_key == screen_key)
        )).scalar_one_or_none()
    return tpl.image_url if tpl else None
