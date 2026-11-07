from sqlalchemy import (
    BigInteger, String, Float, Integer, DateTime, Boolean, Text, JSON, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime
from typing import Optional


class Base(DeclarativeBase):
    pass


# ========== MÓDULO 1: BASE ==========
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
    reserved_stock: Mapped[int] = mapped_column(Integer, default=0)
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    delivery_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    activation_link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_per_purchase: Mapped[int] = mapped_column(Integer, default=10)
    min_per_purchase: Mapped[int] = mapped_column(Integer, default=1)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    sold_count: Mapped[int] = mapped_column(Integer, default=0)
    min_stock_alert: Mapped[int] = mapped_column(Integer, default=0)
    max_stock: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    product_id: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    payment_method: Mapped[Optional[str]] = mapped_column(String(50), default="wallet")
    delivery_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    cancelled_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    confirmed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    cancelled_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Wallet(Base):
    __tablename__ = "wallets"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    balance: Mapped[float] = mapped_column(Float, default=0.0)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    bonus_amount: Mapped[float] = mapped_column(Float, default=0.0)
    credited_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(50), default="mercadopago")
    method: Mapped[str] = mapped_column(String(50), default="pix_mp")
    mp_payment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    external_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pix_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    qr_code_base64: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ticket_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cancelled_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    cancelled_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 2: SETTINGS E LOGS ==========
class BotSettings(Base):
    __tablename__ = "bot_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # Módulo 2
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
    # Módulo 4
    stock_alert_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stock_low_threshold: Mapped[int] = mapped_column(Integer, default=5)
    allow_out_of_stock_sales: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_restock_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Módulo 5
    stock_max_global: Mapped[int] = mapped_column(Integer, default=0)
    stock_reserve_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    stock_reserve_minutes: Mapped[int] = mapped_column(Integer, default=30)
    # Módulo 7
    cart_expiry_hours: Mapped[int] = mapped_column(Integer, default=48)
    cart_max_items_per_user: Mapped[int] = mapped_column(Integer, default=20)
    cart_auto_cleanup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Módulo 9
    mp_access_token: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    mp_public_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    recharge_min: Mapped[float] = mapped_column(Float, default=5.0)
    recharge_max: Mapped[float] = mapped_column(Float, default=500.0)
    recharge_expiration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    recharge_bonus_default: Mapped[float] = mapped_column(Float, default=0.0)
    recharge_daily_limit: Mapped[float] = mapped_column(Float, default=0.0)
    recharge_per_user_limit: Mapped[float] = mapped_column(Float, default=0.0)
    recharge_hours_start: Mapped[int] = mapped_column(Integer, default=0)
    recharge_hours_end: Mapped[int] = mapped_column(Integer, default=24)
    # Módulo 10
    welcome_bonus_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    welcome_bonus_amount: Mapped[float] = mapped_column(Float, default=0.0)
    first_purchase_bonus_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    first_purchase_bonus_percentage: Mapped[float] = mapped_column(Float, default=0.0)
    # Módulo 11
    bot_username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Módulo 12/13
    sales_channel_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    restock_channel_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    support_channel_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    novelties_channel_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    mandatory_channel_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    mandatory_group_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Módulo 13
    purchase_announcement_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    purchase_announcement_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    purchase_announcement_show_name: Mapped[bool] = mapped_column(Boolean, default=True)
    purchase_announcement_show_username: Mapped[bool] = mapped_column(Boolean, default=False)
    purchase_announcement_mask_id: Mapped[bool] = mapped_column(Boolean, default=True)
    purchase_announcement_button_text: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    purchase_announcement_button_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    purchase_announcement_image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    purchase_announcement_send_image: Mapped[bool] = mapped_column(Boolean, default=False)
    purchase_announcement_filter_products: Mapped[bool] = mapped_column(Boolean, default=False)
    purchase_announcement_product_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    restock_announcement_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    restock_announcement_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    restock_announcement_button_text: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    restock_announcement_button_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    restock_announcement_image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    restock_announcement_send_image: Mapped[bool] = mapped_column(Boolean, default=False)
    restock_announcement_min_stock: Mapped[int] = mapped_column(Integer, default=1)
    restock_announcement_min_restock_qty: Mapped[int] = mapped_column(Integer, default=1)
    # Módulo 14
    ranking_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ranking_public: Mapped[bool] = mapped_column(Boolean, default=True)
    ranking_public_size: Mapped[int] = mapped_column(Integer, default=10)
    ranking_default_period: Mapped[str] = mapped_column(String(20), default="month")
    # Módulo 15
    affiliate_approval_mode: Mapped[str] = mapped_column(String(20), default="auto")
    points_per_referral: Mapped[int] = mapped_column(Integer, default=10)
    points_per_purchase: Mapped[int] = mapped_column(Integer, default=0)
    points_conversion_rate: Mapped[float] = mapped_column(Float, default=100.0)
    min_points_to_convert: Mapped[int] = mapped_column(Integer, default=100)
    max_points_per_conversion: Mapped[int] = mapped_column(Integer, default=0)
    min_purchase_for_points: Mapped[float] = mapped_column(Float, default=0.0)
    commission_percent: Mapped[float] = mapped_column(Float, default=0.0)
    max_referrals_per_user: Mapped[int] = mapped_column(Integer, default=0)
    # Módulo 18
    support_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    support_auto_assign: Mapped[bool] = mapped_column(Boolean, default=False)
    support_auto_close_days: Mapped[int] = mapped_column(Integer, default=7)
    support_welcome_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Módulo 19
    require_2fa_admins: Mapped[bool] = mapped_column(Boolean, default=False)
    max_login_attempts: Mapped[int] = mapped_column(Integer, default=5)
    login_lockout_minutes: Mapped[int] = mapped_column(Integer, default=15)
    admin_session_hours: Mapped[int] = mapped_column(Integer, default=24)
    # Meta
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


# ========== MÓDULO 3: WALLET TRANSACTIONS ==========
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


# ========== MÓDULO 4/5: STOCK MOVEMENTS ==========
class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    movement_type: Mapped[str] = mapped_column(String(50))
    quantity: Mapped[int] = mapped_column(Integer)
    stock_before: Mapped[int] = mapped_column(Integer)
    stock_after: Mapped[int] = mapped_column(Integer)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 7: CART ==========
class CartItem(Base):
    __tablename__ = "cart_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    product_id: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# ========== MÓDULO 9: RECHARGE BONUS TIERS ==========
class RechargeBonusTier(Base):
    __tablename__ = "recharge_bonus_tiers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    min_amount: Mapped[float] = mapped_column(Float)
    max_amount: Mapped[float] = mapped_column(Float)
    bonus_percentage: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 10: COUPONS ==========
class Coupon(Base):
    __tablename__ = "coupons"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(20))
    value: Mapped[float] = mapped_column(Float)
    min_purchase: Mapped[float] = mapped_column(Float, default=0.0)
    max_discount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    usage_limit: Mapped[int] = mapped_column(Integer, default=0)
    usage_per_user: Mapped[int] = mapped_column(Integer, default=0)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    applies_to: Mapped[str] = mapped_column(String(20), default="all")
    applies_to_products: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CouponUsage(Base):
    __tablename__ = "coupon_usages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coupon_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    order_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discount_amount: Mapped[float] = mapped_column(Float, default=0.0)
    used_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 11: DEEP LINKS ==========
class DeepLink(Base):
    __tablename__ = "deep_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    destination_type: Mapped[str] = mapped_column(String(50))
    destination_value: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    campaign: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    unique_users: Mapped[int] = mapped_column(Integer, default=0)
    registrations: Mapped[int] = mapped_column(Integer, default=0)
    purchases: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    created_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DeepLinkVisit(Base):
    __tablename__ = "deep_link_visits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deep_link_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    is_new_user: Mapped[bool] = mapped_column(Boolean, default=False)
    converted: Mapped[bool] = mapped_column(Boolean, default=False)
    visited_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 12: NOTIFICATIONS ==========
class NotificationSetting(Base):
    __tablename__ = "notification_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(100))
    icon: Mapped[str] = mapped_column(String(10), default="🔔")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    target_type: Mapped[str] = mapped_column(String(30), default="admins")
    channel_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    template: Mapped[str] = mapped_column(Text)
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    send_image: Mapped[bool] = mapped_column(Boolean, default=False)
    frequency: Mapped[str] = mapped_column(String(20), default="immediate")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(50), index=True)
    target_id: Mapped[str] = mapped_column(String(50))
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 13: CHANNEL PUBLICATIONS ==========
class ChannelPublication(Base):
    __tablename__ = "channel_publications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    publication_type: Mapped[str] = mapped_column(String(20), index=True)
    reference_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    channel_id: Mapped[str] = mapped_column(String(50))
    message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_preview: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 14: REFERRALS ==========
class Referral(Base):
    __tablename__ = "referrals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referrer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    referred_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 15: AFFILIATES ==========
class Affiliate(Base):
    __tablename__ = "affiliates"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    referral_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    points_balance: Mapped[int] = mapped_column(Integer, default=0)
    total_points_earned: Mapped[int] = mapped_column(Integer, default=0)
    total_points_converted: Mapped[int] = mapped_column(Integer, default=0)
    total_balance_earned: Mapped[float] = mapped_column(Float, default=0.0)
    total_referrals: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_referrals: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AffiliateTransaction(Base):
    __tablename__ = "affiliate_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(String(500), default="")
    balance_before: Mapped[int] = mapped_column(Integer, default=0)
    balance_after: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 16: CUSTOMIZATION ==========
class MessageTemplate(Base):
    __tablename__ = "message_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    screen_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(String(30), default="main")
    variables: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MessageTemplateHistory(Base):
    __tablename__ = "message_template_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(Integer, index=True)
    old_content: Mapped[str] = mapped_column(Text)
    new_content: Mapped[str] = mapped_column(Text)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    edited_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ButtonConfig(Base):
    __tablename__ = "button_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    screen_key: Mapped[str] = mapped_column(String(64), index=True)
    button_key: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    action_type: Mapped[str] = mapped_column(String(20), default="callback")
    action_value: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    row_order: Mapped[int] = mapped_column(Integer, default=0)
    col_order: Mapped[int] = mapped_column(Integer, default=0)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    admin_only: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ButtonConfigHistory(Base):
    __tablename__ = "button_config_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    button_id: Mapped[int] = mapped_column(Integer, index=True)
    old_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    new_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    edited_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 17: MINI APP ==========
class MiniAppSettings(Base):
    __tablename__ = "miniapp_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    app_name: Mapped[str] = mapped_column(String(100), default="BELLAMY ADMIN")
    app_short_name: Mapped[str] = mapped_column(String(20), default="Bellamy")
    app_description: Mapped[str] = mapped_column(String(500), default="Painel administrativo Bellamy")
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    banner_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    favicon_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    primary_color: Mapped[str] = mapped_column(String(9), default="#6C5CE7")
    secondary_color: Mapped[str] = mapped_column(String(9), default="#A29BFE")
    accent_color: Mapped[str] = mapped_column(String(9), default="#00B894")
    background_color: Mapped[str] = mapped_column(String(9), default="#0F0F23")
    surface_color: Mapped[str] = mapped_column(String(9), default="#1A1A35")
    text_color: Mapped[str] = mapped_column(String(9), default="#FFFFFF")
    text_secondary_color: Mapped[str] = mapped_column(String(9), default="#B0B0C0")
    border_color: Mapped[str] = mapped_column(String(9), default="#2A2A45")
    success_color: Mapped[str] = mapped_column(String(9), default="#00B894")
    warning_color: Mapped[str] = mapped_column(String(9), default="#FDCB6E")
    danger_color: Mapped[str] = mapped_column(String(9), default="#E17055")
    theme_mode: Mapped[str] = mapped_column(String(20), default="auto")
    border_radius: Mapped[int] = mapped_column(Integer, default=12)
    spacing_unit: Mapped[int] = mapped_column(Integer, default=8)
    animation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    animation_speed: Mapped[str] = mapped_column(String(20), default="normal")
    font_family: Mapped[str] = mapped_column(String(100), default="Inter")
    font_size_base: Mapped[int] = mapped_column(Integer, default=14)
    font_size_heading: Mapped[int] = mapped_column(Integer, default=20)
    menu_items_visible: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    card_style: Mapped[str] = mapped_column(String(20), default="rounded")
    icon_style: Mapped[str] = mapped_column(String(20), default="filled")
    auth_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_token_expiry_hours: Mapped[int] = mapped_column(Integer, default=24)
    require_2fa: Mapped[bool] = mapped_column(Boolean, default=False)
    draft_theme: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    published_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MiniAppSession(Base):
    __tablename__ = "miniapp_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MiniAppThemeHistory(Base):
    __tablename__ = "miniapp_theme_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    old_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    new_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    action: Mapped[str] = mapped_column(String(30))
    admin_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 18: SUPPORT ==========
class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    assigned_to: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rating_comment: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    last_agent_reply_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SupportMessage(Base):
    __tablename__ = "support_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(Integer, index=True)
    sender_type: Mapped[str] = mapped_column(String(20))
    sender_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    media_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    is_internal_note: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SupportAgent(Base):
    __tablename__ = "support_agents"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    level: Mapped[str] = mapped_column(String(20), default="agent")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CannedResponse(Base):
    __tablename__ = "canned_responses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FAQEntry(Base):
    __tablename__ = "faq_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(String(300))
    answer: Mapped[str] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 19: SECURITY ==========
class AdminUser(Base):
    __tablename__ = "admin_users"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(30), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    extra_permissions: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    revoked_permissions: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    twofa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    twofa_secret: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    added_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    attempt_type: Mapped[str] = mapped_column(String(50))
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BackupRecord(Base):
    __tablename__ = "backup_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== MÓDULO 20: REPORTS ==========
class ReportExport(Base):
    __tablename__ = "report_exports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    report_type: Mapped[str] = mapped_column(String(50))
    format: Mapped[str] = mapped_column(String(20))
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    period_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rows_count: Mapped[int] = mapped_column(Integer, default=0)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
