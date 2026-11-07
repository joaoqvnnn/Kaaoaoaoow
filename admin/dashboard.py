from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta

from bot import dp, bot
from config import ADMIN_IDS
from database import AsyncSessionLocal
from models import User, Product, Order, Wallet, Payment

router = Router()
dp.include_router(router)

# ---------- Verificação de admin ----------
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ---------- Função que calcula TODOS os indicadores do dashboard ----------
async def get_dashboard_stats() -> dict:
    async with AsyncSessionLocal() as session:
        # Usuários
        total_users = (await session.execute(select(func.count(User.id)))).scalar() or 0
        active_users = (await session.execute(
            select(func.count(User.id)).where(User.is_active == True)
        )).scalar() or 0
        new_today = (await session.execute(
            select(func.count(User.id)).where(
                User.created_at >= datetime.utcnow().date()
            )
        )).scalar() or 0

        # Produtos
        total_products = (await session.execute(select(func.count(Product.id)))).scalar() or 0
        out_of_stock = (await session.execute(
            select(func.count(Product.id)).where(Product.stock <= 0)
        )).scalar() or 0

        # Vendas
        total_sales = (await session.execute(
            select(func.count(Order.id)).where(Order.status == "completed")
        )).scalar() or 0
        total_revenue = (await session.execute(
            select(func.coalesce(func.sum(Order.total), 0)).where(Order.status == "completed")
        )).scalar() or 0.0
        pending_orders = (await session.execute(
            select(func.count(Order.id)).where(Order.status == "pending")
        )).scalar() or 0
        cancelled_orders = (await session.execute(
            select(func.count(Order.id)).where(Order.status == "cancelled")
        )).scalar() or 0

        # Financeiro
        total_recharges = (await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "confirmed")
        )).scalar() or 0.0
        total_wallet_balance = (await session.execute(
            select(func.coalesce(func.sum(Wallet.balance), 0))
        )).scalar() or 0.0

        return {
            "total_users": total_users,
            "active_users": active_users,
            "new_today": new_today,
            "total_products": total_products,
            "out_of_stock": out_of_stock,
            "total_sales": total_sales,
            "total_revenue": total_revenue,
            "pending_orders": pending_orders,
            "cancelled_orders": cancelled_orders,
            "total_recharges": total_recharges,
            "total_wallet_balance": total_wallet_balance,
        }

# ---------- Teclado principal do Dashboard ----------
def dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Visão geral", callback_data="admin_overview")],
        [InlineKeyboardButton(text="📦 Produtos e estoque", callback_data="admin_products")],
        [InlineKeyboardButton(text="🛒 Pedidos e vendas", callback_data="admin_orders")],
        [InlineKeyboardButton(text="👥 Usuários", callback_data="admin_users")],
        [InlineKeyboardButton(text="💰 Financeiro", callback_data="admin_finance")],
        [InlineKeyboardButton(text="🔗 Deep Links", callback_data="admin_deeplinks")],
        [InlineKeyboardButton(text="📢 Canal de anúncios", callback_data="admin_channel")],
        [InlineKeyboardButton(text="🔔 Notificações", callback_data="admin_notifications")],
        [InlineKeyboardButton(text="💬 Editor de mensagens", callback_data="admin_messages")],
        [InlineKeyboardButton(text="🎨 Aparência e botões", callback_data="admin_appearance")],
        [InlineKeyboardButton(text="📱 Mini App", callback_data="admin_miniapp")],
        [InlineKeyboardButton(text="⚙️ Configurações", callback_data="admin_settings")],
        [InlineKeyboardButton(text="🛡️ Segurança", callback_data="admin_security")],
        [InlineKeyboardButton(text="📋 Relatórios", callback_data="admin_reports")],
        [InlineKeyboardButton(text="🔄 Atualizar", callback_data="admin_refresh")],
    ])

# ---------- Entrada no painel: /admin ----------
@router.message(F.text == "/admin")
async def admin_panel_entry(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Acesso negado.")
        return

    stats = await get_dashboard_stats()

    text = (
        "◈ BELLAMY | ADMINISTRAÇÃO\n\n"
        "Bem-vindo ao centro de controle da plataforma.\n\n"
        "📊 <b>Resumo do sistema</b>\n\n"
        f"👥 Usuários cadastrados: {stats['total_users']}\n"
        f"🟢 Usuários ativos: {stats['active_users']}\n"
        f"📦 Produtos cadastrados: {stats['total_products']}\n"
        f"🛒 Vendas realizadas: {stats['total_sales']}\n"
        f"💰 Faturamento: R$ {stats['total_revenue']:.2f}\n"
        f"💎 Recargas confirmadas: R$ {stats['total_recharges']:.2f}\n"
        f"📦 Produtos sem estoque: {stats['out_of_stock']}\n\n"
        "⚙️ Sistema: <b>Online</b>\n\n"
        "👇 Selecione um módulo abaixo:"
    )

    await message.answer(text, reply_markup=dashboard_keyboard())

# ---------- Callback do botão "🔄 Atualizar" ----------
@router.callback_query(F.data == "admin_refresh")
async def refresh_dashboard(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Acesso negado.", show_alert=True)
        return

    stats = await get_dashboard_stats()
    text = (
        "◈ BELLAMY | ADMINISTRAÇÃO\n\n"
        f"👥 Usuários: {stats['total_users']} | 🟢 Ativos: {stats['active_users']}\n"
        f"📦 Produtos: {stats['total_products']} | ⚠️ Esgotados: {stats['out_of_stock']}\n"
        f"🛒 Vendas: {stats['total_sales']} | Pendentes: {stats['pending_orders']}\n"
        f"💰 Faturamento: R$ {stats['total_revenue']:.2f}\n"
        f"💎 Recargas: R$ {stats['total_recharges']:.2f}\n\n"
        "👇 Selecione um módulo:"
    )
    await callback.message.edit_text(text, reply_markup=dashboard_keyboard())
    await callback.answer("Dashboard atualizado.")
