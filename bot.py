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
    # Сопровождение по картам (премиум)
    'escort_map5': {
        'name': '👑 Сопровождение Карта 5',
        'price': 350,
        'emoji': '🔥',
        'kills': '6-7',
        'loot': 'Весь лут твой!',
        'gear': '💎💎💎💎'
    },
    'escort_map7': {
        'name': '👑 Сопровождение Карта 7',
        'price': 450,
        'emoji': '🔥',
        'kills': '10-15',
        'loot': 'Весь лут твой!',
        'gear': '💎💎💎💎'
    },
    'escort_map8_basic': {
        'name': '👑 Сопровождение Карта 8 (Базовый)',
        'price': 850,
        'emoji': '🔥',
        'kills': '12+',
        'tickets': '5-8',
        'loot': 'Весь лут твой!',
        'gear': '💎💎💎💎'
    },
    'escort_map8_premium': {
        'name': '👑 Сопровождение Карта 8 (Премиум)',
        'price': 1300,
        'emoji': '💎',
        'kills': '18+',
        'tickets': '8-12',
        'loot': 'Весь лут твой!',
        'gear': '💎💎💎💎💎'
    },
    
    # Сопроводы с экипировкой (бюджетные)
    'escort_80': {
        'name': '🎮 Сопровод 80₽',
        'price': 80,
        'emoji': '⚡',
        'includes': '🪖🧥🎒'
    },
    'escort_100': {
        'name': '🎮 Сопровод 100₽',
        'price': 100,
        'emoji': '⚡',
        'includes': '🪖🧥🎒'
    },
    'escort_120': {
        'name': '🎮 Сопровод 120₽',
        'price': 120,
        'emoji': '⚡',
        'includes': '🪖🧥🎒'
    },
    
    # Дополнительные услуги
    'mk_tower': {
        'name': 'МК вышка',
        'price': 30,
        'emoji': '🚀'
    },
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
        [InlineKeyboardButton("🔥 Премиум сопровождение", callback_data="premium_escorts")],
        [InlineKeyboardButton("⚡ Бюджетные сопроводы", callback_data="budget_escorts")],
        [InlineKeyboardButton("🚀 Доп. услуги", callback_data="extra_services")],
        [
            InlineKeyboardButton("🎁 Мои заказы", callback_data="my_orders"),
            InlineKeyboardButton("💎 Инфо", callback_data="about")
        ],
        [
            InlineKeyboardButton("🎯 FAQ", callback_data="faq"),
            InlineKeyboardButton("💬 Поддержка", callback_data="support")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_premium_escorts_menu():
    keyboard = [
        [InlineKeyboardButton("🔥 Карта 5 • 350₽", callback_data="escort_map5")],
        [InlineKeyboardButton("🔥 Карта 7 • 450₽", callback_data="escort_map7")],
        [InlineKeyboardButton("⚔️ Карта 8 Базовый • 850₽", callback_data="escort_map8_basic")],
        [InlineKeyboardButton("👑 Карта 8 Премиум • 1,300₽", callback_data="escort_map8_premium")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_budget_escorts_menu():
    keyboard = [
        [InlineKeyboardButton("⚡ Сопровод 80₽ 🪖🧥🎒", callback_data="escort_80")],
        [InlineKeyboardButton("⚡ Сопровод 100₽ 🪖🧥🎒", callback_data="escort_100")],
        [InlineKeyboardButton("⚡ Сопровод 120₽ 🪖🧥🎒", callback_data="escort_120")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_extra_menu():
    keyboard = [
        [InlineKeyboardButton("🚀 МК вышка • 30₽", callback_data="mk_tower")],
        [InlineKeyboardButton("💬 Другие услуги (ЛС)", url="https://t.me/MetroShopSupport")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_button():
    keyboard = [[InlineKeyboardButton("◀️ На главную", callback_data="main_menu")]]
    return InlineKeyboardMarkup(keyboard)

def get_payment_menu(order_id: str):
    keyboard = [
        [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_payment_{order_id}")],
        [InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_order_{order_id}")],
        [InlineKeyboardButton("◀️ На главную", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_order_menu(order_id: str):
    keyboard = [
        [InlineKeyboardButton("✅ Заказ выполнен", callback_data=f"admin_complete_{order_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ======================== ТЕКСТЫ ========================

WELCOME_TEXT = """
💎━━━━━━━━━━━━━━━💎
    🎮 <b>METRO SHOP</b> 🎮
💎━━━━━━━━━━━━━━━💎

🔥 <b>Профи услуги Metro Royale</b>

━━━━━━━━━━━━━━━━━━━

✨ <b>Что мы предлагаем:</b>

👑 <b>Премиум сопровождение</b>
  ├ Карта 5, 7, 8
  ├ Гарант выносов 💯
  └ Весь лут твой 💎

⚡ <b>Бюджетные сопроводы</b>
  ├ С экипировкой 🪖🧥🎒
  ├ Быстро и недорого
  └ От 80₽ 🚀

🚀 <b>Доп. услуги</b>
  └ МК вышка и другое

━━━━━━━━━━━━━━━━━━━

💪 <b>Почему мы?</b>
  ├ 🏆 Опытные игроки
  ├ ⚡ Быстрое выполнение
  ├ 💰 Честные цены
  ├ 🛡️ Гарантия результата
  └ 🤝 Надежная сделка

━━━━━━━━━━━━━━━━━━━

👇 <b>Выбери услугу ниже 🎯</b>
"""

PREMIUM_ESCORTS_TEXT = """
💎━━━━━━━━━━━━━━━💎
   🔥 <b>ПРЕМИУМ СОПРОВОД</b>
💎━━━━━━━━━━━━━━━💎

━━━━━━━━━━━━━━━━━━━

<b>🔥 КАРТА 5</b> • 350₽
  ├ 💎 Качество: ★★★★
  ├ 🎯 Выносов: 6-7
  ├ 💪 Весь лут твой!
  └ ⚡ Быстро и четко

<b>🔥 КАРТА 7</b> • 450₽
  ├ 💎 Качество: ★★★★
  ├ 🎯 Выносов: 10-15
  ├ 💪 Весь лут твой!
  └ ⚡ Топ результат

<b>⚔️ КАРТА 8 (БАЗОВЫЙ)</b> • 850₽
  ├ 💎 Качество: ★★★★
  ├ 🎯 Выносов: 12+
  ├ 🎁 Билеты: 5-8
  ├ 💪 Весь лут твой!
  └ 🛡️ Гарантия

<b>👑 КАРТА 8 (ПРЕМИУМ)</b> • 1,300₽
  ├ 💎 Качество: ★★★★★
  ├ 🎯 Выносов: 18+
  ├ 🎁 Билеты: 8-12
  ├ 💪 Весь лут твой!
  └ 🏆 Максимальный фарм

━━━━━━━━━━━━━━━━━━━

🔥 <i>Выбери свою карту 👇</i>
"""

BUDGET_ESCORTS_TEXT = """
⚡━━━━━━━━━━━━━━━⚡
   🎮 <b>БЮДЖЕТ СОПРОВОД</b>
⚡━━━━━━━━━━━━━━━⚡

━━━━━━━━━━━━━━━━━━━

<b>⚡ СОПРОВОД • 80₽</b>
  ├ Включено: 🪖 🧥 🎒
  ├ 🚀 Быстро
  └ 💯 Надежно

<b>⚡ СОПРОВОД • 100₽</b>
  ├ Включено: 🪖 🧥 🎒
  ├ 🚀 Быстро
  └ 💯 Надежно

<b>⚡ СОПРОВОД • 120₽</b>
  ├ Включено: 🪖 🧥 🎒
  ├ 🚀 Быстро
  └ 💯 Надежно

━━━━━━━━━━━━━━━━━━━

✨ <b>В комплекте:</b>
  ├ 🪖 Шлем
  ├ 🧥 Бронежилет
  └ 🎒 Рюкзак

💪 <b>Быстро и недорого!</b>

━━━━━━━━━━━━━━━━━━━

👇 <i>Выбери свой пакет 🎯</i>
"""

EXTRA_TEXT = """
🚀━━━━━━━━━━━━━━━🚀
    ⚡ <b>ДОП. УСЛУГИ</b>
🚀━━━━━━━━━━━━━━━🚀

━━━━━━━━━━━━━━━━━━━

<b>🚀 МК ВЫШКА</b> • 30₽
  ├ ⚡ Быстрое выполнение
  ├ 🎯 Профессионалы
  ├ 💯 Гарантия результата
  └ 🔥 Топ качество

━━━━━━━━━━━━━━━━━━━

💬 <b>ИНДИВИДУАЛЬНЫЕ ЗАКАЗЫ</b>

Нужно что-то особенное?
Пиши в ЛС — обсудим! 🤝

━━━━━━━━━━━━━━━━━━━

👇 <i>Выбери услугу 🎯</i>
"""

ABOUT_TEXT = """
💎━━━━━━━━━━━━━━━💎
     ✨ <b>О НАС</b> ✨
💎━━━━━━━━━━━━━━━💎

━━━━━━━━━━━━━━━━━━━

🎮 <b>METRO SHOP</b>
Твой проводник в Metro Royale 🚀

━━━━━━━━━━━━━━━━━━━

🔥 <b>METRO ROYALE — ЭТО:</b>
  ├ 🗺️ Уникальные карты
  ├ 💎 Ценный лут
  ├ ⚔️ Опасные зоны
  ├ 🛡️ Эвакуация
  └ 🎁 Награды

━━━━━━━━━━━━━━━━━━━

✅ <b>НАШИ ГАРАНТИИ:</b>
  ├ 🛡️ Безопасность 💯
  ├ 💪 Опытные игроки
  ├ 💬 Поддержка 24/7
  ├ 💰 Честные цены
  ├ 🤝 Надежная сделка
  └ 🔄 Возврат средств

━━━━━━━━━━━━━━━━━━━

📊 <b>СТАТИСТИКА:</b>
  ├ 👥 3000+ клиентов
  ├ ⭐ Рейтинг 4.9/5
  ├ 🏆 1000+ сопроводов
  └ 🚀 С 2021 года

━━━━━━━━━━━━━━━━━━━

💬 <i>Вопросы? Пиши! 🎯</i>
"""

FAQ_TEXT = """
🎯━━━━━━━━━━━━━━━🎯
      ❓ <b>FAQ</b>
🎯━━━━━━━━━━━━━━━🎯

━━━━━━━━━━━━━━━━━━━

<b>Q: Что такое Metro Royale?</b>
<b>A:</b> 🎮 PvE/PvP режим с лутом и эвакуацией 🚀

<b>Q: Что входит в премиум?</b>
<b>A:</b> 👑 Опытный игрок + гарант выносов + весь лут твой 💎

<b>Q: Чем отличается бюджет?</b>
<b>A:</b> ⚡ Быстро + базовая экипировка 🪖🧥🎒

<b>Q: Гарант выноса — что это?</b>
<b>A:</b> 🎯 Гарантированное число киллов = больше лута! 💰

<b>Q: Как делится лут?</b>
<b>A:</b> 💎 Премиум — всё твоё! Бюджет — стандарт 🤝

<b>Q: Сколько времени?</b>
<b>A:</b> ⏱️ 10-25 минут на игру ⚡

<b>Q: Это безопасно?</b>
<b>A:</b> 🛡️ Да! Играем вместе, пароль не нужен 💯

<b>Q: Способы оплаты?</b>
<b>A:</b> 💳 ЮMoney (автопроверка) 🚀

<b>Q: Что такое билеты?</b>
<b>A:</b> 🎁 Валюта Metro для обмена на предметы 💎

<b>Q: Гарантия возврата?</b>
<b>A:</b> 💯 Да! 100% возврат при проблемах 🤝

━━━━━━━━━━━━━━━━━━━

💬 <i>Еще вопросы? Пиши! 🎯</i>
"""

SUPPORT_TEXT = """
💬━━━━━━━━━━━━━━━💬
    🆘 <b>ПОДДЕРЖКА</b>
💬━━━━━━━━━━━━━━━💬

━━━━━━━━━━━━━━━━━━━

👨‍💼 <b>МЫ ОНЛАЙН 24/7!</b> ⚡

━━━━━━━━━━━━━━━━━━━

📱 <b>КОНТАКТЫ:</b>
  ├ 💬 TG: @MetroShopSupport
  ├ 📧 Email: support@metroshop.ru
  └ ⚡ Ответ: до 15 мин 🚀

━━━━━━━━━━━━━━━━━━━

🎯 <b>МОЖЕМ ПОМОЧЬ С:</b>
  ├ 💰 Проблемы с оплатой
  ├ ⏱️ Статус заказа
  ├ 🔧 Техподдержка
  ├ 💎 Консультации
  ├ 🎁 Индивидуальные заказы
  └ 📝 Жалобы и предложения

━━━━━━━━━━━━━━━━━━━

🔥 <b>ЗА ДРУГИМИ ВЕЩАМИ — В ЛС!</b>

━━━━━━━━━━━━━━━━━━━

💪 <i>Всегда рады помочь! 🤝</i>
"""

user_data_storage = {}

# ======================== ОБРАБОТЧИКИ ========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await add_user(user.id, user.username, user.first_name)
    await update.message.reply_text(WELCOME_TEXT, reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🎯━━━━━━━━━━━━━━━🎯
     🆘 <b>ПОМОЩЬ</b>
🎯━━━━━━━━━━━━━━━🎯

━━━━━━━━━━━━━━━━━━━

📱 <b>КОМАНДЫ:</b>
  ├ /start — Главное меню 🏠
  ├ /help — Справка 🆘
  ├ /orders — Мои заказы 🎁
  └ /stats — Статистика (admin) 📊

━━━━━━━━━━━━━━━━━━━

🛍️ <b>КАК ЗАКАЗАТЬ:</b>
  1️⃣ Выбери услугу 🎯
  2️⃣ Введи PUBG ID 🎮
  3️⃣ Оплати через ЮMoney 💳
  4️⃣ Проверь оплату в боте 🔄
  5️⃣ Получи услугу! 🎁

━━━━━━━━━━━━━━━━━━━

💬 Поддержка: @MetroShopSupport
⚡ Онлайн 24/7! 🚀
"""
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu())

async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    orders = await get_user_orders(user_id, 10)
    
    if not orders:
        text = """
🎁━━━━━━━━━━━━━━━🎁
    📦 <b>МОИ ЗАКАЗЫ</b>
🎁━━━━━━━━━━━━━━━🎁

━━━━━━━━━━━━━━━━━━━

📭 У тебя пока нет заказов

Оформи первый заказ! 🚀

━━━━━━━━━━━━━━━━━━━
"""
    else:
        text = """
🎁━━━━━━━━━━━━━━━🎁
    📦 <b>МОИ ЗАКАЗЫ</b>
🎁━━━━━━━━━━━━━━━🎁

━━━━━━━━━━━━━━━━━━━

"""
        for order in orders:
            status_emoji = {
                'awaiting_payment': '⏳',
                'paid': '✅',
                'completed': '🏆',
                'cancelled': '❌'
            }.get(order['status'], '❓')
            
            text += f"""
<b>🔖 #{order['order_id']}</b>
  ├ 📦 {order['item_name']}
  ├ 💰 {order['price']}₽
  └ {status_emoji}

"""
        text += "━━━━━━━━━━━━━━━━━━━"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu())

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    stats = await get_stats()
    text = f"""
📊━━━━━━━━━━━━━━━📊
   🏆 <b>СТАТИСТИКА</b>
📊━━━━━━━━━━━━━━━📊

━━━━━━━━━━━━━━━━━━━

👥 Пользователей: {stats['total_users']}
📦 Всего заказов: {stats['total_orders']}
✅ Выполнено: {stats['completed_orders']}
💰 Выручка: {stats['total_revenue']:.2f}₽
📅 Сегодня: {stats['today_orders']}

━━━━━━━━━━━━━━━━━━━
"""
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data == "main_menu":
        await query.edit_message_text(WELCOME_TEXT, reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)
    
    elif data == "premium_escorts":
        await query.edit_message_text(PREMIUM_ESCORTS_TEXT, reply_markup=get_premium_escorts_menu(), parse_mode=ParseMode.HTML)
    
    elif data == "budget_escorts":
        await query.edit_message_text(BUDGET_ESCORTS_TEXT, reply_markup=get_budget_escorts_menu(), parse_mode=ParseMode.HTML)
    
    elif data == "extra_services":
        await query.edit_message_text(EXTRA_TEXT, reply_markup=get_extra_menu(), parse_mode=ParseMode.HTML)
    
    elif data == "about":
        await query.edit_message_text(ABOUT_TEXT, reply_markup=get_back_button(), parse_mode=ParseMode.HTML)
    
    elif data == "faq":
        await query.edit_message_text(FAQ_TEXT, reply_markup=get_back_button(), parse_mode=ParseMode.HTML)
    
    elif data == "support":
        keyboard = [
            [InlineKeyboardButton("💬 Написать оператору", url="https://t.me/MetroShopSupport")],
            [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
        ]
        await query.edit_message_text(SUPPORT_TEXT, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    
    elif data == "my_orders":
        orders = await get_user_orders(user_id, 5)
        if not orders:
            text = """
🎁━━━━━━━━━━━━━━━🎁
    📦 <b>МОИ ЗАКАЗЫ</b>
🎁━━━━━━━━━━━━━━━🎁

━━━━━━━━━━━━━━━━━━━

📭 У тебя пока нет заказов

━━━━━━━━━━━━━━━━━━━
"""
        else:
            text = """
🎁━━━━━━━━━━━━━━━🎁
    📦 <b>МОИ ЗАКАЗЫ</b>
🎁━━━━━━━━━━━━━━━🎁

━━━━━━━━━━━━━━━━━━━

"""
            for order in orders:
                text += f"<b>🔖 #{order['order_id']}</b>\n  ├ {order['item_name']}\n  └ {order['price']}₽\n\n"
            text += "━━━━━━━━━━━━━━━━━━━"
        
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())
    
    elif data in PRICES:
        item = PRICES[data]
        user_data_storage[user_id] = {'item_key': data}
        
        # Формируем описание товара
        description_parts = []
        if 'gear' in item:
            description_parts.append(f"  ├ 💎 Качество: {item['gear']}")
        if 'kills' in item:
            description_parts.append(f"  ├ 🎯 Выносов: {item['kills']}")
        if 'tickets' in item:
            description_parts.append(f"  ├ 🎁 Билеты: {item['tickets']}")
        if 'loot' in item:
            description_parts.append(f"  └ 💪 Лут: {item['loot']}")
        if 'includes' in item:
            description_parts.append(f"  └ ✨ Включено: {item['includes']}")
        
        description = "\n".join(description_parts) if description_parts else ""
        
        text = f"""
{item['emoji']}━━━━━━━━━━━━━━━{item['emoji']}
  🤝 <b>ПОДТВЕРЖДЕНИЕ</b>
{item['emoji']}━━━━━━━━━━━━━━━{item['emoji']}

━━━━━━━━━━━━━━━━━━━

<b>{item['name']}</b>
💰 Цена: {item['price']}₽

{description}

━━━━━━━━━━━━━━━━━━━

🎯 <b>ВВЕДИ СВОЙ PUBG ID</b>

Отправь ID своего аккаунта
PUBG Mobile следующим сообщением

📍 <b>Где найти:</b>
  1. Открой PUBG Mobile 🎮
  2. Нажми на профиль 👤
  3. ID под ником 🔍

<i>Пример: 5123456789</i>

━━━━━━━━━━━━━━━━━━━
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
🏆━━━━━━━━━━━━━━━🏆
   ✅ <b>ОПЛАТА ПОЛУЧЕНА!</b>
🏆━━━━━━━━━━━━━━━🏆

━━━━━━━━━━━━━━━━━━━

🎉 Заказ #{order_id} оплачен! 💯

━━━━━━━━━━━━━━━━━━━

📦 <b>Услуга:</b> {order['item_name']}
💰 <b>Сумма:</b> {amount}₽
🎮 <b>PUBG ID:</b> {order['pubg_id']}

⚡ <b>Статус:</b> В обработке 🔄

━━━━━━━━━━━━━━━━━━━

Услуга будет выполнена
в течение 10-30 минут! ⏱️

Мы уведомим тебя когда
всё будет готово 🚀

💬 Вопросы: @MetroShopSupport

━━━━━━━━━━━━━━━━━━━
"""
                
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())
                
                # Уведомляем админов
                for admin_id in ADMIN_IDS:
                    try:
                        admin_text = f"""
🔔 <b>НОВЫЙ ОПЛАЧЕННЫЙ ЗАКАЗ!</b>

📦 Заказ: #{order_id}
👤 @{order['username'] or 'нет'}
🆔 User ID: {user_id}
💎 Услуга: {order['item_name']}
💰 Сумма: {order['price']}₽
🎮 PUBG ID: {order['pubg_id']}

⚡ ТРЕБУЕТСЯ ВЫПОЛНЕНИЕ! 🚀
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
                await query.answer("⏳ Платеж не найден. Попробуй через минуту.", show_alert=True)
        else:
            await query.answer("❌ API недоступен", show_alert=True)
    
    elif data.startswith('cancel_order_'):
        order_id = data.replace('cancel_order_', '')
        await update_order_status(order_id, 'cancelled')
        
        await query.edit_message_text(
            """
❌━━━━━━━━━━━━━━━❌
    🚫 <b>ОТМЕНЕНО</b>
❌━━━━━━━━━━━━━━━❌

━━━━━━━━━━━━━━━━━━━

Твой заказ был отменен

━━━━━━━━━━━━━━━━━━━
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
            await query.edit_message_text(f"✅ Заказ #{order_id} выполнен! 🏆")
            
            try:
                await context.bot.send_message(
                    order['user_id'],
                    f"""
🏆━━━━━━━━━━━━━━━🏆
   🎉 <b>ЗАКАЗ ВЫПОЛНЕН!</b>
🏆━━━━━━━━━━━━━━━🏆

━━━━━━━━━━━━━━━━━━━

Твой заказ #{order_id}
успешно выполнен! 💯

━━━━━━━━━━━━━━━━━━━

📦 <b>Услуга:</b> {order['item_name']}
🎮 <b>PUBG ID:</b> {order['pubg_id']}

━━━━━━━━━━━━━━━━━━━

Проверь игру! 🚀

✨ Спасибо за покупку!
Будем рады видеть снова! 🤝

━━━━━━━━━━━━━━━━━━━
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
❌━━━━━━━━━━━━━━━❌
     🚫 <b>ОШИБКА!</b>
❌━━━━━━━━━━━━━━━❌

━━━━━━━━━━━━━━━━━━━

Неверный формат PUBG ID 🎯

ID должен быть числом
(минимум 8 цифр)

<i>Пример: 5123456789</i>

━━━━━━━━━━━━━━━━━━━
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
💳━━━━━━━━━━━━━━━💳
    💰 <b>ОПЛАТА</b>
💳━━━━━━━━━━━━━━━💳

━━━━━━━━━━━━━━━━━━━

<b>🔖 ЗАКАЗ #{order_id}</b>

📦 Услуга: {item['name']}
🎮 PUBG ID: {pubg_id}
💰 К оплате: {item['price']}₽

━━━━━━━━━━━━━━━━━━━

🏷️ <b>МЕТКА ПЛАТЕЖА:</b>
<code>{payment_label}</code>

━━━━━━━━━━━━━━━━━━━

🎯 <b>КАК ОПЛАТИТЬ:</b>

1️⃣ Нажми "Перейти к оплате" 💳
2️⃣ Выбери способ оплаты 🎯
3️⃣ Оплати {item['price']}₽ 💰
4️⃣ В комментарии укажи метку:
    <code>{payment_label}</code>
5️⃣ Вернись в бот 🔙
6️⃣ Нажми "Проверить оплату" 🔄

━━━━━━━━━━━━━━━━━━━

⚠️ <b>ВАЖНО:</b>
  ├ 🎯 Обязательно укажи метку!
  ├ ⚡ Автопроверка платежа
  └ ⏱️ Заказ действителен 24ч

🚀 После оплаты услуга
будет выполнена за 10-30 мин! 💯

━━━━━━━━━━━━━━━━━━━
"""
    
    keyboard = [
        [InlineKeyboardButton("💳 Перейти к оплате", url=payment_url)],
        [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_payment_{order_id}")],
        [InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_order_{order_id}")]
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
    logger.info(f"🔌 API: {'✅ Подключен' if yoomoney else '❌ Отключен'}")
    
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == '__main__':
    main()
