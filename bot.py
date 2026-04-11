import logging
import os
import asyncio
import random
import string
import requests
from datetime import datetime
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

# Цены на услуги Metro Royale
PRICES = {
    # Карты Metro Royale
    'map5': {'name': '💜 Карта 5', 'price': 200, 'emoji': '🗺️'},
    'map5_vip': {'name': '💜 Карта 5 VIP', 'price': 300, 'emoji': '👑'},
    'map7': {'name': '💜 Карта 7', 'price': 250, 'emoji': '🗺️'},
    'map7_vip': {'name': '💜 Карта 7 VIP', 'price': 350, 'emoji': '👑'},
    'map8': {'name': '💜 Карта 8', 'price': 300, 'emoji': '🗺️'},
    'map8_vip': {'name': '💜 Карта 8 VIP', 'price': 400, 'emoji': '👑'},
    
    # Сопроводы с экипировкой
    'escort_80': {'name': '🤗 Сопровод (80₽)', 'price': 80, 'emoji': '🚇', 'includes': '🪖🧥🎒'},
    'escort_100': {'name': '🤗 Сопровод (100₽)', 'price': 100, 'emoji': '🚇', 'includes': '🪖🧥🎒'},
    'escort_120': {'name': '🤗 Сопровод (120₽)', 'price': 120, 'emoji': '🚇', 'includes': '🪖🧥🎒'},
    
    # Дополнительные услуги
    'mk_tower': {'name': 'МК вышка', 'price': 30, 'emoji': '🔥'},
    'farm_5': {'name': '⛏️ Фарм Metro (5 игр)', 'price': 400, 'emoji': '⛏️'},
    'farm_10': {'name': '⛏️ Фарм Metro (10 игр)', 'price': 750, 'emoji': '⛏️'},
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
        
        data = {"records": records}
        if label:
            data["label"] = label
        
        try:
            response = requests.post(url, headers=headers, data=data, timeout=10)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"YooMoney API error: {response.status_code}")
                return {"operations": []}
        except Exception as e:
            logger.error(f"YooMoney API exception: {e}")
            return {"operations": []}
    
    def check_payment(self, label: str, amount: float) -> tuple[bool, float]:
        """Проверка платежа по метке"""
        history = self.get_operation_history(label=label)
        
        for operation in history.get("operations", []):
            if (operation.get("direction") == "in" and 
                operation.get("status") == "success" and 
                operation.get("label") == label):
                
                operation_amount = float(operation.get("amount", 0))
                if operation_amount >= amount:
                    return True, operation_amount
        
        return False, 0
    
    def create_payment_url(self, receiver: str, amount: float, label: str, comment: str = "") -> str:
        """Создание ссылки для оплаты"""
        params = {
            "receiver": receiver,
            "quickpay-form": "shop",
            "targets": comment or "Оплата в Metro Shop",
            "paymentType": "SB",
            "sum": amount,
            "label": label
        }
        
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"https://yoomoney.ru/quickpay/confirm.xml?{query_string}"

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
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
        ''', (user_id, username, first_name))
        await db.commit()

async def create_order(user_id: int, username: str, item_key: str, item_name: str, price: float, pubg_id: str = None):
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
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM orders WHERE order_id = ?', (order_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_order_status(order_id: str, status: str):
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
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT * FROM orders WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        ''', (user_id, limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def update_user_stats(user_id: int, amount: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            UPDATE users SET total_orders = total_orders + 1, total_spent = total_spent + ?
            WHERE user_id = ?
        ''', (amount, user_id))
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute('SELECT COUNT(*) as count FROM users') as cursor:
            total_users = (await cursor.fetchone())['count']
        
        async with db.execute('SELECT COUNT(*) as count FROM orders') as cursor:
            total_orders = (await cursor.fetchone())['count']
        
        async with db.execute("SELECT COUNT(*) as count FROM orders WHERE status = 'completed'") as cursor:
            completed_orders = (await cursor.fetchone())['count']
        
        async with db.execute("SELECT SUM(price) as sum FROM orders WHERE status = 'completed'") as cursor:
            total_revenue = (await cursor.fetchone())['sum'] or 0
        
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
        [InlineKeyboardButton("🗺️ Карты Metro Royale", callback_data="metro_maps")],
        [InlineKeyboardButton("🚇 Сопроводы с экипировкой", callback_data="escorts")],
        [InlineKeyboardButton("⛏️ Фарм услуги", callback_data="farm_services")],
        [InlineKeyboardButton("🔥 Дополнительные услуги", callback_data="extra_services")],
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

def get_maps_menu():
    keyboard = [
        [
            InlineKeyboardButton("💜 Карта 5 — 200₽", callback_data="map5"),
            InlineKeyboardButton("👑 Карта 5 VIP — 300₽", callback_data="map5_vip")
        ],
        [
            InlineKeyboardButton("💜 Карта 7 — 250₽", callback_data="map7"),
            InlineKeyboardButton("👑 Карта 7 VIP — 350₽", callback_data="map7_vip")
        ],
        [
            InlineKeyboardButton("💜 Карта 8 — 300₽", callback_data="map8"),
            InlineKeyboardButton("👑 Карта 8 VIP — 400₽", callback_data="map8_vip")
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_escorts_menu():
    keyboard = [
        [InlineKeyboardButton("🤗 Сопровод 80₽ (🪖🧥🎒)", callback_data="escort_80")],
        [InlineKeyboardButton("🤗 Сопровод 100₽ (🪖🧥🎒)", callback_data="escort_100")],
        [InlineKeyboardButton("🤗 Сопровод 120₽ (🪖🧥🎒)", callback_data="escort_120")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_farm_menu():
    keyboard = [
        [InlineKeyboardButton("⛏️ Фарм 5 игр — 400₽", callback_data="farm_5")],
        [InlineKeyboardButton("⛏️ Фарм 10 игр — 750₽", callback_data="farm_10")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_extra_menu():
    keyboard = [
        [InlineKeyboardButton("🔥 МК вышка — 30₽", callback_data="mk_tower")],
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
🤩⚡⚡⚡⚡⚡⚡🤩

<b>🚇 Metro Shop - PUBG Mobile</b>

🤩⚡⚡⚡⚡⚡⚡🤩
🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋

✨ <b>Профессиональные услуги Metro Royale:</b>

🗺️ Карты Metro (5, 7, 8 + VIP)
🚇 Сопроводы с экипировкой
⛏️ Эффективный фарм
🔥 Дополнительные услуги

💎 <b>Наши преимущества:</b>
├ ⚡ Быстрое выполнение
├ 🛡️ Опытные игроки
├ 💯 Гарантия эвакуации
├ 💰 Честные цены
└ 💬 Поддержка 24/7

🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋
➖➖➖➖➖➖➖➖➖➖➖

📱 Выберите нужную услугу! 👇
"""

MAPS_TEXT = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>🗺️ Карты Metro Royale</b>

➖➖➖➖➖➖➖➖➖➖➖

💜 <b>Карта 5</b> — 200₽
├ Стандартная карта
└ Средний лут

👑 <b>Карта 5 VIP</b> — 300₽
├ VIP версия
└ Улучшенный лут

💜 <b>Карта 7</b> — 250₽
├ Стандартная карта
└ Хороший лут

👑 <b>Карта 7 VIP</b> — 350₽
├ VIP версия
└ Отличный лут

💜 <b>Карта 8</b> — 300₽
├ Стандартная карта
└ Лучший лут

👑 <b>Карта 8 VIP</b> — 400₽
├ VIP версия
└ Максимальный лут

➖➖➖➖➖➖➖➖➖➖➖
🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋

<i>Выберите нужную карту</i> 👇
"""

ESCORTS_TEXT = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>🚇 Сопроводы с экипировкой</b>

🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋
➖➖➖➖➖➖➖➖➖➖➖

🤗 <b>Сопровод 80₽</b>
├ Входит: 🪖 🧥 🎒
├ Опытный игрок
└ Гарантия эвакуации

🤗 <b>Сопровод 100₽</b>
├ Входит: 🪖 🧥 🎒
├ Профессиональная помощь
└ Безопасный проход

🤗 <b>Сопровод 120₽</b>
├ Входит: 🪖 🧥 🎒
├ Премиум сопровождение
└ Максимум лута

➖➖➖➖➖➖➖➖➖➖➖
🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋

<i>Выберите нужный сопровод</i> 👇
"""

FARM_TEXT = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>⛏️ Фарм услуги Metro Royale</b>

➖➖➖➖➖➖➖➖➖➖➖

⛏️ <b>Фарм 5 игр</b> — 400₽
├ 5 успешных рейдов
├ Максимум добычи
├ Безопасная эвакуация
└ Делёжка лута 50/50

⛏️ <b>Фарм 10 игр</b> — 750₽
├ 10 успешных рейдов
├ Эффективный фарм
├ Гарантия результата
└ Делёжка лута 50/50

💎 <b>Что вы получите:</b>
├ Ценные предметы
├ Игровая валюта
├ Экипировка
└ Опыт прохождения

➖➖➖➖➖➖➖➖➖➖➖
🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋

<i>Выберите пакет фарма</i> 👇
"""

EXTRA_TEXT = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>🔥 Дополнительные услуги</b>

➖➖➖➖➖➖➖➖➖➖➖

🔥 <b>МК вышка</b> — 30₽
├ Быстрое выполнение
├ Профессиональная помощь
└ Гарантия результата

➖➖➖➖➖➖➖➖➖➖➖
🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋

<i>Выберите услугу</i> 👇
"""

ABOUT_TEXT = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>ℹ️ О Metro Shop</b>

➖➖➖➖➖➖➖➖➖➖➖

🏪 <b>Metro Shop</b> — профессиональный сервис для Metro Royale

🚇 <b>Metro Royale</b> — это:
├ 🗺️ Уникальные карты
├ 💼 Ценный лут
├ ⚔️ Опасные зоны
├ 🚁 Эвакуация
└ 💰 Награды

✅ <b>Наши гарантии:</b>
├ 🔐 Безопасность аккаунта
├ ⚡ Опытные игроки
├ 💬 Поддержка 24/7
├ 💸 Честные цены
└ 🔄 Возврат при проблемах

📊 <b>Статистика:</b>
├ 👥 3000+ клиентов
├ ⭐ Рейтинг 4.9/5
└ 📈 Работаем с 2021 года

➖➖➖➖➖➖➖➖➖➖➖
🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋
"""

FAQ_TEXT = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>❓ Частые вопросы (FAQ)</b>

➖➖➖➖➖➖➖➖➖➖➖

<b>Q: Что такое Metro Royale?</b>
A: 🚇 Это PvE/PvP режим в PUBG Mobile где нужно собирать лут и эвакуироваться.

<b>Q: Что входит в сопровод?</b>
A: 🪖🧥🎒 Экипировка + опытный игрок, который поможет выжить и эвакуироваться.

<b>Q: Как делится лут?</b>
A: 💎 При фарме лут делится 50/50, при сопроводе всё ваше.

<b>Q: Сколько времени занимает?</b>
A: ⏱️ Одна игра 10-20 минут, фарм-пакет за 2-4 часа.

<b>Q: Безопасно ли?</b>
A: 🛡️ Да! Мы не запрашиваем пароль, играем с вашего аккаунта или по приглашению.

<b>Q: Способы оплаты?</b>
A: 💳 ЮMoney с автоматической проверкой платежа.

<b>Q: Гарантия возврата?</b>
A: 💯 Да! Полный возврат если услуга не выполнена.

➖➖➖➖➖➖➖➖➖➖➖
🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋
"""

SUPPORT_TEXT = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>💬 Служба поддержки</b>

➖➖➖➖➖➖➖➖➖➖➖

👨‍💼 <b>Операторы онлайн 24/7!</b>

📱 <b>Контакты:</b>
├ 💬 Telegram: @MetroShopSupport
├ 📧 Email: support@metroshop.ru
└ ⚡ Ответ до 15 минут

❓ <b>По каким вопросам:</b>
├ 💰 Проблемы с оплатой
├ ⏱️ Статус заказа
├ 🔧 Техподдержка
├ 💎 Консультации
└ 📝 Жалобы и предложения

➖➖➖➖➖➖➖➖➖➖➖
🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋
"""

user_data_storage = {}

# ======================== ОБРАБОТЧИКИ ========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await add_user(user.id, user.username, user.first_name)
    await update.message.reply_text(WELCOME_TEXT, reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>🆘 Помощь по боту</b>

📱 <b>Команды:</b>
├ /start — Главное меню
├ /help — Справка
├ /orders — Мои заказы
└ /stats — Статистика (admin)

🛍️ <b>Как заказать:</b>
1️⃣ Выберите услугу
2️⃣ Введите PUBG ID
3️⃣ Оплатите через ЮMoney
4️⃣ Проверьте оплату
5️⃣ Получите услугу!

💬 Поддержка: @MetroShopSupport
"""
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu())

async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    orders = await get_user_orders(user_id, 10)
    
    if not orders:
        text = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>💼 Мои заказы</b>

➖➖➖➖➖➖➖➖➖➖➖

📭 У вас пока нет заказов.

Оформите первый заказ! 🎮

➖➖➖➖➖➖➖➖➖➖➖
"""
    else:
        text = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>💼 Ваши заказы:</b>

➖➖➖➖➖➖➖➖➖➖➖

"""
        for order in orders:
            status_emoji = {
                'awaiting_payment': '⏳',
                'paid': '✅',
                'completed': '✅',
                'cancelled': '❌'
            }.get(order['status'], '❓')
            
            text += f"""
🔖 <b>#{order['order_id']}</b>
├ 📦 {order['item_name']}
├ 💰 {order['price']}₽
└ 📊 {status_emoji}

"""
        text += "➖➖➖➖➖➖➖➖➖➖➖"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu())

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    stats = await get_stats()
    text = f"""
🤩⚡⚡⚡⚡⚡⚡🤩

<b>📊 Статистика Metro Shop</b>

➖➖➖➖➖➖➖➖➖➖➖

👥 Пользователи: {stats['total_users']}
📦 Всего заказов: {stats['total_orders']}
✅ Выполнено: {stats['completed_orders']}
💰 Выручка: {stats['total_revenue']:.2f}₽
📅 Сегодня: {stats['today_orders']}

➖➖➖➖➖➖➖➖➖➖➖
"""
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data == "main_menu":
        await query.edit_message_text(WELCOME_TEXT, reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)
    
    elif data == "metro_maps":
        await query.edit_message_text(MAPS_TEXT, reply_markup=get_maps_menu(), parse_mode=ParseMode.HTML)
    
    elif data == "escorts":
        await query.edit_message_text(ESCORTS_TEXT, reply_markup=get_escorts_menu(), parse_mode=ParseMode.HTML)
    
    elif data == "farm_services":
        await query.edit_message_text(FARM_TEXT, reply_markup=get_farm_menu(), parse_mode=ParseMode.HTML)
    
    elif data == "extra_services":
        await query.edit_message_text(EXTRA_TEXT, reply_markup=get_extra_menu(), parse_mode=ParseMode.HTML)
    
    elif data == "about":
        await query.edit_message_text(ABOUT_TEXT, reply_markup=get_back_button(), parse_mode=ParseMode.HTML)
    
    elif data == "faq":
        await query.edit_message_text(FAQ_TEXT, reply_markup=get_back_button(), parse_mode=ParseMode.HTML)
    
    elif data == "support":
        keyboard = [
            [InlineKeyboardButton("💬 Написать", url="https://t.me/MetroShopSupport")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        await query.edit_message_text(SUPPORT_TEXT, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    
    elif data == "my_orders":
        orders = await get_user_orders(user_id, 5)
        if not orders:
            text = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>💼 Мои заказы</b>

➖➖➖➖➖➖➖➖➖➖➖

📭 У вас пока нет заказов.

➖➖➖➖➖➖➖➖➖➖➖
"""
        else:
            text = """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>💼 Ваши заказы:</b>

➖➖➖➖➖➖➖➖➖➖➖

"""
            for order in orders:
                text += f"🔖 #{order['order_id']}\n├ {order['item_name']}\n└ {order['price']}₽\n\n"
            text += "➖➖➖➖➖➖➖➖➖➖➖"
        
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())
    
    elif data in PRICES:
        item = PRICES[data]
        user_data_storage[user_id] = {'item_key': data}
        
        includes_text = f"\n├ Входит: {item['includes']}" if 'includes' in item else ""
        
        text = f"""
🤩⚡⚡⚡⚡⚡⚡🤩

<b>{item['emoji']} Подтверждение заказа</b>

➖➖➖➖➖➖➖➖➖➖➖

📦 <b>Услуга:</b> {item['name']}
💰 <b>Цена:</b> {item['price']}₽{includes_text}

⚠️ <b>ВАЖНО: Введите ваш PUBG ID</b>

Отправьте ID вашего аккаунта PUBG Mobile следующим сообщением.

📍 <b>Где найти PUBG ID:</b>
1. Откройте PUBG Mobile
2. Нажмите на профиль
3. ID под ником

<i>Пример: 5123456789</i>

➖➖➖➖➖➖➖➖➖➖➖
"""
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отменить", callback_data="main_menu")]])
        )
    
    elif data.startswith('check_payment_'):
        order_id = data.replace('check_payment_', '')
        order = await get_order(order_id)
        
        if not order or order['status'] != 'awaiting_payment':
            await query.answer("❌ Ошибка заказа", show_alert=True)
            return
        
        await query.answer("🔄 Проверяю платеж...", show_alert=False)
        
        if yoomoney:
            is_paid, amount = yoomoney.check_payment(order['payment_label'], order['price'])
            
            if is_paid:
                await update_order_status(order_id, 'paid')
                await update_user_stats(user_id, order['price'])
                
                text = f"""
🤩⚡⚡⚡⚡⚡⚡🤩

<b>✅ Платеж получен!</b>

➖➖➖➖➖➖➖➖➖➖➖

🎉 Заказ #{order_id} оплачен!

📦 <b>Услуга:</b> {order['item_name']}
💰 <b>Сумма:</b> {amount}₽
🆔 <b>PUBG ID:</b> {order['pubg_id']}

⏳ <b>Статус:</b> В обработке

Услуга будет выполнена в течение 10-30 минут!
Мы уведомим вас когда всё будет готово.

💬 Вопросы: @MetroShopSupport

➖➖➖➖➖➖➖➖➖➖➖
🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋
"""
                
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())
                
                # Уведомляем админов
                for admin_id in ADMIN_IDS:
                    try:
                        admin_text = f"""
🔔 <b>Новый оплаченный заказ!</b>

📦 Заказ: #{order_id}
👤 @{order['username'] or 'нет'}
🆔 User ID: {user_id}
💎 Услуга: {order['item_name']}
💰 Сумма: {order['price']}₽
🎮 PUBG ID: {order['pubg_id']}

⚡ Требуется выполнение!
"""
                        await context.bot.send_message(
                            admin_id,
                            admin_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=get_admin_order_menu(order_id)
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки админу: {e}")
            else:
                await query.answer("⏳ Платеж не найден. Попробуйте через минуту.", show_alert=True)
        else:
            await query.answer("❌ API недоступен", show_alert=True)
    
    elif data.startswith('cancel_order_'):
        order_id = data.replace('cancel_order_', '')
        await update_order_status(order_id, 'cancelled')
        
        await query.edit_message_text(
            """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>❌ Заказ отменен</b>

➖➖➖➖➖➖➖➖➖➖➖
""",
            reply_markup=get_main_menu(),
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith('admin_complete_'):
        if user_id not in ADMIN_IDS:
            return
        
        order_id = data.replace('admin_complete_', '')
        order = await get_order(order_id)
        
        if order:
            await update_order_status(order_id, 'completed')
            await query.edit_message_text(f"✅ Заказ #{order_id} выполнен и закрыт!")
            
            try:
                await context.bot.send_message(
                    order['user_id'],
                    f"""
🤩⚡⚡⚡⚡⚡⚡🤩

<b>🎉 Заказ выполнен!</b>

➖➖➖➖➖➖➖➖➖➖➖

Ваш заказ #{order_id} успешно выполнен!

📦 <b>Услуга:</b> {order['item_name']}
🎮 <b>PUBG ID:</b> {order['pubg_id']}

Проверьте игру!

⭐ Спасибо за покупку! Будем рады видеть вас снова!

➖➖➖➖➖➖➖➖➖➖➖
🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋
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
            """
🤩⚡⚡⚡⚡⚡⚡🤩

<b>❌ Неверный формат PUBG ID</b>

➖➖➖➖➖➖➖➖➖➖➖

ID должен состоять из цифр (минимум 8).

<i>Пример: 5123456789</i>

➖➖➖➖➖➖➖➖➖➖➖
""",
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
    
    payment_url = yoomoney.create_payment_url(
        YOOMONEY_WALLET,
        item['price'],
        payment_label,
        f"Metro Shop - {item['name']}"
    ) if yoomoney else f"https://yoomoney.ru/to/{YOOMONEY_WALLET}"
    
    text = f"""
🤩⚡⚡⚡⚡⚡⚡🤩

<b>💳 Оплата заказа #{order_id}</b>

➖➖➖➖➖➖➖➖➖➖➖

📦 <b>Услуга:</b> {item['name']}
🆔 <b>PUBG ID:</b> {pubg_id}
💰 <b>К оплате:</b> {item['price']}₽

🏷️ <b>Метка платежа (обязательно!):</b>
<code>{payment_label}</code>

📝 <b>Инструкция:</b>
1️⃣ Перейдите по ссылке
2️⃣ Выберите способ оплаты
3️⃣ Оплатите {item['price']}₽
4️⃣ В комментарии: <code>{payment_label}</code>
5️⃣ Нажмите "Проверить оплату"

⚠️ <b>ВАЖНО:</b> Обязательно укажите метку!

⏰ Платеж проверяется автоматически
🕐 Заказ действителен 24 часа

➖➖➖➖➖➖➖➖➖➖➖
🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋
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

async def post_init(application: Application):
    await init_db()
    logger.info("✅ База данных инициализирована")

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
    logger.info(f"🔌 API: {'✅' if yoomoney else '❌'}")
    
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == '__main__':
    main()
