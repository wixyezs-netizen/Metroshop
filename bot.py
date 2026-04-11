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

# Попытка загрузить .env (если локально)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# -------------------- КОНФИГУРАЦИЯ --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения!")

admin_ids_str = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()] if admin_ids_str else []

YOOMONEY_ACCESS_TOKEN = os.getenv("YOOMONEY_ACCESS_TOKEN")
YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET")

if not YOOMONEY_ACCESS_TOKEN or not YOOMONEY_WALLET:
    logging.warning("⚠️ YOOMONEY_ACCESS_TOKEN или YOOMONEY_WALLET не заданы! Оплата не будет работать.")
    yoomoney_client = None
else:
    yoomoney_client = Client(YOOMONEY_ACCESS_TOKEN)

DB_PATH = "metro_bot.db"

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
            CREATE TABLE IF NOT EXISTS tariffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price INTEGER NOT NULL,
                description TEXT,
                emoji TEXT,
                category TEXT,
                is_active BOOLEAN DEFAULT 1
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                tariff_id INTEGER NOT NULL,
                route TEXT,
                order_time TIMESTAMP,
                contact TEXT NOT NULL,
                server TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                rating INTEGER CHECK(rating BETWEEN 1 AND 5),
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders(id),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')
        # Очистка старых тарифов и вставка новых
        await db.execute('DELETE FROM tariffs')
        tariffs_new = [
            # Услуги сопровождения по картам
            (1, 'Карта-5️⃣', 350, 'Гарант выноса: 6➖7⚡⚡\nШмотки: Весь лут с типов ваш!', '🤩', 'soprov'),
            (2, 'Карта-7️⃣', 450, 'Гарант выноса: 10➖15⚡⚡\nШмотки: Весь лут с типов ваш!', '🤩', 'soprov'),
            (3, 'Карта-8️⃣', 850, 'Гарант выноса: 12➖⚡⚡\nБилеты: 5➖8⚡⚡\nШмотки: Весь лут с типов ⚡⚡⚡!', '🤩', 'soprov'),
            (4, 'Карта-8️⃣ PRO', 1300, 'Гарант выноса: 18➖⚡⚡\nБилеты: 8➖12⚡⚡\nШмотки: Весь лут с типов ⚡⚡⚡!', '🤩', 'soprov'),
            # Сеты защиты (фулл)
            (5, 'Фулл 6 Обычная', 80, '🪖🧥🎒 (шлем, броня, рюкзак) — базовый комплект', '🤗', 'sets'),
            (6, 'Фулл 6 Кобра', 100, '🪖🧥🎒 улучшенный комплект', '🤗', 'sets'),
            (7, 'Фулл 6 Сталь', 120, '🪖🧥🎒 топовый комплект', '🤗', 'sets'),
            # Оружие и прочее
            (8, 'МК вышка', 30, '🔥 МК вышка', '🤩', 'other'),
        ]
        for t in tariffs_new:
            await db.execute(
                'INSERT INTO tariffs (id, name, price, description, emoji, category) VALUES (?, ?, ?, ?, ?, ?)',
                t
            )
        await db.commit()

# -------------------- ФУНКЦИИ РАБОТЫ С БД --------------------
async def db_add_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)',
            (user_id, username, full_name)
        )
        await db.commit()

async def db_get_tariffs(only_active: bool = True, category: str = None) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        query = 'SELECT id, name, price, description, emoji, category FROM tariffs'
        conditions = []
        params = []
        if only_active:
            conditions.append('is_active = 1')
        if category:
            conditions.append('category = ?')
            params.append(category)
        if conditions:
            query += ' WHERE ' + ' AND '.join(conditions)
        query += ' ORDER BY id'
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [{'id': r[0], 'name': r[1], 'price': r[2], 'description': r[3],
                     'emoji': r[4], 'category': r[5]} for r in rows]

async def db_get_tariff(tariff_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT id, name, price, description, emoji, category FROM tariffs WHERE id = ?',
            (tariff_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return {'id': row[0], 'name': row[1], 'price': row[2], 'description': row[3],
                    'emoji': row[4], 'category': row[5]} if row else None

async def db_create_order(order_id: str, user_id: int, tariff_id: int, contact: str,
                          route: str = None, order_time: str = None, server: str = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            '''INSERT INTO orders (id, user_id, tariff_id, route, order_time, contact, server, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (order_id, user_id, tariff_id, route, order_time, contact, server, 'pending')
        )
        await db.commit()

async def db_update_order_status(order_id: str, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (status, order_id)
        )
        await db.commit()

async def db_get_order(order_id: str) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT o.id, o.user_id, o.tariff_id, o.route, o.order_time, o.contact, o.server, o.status,
                   o.created_at, t.name as tariff_name, t.price as tariff_price, t.emoji,
                   u.username, u.full_name
            FROM orders o
            JOIN tariffs t ON o.tariff_id = t.id
            JOIN users u ON o.user_id = u.user_id
            WHERE o.id = ?
        ''', (order_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                'id': row[0], 'user_id': row[1], 'tariff_id': row[2], 'route': row[3],
                'order_time': row[4], 'contact': row[5], 'server': row[6], 'status': row[7],
                'created_at': row[8], 'tariff_name': row[9], 'tariff_price': row[10],
                'emoji': row[11], 'username': row[12], 'full_name': row[13]
            }

async def db_get_pending_orders() -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id FROM orders WHERE status = ?', ('pending',)) as cursor:
            rows = await cursor.fetchall()
            return [{'id': r[0]} for r in rows]

async def db_get_user_orders(user_id: int) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT o.id, t.name, t.emoji, o.route, o.order_time, o.status, o.created_at
            FROM orders o
            JOIN tariffs t ON o.tariff_id = t.id
            WHERE o.user_id = ?
            ORDER BY o.created_at DESC
        ''', (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [{'id': r[0], 'tariff': r[1], 'emoji': r[2], 'route': r[3],
                     'time': r[4], 'status': r[5], 'created': r[6]} for r in rows]

async def db_get_all_orders(status_filter: Optional[str] = None) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        query = '''
            SELECT o.id, o.user_id, t.name, t.emoji, o.route, o.order_time, o.status, o.created_at, u.username
            FROM orders o
            JOIN tariffs t ON o.tariff_id = t.id
            JOIN users u ON o.user_id = u.user_id
        '''
        params = []
        if status_filter:
            query += ' WHERE o.status = ?'
            params.append(status_filter)
        query += ' ORDER BY o.created_at DESC'
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [{'id': r[0], 'user_id': r[1], 'tariff': r[2], 'emoji': r[3],
                     'route': r[4], 'time': r[5], 'status': r[6], 'created': r[7],
                     'username': r[8]} for r in rows]

async def db_get_all_users() -> List[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM users') as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

# -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
def create_payment_link(amount: float, label: str, description: str) -> Optional[str]:
    if not YOOMONEY_WALLET:
        return None
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
    if not yoomoney_client:
        return False
    try:
        history = yoomoney_client.operation_history(label=label)
        for op in history.operations:
            if op.status == 'success':
                return True
    except Exception as e:
        logging.error(f"Ошибка проверки платежа {label}: {e}")
    return False

async def notify_admin_new_order(order_id: str):
    if not ADMIN_IDS:
        return
    order = await db_get_order(order_id)
    if not order:
        return
    text = (
        f"⚡⚡⚡ НОВЫЙ ЗАКАЗ ⚡⚡⚡\n"
        f"🆔 <code>{order['id'][:8]}</code>\n"
        f"{order['emoji']} {order['tariff_name']}\n"
        f"💰 | {order['tariff_price']}руб |\n"
        f"👤 {order['full_name']} (@{order['username']})\n"
        f"📞 {order['contact']}\n"
        f"🌍 Сервер: {order['server'] or '—'}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except:
            pass

async def notify_user_paid(order_id: str):
    order = await db_get_order(order_id)
    if order:
        try:
            text = (
                f"⚡⚡⚡ ОПЛАТА ПРОШЛА ⚡⚡⚡\n"
                f"✅ {order['emoji']} {order['tariff_name']} ✅\n"
                f"➖➖➖➖➖➖➖➖➖➖➖\n"
                f"Спасибо за заказ! Ожидайте связи.\n"
                f"➖➖➖➖➖➖➖➖➖➖➖\n"
                f"🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋"
            )
            await bot.send_message(order['user_id'], text, parse_mode="HTML")
        except:
            pass

# -------------------- ФОНОВАЯ ПРОВЕРКА ПЛАТЕЖЕЙ --------------------
async def payment_checker():
    if not yoomoney_client:
        logging.warning("Фоновая проверка отключена (нет токена ЮMoney).")
        return
    while True:
        try:
            pending = await db_get_pending_orders()
            for p in pending:
                order_id = p['id']
                if check_payment_by_label(order_id):
                    await db_update_order_status(order_id, 'paid')
                    await notify_user_paid(order_id)
                    await notify_admin_new_order(order_id)
                    logging.info(f"Заказ {order_id} автоматически подтверждён")
        except Exception as e:
            logging.error(f"Ошибка в фоновой проверке: {e}")
        await asyncio.sleep(15)

# -------------------- КЛАВИАТУРЫ --------------------
def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🛒 КАТАЛОГ УСЛУГ", callback_data="catalog"))
    builder.row(InlineKeyboardButton(text="👤 ПРОФИЛЬ", callback_data="profile"))
    builder.row(InlineKeyboardButton(text="ℹ️ О СЕРВИСЕ", callback_data="about"))
    if user_id in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="⚙️ АДМИН-ПАНЕЛЬ", callback_data="admin"))
    return builder.as_markup()

def catalog_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🤩 СОПРОВОЖДЕНИЕ ПО КАРТАМ", callback_data="cat_soprov"))
    builder.row(InlineKeyboardButton(text="🛡️ ФУЛЛ СЕТЫ", callback_data="cat_sets"))
    builder.row(InlineKeyboardButton(text="🔫 ДРУГОЕ", callback_data="cat_other"))
    builder.row(InlineKeyboardButton(text="🔙 НАЗАД", callback_data="back_to_main"))
    return builder.as_markup()

def tariff_keyboard(tariffs: List[Dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        text = f"{t['emoji']} {t['name']} — | {t['price']}₽ |"
        builder.row(InlineKeyboardButton(text=text, callback_data=f"tariff_{t['id']}"))
    builder.row(InlineKeyboardButton(text="🔙 НАЗАД", callback_data="catalog"))
    return builder.as_markup()

def payment_keyboard(payment_url: str, order_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 | ОПЛАТИТЬ |", url=payment_url))
    builder.row(InlineKeyboardButton(text="🔄 ПРОВЕРИТЬ ОПЛАТУ", callback_data=f"check_{order_id}"))
    builder.row(InlineKeyboardButton(text="🔙 В КАТАЛОГ", callback_data="catalog"))
    return builder.as_markup()

# -------------------- СОСТОЯНИЯ FSM --------------------
class OrderState(StatesGroup):
    choosing_tariff = State()
    entering_route = State()
    entering_time = State()
    entering_contact = State()
    entering_server = State()

class AdminState(StatesGroup):
    waiting_broadcast_text = State()

# -------------------- ОБРАБОТЧИКИ ПОЛЬЗОВАТЕЛЯ --------------------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await db_add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    text = (
        "🤩⚡⚡⚡⚡⚡⚡🤩\n"
        "       METRO CARRY SHOP\n"
        "🤩⚡⚡⚡⚡⚡⚡⚡⚡🤩\n"
        "🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋🦋\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "🔥 НОВЫЙ СЕЗОН — АДЕКВАТНЫЕ ЦЕНЫ! 🔥\n"
        "Выберите действие:"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_keyboard(message.from_user.id))

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🤩⚡⚡⚡ ГЛАВНОЕ МЕНЮ ⚡⚡⚡🤩",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(callback.from_user.id)
    )

@dp.callback_query(F.data == "about")
async def show_about(callback: CallbackQuery):
    text = (
        "ℹ️ О СЕРВИСЕ\n"
        "➖➖➖➖➖➖➖➖➖➖➖\n"
        "Мы предоставляем услуги сопровождения в Metro Royale (PUBG Mobile).\n"
        "✔ Гарантия выноса\n"
        "✔ Весь лут ваш\n"
        "✔ Быстрая выдача\n\n"
        "💰 Оплата через ЮMoney\n"
        "📞 По вопросам: 😎 @PeRF_men"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard(callback.from_user.id))

@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    user = callback.from_user
    orders_count = len(await db_get_user_orders(user.id))
    text = (
        f"👤 ПРОФИЛЬ\n"
        f"➖➖➖➖➖➖➖➖➖➖➖\n"
        f"Имя: {user.full_name}\n"
        f"Username: @{user.username}\n"
        f"ID: <code>{user.id}</code>\n"
        f"Заказов: {orders_count}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard(callback.from_user.id))

@dp.callback_query(F.data == "catalog")
async def show_catalog(callback: CallbackQuery):
    await callback.message.edit_text(
        "🛒 ВЫБЕРИТЕ КАТЕГОРИЮ:",
        reply_markup=catalog_keyboard()
    )

@dp.callback_query(F.data.startswith("cat_"))
async def show_tariffs_by_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.replace("cat_", "")
    tariffs = await db_get_tariffs(category=category)
    if not tariffs:
        await callback.answer("В этой категории пока нет услуг", show_alert=True)
        return
    await state.set_state(OrderState.choosing_tariff)
    await callback.message.edit_text(
        "🤩 ВЫБЕРИТЕ УСЛУГУ:",
        reply_markup=tariff_keyboard(tariffs)
    )

@dp.callback_query(F.data.startswith("tariff_"))
async def process_tariff(callback: CallbackQuery, state: FSMContext):
    tariff_id = int(callback.data.split("_")[1])
    tariff = await db_get_tariff(tariff_id)
    if not tariff:
        await callback.answer("Тариф не найден")
        return

    await state.update_data(tariff_id=tariff_id, price=tariff['price'],
                            tariff_name=tariff['name'], emoji=tariff['emoji'],
                            category=tariff['category'])

    # Для сопровождения запрашиваем маршрут и время, для остальных – сразу контакт
    if tariff['category'] == 'soprov':
        await state.set_state(OrderState.entering_route)
        text = (
            f"⚡⚡⚡⚡⚡⚡\n"
            f"{tariff['emoji']} {tariff['name']} {tariff['emoji']}\n"
            f"➖➖➖➖➖➖➖➖➖➖➖\n"
            f"{tariff['description']}\n"
            f"➖➖➖➖➖➖➖➖➖➖➖\n"
            f"💰 Цена: | {tariff['price']}руб |\n"
            f"⚡⚡⚡⚡⚡⚡\n\n"
            f"🛤 <b>Введите маршрут (откуда и куда):</b>"
        )
    else:
        await state.set_state(OrderState.entering_contact)
        text = (
            f"⚡⚡⚡⚡⚡⚡\n"
            f"{tariff['emoji']} {tariff['name']} {tariff['emoji']}\n"
            f"➖➖➖➖➖➖➖➖➖➖➖\n"
            f"{tariff['description']}\n"
            f"➖➖➖➖➖➖➖➖➖➖➖\n"
            f"💰 Цена: | {tariff['price']}руб |\n"
            f"⚡⚡⚡⚡⚡⚡\n\n"
            f"📞 <b>Введите ваш контакт (Telegram/Discord/ник):</b>"
        )
    await callback.message.edit_text(text, parse_mode="HTML")

@dp.message(OrderState.entering_route)
async def process_route(message: Message, state: FSMContext):
    await state.update_data(route=message.text)
    await state.set_state(OrderState.entering_time)
    await message.answer("⏰ <b>Укажите желаемое время (например, сегодня 20:00):</b>", parse_mode="HTML")

@dp.message(OrderState.entering_time)
async def process_time(message: Message, state: FSMContext):
    await state.update_data(order_time=message.text)
    await state.set_state(OrderState.entering_contact)
    await message.answer("📞 <b>Введите ваш контакт (Telegram/Discord/ник):</b>", parse_mode="HTML")

@dp.message(OrderState.entering_contact)
async def process_contact(message: Message, state: FSMContext):
    await state.update_data(contact=message.text)
    data = await state.get_data()
    if data.get('category') == 'soprov':
        await state.set_state(OrderState.entering_server)
        await message.answer("🌍 <b>Введите ваш сервер/регион (например, EU, CIS):</b>", parse_mode="HTML")
    else:
        await state.update_data(server=None)
        await finalize_order(message, state)

@dp.message(OrderState.entering_server)
async def process_server(message: Message, state: FSMContext):
    await state.update_data(server=message.text)
    await finalize_order(message, state)

async def finalize_order(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = str(uuid.uuid4())
    user_id = message.from_user.id

    await db_create_order(
        order_id, user_id,
        data['tariff_id'], data['contact'],
        route=data.get('route'), order_time=data.get('order_time'),
        server=data.get('server')
    )

    payment_url = create_payment_link(
        amount=data['price'],
        label=order_id,
        description=f"{data['emoji']} {data['tariff_name']}"
    )

    if not payment_url:
        await message.answer("❌ Ошибка создания платежа. Попробуйте позже.")
        return

    await state.clear()
    text = (
        f"⚡⚡⚡ ЗАКАЗ №{order_id[:8]} ⚡⚡⚡\n"
        f"{data['emoji']} {data['tariff_name']} {data['emoji']}\n"
        f"➖➖➖➖➖➖➖➖➖➖➖\n"
        f"💰 К ОПЛАТЕ: | {data['price']}руб |\n"
    )
    if data.get('route'):
        text += f"🛤 Маршрут: {data['route']}\n"
    if data.get('order_time'):
        text += f"⏰ Время: {data['order_time']}\n"
    text += (
        f"📞 Контакты: {data['contact']}\n"
        f"🌍 Сервер: {data.get('server', '—')}\n"
        f"➖➖➖➖➖➖➖➖➖➖➖\n"
        f"💳 <i>Оплата проверяется автоматически</i>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=payment_keyboard(payment_url, order_id))
    await notify_admin_new_order(order_id)

@dp.callback_query(F.data.startswith("check_"))
async def manual_check_payment(callback: CallbackQuery):
    order_id = callback.data.replace("check_", "")
    if check_payment_by_label(order_id):
        await db_update_order_status(order_id, 'paid')
        await notify_user_paid(order_id)
        await callback.message.edit_text(callback.message.text + "\n\n✅ Оплата получена!", reply_markup=None)
    else:
        await callback.answer("❌ Оплата ещё не найдена. Попробуйте позже.", show_alert=True)

# -------------------- АДМИН-ПАНЕЛЬ (базовая) --------------------
@dp.callback_query(F.data == "admin")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещён")
        return
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📦 ЗАКАЗЫ", callback_data="admin_orders"))
    builder.row(InlineKeyboardButton(text="📨 РАССЫЛКА", callback_data="admin_broadcast"))
    builder.row(InlineKeyboardButton(text="🔙 ВЫХОД", callback_data="back_to_main"))
    await callback.message.edit_text("⚙️ АДМИН-ПАНЕЛЬ", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "admin_orders")
async def admin_orders(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    orders = await db_get_all_orders()
    if not orders:
        await callback.message.edit_text("Заказов нет.", reply_markup=admin_panel_keyboard())
        return
    text = "📋 ПОСЛЕДНИЕ ЗАКАЗЫ:\n\n"
    for o in orders[:5]:
        text += f"<code>{o['id'][:8]}</code> {o['emoji']} {o['tariff']} — {o['status']} ({o['username']})\n"
    await callback.message.edit_text(text, parse_mode="HTML")

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminState.waiting_broadcast_text)
    await callback.message.edit_text("📨 Введите текст для рассылки:")

@dp.message(AdminState.waiting_broadcast_text)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    users = await db_get_all_users()
    success = 0
    for uid in users:
        try:
            await bot.send_message(uid, message.text)
            success += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await state.clear()
    await message.answer(f"✅ Рассылка завершена. Отправлено {success}/{len(users)} пользователям.")

# -------------------- ЗАПУСК --------------------
async def on_startup():
    await init_db()
    asyncio.create_task(payment_checker())
    logging.info("Metro Carry Shop запущен!")

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
