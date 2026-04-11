import logging
import os
import asyncio
import random
import string
from datetime import datetime, timedelta
from typing import Dict, List
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

from yoomoney import Quickpay, Client

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
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',')]
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

# ======================== БАЗА ДАННЫХ ========================

async def init_db():
    """Инициализация базы данных"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица пользователей
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
        
        # Таблица заказов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                user_id INTEGER,
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
        
        # Таблица платежей для отслеживания
        await db.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                order_id TEXT,
                amount REAL,
                label TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders (order_id)
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

async def create_order(user_id: int, item_key: str, item_name: str, price: float, pubg_id: str = None):
    """Создание заказа"""
    order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
    payment_label = f"ORDER_{order_id}"
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT INTO orders (order_id, user_id, item_key, item_name, price, status, payment_label, pubg_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (order_id, user_id, item_key, item_name, price, 'awaiting_payment', payment_label, pubg_id))
        await db.commit()
    
    return order_id, payment_label

async def get_order(order_id: str):
    """Получение информации о заказе"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM orders WHERE order_id = ?', (order_id,)) as cursor:
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
            AND created_at > datetime('now', '-1 day')
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

# ======================== YOOMONEY ИНТЕГРАЦИЯ ========================

def create_payment_link(amount: float, label: str, description: str):
    """Создание ссылки для оплаты через ЮMoney"""
    quickpay = Quickpay(
        receiver=YOOMONEY_WALLET,
        quickpay_form="shop",
        targets=description,
        paymentType="SB",
        sum=amount,
        label=label
    )
    return quickpay.redirected_url

async def check_payment(label: str):
    """Проверка платежа по метке"""
    try:
        client = Client(YOOMONEY_TOKEN)
        history = client.operation_history(label=label)
        
        for operation in history.operations:
            if operation.label == label and operation.status == "success":
                return True, operation.amount
        return False, 0
    except Exception as e:
        logger.error(f"Ошибка проверки платежа: {e}")
        return False, 0

# ======================== КЛАВИАТУРЫ ========================

def get_main_menu():
    """Главное меню"""
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
    """Меню покупки UC"""
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
    """Меню покупки проходок"""
    keyboard = [
        [InlineKeyboardButton("🎫 Royale Pass — 800₽", callback_data="rp_pass")],
        [InlineKeyboardButton("👑 Elite Pass Plus — 2,000₽", callback_data="rp_elite")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_boost_menu():
    """Меню прокачки рейтинга"""
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
    """Меню услуг Metro Royale"""
    keyboard = [
        [InlineKeyboardButton("🚇 Сопровождение (1 игра) — 300₽", callback_data="metro_escort")],
        [InlineKeyboardButton("⛏️ Фарм Metro (5 игр) — 1,200₽", callback_data="metro_farm")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_confirmation_menu(item_key: str):
    """Меню подтверждения заказа"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{item_key}"),
            InlineKeyboardButton("❌ Отменить", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_payment_menu(order_id: str):
    """Меню с кнопкой проверки оплаты"""
    keyboard = [
        [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_payment_{order_id}")],
        [InlineKeyboardButton("❌ Отменить заказ", callback_data=f"cancel_order_{order_id}")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_button():
    """Кнопка назад"""
    keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]]
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

🏪 <b>Metro Shop</b> — это профессиональный сервис для игроков PUBG Mobile, предоставляющий широкий спектр игровых услуг.

🚇 <b>Metro Royale</b> — это уникальный PvE/PvP режим PUBG Mobile, где игроки:
├ 🗺️ Исследуют опасные локации
├ 💼 Собирают ценный лут
├ ⚔️ Сражаются с ботами и игроками
├ 🚁 Эвакуируются с добычей
└ 💰 Продают найденные предметы

✅ <b>Наши гарантии:</b>
├ 🔐 Безопасность аккаунта
├ ⚡ Быстрое выполнение заказов
├ 💬 Профессиональная поддержка
├ 💸 Честные цены
└ 🔄 Возврат средств при проблемах

📊 <b>Статистика:</b>
├ 👥 5000+ довольных клиентов
├ ⭐ Рейтинг 4.9/5.0
└ 📈 Работаем с 2020 года

💬 Остались вопросы? Обращайтесь в поддержку!
"""

FAQ_TEXT = """
❓ <b>Частые вопросы (FAQ)</b>

<b>Q: Безопасно ли передавать данные аккаунта?</b>
A: ✅ Да! Мы используем защищенные каналы связи и не сохраняем ваши данные. Более 5000 выполненных заказов без единого бана.

<b>Q: Сколько времени занимает пополнение UC?</b>
A: ⚡ От 5 до 30 минут после подтверждения оплаты.

<b>Q: Какие способы оплаты доступны?</b>
A: 💳 ЮMoney с автоматической проверкой платежа.

<b>Q: Что делать если UC не пришли?</b>
A: 📞 Свяжитесь с поддержкой — мы решим проблему в течение 1 часа.

<b>Q: Могут ли забанить за покупку UC?</b>
A: 🛡️ Нет! Мы пополняем через официальные методы.

<b>Q: Сколько стоит прокачка рейтинга?</b>
A: 📊 Зависит от текущего и желаемого ранга. Цены от 500₽.

<b>Q: Что такое Metro Royale?</b>
A: 🚇 Это PvE/PvP режим где нужно собирать лут и эвакуироваться. Мы поможем вам эффективно фармить ценности!

<b>Q: Есть ли гарантия возврата?</b>
A: 💯 Да! Если услуга не выполнена — полный возврат средств.

💬 Не нашли ответ? Напишите в поддержку!
"""

SUPPORT_TEXT = """
💬 <b>Служба поддержки</b>

👨‍💼 <b>Наши операторы готовы помочь вам 24/7!</b>

📱 <b>Способы связи:</b>
├ 💬 Telegram: @MetroShopSupport
├ 📧 Email: support@metroshop.ru
└ ⚡ Время ответа: до 15 минут

🕐 <b>Мы работаем круглосуточно!</b>

❓ <b>По каким вопросам можно обращаться:</b>
├ 💰 Проблемы с оплатой
├ ⏱️ Статус заказа
├ 🔧 Технические вопросы
├ 💎 Консультация по услугам
└ 📝 Жалобы и предложения

<i>Нажмите кнопку ниже для связи с оператором</i> 👇
"""

# ======================== ОБРАБОТЧИКИ ========================

# Хранилище для временных данных пользователей
user_data_storage = {}

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    await add_user(user.id, user.username, user.first_name)
    
    await update.message.reply_text(
        WELCOME_TEXT,
        reply_markup=get_main_menu(),
        parse_mode=ParseMode.HTML
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    text = """
🆘 <b>Помощь по боту Metro Shop</b>

📱 <b>Доступные команды:</b>
├ /start — Главное меню
├ /help — Справка по боту
└ /orders — Мои заказы

🛍️ <b>Как сделать заказ:</b>
1️⃣ Выберите нужный раздел в меню
2️⃣ Выберите товар/услугу
3️⃣ Введите свой PUBG ID
4️⃣ Подтвердите заказ
5️⃣ Оплатите через ЮMoney
6️⃣ Проверьте оплату в боте
7️⃣ Получите свой заказ!

💬 <b>Нужна помощь?</b>
Обратитесь в поддержку: @MetroShopSupport

⚡ Мы работаем 24/7!
"""
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu()
    )

async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр заказов пользователя"""
    user_id = update.effective_user.id
    orders = await get_user_orders(user_id, 10)
    
    if not orders:
        text = """
💼 <b>Мои заказы</b>

📭 У вас пока нет заказов.

Оформите первый заказ через главное меню! 🎮
"""
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
                'paid': 'Оплачен, в обработке',
                'processing': 'В работе',
                'completed': 'Выполнен',
                'cancelled': 'Отменен'
            }.get(order['status'], 'Неизвестно')
            
            text += f"""
🔖 <b>Заказ #{order['order_id']}</b>
├ 📦 Товар: {order['item_name']}
├ 💰 Сумма: {order['price']}₽
├ 📊 Статус: {status_emoji} {status_text}
└ 📅 Дата: {order['created_at'][:16]}

"""
    
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu()
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика для админов"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Всего пользователей
        async with db.execute('SELECT COUNT(*) FROM users') as cursor:
            total_users = (await cursor.fetchone())[0]
        
        # Всего заказов
        async with db.execute('SELECT COUNT(*) FROM orders') as cursor:
            total_orders = (await cursor.fetchone())[0]
        
        # Сумма продаж
        async with db.execute('SELECT SUM(price) FROM orders WHERE status = "completed"') as cursor:
            total_revenue = (await cursor.fetchone())[0] or 0
        
        # Заказы за сегодня
        async with db.execute('''
            SELECT COUNT(*) FROM orders 
            WHERE DATE(created_at) = DATE('now')
        ''') as cursor:
            today_orders = (await cursor.fetchone())[0]
    
    text = f"""
📊 <b>Статистика Metro Shop</b>

👥 <b>Пользователи:</b> {total_users}
📦 <b>Всего заказов:</b> {total_orders}
💰 <b>Общая выручка:</b> {total_revenue:.2f}₽
📅 <b>Заказов сегодня:</b> {today_orders}

📈 <b>Активность:</b>
└ Средний чек: {(total_revenue / total_orders if total_orders > 0 else 0):.2f}₽
"""
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
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

<i>Нажмите на нужный пакет для оформления заказа</i> 👇
"""
        await query.edit_message_text(
            text,
            reply_markup=get_uc_menu(),
            parse_mode=ParseMode.HTML
        )
    
    # Покупка проходок
    elif data == "buy_passes":
        text = """
🎫 <b>Покупка Royale Pass и Elite Pass</b>

📅 Доступные проходки текущего сезона:

🎫 <b>Royale Pass</b>
├ ✨ Базовая версия
├ 🎁 Доступ к наградам
└ ⏱️ Мгновенная активация

👑 <b>Elite Pass Plus</b>
├ 💎 Премиум версия
├ 🎁 Все награды + бонусы
├ ⚡ +25 уровней сразу
└ ⏱️ Мгновенная активация

<i>Выберите нужную проходку</i> 👇
"""
        await query.edit_message_text(
            text,
            reply_markup=get_passes_menu(),
            parse_mode=ParseMode.HTML
        )
    
    # Прокачка рейтинга
    elif data == "boost_rank":
        text = """
📈 <b>Прокачка рейтинга PUBG Mobile</b>

🎮 <b>Условия:</b>
├ 👨‍💼 Профессиональные бустеры
├ ⚡ Скорость: 1-7 дней (зависит от ранга)
├ 🛡️ Без использования читов
├ 🎯 K/D сохраняется или улучшается
└ 💯 Гарантия результата

⚠️ <b>Важно:</b> Укажите текущий ранг при заказе

<i>Выберите желаемый ранг</i> 👇
"""
        await query.edit_message_text(
            text,
            reply_markup=get_boost_menu(),
            parse_mode=ParseMode.HTML
        )
    
    # Metro Royale услуги
    elif data == "metro_services":
        text = """
🚇 <b>Услуги Metro Royale</b>

🗺️ <b>О режиме Metro Royale:</b>
├ 🎯 PvE/PvP режим выживания
├ 💼 Сбор ценного лута
├ ⚔️ Сражения с ботами и игроками
├ 🚁 Эвакуация с добычей
└ 💰 Продажа найденных предметов

✨ <b>Наши услуги:</b>

🚇 <b>Сопровождение (1 игра)</b>
├ Опытный игрок поможет выжить
├ Гарантированная эвакуация
└ Делёжка лута 50/50

⛏️ <b>Фарм Metro (5 игр)</b>
├ Эффективный фарм ценностей
├ Максимальная добыча
└ Безопасная эвакуация

<i>Выберите нужную услугу</i> 👇
"""
        await query.edit_message_text(
            text,
            reply_markup=get_metro_menu(),
            parse_mode=ParseMode.HTML
        )
    
    # О магазине
    elif data == "about":
        await query.edit_message_text(
            ABOUT_TEXT,
            reply_markup=get_back_button(),
            parse_mode=ParseMode.HTML
        )
    
    # FAQ
    elif data == "faq":
        await query.edit_message_text(
            FAQ_TEXT,
            reply_markup=get_back_button(),
            parse_mode=ParseMode.HTML
        )
    
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
            text = """
💼 <b>Мои заказы</b>

📭 У вас пока нет заказов.

Оформите первый заказ через главное меню! 🎮
"""
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
                    'paid': 'Оплачен, в обработке',
                    'processing': 'В работе',
                    'completed': 'Выполнен',
                    'cancelled': 'Отменен'
                }.get(order['status'], 'Неизвестно')
                
                text += f"""
🔖 <b>Заказ #{order['order_id']}</b>
├ 📦 Товар: {order['item_name']}
├ 💰 Сумма: {order['price']}₽
├ 📊 Статус: {status_emoji} {status_text}
└ 📅 Дата: {order['created_at'][:16]}

"""
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_button()
        )
    
    # Обработка выбора товара
    elif data in PRICES:
        item = PRICES[data]
        item_name = item['name']
        emoji = item['emoji']
        price = item['price']
        
        # Сохраняем выбранный товар для пользователя
        user_data_storage[user_id] = {'item_key': data}
        
        text = f"""
{emoji} <b>Подтверждение заказа</b>

📦 <b>Товар:</b> {item_name}
💰 <b>Цена:</b> {price}₽

⚠️ <b>ВАЖНО: Введите ваш PUBG ID</b>

Отправьте ID вашего аккаунта PUBG Mobile следующим сообщением.

Где найти PUBG ID:
1. Откройте PUBG Mobile
2. Нажмите на иконку профиля
3. ID указан под вашим ником

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
            await query.answer(f"ℹ️ Статус заказа: {order['status']}", show_alert=True)
            return
        
        # Проверяем платеж
        await query.answer("🔄 Проверяю платеж...", show_alert=False)
        
        is_paid, amount = await check_payment(order['payment_label'])
        
        if is_paid:
            # Обновляем статус заказа
            await update_order_status(order_id, 'paid')
            await update_user_stats(user_id, order['price'])
            
            # Уведомляем пользователя
            text = f"""
✅ <b>Платеж получен!</b>

🎉 Заказ #{order_id} успешно оплачен!

📦 <b>Товар:</b> {order['item_name']}
💰 <b>Сумма:</b> {amount}₽
🆔 <b>PUBG ID:</b> {order['pubg_id']}

⏳ <b>Статус:</b> Передан в обработку

Ваш заказ будет выполнен в течение 5-30 минут.
Мы отправим уведомление когда товар будет доставлен!

💬 По вопросам: @MetroShopSupport
"""
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_button()
            )
            
            # Уведомляем админов
            for admin_id in ADMIN_IDS:
                try:
                    admin_text = f"""
🔔 <b>Новый оплаченный заказ!</b>

📦 Заказ: #{order_id}
👤 Пользователь: {query.from_user.first_name} (@{query.from_user.username or 'нет'})
🆔 User ID: {user_id}
💎 Товар: {order['item_name']}
💰 Сумма: {order['price']}₽
🎮 PUBG ID: {order['pubg_id']}

⚡ Требуется выполнение!
"""
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=admin_text,
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
        else:
            await query.answer("⏳ Платеж еще не получен. Попробуйте через минуту.", show_alert=True)
    
    # Отмена заказа
    elif data.startswith('cancel_order_'):
        order_id = data.replace('cancel_order_', '')
        await update_order_status(order_id, 'cancelled')
        
        await query.edit_message_text(
            "❌ <b>Заказ отменен</b>\n\nВыберите нужный раздел:",
            reply_markup=get_main_menu(),
            parse_mode=ParseMode.HTML
        )

async def handle_pubg_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода PUBG ID"""
    user_id = update.effective_user.id
    
    # Проверяем, есть ли у пользователя активный выбор товара
    if user_id not in user_data_storage:
        return
    
    pubg_id = update.message.text.strip()
    
    # Валидация PUBG ID (должен быть числом)
    if not pubg_id.isdigit() or len(pubg_id) < 8:
        await update.message.reply_text(
            "❌ <b>Неверный формат PUBG ID</b>\n\nID должен состоять только из цифр и содержать минимум 8 символов.\n\n<i>Пример: 5123456789</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    item_key = user_data_storage[user_id]['item_key']
    item = PRICES[item_key]
    
    # Создаем заказ
    order_id, payment_label = await create_order(
        user_id=user_id,
        item_key=item_key,
        item_name=item['name'],
        price=item['price'],
        pubg_id=pubg_id
    )
    
    # Генерируем ссылку для оплаты
    payment_url = create_payment_link(
        amount=item['price'],
        label=payment_label,
        description=f"Metro Shop - {item['name']}"
    )
    
    # Очищаем временные данные
    del user_data_storage[user_id]
    
    text = f"""
💳 <b>Оплата заказа #{order_id}</b>

📦 <b>Товар:</b> {item['name']}
🆔 <b>PUBG ID:</b> {pubg_id}
💰 <b>К оплате:</b> {item['price']}₽

🔗 <b>Ссылка для оплаты:</b>
<a href="{payment_url}">Оплатить через ЮMoney</a>

📝 <b>Инструкция:</b>
1️⃣ Перейдите по ссылке выше
2️⃣ Выберите способ оплаты (СБП/карта)
3️⃣ Оплатите точную сумму: {item['price']}₽
4️⃣ Вернитесь в бот и нажмите "Проверить оплату"

⚠️ <b>Важно:</b>
├ Платеж проверяется автоматически
├ Проверка занимает 10-30 секунд
└ Заказ действителен 24 часа

⏰ После оплаты товар будет доставлен в течение 5-30 минут!
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
    pending_orders = await get_pending_orders()
    
    for order in pending_orders:
        is_paid, amount = await check_payment(order['payment_label'])
        
        if is_paid:
            # Обновляем статус заказа
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

Ваш заказ будет выполнен в течение 5-30 минут.
Мы отправим уведомление когда товар будет доставлен!

💬 По вопросам: @MetroShopSupport
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
👤 User ID: {order['user_id']}
💎 Товар: {order['item_name']}
💰 Сумма: {order['price']}₽
🎮 PUBG ID: {order['pubg_id']}

⚡ Требуется выполнение!
"""
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=admin_text,
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass

# ======================== ГЛАВНАЯ ФУНКЦИЯ ========================

async def post_init(application: Application):
    """Инициализация после запуска бота"""
    await init_db()
    logger.info("✅ База данных инициализирована")
    
    # Запускаем периодическую проверку платежей каждые 30 секунд
    job_queue = application.job_queue
    job_queue.run_repeating(check_pending_payments, interval=30, first=10)
    logger.info("✅ Автопроверка платежей запущена")

def main():
    """Запуск бота"""
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("stats", admin_stats))
    
    # Регистрируем обработчик кнопок
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Обработчик текстовых сообщений (для PUBG ID)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pubg_id))
    
    # Запускаем бота
    logger.info("🚀 Metro Shop Bot запущен!")
    logger.info(f"💳 ЮMoney кошелек: {YOOMONEY_WALLET}")
    logger.info(f"👨‍💼 Админы: {ADMIN_IDS}")
    
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == '__main__':
    main()
