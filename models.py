from sqlalchemy import (
    BigInteger, String, Float, Integer, DateTime, Boolean, Text, JSON, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime
from typing import Optional

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    username: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    block_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    blocked_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    promo_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    delivery_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    activation_link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_per_purchase: Mapped[int] = mapped_column(Integer, default=10)
    min_per_purchase: Mapped[int] = mapped_column(Integer, default=1)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    sold_count: Mapped[int] = mapped_column(Integer, default=0)
    min_stock_alert: Mapped[int] = mapped_column(Integer, default=0)  # 0 = usa global
    max_stock: Mapped[int] = mapped_column(Integer, default=0)        # 0 = sem limite
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    movement_type: Mapped[str] = mapped_column(String(50))  # restock / withdraw
    quantity: Mapped[int] = mapped_column(Integer)
    stock_before: Mapped[int] = mapped_column(Integer)
    stock_after: Mapped[int] = mapped_column(Integer)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    product_id: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class Wallet(Base):
    __tablename__ = "wallets"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    balance: Mapped[float] = mapped_column(Float, default=0.0)

class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[float] = mapped_column(Float)
    type: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(500), default="")
    balance_before: Mapped[float] = mapped_column(Float, default=0.0)
    balance_after: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class BotSettings(Base):
    __tablename__ = "bot_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    bot_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    maintenance_message: Mapped[str] = mapped_column(
        Text,
        default=(
            "🛠️ BELLAMY EM MANUTENÇÃO\n\n"
            "Estamos realizando melhorias em nossa plataforma.\n"
            "Voltaremos em breve.\n\n"
            "Agradecemos sua compreensão."
        ),
    )
    maintenance_support_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    maintenance_status_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    block_new_orders: Mapped[bool] = mapped_column(Boolean, default=False)
    block_new_recharges: Mapped[bool] = mapped_column(Boolean, default=False)
    block_purchases: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_order_query: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_profile_access: Mapped[bool] = mapped_column(Boolean, default=True)
    catalog_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cart_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    recharge_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    affiliates_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    points_conversion_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    registration_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    support_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Campos do Módulo 4 — estoque automático
    stock_alert_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stock_low_threshold: Mapped[int] = mapped_column(Integer, default=5)
    allow_out_of_stock_sales: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_restock_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

class AdminLog(Base):
    __tablename__ = "admin_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(255))
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
