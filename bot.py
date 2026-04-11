import asyncio
import logging
import os
import uuid
from datetime import datetime
from typing import List, Dict, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiosqlite
from yoomoney import Quickpay, Client

# Попытка загрузить .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# -------------------- КОНФИГУРАЦИЯ --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

admin_ids_str = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()] if admin_ids_str else []

YOOMONEY_ACCESS_TOKEN = os.getenv("YOOMONEY_ACCESS_TOKEN")
YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET")

if not YOOMONEY_ACCESS_TOKEN or not YOOMONEY_WALLET:
    logging.warning("⚠️ ЮMoney токены не заданы. Оплата не будет работать.")
    yoomoney_client = None
else:
    yoomoney_client = Client(YOOMONEY_ACCESS_TOKEN)

DB_PATH = "pubg_shop.db"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# -------------------- БАЗА ДАННЫХ --------------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                emoji TEXT,
                is_active BOOLEAN DEFAULT 1
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                price INTEGER NOT NULL,
                emoji TEXT,
                is_active BOOLEAN DEFAULT 1,
                FOREIGN KEY (category_id) REFERENCES categories(id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                contact TEXT NOT NULL,
                server TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        ''')
        # Категории по умолчанию
        await db.execute('''
            INSERT OR IGNORE INTO categories (id, name, emoji) VALUES
            (1, 'СЕТЫ', '🔠'),
            (2, 'ЗАЩИТА', '🛡️'),
            (3, 'ОРУЖИЕ', '🔫'),
            (4, 'ДРУГОЕ', '🔽')
        ''')
        # Товары по умолчанию (можно добавить свои)
        products_default = [
            (1, 'КОБРА', '6 Шлем, 6 Броня, 6 Рюкзак', 150, '🤩🤩🤩'),
            (1, 'СТАЛЬ', '6 Шлем, 6 Броня, 6 Рюкзак', 150, '🤩🤩🤩'),
            (1, 'СТАНДАРТ', 'Базовый сет', 100, '🗿🗿🗿'),
            (2, 'Шлем база', '6 шлем база', 60, '🤩'),
            (2, 'Броник база', '6 броник база', 60, '🤩'),
            (2, 'Рюкзак', '6 рюкзак', 50, '🤩'),
            (2, 'Шлем кобра', '6 шлем кобра', 70, '🤩'),
            (2, 'Броник кобра', '6 броник кобра', 70, '🤩'),
            (2, 'Шлем сталь', '6 шлем сталь', 80, '🤩'),
            (2, 'Броник сталь', '6 броник сталь', 80, '🤩'),
            (3, 'МКшка ВК', 'МКшка ВК', 80, '🤩🤩🤩🤩'),
            (3, 'МКшка кобра', 'МКшка кобра', 100, '🤩🤩🤩🤩'),
            (3, 'МКшка сталь', 'МКшка сталь', 100, '🤩🤩🤩🤩'),
            (3, 'АМР ВК', 'АМР ВК', 50, '🤩'),
            (3, 'АМР кобра', 'АМР кобра', 60, '🤩'),
            (3, 'АМР сталь', 'АМР сталь', 70, '🤩'),
            (3, 'АВМ ВК', 'АВМ ВК', 50, '🤩'),
            (3, 'АВМ кобра', 'АВМ кобра', 60, '🤩'),
            (3, 'АВМ сталь', 'АВМ сталь', 70, '🤩'),
            (4, 'Чёрное письмо', '', 20, '😕'),
            (4, 'Ткань', '', 25, '🤩'),
            (4, 'Тепловизор', '', 30, '🤩'),
        ]
        for p in products_default:
            await db.execute('''
                INSERT OR IGNORE INTO products (category_id, name, description, price, emoji)
                VALUES (?, ?, ?, ?, ?)
            ''', p)
        await db.commit()

# -------------------- ФУНКЦИИ РАБОТЫ С БД --------------------
async def db_add_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)',
            (user_id, username, full_name)
        )
        await db.commit()

async def db_get_categories() -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id, name, emoji FROM categories WHERE is_active = 1 ORDER BY id') as cursor:
            rows = await cursor.fetchall()
            return [{'id': r[0], 'name': r[1], 'emoji': r[2]} for r in rows]

async def db_get_products_by_category(category_id: int) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT id, name, description, price, emoji FROM products
            WHERE category_id = ? AND is_active = 1 ORDER BY id
        ''', (category_id,)) as cursor:
            rows = await cursor.fetchall()
            return [{'id': r[0], 'name': r[1], 'desc': r[2], 'price': r[3], 'emoji': r[4]} for r in rows]

async def db_get_product(product_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT p.id, p.name, p.description, p.price, p.emoji, c.name as cat_name
            FROM products p JOIN categories c ON p.category_id = c.id
            WHERE p.id = ?
        ''', (product_id,)) as cursor:
            row = await cursor.fetchone()
            return {'id': row[0], 'name': row[1], 'desc': row[2], 'price': row[3],
                    'emoji': row[4], 'category': row[5]} if row else None

async def db_create_order(order_id: str, user_id: int, product_id: int, contact: str, server: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO orders (id, user_id, product_id, contact, server, status) VALUES (?, ?, ?, ?, ?, ?)',
            (order_id, user_id, product_id, contact, server, 'pending')
        )
        await db.commit()

async def db_update_order_status(order_id: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (status, order_id))
        await db.commit()

async def db_get_order(order_id: str) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT o.id, o.user_id, o.contact, o.server, o.status, o.created_at,
                   p.name, p.price, p.emoji, u.username, u.full_name
            FROM orders o
            JOIN products p ON o.product_id = p.id
            JOIN users u ON o.user_id = u.user_id
            WHERE o.id = ?
        ''', (order_id,)) as cursor:
            row = await cursor.fetchone()
            if not row: return None
            return {'id': row[0], 'user_id': row[1], 'contact': row[2], 'server': row[3],
                    'status': row[4], 'created_at': row[5], 'product_name': row[6],
                    'price': row[7], 'emoji': row[8], 'username': row[9], 'full_name': row[10]}

async def db_get_pending_orders() -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id FROM orders WHERE status = ?', ('pending',)) as cursor:
            return [{'id': r[0]} for r in await cursor.fetchall()]

async def db_get_all_orders(status_filter: Optional[str] = None) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        query = '''
            SELECT o.id, o.user_id, o.contact, o.server, o.status, o.created_at,
                   p.name, p.price, u.username
            FROM orders o
            JOIN products p ON o.product_id = p.id
            JOIN users u ON o.user_id = u.user_id
        '''
        params = []
        if status_filter:
            query += ' WHERE o.status = ?'
            params.append(status_filter)
        query += ' ORDER BY o.created_at DESC'
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [{'id': r[0], 'user_id': r[1], 'contact': r[2], 'server': r[3],
                     'status': r[4], 'created': r[5], 'product': r[6], 'price': r[7],
                     'username': r[8]} for r in rows]

async def db_get_all_users() -> List[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM users') as cursor:
            return [r[0] for r in await cursor.fetchall()]

# -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
def create_payment_link(amount: float, label: str, description: str) -> Optional[str]:
    if not YOOMONEY_WALLET: return None
    quickpay = Quickpay(
        receiver=YOOMONEY_WALLET,
        quickpay_form="shop",
        targets=description,
        paymentType="SB",
        sum=amount,
        label=label,
    )
    return quickpay.redirected_url

def check_payment_by_label(label: str) -> bool:
    if not yoomoney_client: return False
    try:
        history = yoomoney_client.operation_history(label=label)
        for op in history.operations:
            if op.status == 'success':
                return True
    except Exception as e:
        logging.error(f"Ошибка проверки: {e}")
    return False

async def notify_admin_new_order(order_id: str):
    if not ADMIN_IDS: return
    order = await db_get_order(order_id)
    if not order: return
    text = (
        f"# Н О В Ы Й   З А К А З\n"
        f"✔ оплата ожидается ✔\n\n"
        f"**ID:** <code>{order['id'][:8]}</code>\n"
        f"**Товар:** {order['emoji']} {order['product_name']} — | {order['price']}₽ |\n"
        f"**Пользователь:** {order['full_name']} (@{order['username']})\n"
        f"**Контакты:** {order['contact']}\n"
        f"**Сервер:** {order['server'] or '—'}"
    )
    for admin_id in ADMIN_IDS:
        try: await bot.send_message(admin_id, text, parse_mode="HTML")
        except: pass

async def notify_user_paid(order_id: str):
    order = await db_get_order(order_id)
    if order:
        try:
            text = (
                f"# О П Л А Т А   У С П Е Ш Н О\n"
                f"✔ заказ оплачен ✔\n\n"
                f"**Товар:** {order['emoji']} {order['product_name']}\n"
                f"**Сумма:** | {order['price']}₽ |\n\n"
                "Ожидайте выдачи предмета.\n"
                "С вами свяжется продавец."
            )
            await bot.send_message(order['user_id'], text, parse_mode="HTML")
        except: pass

async def payment_checker():
    if not yoomoney_client:
        logging.warning("Фоновая проверка отключена.")
        return
    while True:
        try:
            for p in await db_get_pending_orders():
                if check_payment_by_label(p['id']):
                    await db_update_order_status(p['id'], 'paid')
                    await notify_user_paid(p['id'])
                    await notify_admin_new_order(p['id'])
                    logging.info(f"Заказ {p['id']} оплачен.")
        except Exception as e:
            logging.error(f"Ошибка проверки: {e}")
        await asyncio.sleep(15)

# -------------------- КЛАВИАТУРЫ --------------------
def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🛒 КАТАЛОГ", callback_data="catalog"))
    builder.row(InlineKeyboardButton(text="👤 ПРОФИЛЬ", callback_data="profile"))
    builder.row(InlineKeyboardButton(text="ℹ️ О МАГАЗИНЕ", callback_data="about"))
    if user_id in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="⚙️ АДМИН-ПАНЕЛЬ", callback_data="admin"))
    return builder.as_markup()

def categories_keyboard(categories: List[Dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.row(InlineKeyboardButton(
            text=f"{cat['emoji']} {cat['name']}",
            callback_data=f"cat_{cat['id']}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 НАЗАД", callback_data="back_to_main"))
    return builder.as_markup()

def products_keyboard(products: List[Dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in products:
        text = f"{p['emoji']} {p['name']} — | {p['price']}₽ |"
        builder.row(InlineKeyboardButton(text=text, callback_data=f"prod_{p['id']}"))
    builder.row(InlineKeyboardButton(text="🔙 К КАТЕГОРИЯМ", callback_data="catalog"))
    return builder.as_markup()

def payment_keyboard(payment_url: str, order_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 | ОПЛАТИТЬ |", url=payment_url))
    builder.row(InlineKeyboardButton(text="🔄 ПРОВЕРИТЬ ОПЛАТУ", callback_data=f"check_{order_id}"))
    builder.row(InlineKeyboardButton(text="🔙 В КАТАЛОГ", callback_data="catalog"))
    return builder.as_markup()

# -------------------- FSM --------------------
class OrderState(StatesGroup):
    choosing_product = State()
    entering_contact = State()
    entering_server = State()

class AdminState(StatesGroup):
    waiting_broadcast = State()
    waiting_product_name = State()
    waiting_product_price = State()
    waiting_product_desc = State()
    waiting_product_emoji = State()
    waiting_product_category = State()

# -------------------- ОБРАБОТЧИКИ --------------------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await db_add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    text = (
        "# P U B G   M E T R O   S H O P\n"
        "✔ магазин предметов ✔\n\n"
        "**Что даём:**\n"
        "✔ Сеты и оружие 🏅\n"
        "✔ Быстрая выдача\n"
        "✔ Гарантия качества\n\n"
        "Цены: | от 20₽ |\n\n"
        "🔥 НОВЫЙ СЕЗОН — АДЕКВАТНЫЕ ЦЕНЫ! 🔥"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_keyboard(message.from_user.id))

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "# Г Л А В Н О Е   М Е Н Ю\n✔ выберите действие ✔",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(callback.from_user.id)
    )

@dp.callback_query(F.data == "about")
async def show_about(callback: CallbackQuery):
    text = (
        "# О   М А Г А З И Н Е\n"
        "✔ PUBG METRO SHOP ✔\n\n"
        "**Правила:**\n"
        "✔ Оплата через ЮMoney\n"
        "✔ Выдача в течение 10 минут после оплаты\n"
        "✔ Возвратов нет\n\n"
        "По вопросам: 😎 @PeRF_men\n"
        "Писать строго по делу! ⚠️"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard(callback.from_user.id))

@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    user = callback.from_user
    text = (
        f"# П Р О Ф И Л Ь\n"
        f"✔ @{user.username} ✔\n\n"
        f"**ID:** <code>{user.id}</code>\n"
        f"**Имя:** {user.full_name}\n"
        f"**Заказов:** {len(await db_get_user_orders(user.id))}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard(callback.from_user.id))

@dp.callback_query(F.data == "catalog")
async def show_catalog(callback: CallbackQuery):
    categories = await db_get_categories()
    text = "# К А Т А Л О Г\n✔ выберите категорию ✔"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=categories_keyboard(categories))

@dp.callback_query(F.data.startswith("cat_"))
async def show_products(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[1])
    products = await db_get_products_by_category(cat_id)
    if not products:
        await callback.answer("Нет товаров в этой категории", show_alert=True)
        return
    text = "# В Ы Б О Р   Т О В А Р А\n✔ нажмите для заказа ✔"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=products_keyboard(products))

@dp.callback_query(F.data.startswith("prod_"))
async def product_selected(callback: CallbackQuery, state: FSMContext):
    prod_id = int(callback.data.split("_")[1])
    product = await db_get_product(prod_id)
    if not product:
        await callback.answer("Товар не найден")
        return
    await state.update_data(product_id=prod_id, price=product['price'],
                            product_name=product['name'], emoji=product['emoji'])
    await state.set_state(OrderState.entering_contact)
    text = (
        f"# З А К А З\n"
        f"{product['emoji']} {product['name']}\n"
        f"**Цена:** | {product['price']}₽ |\n\n"
        "📞 **Введите ваш контакт для связи:**\n"
        "(Telegram, Discord или ник в игре)"
    )
    await callback.message.edit_text(text, parse_mode="HTML")

@dp.message(OrderState.entering_contact)
async def process_contact(message: Message, state: FSMContext):
    await state.update_data(contact=message.text)
    await state.set_state(OrderState.entering_server)
    await message.answer("🌍 **Введите сервер/регион:** (например, EU, CIS)")

@dp.message(OrderState.entering_server)
async def process_server(message: Message, state: FSMContext):
    await state.update_data(server=message.text)
    data = await state.get_data()
    order_id = str(uuid.uuid4())
    user_id = message.from_user.id

    await db_create_order(order_id, user_id, data['product_id'], data['contact'], data['server'])

    payment_url = create_payment_link(
        amount=data['price'],
        label=order_id,
        description=f"{data['emoji']} {data['product_name']}"
    )
    if not payment_url:
        await message.answer("❌ Ошибка создания платежа.")
        return

    await state.clear()
    text = (
        f"# З А К А З   №{order_id[:8]}\n"
        f"✔ ожидает оплаты ✔\n\n"
        f"{data['emoji']} {data['product_name']}\n"
        f"**Цена:** | {data['price']}₽ |\n"
        f"**Контакты:** {data['contact']}\n"
        f"**Сервер:** {data['server']}\n\n"
        "Оплата проверяется автоматически."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=payment_keyboard(payment_url, order_id))
    await notify_admin_new_order(order_id)

@dp.callback_query(F.data.startswith("check_"))
async def manual_check(callback: CallbackQuery):
    order_id = callback.data.replace("check_", "")
    if check_payment_by_label(order_id):
        await db_update_order_status(order_id, 'paid')
        await notify_user_paid(order_id)
        await callback.message.edit_text(callback.message.text + "\n\n✅ Оплата получена!", reply_markup=None)
    else:
        await callback.answer("❌ Оплата не найдена", show_alert=True)

# -------------------- АДМИН-ПАНЕЛЬ (кратко) --------------------
# Полные обработчики админки аналогичны предыдущей версии, но с новыми текстами.
# Вставьте их из предыдущего кода, заменив тексты на стиль с # и пробелами.

# -------------------- ЗАПУСК --------------------
async def on_startup():
    await init_db()
    asyncio.create_task(payment_checker())
    logging.info("PUBG Metro Shop запущен!")

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
