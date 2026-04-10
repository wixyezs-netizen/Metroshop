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

# Попытка загрузить .env (если есть) – не обязательно на хостинге
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
if admin_ids_str:
    ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]
else:
    ADMIN_IDS = []

YOOMONEY_ACCESS_TOKEN = os.getenv("YOOMONEY_ACCESS_TOKEN")
YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET")

# Проверка только если реально нужна оплата (можно ослабить для теста)
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
                is_active BOOLEAN DEFAULT 1
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                tariff_id INTEGER NOT NULL,
                route TEXT NOT NULL,
                order_time TIMESTAMP NOT NULL,
                contact TEXT NOT NULL,
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
        # Тарифы по умолчанию
        await db.execute('''
            INSERT OR IGNORE INTO tariffs (id, name, price, description) VALUES
            (1, '1 час', 500, 'Сопровождение в течение 1 часа'),
            (2, '3 часа', 1200, 'Сопровождение в течение 3 часов'),
            (3, 'Весь день', 2500, 'Сопровождение на целый день (до 8 часов)')
        ''')
        await db.commit()

# ... (все остальные функции db_... остаются без изменений, копируйте их из предыдущего кода)

# Вставляю все функции для краткости в ответе, но вам нужно взять их из предыдущего полного кода.
# Здесь приведу только изменённые/важные части.

async def db_add_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)',
            (user_id, username, full_name)
        )
        await db.commit()

async def db_get_tariffs(only_active: bool = True) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        query = 'SELECT id, name, price, description FROM tariffs'
        if only_active:
            query += ' WHERE is_active = 1'
        query += ' ORDER BY id'
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [{'id': r[0], 'name': r[1], 'price': r[2], 'description': r[3]} for r in rows]

async def db_get_tariff(tariff_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT id, name, price, description FROM tariffs WHERE id = ?',
            (tariff_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return {'id': row[0], 'name': row[1], 'price': row[2], 'description': row[3]} if row else None

async def db_create_order(order_id: str, user_id: int, tariff_id: int,
                          route: str, order_time: str, contact: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            '''INSERT INTO orders (id, user_id, tariff_id, route, order_time, contact, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (order_id, user_id, tariff_id, route, order_time, contact, 'pending')
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
            SELECT o.id, o.user_id, o.tariff_id, o.route, o.order_time, o.contact, o.status,
                   o.created_at, t.name as tariff_name, t.price as tariff_price,
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
                'order_time': row[4], 'contact': row[5], 'status': row[6],
                'created_at': row[7], 'tariff_name': row[8], 'tariff_price': row[9],
                'username': row[10], 'full_name': row[11]
            }

async def db_get_pending_orders() -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id FROM orders WHERE status = ?', ('pending',)) as cursor:
            rows = await cursor.fetchall()
            return [{'id': r[0]} for r in rows]

async def db_get_user_orders(user_id: int) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT o.id, t.name, o.route, o.order_time, o.status, o.created_at
            FROM orders o
            JOIN tariffs t ON o.tariff_id = t.id
            WHERE o.user_id = ?
            ORDER BY o.created_at DESC
        ''', (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [{'id': r[0], 'tariff': r[1], 'route': r[2], 'time': r[3],
                     'status': r[4], 'created': r[5]} for r in rows]

async def db_get_all_orders(status_filter: Optional[str] = None) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        query = '''
            SELECT o.id, o.user_id, t.name, o.route, o.order_time, o.status, o.created_at, u.username
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
            return [{'id': r[0], 'user_id': r[1], 'tariff': r[2], 'route': r[3],
                     'time': r[4], 'status': r[5], 'created': r[6], 'username': r[7]} for r in rows]

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
        f"🆕 <b>Новый заказ</b>\n"
        f"ID: <code>{order['id']}</code>\n"
        f"Пользователь: {order['full_name']} (@{order['username']})\n"
        f"Тариф: {order['tariff_name']} ({order['tariff_price']} ₽)\n"
        f"Маршрут: {order['route']}\n"
        f"Время: {order['order_time']}\n"
        f"Контакты: {order['contact']}"
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
            await bot.send_message(
                order['user_id'],
                f"✅ Ваш заказ <code>{order['id'][:8]}</code> оплачен! Ожидайте связи от сопровождающего.",
                parse_mode="HTML"
            )
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
    builder.row(InlineKeyboardButton(text="🚇 Заказать сопровождение", callback_data="order"))
    builder.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"))
    builder.row(InlineKeyboardButton(text="ℹ️ О сервисе", callback_data="about"))
    if user_id in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin"))
    return builder.as_markup()

def profile_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📋 Мои заказы", callback_data="my_orders"))
    builder.row(InlineKeyboardButton(text="🔙 На главную", callback_data="back_to_main"))
    return builder.as_markup()

def tariff_keyboard(tariffs: List[Dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        builder.row(InlineKeyboardButton(
            text=f"{t['name']} — {t['price']} ₽",
            callback_data=f"tariff_{t['id']}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()

def payment_keyboard(payment_url: str, order_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Оплатить", url=payment_url))
    builder.row(InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_payment_{order_id}"))
    builder.row(InlineKeyboardButton(text="🔙 К тарифам", callback_data="order"))
    return builder.as_markup()

def admin_main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📦 Заказы", callback_data="admin_orders"))
    builder.row(InlineKeyboardButton(text="🏷️ Управление тарифами", callback_data="admin_tariffs"))
    builder.row(InlineKeyboardButton(text="📨 Рассылка", callback_data="admin_broadcast"))
    builder.row(InlineKeyboardButton(text="🔙 Выход", callback_data="back_to_main"))
    return builder.as_markup()

def admin_orders_filter_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Все", callback_data="admin_orders_all"))
    builder.row(InlineKeyboardButton(text="Ожидают оплаты", callback_data="admin_orders_pending"))
    builder.row(InlineKeyboardButton(text="Оплачены", callback_data="admin_orders_paid"))
    builder.row(InlineKeyboardButton(text="В работе", callback_data="admin_orders_in_progress"))
    builder.row(InlineKeyboardButton(text="Завершены", callback_data="admin_orders_completed"))
    builder.row(InlineKeyboardButton(text="Отменены", callback_data="admin_orders_cancelled"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin"))
    return builder.as_markup()

# -------------------- СОСТОЯНИЯ FSM --------------------
class OrderState(StatesGroup):
    choosing_tariff = State()
    entering_route = State()
    entering_time = State()
    entering_contact = State()

class AdminState(StatesGroup):
    waiting_broadcast_text = State()
    waiting_tariff_name = State()
    waiting_tariff_price = State()
    waiting_tariff_desc = State()

# -------------------- ОБРАБОТЧИКИ (сокращённо, нужно скопировать все из предыдущего ответа) --------------------
# Ниже приведены только ключевые обработчики, остальные идентичны предыдущей версии.

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await db_add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer(
        "👋 Добро пожаловать в сервис сопровождения в метро!\n"
        "Мы поможем вам комфортно и безопасно добраться до нужной станции.",
        reply_markup=main_menu_keyboard(message.from_user.id)
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu_keyboard(callback.from_user.id))

@dp.callback_query(F.data == "about")
async def show_about(callback: CallbackQuery):
    text = (
        "ℹ️ <b>О сервисе</b>\n\n"
        "Мы предоставляем услуги личного сопровождающего в метро.\n"
        "Помощь с навигацией, пересадками, покупкой билетов и просто дружеская поддержка.\n\n"
        "💰 Оплата принимается онлайн через ЮMoney."
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard(callback.from_user.id))

@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    user = callback.from_user
    orders_count = len(await db_get_user_orders(user.id))
    text = (
        f"👤 <b>Профиль</b>\n"
        f"Имя: {user.full_name}\n"
        f"Username: @{user.username}\n"
        f"ID: <code>{user.id}</code>\n"
        f"Заказов: {orders_count}\n"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=profile_keyboard())

@dp.callback_query(F.data == "my_orders")
async def show_my_orders(callback: CallbackQuery):
    orders = await db_get_user_orders(callback.from_user.id)
    if not orders:
        await callback.message.edit_text("У вас пока нет заказов.", reply_markup=profile_keyboard())
        return
    text = "📋 <b>Ваши заказы:</b>\n\n"
    for o in orders:
        status_emoji = {
            'pending': '⏳',
            'paid': '✅',
            'in_progress': '🚇',
            'completed': '🏁',
            'cancelled': '❌',
        }.get(o['status'], '❓')
        text += (
            f"{status_emoji} <code>{o['id'][:8]}</code> — {o['tariff']}\n"
            f"Маршрут: {o['route']}\n"
            f"Время: {o['time']}\n"
            f"Статус: {o['status']}\n"
            f"Дата: {o['created']}\n\n"
        )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=profile_keyboard())

@dp.callback_query(F.data == "order")
async def start_order(callback: CallbackQuery, state: FSMContext):
    tariffs = await db_get_tariffs()
    if not tariffs:
        await callback.answer("Нет доступных тарифов", show_alert=True)
        return
    await state.set_state(OrderState.choosing_tariff)
    await callback.message.edit_text("Выберите подходящий тариф:", reply_markup=tariff_keyboard(tariffs))

@dp.callback_query(F.data.startswith("tariff_"))
async def process_tariff(callback: CallbackQuery, state: FSMContext):
    tariff_id = int(callback.data.split("_")[1])
    tariff = await db_get_tariff(tariff_id)
    if not tariff:
        await callback.answer("Тариф не найден")
        return
    await state.update_data(tariff_id=tariff_id, price=tariff['price'], tariff_name=tariff['name'])
    await state.set_state(OrderState.entering_route)
    await callback.message.edit_text(
        f"✅ Вы выбрали: <b>{tariff['name']}</b> — {tariff['price']} ₽\n\n"
        "🛤 <b>Введите маршрут:</b>\n"
        "Откуда и куда вам нужно добраться?",
        parse_mode="HTML"
    )

@dp.message(OrderState.entering_route)
async def process_route(message: Message, state: FSMContext):
    await state.update_data(route=message.text)
    await state.set_state(OrderState.entering_time)
    await message.answer(
        "⏰ <b>Укажите желаемое время начала:</b>\n"
        "Формат: ДД.ММ.ГГГГ ЧЧ:ММ (например, 25.12.2023 14:30)",
        parse_mode="HTML"
    )

@dp.message(OrderState.entering_time)
async def process_time(message: Message, state: FSMContext):
    try:
        dt = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
        if dt < datetime.now():
            await message.answer("❌ Время должно быть в будущем.")
            return
    except ValueError:
        await message.answer("❌ Неверный формат. Пример: 25.12.2023 14:30")
        return
    await state.update_data(order_time=dt.isoformat())
    await state.set_state(OrderState.entering_contact)
    await message.answer(
        "📞 <b>Оставьте контакт для связи:</b>\n"
        "Номер телефона или Telegram.",
        parse_mode="HTML"
    )

@dp.message(OrderState.entering_contact)
async def process_contact(message: Message, state: FSMContext):
    await state.update_data(contact=message.text)
    data = await state.get_data()

    order_id = str(uuid.uuid4())
    user_id = message.from_user.id

    await db_create_order(
        order_id, user_id,
        data['tariff_id'], data['route'],
        data['order_time'], data['contact']
    )

    payment_url = create_payment_link(
        amount=data['price'],
        label=order_id,
        description=f"Сопровождение в метро: {data['tariff_name']}"
    )

    if not payment_url:
        await message.answer("❌ Ошибка создания платежа. Попробуйте позже.")
        return

    await state.clear()
    await message.answer(
        f"🧾 <b>Заказ №{order_id[:8]}</b>\n"
        f"Тариф: {data['tariff_name']}\n"
        f"Маршрут: {data['route']}\n"
        f"Время: {data['order_time']}\n"
        f"Сумма к оплате: <b>{data['price']} ₽</b>\n\n"
        "Для оплаты перейдите по ссылке 👇\n"
        "<i>Оплата проверяется автоматически каждые 15 секунд.</i>",
        parse_mode="HTML",
        reply_markup=payment_keyboard(payment_url, order_id)
    )
    await notify_admin_new_order(order_id)

@dp.callback_query(F.data.startswith("check_payment_"))
async def manual_check_payment(callback: CallbackQuery):
    order_id = callback.data.replace("check_payment_", "")
    if check_payment_by_label(order_id):
        await db_update_order_status(order_id, 'paid')
        await notify_user_paid(order_id)
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ Оплата получена!",
            reply_markup=None
        )
    else:
        await callback.answer("❌ Оплата ещё не найдена. Попробуйте позже или дождитесь авто-проверки.", show_alert=True)

# -------------------- АДМИН-ПАНЕЛЬ (сокращённо, но нужно вставить все) --------------------
# Вставьте сюда все обработчики админки из предыдущего полного кода (admin_panel, admin_orders_menu, admin_orders_list, admin_order_detail, admin_set_status, admin_tariffs_list, admin_add_tariff_start, admin_add_tariff_name, admin_add_tariff_price, admin_add_tariff_desc, admin_broadcast_start, admin_broadcast_send)
# Из-за ограничения длины ответа я не могу вставить полный код, но вы можете взять его из предыдущего ответа (начиная с # -------------------- АДМИН-ПАНЕЛЬ --------------------).
# Убедитесь, что все функции скопированы.

# -------------------- ЗАПУСК --------------------
async def on_startup():
    await init_db()
    asyncio.create_task(payment_checker())
    logging.info("Бот запущен!")

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
