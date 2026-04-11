import logging
import os
import asyncio
import random
import string
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import aiosqlite
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from telegram.constants import ParseMode

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================== КОНФИГУРАЦИЯ ========================

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x]
YOOMONEY_TOKEN = os.getenv('YOOMONEY_ACCESS_TOKEN')
YOOMONEY_WALLET = os.getenv('YOOMONEY_WALLET')

# База данных
DB_NAME = 'metro_shop.db'

# Цены на товары и услуги
PRICES = {
    'uc_60': {'amount': 60, 'price': 150, 'emoji': '💎', 'name': '60 UC'},
    'uc_300': {'amount': 300, 'price': 700, 'emoji': '💎', 'name': '300 UC'},
    'uc_600': {'amount': 600, 'price': 1300, 'emoji': '💎', 'name': '600 UC'},
    'uc_1500': {'amount': 1500, 'price': 3000, 'emoji': '💎', 'name': '1500 UC'},
    'uc_3000': {'amount': 3000, 'price': 5800, 'emoji': '💎', 'name': '3000 UC'},
    'uc_6000': {'amount': 6000, 'price': 11000, 'emoji': '💎', 'name': '6000 UC'},
    
    'rp_pass': {'name': 'Royale Pass', 'price': 800, 'emoji': '🎫'},
    'rp_elite': {'name': 'Elite Pass Plus', 'price': 2000, 'emoji': '👑'},
    
    'boost_bronze': {'name': 'Прокачка до Bronze', 'price': 500, 'emoji': '🥉'},
    'boost_silver': {'name': 'Прокачка до Silver', 'price': 1000, 'emoji': '🥈'},
    'boost_gold': {'name': 'Прокачка до Gold', 'price': 2000, 'emoji': '🥇'},
    'boost_platinum': {'name': 'Прокачка до Platinum', 'price': 3500, 'emoji': '💠'},
    'boost_diamond': {'name': 'Прокачка до Diamond', 'price': 5500, 'emoji': '💎'},
    'boost_crown': {'name': 'Прокачка до Crown', 'price': 8000, 'emoji': '👑'},
    'boost_ace': {'name': 'Прокачка до Ace', 'price': 12000, 'emoji': '🏆'},
    
    'metro_escort': {'name': 'Сопровождение Metro (1 игра)', 'price': 300, 'emoji': '🚇'},
    'metro_farm': {'name': 'Фарм Metro (5 игр)', 'price': 1200, 'emoji': '⛏️'},
}

# ======================== YOOMONEY API ========================

class YooMoneyAPI:
    """Класс для работы с ЮMoney API"""
    
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://yoomoney.ru/api"
    
    def get_operation_history(self, label: Optional[str] = None, records: int = 100) -> dict:
        """Получение истории операций"""
        url = f"{self.base_url}/operation-history"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        data = {
            "records": records
        }
        
        if label:
            data["label"] = label
        
        try:
            response = requests.post(url, headers=headers, data=data)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"YooMoney API error: {response.status_code} - {response.text}")
                return {"operations": []}
        except Exception as e:
            logger.error(f"YooMoney API exception: {e}")
            return {"operations": []}
    
    def check_payment(self, label: str, amount: float) -> tuple[bool, float]:
        """Проверка платежа по метке"""
        history = self.get_operation_history(label=label)
        
        if "operations" not in history:
            return False, 0
        
        for operation in history.get("operations", []):
            # Проверяем что это входящий платеж
            if operation.get("direction") != "in":
                continue
            
            # Проверяем статус
            if operation.get("status") != "success":
                continue
            
            # Проверяем метку
            if operation.get("label") != label:
                continue
            
            # Проверяем сумму
            operation_amount = float(operation.get("amount", 0))
            if operation_amount >= amount:
                return True, operation_amount
        
        return False, 0
    
    def create_payment_form_url(self, receiver: str, amount: float, label: str, comment: str = "") -> str:
        """Создание ссылки для оплаты"""
        base_url = "https://yoomoney.ru/quickpay/confirm.xml"
        
        params = {
            "receiver": receiver,
            "quickpay-form": "shop",
            "targets": comment or "Оплата в Metro Shop",
            "paymentType": "SB",
            "sum": amount,
            "label": label
        }
        
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"{base_url}?{query_string}"

# Инициализация YooMoney API
yoomoney = YooMoneyAPI(YOOMONEY_TOKEN) if YOOMONEY_TOKEN else None

# ======================== БАЗА ДАННЫХ ========================

async def init_db():
    """Инициализация базы данных"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_orders INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                user_id INTEGER,
                username TEXT,
                item_key TEXT,
                item_name TEXT,
                price REAL,
                status TEXT,
                payment_label TEXT,
                pubg_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        await db.commit()

async def add_user(user_id: int, username: str, first_name: str):
    """Добавление пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
        ''', (user_id, username, first_name))
        await db.commit()

async def create_order(user_id: int, username: str, item_key: str, item_name: str, price: float, pubg_id: str = None):
    """Создание заказа"""
    order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
    payment_label = f"METRO_{order_id}"
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT INTO orders (order_id, user_id, username, item_key, item_name, price, status, payment_label, pubg_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (order_id, user_id, username, item_key, item_name, price, 'awaiting_payment', payment_label, pubg_id))
        await db.commit()
    
    return order_id, payment_label

async def get_order(order_id: str):
    """Получение информации о заказе"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM orders WHERE order_id = ?', (order_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_order_by_label(label: str):
    """Получение заказа по метке платежа"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM orders WHERE payment_label = ?', (label,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_order_status(order_id: str, status: str):
    """Обновление статуса заказа"""
    async with aiosqlite.connect(DB_NAME) as db:
        if status == 'paid':
            await db.execute('''
                UPDATE orders SET status = ?, paid_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
            ''', (status, order_id))
        elif status == 'completed':
            await db.execute('''
                UPDATE orders SET status = ?, completed_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
            ''', (status, order_id))
        else:
            await db.execute('UPDATE orders SET status = ? WHERE order_id = ?', (status, order_id))
        await db.commit()

async def get_user_orders(user_id: int, limit: int = 10):
    """Получение заказов пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT * FROM orders WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        ''', (user_id, limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def get_pending_orders():
    """Получение ожидающих оплаты заказов"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT * FROM orders WHERE status = 'awaiting_payment'
            AND created_at > datetime('now', '-24 hours')
        ''') as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def update_user_stats(user_id: int, amount: float):
    """Обновление статистики пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            UPDATE users SET total_orders = total_orders + 1, total_spent = total_spent + ?
            WHERE user_id = ?
        ''', (amount, user_id))
        await db.commit()

async def get_stats():
    """Получение статистики"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        
        # Всего пользователей
        async with db.execute('SELECT COUNT(*) as count FROM users') as cursor:
            total_users = (await cursor.fetchone())['count']
        
        # Всего заказов
        async with db.execute('SELECT COUNT(*) as count FROM orders') as cursor:
            total_orders = (await cursor.fetchone())['count']
        
        # Выполненных заказов
        async with db.execute("SELECT COUNT(*) as count FROM orders WHERE status = 'completed'") as cursor:
            completed_orders = (await cursor.fetchone())['count']
        
        # Общая выручка
        async with db.execute("SELECT SUM(price) as sum FROM orders WHERE status = 'completed'") as cursor:
            total_revenue = (await cursor.fetchone())['sum'] or 0
        
        # Заказов сегодня
        async with db.execute('''
            SELECT COUNT(*) as count FROM orders 
            WHERE DATE(created_at) = DATE('now')
        ''') as cursor:
            today_orders = (await cursor.fetchone())['count']
        
        return {
            'total_users': total_users,
            'total_orders': total_orders,
            'completed_orders': completed_orders,
            'total_revenue': total_revenue,
            'today_orders': today_orders
        }

# ======================== КЛАВИАТУРЫ ========================

def get_main_menu():
    keyboard = [
        [
            InlineKeyboardButton("💎 Купить UC", callback_data="buy_uc"),
            InlineKeyboardButton("🎫 Проходки", callback_data="buy_passes")
        ],
        [
            InlineKeyboardButton("📈 Прокачка рейтинга", callback_data="boost_rank"),
            InlineKeyboardButton("🚇 Metro Royale", callback_data="metro_services")
        ],
        [
            InlineKeyboardButton("💼 Мои заказы", callback_data="my_orders"),
            InlineKeyboardButton("ℹ️ О нас", callback_data="about")
        ],
        [
            InlineKeyboardButton("❓ FAQ", callback_data="faq"),
            InlineKeyboardButton("💬 Поддержка", callback_data="support")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_uc_menu():
    keyboard = [
        [
            InlineKeyboardButton("💎 60 UC — 150₽", callback_data="uc_60"),
            InlineKeyboardButton("💎 300 UC — 700₽", callback_data="uc_300")
        ],
        [
            InlineKeyboardButton("💎 600 UC — 1,300₽", callback_data="uc_600"),
            InlineKeyboardButton("💎 1500 UC — 3,000₽", callback_data="uc_1500")
        ],
        [
            InlineKeyboardButton("💎 3000 UC — 5,800₽", callback_data="uc_3000"),
            InlineKeyboardButton("💎 6000 UC — 11,000₽", callback_data="uc_6000")
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_passes_menu():
    keyboard = [
        [InlineKeyboardButton("🎫 Royale Pass — 800₽", callback_data="rp_pass")],
        [InlineKeyboardButton("👑 Elite Pass Plus — 2,000₽", callback_data="rp_elite")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_boost_menu():
    keyboard = [
        [
            InlineKeyboardButton("🥉 Bronze — 500₽", callback_data="boost_bronze"),
            InlineKeyboardButton("🥈 Silver — 1,000₽", callback_data="boost_silver")
        ],
        [
            InlineKeyboardButton("🥇 Gold — 2,000₽", callback_data="boost_gold"),
            InlineKeyboardButton("💠 Platinum — 3,500₽", callback_data="boost_platinum")
        ],
        [
            InlineKeyboardButton("💎 Diamond — 5,500₽", callback_data="boost_diamond"),
            InlineKeyboardButton("👑 Crown — 8,000₽", callback_data="boost_crown")
        ],
        [InlineKeyboardButton("🏆 Ace — 12,000₽", callback_data="boost_ace")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_metro_menu():
    keyboard = [
        [InlineKeyboardButton("🚇 Сопровождение (1 игра) — 300₽", callback_data="metro_escort")],
        [InlineKeyboardButton("⛏️ Фарм Metro (5 игр) — 1,200₽", callback_data="metro_farm")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_button():
    keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]]
    return InlineKeyboardMarkup(keyboard)

def get_payment_menu(order_id: str):
    keyboard = [
        [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_payment_{order_id}")],
        [InlineKeyboardButton("❌ Отменить заказ", callback_data=f"cancel_order_{order_id}")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_order_menu(order_id: str):
    keyboard = [
        [InlineKeyboardButton("✅ Заказ выполнен", callback_data=f"admin_complete_{order_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ======================== ТЕКСТЫ ========================

WELCOME_TEXT = """
🎮 <b>Добро пожаловать в Metro Shop PUBG Mobile!</b>

💎 Ваш надежный магазин игровых услуг:
├ 💰 Пополнение UC
├ 🎫 Покупка проходок
├ 📈 Прокачка рейтинга
├ 🚇 Услуги Metro Royale
└ 🎁 Эксклюзивные скины

✨ <b>Преимущества работы с нами:</b>
├ ⚡ Быстрое выполнение (5-30 мин)
├ 🛡️ Безопасные сделки
├ 💯 Гарантия возврата
├ 🎯 Более 5000 довольных клиентов
├ 💳 Автоматическая оплата через ЮMoney
└ 💬 Поддержка 24/7

📱 Выберите нужный раздел в меню ниже! 👇
"""

ABOUT_TEXT = """
ℹ️ <b>О Metro Shop</b>

🏪 <b>Metro Shop</b> — профессиональный сервис для PUBG Mobile

🚇 <b>Metro Royale</b> — PvE/PvP режим:
├ 🗺️ Исследование локаций
├ 💼 Сбор ценного лута
├ ⚔️ Сражения с ботами и игроками
├ 🚁 Эвакуация с добычей
└ 💰 Продажа предметов

✅ <b>Наши гарантии:</b>
├ 🔐 Безопасность аккаунта
├ ⚡ Быстрое выполнение
├ 💬 Поддержка 24/7
├ 💸 Честные цены
└ 🔄 Возврат при проблемах

📊 <b>Статистика:</b>
├ 👥 5000+ клиентов
├ ⭐ Рейтинг 4.9/5.0
└ 📈 Работаем с 2020 года
"""

FAQ_TEXT = """
❓ <b>Частые вопросы (FAQ)</b>

<b>Q: Безопасно ли передавать данные аккаунта?</b>
A: ✅ Да! Мы не сохраняем ваши данные. 5000+ заказов без банов.

<b>Q: Сколько времени занимает пополнение UC?</b>
A: ⚡ 5-30 минут после оплаты.

<b>Q: Какие способы оплаты?</b>
A: 💳 ЮMoney с автопроверкой платежа.

<b>Q: Что если UC не пришли?</b>
A: 📞 Свяжитесь с поддержкой — решим за 1 час.

<b>Q: Могут ли забанить?</b>
A: 🛡️ Нет! Используем официальные методы.

<b>Q: Стоимость прокачки?</b>
A: 📊 От 500₽ в зависимости от ранга.

<b>Q: Что такое Metro Royale?</b>
A: 🚇 PvE/PvP режим для фарма ценностей.

<b>Q: Есть гарантия возврата?</b>
A: 💯 Да! Полный возврат при проблемах.
"""

SUPPORT_TEXT = """
💬 <b>Служба поддержки</b>

👨‍💼 <b>Операторы онлайн 24/7!</b>

📱 <b>Контакты:</b>
├ 💬 Telegram: @MetroShopSupport
├ 📧 Email: support@metroshop.ru
└ ⚡ Ответ до 15 минут

❓ <b>Вопросы:</b>
├ 💰 Проблемы с оплатой
├ ⏱️ Статус заказа
├ 🔧 Техподдержка
├ 💎 Консультации
└ 📝 Жалобы и предложения
"""

# Хранилище для временных данных
user_data_storage = {}

# ======================== ОБРАБОТЧИКИ ========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await add_user(user.id, user.username, user.first_name)
    
    await update.message.reply_text(
        WELCOME_TEXT,
        reply_markup=get_main_menu(),
        parse_mode=ParseMode.HTML
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🆘 <b>Помощь по боту Metro Shop</b>

📱 <b>Команды:</b>
├ /start — Главное меню
├ /help — Справка
├ /orders — Мои заказы
└ /stats — Статистика (admin)

🛍️ <b>Как заказать:</b>
1️⃣ Выберите раздел
2️⃣ Выберите товар
3️⃣ Введите PUBG ID
4️⃣ Оплатите через ЮMoney
5️⃣ Проверьте оплату в боте
6️⃣ Получите заказ!

💬 Поддержка: @MetroShopSupport
⚡ Работаем 24/7!
"""
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu())

async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    orders = await get_user_orders(user_id, 10)
    
    if not orders:
        text = "💼 <b>Мои заказы</b>\n\n📭 У вас пока нет заказов."
    else:
        text = "💼 <b>Ваши последние заказы:</b>\n\n"
        for order in orders:
            status_emoji = {
                'awaiting_payment': '⏳',
                'paid': '✅',
                'processing': '🔄',
                'completed': '✅',
                'cancelled': '❌'
            }.get(order['status'], '❓')
            
            status_text = {
                'awaiting_payment': 'Ожидает оплаты',
                'paid': 'Оплачен, в работе',
                'processing': 'В работе',
                'completed': 'Выполнен',
                'cancelled': 'Отменен'
            }.get(order['status'], 'Неизвестно')
            
            text += f"""
🔖 <b>#{order['order_id']}</b>
├ 📦 {order['item_name']}
├ 💰 {order['price']}₽
├ 📊 {status_emoji} {status_text}
└ 📅 {order['created_at'][:16]}

"""
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu())

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Команда только для администраторов")
        return
    
    stats = await get_stats()
    
    text = f"""
📊 <b>Статистика Metro Shop</b>

👥 <b>Пользователи:</b> {stats['total_users']}
📦 <b>Всего заказов:</b> {stats['total_orders']}
✅ <b>Выполнено:</b> {stats['completed_orders']}
💰 <b>Общая выручка:</b> {stats['total_revenue']:.2f}₽
📅 <b>Заказов сегодня:</b> {stats['today_orders']}

📈 <b>Средний чек:</b> {(stats['total_revenue'] / stats['completed_orders'] if stats['completed_orders'] > 0 else 0):.2f}₽
"""
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    # Главное меню
    if data == "main_menu":
        await query.edit_message_text(
            WELCOME_TEXT,
            reply_markup=get_main_menu(),
            parse_mode=ParseMode.HTML
        )
    
    # Покупка UC
    elif data == "buy_uc":
        text = """
💎 <b>Пополнение UC (Unknown Cash)</b>

Выберите нужное количество UC:

⚡ <b>Скорость:</b> 5-30 минут
🛡️ <b>Безопасность:</b> 100% гарантия
💯 <b>Способ:</b> Официальное пополнение
💳 <b>Оплата:</b> ЮMoney (автопроверка)

<i>Нажмите на нужный пакет</i> 👇
"""
        await query.edit_message_text(text, reply_markup=get_uc_menu(), parse_mode=ParseMode.HTML)
    
    # Покупка проходок
    elif data == "buy_passes":
        text = """
🎫 <b>Покупка Royale Pass</b>

📅 Доступные проходки текущего сезона:

🎫 <b>Royale Pass</b>
├ ✨ Базовая версия
└ ⏱️ Мгновенная активация

👑 <b>Elite Pass Plus</b>
├ 💎 Премиум версия
├ ⚡ +25 уровней сразу
└ ⏱️ Мгновенная активация
"""
        await query.edit_message_text(text, reply_markup=get_passes_menu(), parse_mode=ParseMode.HTML)
    
    # Прокачка рейтинга
    elif data == "boost_rank":
        text = """
📈 <b>Прокачка рейтинга PUBG Mobile</b>

🎮 <b>Условия:</b>
├ 👨‍💼 Профессиональные бустеры
├ ⚡ Срок: 1-7 дней
├ 🛡️ Без читов
├ 🎯 K/D сохраняется
└ 💯 Гарантия результата

<i>Выберите желаемый ранг</i> 👇
"""
        await query.edit_message_text(text, reply_markup=get_boost_menu(), parse_mode=ParseMode.HTML)
    
    # Metro Royale
    elif data == "metro_services":
        text = """
🚇 <b>Услуги Metro Royale</b>

🗺️ <b>О режиме:</b>
├ 🎯 PvE/PvP выживание
├ 💼 Сбор лута
├ ⚔️ Сражения
├ 🚁 Эвакуация
└ 💰 Продажа предметов

✨ <b>Наши услуги:</b>

🚇 <b>Сопровождение (1 игра)</b>
├ Опытный игрок
├ Гарантия эвакуации
└ Делёжка 50/50

⛏️ <b>Фарм (5 игр)</b>
├ Эффективный фарм
├ Максимум добычи
└ Безопасность
"""
        await query.edit_message_text(text, reply_markup=get_metro_menu(), parse_mode=ParseMode.HTML)
    
    # О магазине
    elif data == "about":
        await query.edit_message_text(ABOUT_TEXT, reply_markup=get_back_button(), parse_mode=ParseMode.HTML)
    
    # FAQ
    elif data == "faq":
        await query.edit_message_text(FAQ_TEXT, reply_markup=get_back_button(), parse_mode=ParseMode.HTML)
    
    # Поддержка
    elif data == "support":
        keyboard = [
            [InlineKeyboardButton("💬 Написать оператору", url="https://t.me/MetroShopSupport")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        await query.edit_message_text(
            SUPPORT_TEXT,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    # Мои заказы
    elif data == "my_orders":
        orders = await get_user_orders(user_id, 5)
        
        if not orders:
            text = "💼 <b>Мои заказы</b>\n\n📭 У вас пока нет заказов."
        else:
            text = "💼 <b>Ваши последние заказы:</b>\n\n"
            for order in orders:
                status_emoji = {
                    'awaiting_payment': '⏳',
                    'paid': '✅',
                    'processing': '🔄',
                    'completed': '✅',
                    'cancelled': '❌'
                }.get(order['status'], '❓')
                
                text += f"""
🔖 <b>#{order['order_id']}</b>
├ 📦 {order['item_name']}
├ 💰 {order['price']}₽
└ 📊 {status_emoji} {order['status']}

"""
        
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())
    
    # Выбор товара
    elif data in PRICES:
        item = PRICES[data]
        user_data_storage[user_id] = {'item_key': data}
        
        text = f"""
{item['emoji']} <b>Подтверждение заказа</b>

📦 <b>Товар:</b> {item['name']}
💰 <b>Цена:</b> {item['price']}₽

⚠️ <b>ВАЖНО: Введите ваш PUBG ID</b>

Отправьте ID вашего аккаунта PUBG Mobile следующим сообщением.

📍 <b>Где найти PUBG ID:</b>
1. Откройте PUBG Mobile
2. Нажмите на профиль
3. ID под ником

<i>Пример: 5123456789</i>
"""
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отменить", callback_data="main_menu")]])
        )
    
    # Проверка оплаты
    elif data.startswith('check_payment_'):
        order_id = data.replace('check_payment_', '')
        order = await get_order(order_id)
        
        if not order:
            await query.answer("❌ Заказ не найден", show_alert=True)
            return
        
        if order['status'] != 'awaiting_payment':
            await query.answer(f"ℹ️ Статус: {order['status']}", show_alert=True)
            return
        
        await query.answer("🔄 Проверяю платеж...", show_alert=False)
        
        # Проверяем платеж через API
        if yoomoney:
            is_paid, amount = yoomoney.check_payment(order['payment_label'], order['price'])
            
            if is_paid:
                await update_order_status(order_id, 'paid')
                await update_user_stats(user_id, order['price'])
                
                text = f"""
✅ <b>Платеж получен!</b>

🎉 Заказ #{order_id} успешно оплачен!

📦 <b>Товар:</b> {order['item_name']}
💰 <b>Сумма:</b> {amount}₽
🆔 <b>PUBG ID:</b> {order['pubg_id']}

⏳ <b>Статус:</b> Передан в обработку

Ваш заказ будет выполнен в течение 5-30 минут!
Мы отправим уведомление когда товар будет доставлен.

💬 Вопросы: @MetroShopSupport
"""
                
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())
                
                # Уведомляем админов
                for admin_id in ADMIN_IDS:
                    try:
                        admin_text = f"""
🔔 <b>Новый оплаченный заказ!</b>

📦 Заказ: #{order_id}
👤 @{order['username'] or 'нет username'}
🆔 User ID: {user_id}
💎 Товар: {order['item_name']}
💰 Сумма: {order['price']}₽
🎮 PUBG ID: {order['pubg_id']}

⚡ Требуется выполнение!
"""
                        await context.bot.send_message(
                            chat_id=admin_id,
                            text=admin_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=get_admin_order_menu(order_id)
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки админу: {e}")
            else:
                await query.answer("⏳ Платеж еще не получен. Попробуйте через минуту.", show_alert=True)
        else:
            await query.answer("❌ API ЮMoney недоступен", show_alert=True)
    
    # Отмена заказа
    elif data.startswith('cancel_order_'):
        order_id = data.replace('cancel_order_', '')
        await update_order_status(order_id, 'cancelled')
        
        await query.edit_message_text(
            "❌ <b>Заказ отменен</b>",
            reply_markup=get_main_menu(),
            parse_mode=ParseMode.HTML
        )
    
    # Админ - заказ выполнен
    elif data.startswith('admin_complete_'):
        if user_id not in ADMIN_IDS:
            await query.answer("❌ Только для админов", show_alert=True)
            return
        
        order_id = data.replace('admin_complete_', '')
        order = await get_order(order_id)
        
        if order:
            await update_order_status(order_id, 'completed')
            await query.edit_message_text(f"✅ Заказ #{order_id} выполнен и закрыт!")
            
            # Уведомляем клиента
            try:
                await context.bot.send_message(
                    chat_id=order['user_id'],
                    text=f"""
🎉 <b>Заказ выполнен!</b>

Ваш заказ #{order_id} успешно выполнен!

📦 <b>Товар:</b> {order['item_name']}
🎮 <b>PUBG ID:</b> {order['pubg_id']}

Проверьте ваш аккаунт PUBG Mobile!

⭐ Спасибо за покупку! Будем рады видеть вас снова!
""",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass

async def handle_pubg_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_data_storage:
        return
    
    pubg_id = update.message.text.strip()
    
    if not pubg_id.isdigit() or len(pubg_id) < 8:
        await update.message.reply_text(
            "❌ <b>Неверный формат PUBG ID</b>\n\nID должен состоять из цифр (минимум 8).\n\n<i>Пример: 5123456789</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    item_key = user_data_storage[user_id]['item_key']
    item = PRICES[item_key]
    
    order_id, payment_label = await create_order(
        user_id=user_id,
        username=update.effective_user.username,
        item_key=item_key,
        item_name=item['name'],
        price=item['price'],
        pubg_id=pubg_id
    )
    
    del user_data_storage[user_id]
    
    # Генерируем ссылку для оплаты
    if yoomoney:
        payment_url = yoomoney.create_payment_form_url(
            receiver=YOOMONEY_WALLET,
            amount=item['price'],
            label=payment_label,
            comment=f"Metro Shop - {item['name']}"
        )
    else:
        payment_url = f"https://yoomoney.ru/to/{YOOMONEY_WALLET}"
    
    text = f"""
💳 <b>Оплата заказа #{order_id}</b>

📦 <b>Товар:</b> {item['name']}
🆔 <b>PUBG ID:</b> {pubg_id}
💰 <b>К оплате:</b> {item['price']}₽

🔗 <b>Ссылка для оплаты:</b>
<a href="{payment_url}">Оплатить через ЮMoney</a>

🏷️ <b>Метка платежа (обязательно!):</b>
<code>{payment_label}</code>

📝 <b>Инструкция:</b>
1️⃣ Перейдите по ссылке
2️⃣ Выберите способ оплаты
3️⃣ Оплатите {item['price']}₽
4️⃣ В комментарии укажите: <code>{payment_label}</code>
5️⃣ Вернитесь и нажмите "Проверить оплату"

⚠️ <b>ВАЖНО:</b> Обязательно укажите метку платежа!

⏰ Платеж проверяется автоматически (10-30 сек)
🕐 Заказ действителен 24 часа
"""
    
    keyboard = [
        [InlineKeyboardButton("💳 Перейти к оплате", url=payment_url)],
        [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_payment_{order_id}")],
        [InlineKeyboardButton("❌ Отменить заказ", callback_data=f"cancel_order_{order_id}")]
    ]
    
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True
    )

async def check_pending_payments(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая проверка ожидающих платежей"""
    if not yoomoney:
        return
    
    pending_orders = await get_pending_orders()
    
    for order in pending_orders:
        is_paid, amount = yoomoney.check_payment(order['payment_label'], order['price'])
        
        if is_paid:
            await update_order_status(order['order_id'], 'paid')
            await update_user_stats(order['user_id'], order['price'])
            
            # Уведомляем пользователя
            try:
                text = f"""
✅ <b>Платеж получен!</b>

🎉 Ваш заказ #{order['order_id']} успешно оплачен!

📦 <b>Товар:</b> {order['item_name']}
💰 <b>Сумма:</b> {amount}₽

⏳ <b>Статус:</b> Передан в обработку

Ваш заказ будет выполнен в течение 5-30 минут!
"""
                await context.bot.send_message(
                    chat_id=order['user_id'],
                    text=text,
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
            
            # Уведомляем админов
            for admin_id in ADMIN_IDS:
                try:
                    admin_text = f"""
🔔 <b>Новый оплаченный заказ!</b>

📦 Заказ: #{order['order_id']}
👤 @{order['username'] or 'нет'}
🆔 User ID: {order['user_id']}
💎 Товар: {order['item_name']}
💰 Сумма: {order['price']}₽
🎮 PUBG ID: {order['pubg_id']}

⚡ Требуется выполнение!
"""
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=admin_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_admin_order_menu(order['order_id'])
                    )
                except:
                    pass

async def post_init(application: Application):
    await init_db()
    logger.info("✅ База данных инициализирована")
    
    if yoomoney:
        # Запускаем автопроверку каждые 30 секунд
        job_queue = application.job_queue
        job_queue.run_repeating(check_pending_payments, interval=30, first=10)
        logger.info("✅ Автопроверка платежей запущена")
    else:
        logger.warning("⚠️ YooMoney API недоступен - автопроверка отключена")

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("stats", stats_command))
    
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pubg_id))
    
    logger.info("🚀 Metro Shop Bot запущен!")
    logger.info(f"💳 ЮMoney: {YOOMONEY_WALLET}")
    logger.info(f"👨‍💼 Админы: {ADMIN_IDS}")
    logger.info(f"🔌 API: {'✅ Подключен' if yoomoney else '❌ Отключен'}")
    
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == '__main__':
    main()
