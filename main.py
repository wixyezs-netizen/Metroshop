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
import json
from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
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
DOMAIN = os.getenv('DOMAIN', 'wixyezmetroshop.bothost.ru')
PORT = int(os.getenv('PORT', 8080))

DB_NAME = 'metro_shop.db'

# Цены на услуги
PRICES = {
    'escort_map5': {
        'name': '👑 Сопровождение Карта 5',
        'price': 350,
        'emoji': '🔥',
        'kills': '6-7',
        'loot': 'Весь лут твой!',
        'gear': '💎💎💎💎',
        'category': 'premium',
        'description': 'Профессиональное сопровождение на Карте 5'
    },
    'escort_map7': {
        'name': '👑 Сопровождение Карта 7',
        'price': 450,
        'emoji': '🔥',
        'kills': '10-15',
        'loot': 'Весь лут твой!',
        'gear': '💎💎💎💎',
        'category': 'premium',
        'description': 'Профессиональное сопровождение на Карте 7'
    },
    'escort_map8_basic': {
        'name': '👑 Сопровождение Карта 8 (Базовый)',
        'price': 850,
        'emoji': '🔥',
        'kills': '12+',
        'tickets': '5-8',
        'loot': 'Весь лут твой!',
        'gear': '💎💎💎💎',
        'category': 'premium',
        'description': 'Базовый пакет для Карты 8'
    },
    'escort_map8_premium': {
        'name': '👑 Сопровождение Карта 8 (Премиум)',
        'price': 1300,
        'emoji': '💎',
        'kills': '18+',
        'tickets': '8-12',
        'loot': 'Весь лут твой!',
        'gear': '💎💎💎💎💎',
        'category': 'premium',
        'description': 'Премиум пакет для Карты 8'
    },
    'escort_80': {
        'name': '🎮 Сопровод 80₽',
        'price': 80,
        'emoji': '⚡',
        'includes': '🪖🧥🎒',
        'category': 'budget',
        'description': 'Бюджетное сопровождение с базовой экипировкой'
    },
    'escort_100': {
        'name': '🎮 Сопровод 100₽',
        'price': 100,
        'emoji': '⚡',
        'includes': '🪖🧥🎒',
        'category': 'budget',
        'description': 'Бюджетное сопровождение с базовой экипировкой'
    },
    'escort_120': {
        'name': '🎮 Сопровод 120₽',
        'price': 120,
        'emoji': '⚡',
        'includes': '🪖🧥🎒',
        'category': 'budget',
        'description': 'Бюджетное сопровождение с базовой экипировкой'
    },
    'mk_tower': {
        'name': '🚀 МК вышка',
        'price': 30,
        'emoji': '🚀',
        'category': 'extra',
        'description': 'Быстрое выполнение МК вышка'
    },
}

# ======================== HTML WEB APP ========================

WEBAPP_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Metro Shop</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        :root {
            --primary: #FF6B35;
            --secondary: #004E89;
            --accent: #FFD700;
            --dark: #1A1A2E;
            --light: #16213E;
            --text: #FFFFFF;
            --text-secondary: #B0B0B0;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, var(--dark) 0%, var(--light) 100%);
            color: var(--text);
            min-height: 100vh;
            padding-bottom: 100px;
        }

        .header {
            background: linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%);
            padding: 20px;
            text-align: center;
            box-shadow: 0 4px 20px rgba(255, 107, 53, 0.3);
            position: sticky;
            top: 0;
            z-index: 100;
        }

        .header h1 {
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 5px;
        }

        .tabs {
            display: flex;
            background: var(--light);
            padding: 10px;
            gap: 10px;
            overflow-x: auto;
            position: sticky;
            top: 88px;
            z-index: 99;
        }

        .tab {
            flex: 1;
            min-width: 100px;
            padding: 12px 20px;
            border: none;
            border-radius: 12px;
            background: var(--dark);
            color: var(--text);
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
        }

        .tab.active {
            background: linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%);
            box-shadow: 0 4px 15px rgba(255, 107, 53, 0.4);
        }

        .container {
            padding: 20px;
            max-width: 600px;
            margin: 0 auto;
        }

        .service-grid {
            display: grid;
            gap: 15px;
            margin-top: 20px;
        }

        .service-card {
            background: var(--light);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
            transition: transform 0.3s;
            cursor: pointer;
        }

        .service-card:hover {
            transform: translateY(-5px);
        }

        .service-image {
            width: 100%;
            height: 180px;
            background: linear-gradient(135deg, var(--secondary) 0%, var(--primary) 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 64px;
        }

        .service-content {
            padding: 20px;
        }

        .service-title {
            font-size: 18px;
            font-weight: 700;
            margin-bottom: 10px;
        }

        .service-description {
            font-size: 13px;
            color: var(--text-secondary);
            margin-bottom: 15px;
        }

        .service-features {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 15px;
        }

        .feature-badge {
            background: rgba(255, 107, 53, 0.2);
            color: var(--accent);
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }

        .service-footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-top: 15px;
            border-top: 1px solid rgba(255,255,255,0.1);
        }

        .service-price {
            font-size: 24px;
            font-weight: 700;
            color: var(--accent);
        }

        .buy-button {
            background: linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%);
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 12px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
        }

        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.8);
            z-index: 1000;
        }

        .modal-content {
            position: absolute;
            bottom: 0;
            width: 100%;
            background: var(--light);
            border-radius: 24px 24px 0 0;
            padding: 30px 20px;
            max-height: 80vh;
            overflow-y: auto;
        }

        .modal-title {
            font-size: 22px;
            font-weight: 700;
            margin-bottom: 10px;
            text-align: center;
        }

        .close-modal {
            position: absolute;
            top: 15px;
            right: 15px;
            font-size: 28px;
            cursor: pointer;
            width: 36px;
            height: 36px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            background: var(--dark);
        }

        .input-group {
            margin-bottom: 20px;
        }

        .input-label {
            display: block;
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 8px;
            color: var(--text-secondary);
        }

        .input-field {
            width: 100%;
            padding: 15px;
            background: var(--dark);
            border: 2px solid transparent;
            border-radius: 12px;
            color: var(--text);
            font-size: 16px;
        }

        .input-field:focus {
            outline: none;
            border-color: var(--primary);
        }

        .submit-button {
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%);
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 700;
            cursor: pointer;
        }

        .price-summary {
            background: var(--dark);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }

        .price-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
            font-size: 14px;
        }

        .price-row.total {
            font-size: 20px;
            font-weight: 700;
            color: var(--accent);
            padding-top: 15px;
            border-top: 1px solid rgba(255,255,255,0.1);
        }

        .promo-section {
            background: var(--dark);
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 20px;
        }

        .promo-input-wrapper {
            display: flex;
            gap: 10px;
        }

        .promo-input {
            flex: 1;
        }

        .apply-promo-btn {
            padding: 15px 20px;
            background: var(--secondary);
            color: white;
            border: none;
            border-radius: 12px;
            font-weight: 600;
            cursor: pointer;
        }

        .loading {
            text-align: center;
            padding: 40px;
        }

        .spinner {
            border: 4px solid rgba(255,255,255,0.1);
            border-top: 4px solid var(--primary);
            border-radius: 50%;
            width: 50px;
            height: 50px;
            animation: spin 1s linear infinite;
            margin: 20px auto;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🎮 METRO SHOP</h1>
        <p>Профессиональные услуги Metro Royale</p>
    </div>

    <div class="tabs">
        <button class="tab active" data-category="all">🔥 Все</button>
        <button class="tab" data-category="premium">👑 Премиум</button>
        <button class="tab" data-category="budget">⚡ Бюджет</button>
        <button class="tab" data-category="extra">🚀 Доп.</button>
    </div>

    <div class="container">
        <div id="services" class="service-grid">
            <div class="loading">
                <div class="spinner"></div>
                Загрузка услуг...
            </div>
        </div>
    </div>

    <div id="orderModal" class="modal">
        <div class="modal-content">
            <span class="close-modal">&times;</span>
            <div class="modal-title" id="modalTitle"></div>
            <div id="modalPrice" style="color: var(--accent); font-size: 28px; font-weight: 700; text-align: center; margin: 10px 0;"></div>

            <div class="promo-section">
                <div class="input-label">🎁 Есть промокод?</div>
                <div class="promo-input-wrapper">
                    <input type="text" id="promoCode" class="input-field promo-input" placeholder="Введите промокод">
                    <button class="apply-promo-btn" onclick="applyPromo()">Применить</button>
                </div>
                <div id="promoMessage" style="margin-top: 10px; font-size: 13px;"></div>
            </div>

            <div class="price-summary">
                <div class="price-row">
                    <span>Цена услуги:</span>
                    <span id="basePrice">0₽</span>
                </div>
                <div class="price-row" id="discountRow" style="display: none;">
                    <span>Скидка:</span>
                    <span id="discountAmount" style="color: var(--accent);">0₽</span>
                </div>
                <div class="price-row total">
                    <span>К оплате:</span>
                    <span id="finalPrice">0₽</span>
                </div>
            </div>

            <div class="input-group">
                <label class="input-label">🎮 Ваш PUBG Mobile ID</label>
                <input type="text" id="pubgId" class="input-field" placeholder="Например: 5123456789">
                <div style="margin-top: 8px; font-size: 12px; color: var(--text-secondary);">
                    📍 Найти можно в профиле PUBG Mobile
                </div>
            </div>

            <button class="submit-button" onclick="submitOrder()">
                💳 Перейти к оплате
            </button>
        </div>
    </div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        let services = {};
        let currentService = null;
        let currentDiscount = 0;

        async function loadServices() {
            try {
                const response = await fetch('/api/services');
                services = await response.json();
                renderServices('all');
            } catch (error) {
                document.getElementById('services').innerHTML = '<div style="text-align:center;padding:40px;">❌ Ошибка загрузки</div>';
            }
        }

        function renderServices(category) {
            const container = document.getElementById('services');
            const filtered = category === 'all' 
                ? Object.entries(services)
                : Object.entries(services).filter(([key, item]) => item.category === category);

            if (filtered.length === 0) {
                container.innerHTML = '<div style="text-align:center;padding:40px;">📭 Услуги не найдены</div>';
                return;
            }

            container.innerHTML = filtered.map(([key, item]) => `
                <div class="service-card" onclick="openModal('${key}')">
                    <div class="service-image">${item.emoji}</div>
                    <div class="service-content">
                        <div class="service-title">${item.name}</div>
                        <div class="service-description">${item.description || 'Качественное выполнение гарантировано'}</div>
                        <div class="service-features">
                            ${item.kills ? `<span class="feature-badge">🎯 Киллов: ${item.kills}</span>` : ''}
                            ${item.tickets ? `<span class="feature-badge">🎁 Билеты: ${item.tickets}</span>` : ''}
                            ${item.includes ? `<span class="feature-badge">${item.includes}</span>` : ''}
                        </div>
                        <div class="service-footer">
                            <div class="service-price">${item.price}₽</div>
                            <button class="buy-button">Купить 🛒</button>
                        </div>
                    </div>
                </div>
            `).join('');
        }

        function openModal(serviceKey) {
            currentService = serviceKey;
            const item = services[serviceKey];
            
            document.getElementById('modalTitle').textContent = item.name;
            document.getElementById('modalPrice').textContent = `${item.price}₽`;
            document.getElementById('basePrice').textContent = `${item.price}₽`;
            document.getElementById('finalPrice').textContent = `${item.price}₽`;
            document.getElementById('pubgId').value = '';
            document.getElementById('promoCode').value = '';
            document.getElementById('promoMessage').textContent = '';
            document.getElementById('discountRow').style.display = 'none';
            currentDiscount = 0;
            
            document.getElementById('orderModal').style.display = 'block';
        }

        document.querySelector('.close-modal').onclick = function() {
            document.getElementById('orderModal').style.display = 'none';
        }

        async function applyPromo() {
            const code = document.getElementById('promoCode').value.trim();
            if (!code) return;

            try {
                const response = await fetch('/api/check-promo', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({code})
                });

                const data = await response.json();
                const msgEl = document.getElementById('promoMessage');

                if (data.valid) {
                    currentDiscount = data.discount;
                    msgEl.innerHTML = `<span style="color: var(--accent);">✅ Промокод применен! Скидка ${data.discount}%</span>`;
                    updatePrice();
                } else {
                    msgEl.innerHTML = `<span style="color: #ff4444;">❌ ${data.message}</span>`;
                    currentDiscount = 0;
                    updatePrice();
                }
            } catch (error) {
                console.error(error);
            }
        }

        function updatePrice() {
            const item = services[currentService];
            const basePrice = item.price;
            const discountAmount = Math.round(basePrice * currentDiscount / 100);
            const finalPrice = basePrice - discountAmount;

            document.getElementById('basePrice').textContent = `${basePrice}₽`;
            
            if (currentDiscount > 0) {
                document.getElementById('discountRow').style.display = 'flex';
                document.getElementById('discountAmount').textContent = `-${discountAmount}₽`;
                document.getElementById('finalPrice').textContent = `${finalPrice}₽`;
            } else {
                document.getElementById('discountRow').style.display = 'none';
                document.getElementById('finalPrice').textContent = `${basePrice}₽`;
            }
        }

        function submitOrder() {
            const pubgId = document.getElementById('pubgId').value.trim();
            
            if (!pubgId) {
                tg.showAlert('Пожалуйста, введите ваш PUBG ID');
                return;
            }

            if (pubgId.length < 8 || !/^\\d+$/.test(pubgId)) {
                tg.showAlert('PUBG ID должен содержать минимум 8 цифр');
                return;
            }

            const promoCode = document.getElementById('promoCode').value.trim();

            tg.sendData(JSON.stringify({
                service: currentService,
                pubgId: pubgId,
                promoCode: promoCode,
                discount: currentDiscount
            }));

            tg.close();
        }

        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', function() {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                this.classList.add('active');
                renderServices(this.dataset.category);
            });
        });

        loadServices();
    </script>
</body>
</html>
"""

# ======================== YOOMONEY API ========================

class YooMoneyAPI:
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://yoomoney.ru/api"
    
    def get_operation_history(self, label: Optional[str] = None, records: int = 100) -> dict:
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

yoomoney = YooMoneyAPI(YOOMONEY_TOKEN) if YOOMONEY_TOKEN else None

# ======================== БАЗА ДАННЫХ ========================

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_orders INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                discount INTEGER DEFAULT 0
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
                rating INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                discount INTEGER,
                max_uses INTEGER,
                current_uses INTEGER DEFAULT 0,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

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
        
        async with db.execute("SELECT COUNT(*) as count FROM orders WHERE DATE(created_at) = DATE('now')") as cursor:
            today_orders = (await cursor.fetchone())['count']
        
        async with db.execute("SELECT COUNT(*) as count FROM orders WHERE status = 'paid'") as cursor:
            pending_orders = (await cursor.fetchone())['count']
        
        return {
            'total_users': total_users,
            'total_orders': total_orders,
            'completed_orders': completed_orders,
            'total_revenue': total_revenue,
            'today_orders': today_orders,
            'pending_orders': pending_orders
        }

async def check_promocode(code: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM promocodes WHERE code = ?', (code,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            promo = dict(row)
            
            if promo['current_uses'] >= promo['max_uses']:
                return None
            
            if datetime.fromisoformat(promo['expires_at']) < datetime.now():
                return None
            
            return promo

async def use_promocode(code: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE promocodes SET current_uses = current_uses + 1 WHERE code = ?', (code,))
        await db.commit()

# ======================== WEB SERVER ========================

async def web_app_handler(request):
    return web.Response(text=WEBAPP_HTML, content_type='text/html')

async def api_services_handler(request):
    return web.json_response(PRICES)

async def api_check_promo_handler(request):
    data = await request.json()
    code = data.get('code', '')
    
    promo = await check_promocode(code)
    
    if promo:
        return web.json_response({'valid': True, 'discount': promo['discount']})
    else:
        return web.json_response({'valid': False, 'message': 'Промокод не найден или истек'})

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', web_app_handler)
    app.router.add_get('/api/services', api_services_handler)
    app.router.add_post('/api/check-promo', api_check_promo_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Web server started on port {PORT}")

# ======================== КЛАВИАТУРЫ ========================

def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("🛍️ Открыть каталог", web_app=WebAppInfo(url=f"https://{DOMAIN}"))],
        [
            InlineKeyboardButton("🎁 Мои заказы", callback_data="my_orders"),
            InlineKeyboardButton("💎 Профиль", callback_data="profile")
        ],
        [
            InlineKeyboardButton("🎯 FAQ", callback_data="faq"),
            InlineKeyboardButton("💬 Поддержка", url="https://t.me/MetroShopSupport")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ На главную", callback_data="main_menu")]])

def get_payment_menu(order_id: str):
    keyboard = [
        [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_payment_{order_id}")],
        [InlineKeyboardButton("💬 Поддержка", url="https://t.me/MetroShopSupport")],
        [InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_order_{order_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_order_menu(order_id: str):
    keyboard = [[InlineKeyboardButton("✅ Заказ выполнен", callback_data=f"admin_complete_{order_id}")]]
    return InlineKeyboardMarkup(keyboard)

# ======================== ТЕКСТЫ ========================

WELCOME_TEXT = """
╔═══════════════════════╗
   🎮 <b>METRO SHOP</b> 🎮
╚═══════════════════════╝

<b>💎 Профессиональные услуги Metro Royale</b>

━━━━━━━━━━━━━━━━━━━━━

✨ <b>Что мы предлагаем:</b>

🔥 <b>Премиум сопровождение</b>
   ├ Карта 5, 7, 8
   ├ Гарант выносов 💯
   └ Весь лут твой 💎

⚡ <b>Бюджетные сопроводы</b>
   ├ С экипировкой 🪖🧥🎒
   ├ Быстро и недорого
   └ От 80₽ 🚀

━━━━━━━━━━━━━━━━━━━━━

💪 <b>Наши преимущества:</b>
   ├ 🏆 Опытные игроки
   ├ ⚡ Быстрое выполнение (10-30 мин)
   ├ 💰 Честные цены
   ├ 🛡️ Гарантия результата
   └ 🤝 Безопасная сделка

━━━━━━━━━━━━━━━━━━━━━

👇 <b>Нажмите "Открыть каталог"</b>
"""

FAQ_TEXT = """
╔═══════════════════════╗
      ❓ <b>FAQ</b>
╚═══════════════════════╝

━━━━━━━━━━━━━━━━━━━━━

<b>Q: Что такое Metro Royale?</b>
<b>A:</b> 🎮 PvE/PvP режим PUBG Mobile с возможностью собирать лут и эвакуироваться

<b>Q: Что входит в премиум?</b>
<b>A:</b> 👑 Опытный игрок сопровождает вас, гарантирует киллы, весь лут остается вам

<b>Q: Чем отличается бюджет?</b>
<b>A:</b> ⚡ Быстрое прохождение с базовой экипировкой

<b>Q: Это безопасно?</b>
<b>A:</b> 🛡️ Да! Играем вместе с вами, доступ к аккаунту не требуется

<b>Q: Способы оплаты?</b>
<b>A:</b> 💳 ЮMoney с автоматической проверкой платежа

<b>Q: Гарантия возврата?</b>
<b>A:</b> 💯 Да! 100% возврат средств при невыполнении

<b>Q: Как использовать промокод?</b>
<b>A:</b> 🎁 При оформлении заказа введите код для получения скидки

━━━━━━━━━━━━━━━━━━━━━
"""

# ======================== ОБРАБОТЧИКИ ========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await add_user(user.id, user.username, user.first_name)
    await update.message.reply_text(WELCOME_TEXT, reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    orders = await get_user_orders(user_id, 10)
    
    if not orders:
        text = "📭 У вас пока нет заказов"
    else:
        text = "<b>📦 МОИ ЗАКАЗЫ</b>\n\n"
        for order in orders:
            status_emoji = {
                'awaiting_payment': '⏳',
                'paid': '✅',
                'completed': '🏆',
                'cancelled': '❌'
            }.get(order['status'], '❓')
            
            text += f"<b>#{order['order_id']}</b> {status_emoji}\n"
            text += f"  ├ {order['item_name']}\n"
            text += f"  └ {order['price']}₽\n\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu())

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    stats = await get_stats()
    text = f"""
╔═══════════════════════╗
   📊 <b>СТАТИСТИКА</b>
╚═══════════════════════╝

━━━━━━━━━━━━━━━━━━━━━

👥 Пользователей: {stats['total_users']}
📦 Всего заказов: {stats['total_orders']}
✅ Выполнено: {stats['completed_orders']}
⏳ В обработке: {stats['pending_orders']}
💰 Выручка: {stats['total_revenue']:.2f}₽
📅 Сегодня: {stats['today_orders']}

━━━━━━━━━━━━━━━━━━━━━
"""
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data == "main_menu":
        await query.edit_message_text(WELCOME_TEXT, reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)
    
    elif data == "faq":
        await query.edit_message_text(FAQ_TEXT, reply_markup=get_back_button(), parse_mode=ParseMode.HTML)
    
    elif data == "profile":
        user = await get_user(user_id)
        if user:
            status = "👑 VIP" if user['total_orders'] >= 20 else "💎 Золотой" if user['total_orders'] >= 10 else "⭐ Серебряный" if user['total_orders'] >= 5 else "🆕 Новичок"
            
            text = f"""
╔═══════════════════════╗
   👤 <b>ВАШ ПРОФИЛЬ</b>
╚═══════════════════════╝

━━━━━━━━━━━━━━━━━━━━━

👤 <b>Пользователь:</b> {user['first_name']}
🆔 <b>ID:</b> <code>{user['user_id']}</code>

━━━━━━━━━━━━━━━━━━━━━

📊 <b>Статистика:</b>
   ├ 📦 Заказов: {user['total_orders']}
   ├ 💰 Потрачено: {user['total_spent']}₽
   ├ 🎁 Скидка: {user['discount']}%
   └ ⭐ Статус: {status}

━━━━━━━━━━━━━━━━━━━━━

💎 <b>Бонусная программа:</b>
   ├ 5+ заказов → 5% скидка
   ├ 10+ заказов → 10% скидка
   └ 20+ заказов → 15% скидка

━━━━━━━━━━━━━━━━━━━━━
"""
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())
    
    elif data == "my_orders":
        orders = await get_user_orders(user_id, 5)
        if not orders:
            text = "📭 У вас пока нет заказов"
        else:
            text = "<b>📦 МОИ ЗАКАЗЫ (последние 5)</b>\n\n"
            for order in orders:
                status_emoji = {
                    'awaiting_payment': '⏳',
                    'paid': '✅',
                    'completed': '🏆',
                    'cancelled': '❌'
                }.get(order['status'], '❓')
                
                text += f"<b>#{order['order_id']}</b> {status_emoji}\n"
                text += f"  ├ {order['item_name']}\n"
                text += f"  └ {order['price']}₽\n\n"
        
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())
    
    elif data.startswith('check_payment_'):
        order_id = data.replace('check_payment_', '')
        order = await get_order(order_id)
        
        if not order or order['status'] != 'awaiting_payment':
            await query.answer("❌ Ошибка заказа", show_alert=True)
            return
        
        await query.answer("🔄 Проверяю платеж...")
        
        if yoomoney:
            is_paid, amount = yoomoney.check_payment(order['payment_label'], order['price'])
            
            if is_paid:
                await update_order_status(order_id, 'paid')
                await update_user_stats(user_id, order['price'])
                
                text = f"""
╔═══════════════════════╗
   ✅ <b>ОПЛАТА ПОЛУЧЕНА!</b>
╚═══════════════════════╝

━━━━━━━━━━━━━━━━━━━━━

🎉 Заказ <b>#{order_id}</b> оплачен! 💯

━━━━━━━━━━━━━━━━━━━━━

📦 <b>Услуга:</b> {order['item_name']}
💰 <b>Сумма:</b> {amount}₽
🎮 <b>PUBG ID:</b> {order['pubg_id']}

⚡ <b>Статус:</b> В обработке 🔄

━━━━━━━━━━━━━━━━━━━━━

Услуга будет выполнена
в течение 10-30 минут! ⏱️

Мы уведомим вас когда
всё будет готово 🚀

💬 Вопросы: @MetroShopSupport

━━━━━━━━━━━━━━━━━━━━━
"""
                
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())
                
                # Уведомляем админов
                for admin_id in ADMIN_IDS:
                    try:
                        admin_text = f"""
🔔 <b>НОВЫЙ ОПЛАЧЕННЫЙ ЗАКАЗ!</b>

📦 <b>Заказ:</b> #{order_id}
👤 <b>Клиент:</b> @{order['username'] or 'нет'}
🆔 <b>User ID:</b> {user_id}
💎 <b>Услуга:</b> {order['item_name']}
💰 <b>Сумма:</b> {order['price']}₽
🎮 <b>PUBG ID:</b> {order['pubg_id']}

⚡ <b>ТРЕБУЕТСЯ ВЫПОЛНЕНИЕ!</b> 🚀
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
        
        text = """
╔═══════════════════════╗
    🚫 <b>ОТМЕНЕНО</b>
╚═══════════════════════╝

━━━━━━━━━━━━━━━━━━━━━

Ваш заказ был отменен

━━━━━━━━━━━━━━━━━━━━━
"""
        await query.edit_message_text(text, reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)
    
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
╔═══════════════════════╗
   🎉 <b>ЗАКАЗ ВЫПОЛНЕН!</b>
╚═══════════════════════╝

━━━━━━━━━━━━━━━━━━━━━

Ваш заказ <b>#{order_id}</b>
успешно выполнен! 💯

━━━━━━━━━━━━━━━━━━━━━

📦 <b>Услуга:</b> {order['item_name']}
🎮 <b>PUBG ID:</b> {order['pubg_id']}

━━━━━━━━━━━━━━━━━━━━━

Проверьте игру! 🚀

✨ Спасибо за покупку!
Будем рады видеть снова! 🤝

━━━━━━━━━━━━━━━━━━━━━
""",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass

async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = json.loads(update.effective_message.web_app_data.data)
        user = update.effective_user
        
        service_key = data['service']
        pubg_id = data['pubgId']
        promo_code = data.get('promoCode', '')
        discount = data.get('discount', 0)
        
        item = PRICES[service_key]
        final_price = item['price']
        
        if promo_code and discount > 0:
            final_price = round(item['price'] * (1 - discount / 100))
            await use_promocode(promo_code)
        
        order_id, payment_label = await create_order(
            user_id=user.id,
            username=user.username,
            item_key=service_key,
            item_name=item['name'],
            price=final_price,
            pubg_id=pubg_id
        )
        
        payment_url = yoomoney.create_payment_url(
            YOOMONEY_WALLET,
            final_price,
            payment_label,
            f"Metro Shop - {item['name']}"
        ) if yoomoney else f"https://yoomoney.ru/to/{YOOMONEY_WALLET}"
        
        discount_text = f"\n💰 <b>Скидка:</b> {discount}%\n🎁 <b>Промокод:</b> {promo_code}" if discount > 0 else ""
        
        text = f"""
╔═══════════════════════╗
    💳 <b>ОПЛАТА ЗАКАЗА</b>
╚═══════════════════════╝

━━━━━━━━━━━━━━━━━━━━━

<b>🔖 ЗАКАЗ #{order_id}</b>

📦 <b>Услуга:</b> {item['name']}
🎮 <b>PUBG ID:</b> {pubg_id}{discount_text}
💰 <b>К оплате:</b> {final_price}₽

━━━━━━━━━━━━━━━━━━━━━

🏷️ <b>МЕТКА ПЛАТЕЖА:</b>
<code>{payment_label}</code>

━━━━━━━━━━━━━━━━━━━━━

🎯 <b>КАК ОПЛАТИТЬ:</b>

1️⃣ Нажмите "Перейти к оплате" 💳
2️⃣ Выберите способ оплаты 🎯
3️⃣ Оплатите {final_price}₽ 💰
4️⃣ В комментарии укажите метку:
    <code>{payment_label}</code>
5️⃣ Вернитесь в бот 🔙
6️⃣ Нажмите "Проверить оплату" 🔄

━━━━━━━━━━━━━━━━━━━━━

⚠️ <b>ВАЖНО:</b>
   ├ 🎯 Обязательно укажите метку!
   ├ ⚡ Автоматическая проверка
   └ ⏱️ Заказ действителен 24 часа

🚀 После оплаты услуга будет
выполнена за 10-30 минут! 💯

━━━━━━━━━━━━━━━━━━━━━
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
    
    except Exception as e:
        logger.error(f"Error handling webapp data: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова.")

async def post_init(application: Application):
    await init_db()
    logger.info("✅ База данных инициализирована")
    
    # Запуск веб-сервера
    asyncio.create_task(start_web_server())

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("🚀 Metro Shop Bot запущен!")
    logger.info(f"🌐 Домен: {DOMAIN}")
    logger.info(f"🔌 Порт: {PORT}")
    logger.info(f"💳 ЮMoney: {YOOMONEY_WALLET}")
    logger.info(f"👨‍💼 Админы: {ADMIN_IDS}")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == '__main__':
    main()
