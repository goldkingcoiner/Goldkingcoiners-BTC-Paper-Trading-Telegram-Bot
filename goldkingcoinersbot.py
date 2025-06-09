import os
import logging
import json
import time
from datetime import datetime
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import ccxt
import pandas as pd
import mplfinance as mpf
from telegram.ext import CallbackContext
from functools import wraps
import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend
from html import escape
from typing import Dict, List
from uuid import uuid4
import asyncio
import feedparser
from rapidfuzz import fuzz
# --- Configuration ---

load_dotenv("bot_token.env")
TOKEN = os.getenv("BOT_TOKEN")

TRADE_FEE = 0.001  # 0.1%
user_last_interaction = {}
COOLDOWN_TIME = 1.0
MIN_TRADE_AMOUNT = 1.0  # minimum USD value for any trade

# --- Data Management ---
DATA_FILE = 'bot_data.json'
ORDERS = []
LIMIT_ORDERS = {}
WINNER_ID = None
WINNER_ANNOUNCED = False
RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblockcrypto.com/rss",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "Decrypt": "https://decrypt.co/feed",
}

def rate_limit_decorator(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = str(update.effective_user.id)  # User identifier
        current_time = time.time()  # Current time in seconds

        # Check if user has already interacted recently
        if user_id in user_last_interaction:
            last_interaction_time = user_last_interaction[user_id]
            if current_time - last_interaction_time < COOLDOWN_TIME:
                # Inform the user that they are sending commands too quickly
                
                return  # Prevent the command from executing if it's too fast

        # Update the last interaction time for the user
        user_last_interaction[user_id] = current_time

        # Call the original function (the command handler)
        return await func(update, context, *args, **kwargs)

    return wrapper

def load_data():
    global USERS, _last_price, _last_price_time, LIMIT_ORDERS, WINNER_ID, WINNER_ANNOUNCED

    try:
        if os.path.exists(DATA_FILE) and os.path.getsize(DATA_FILE) > 0:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                logger.info(f"Data loaded successfully: {data}")

                USERS = data.get('users', {})
                _last_price = data.get('price_data', {}).get('last_price', None)
                _last_price_time = data.get('price_data', {}).get('last_price_time', 0)
                LIMIT_ORDERS = data.get('limit_orders', {})
                WINNER_ID = data.get('winner_id', None)
                WINNER_ANNOUNCED = data.get('winner_announced', False)

                logger.info(f"Loaded USERS: {USERS}")
        else:
            logger.warning(f"Data file {DATA_FILE} is empty or doesn't exist. Initializing new data.")
            USERS = {}
            _last_price = None
            _last_price_time = 0
            LIMIT_ORDERS = {}
            WINNER_ID = None
            WINNER_ANNOUNCED = False
            save_data()
            logger.info("Initialized new data file with empty structure.")

        return USERS, _last_price, _last_price_time

    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Error loading data: {e}")
        USERS = {}
        _last_price = None
        _last_price_time = 0
        LIMIT_ORDERS = {}
        WINNER_ID = None
        WINNER_ANNOUNCED = False
        save_data()
        logger.info("Initialized new data file with empty structure.")
        return USERS, _last_price, _last_price_time


def save_data():
    try:
        with open(DATA_FILE, 'w') as f:
            data = {
                'users': USERS,
                'winner_id': WINNER_ID,
                'winner_announced': WINNER_ANNOUNCED,
                'price_data': {
                    'last_price': _last_price,
                    'last_price_time': _last_price_time
                },
                'limit_orders': LIMIT_ORDERS
            }
            json.dump(data, f)
            logger.info(f"Data saved successfully: {data}")
    except Exception as e:
        logger.error(f"Error saving data: {e}")


logger = logging.getLogger(__name__)

USERS, _last_price, _last_price_time = load_data()

def get_reserved_usd(user_id):
    return sum(
        order['usd_amount'] for order in LIMIT_ORDERS.values()
        if order['user_id'] == user_id and order['type'] in ['buy', 'stopbuy']
    )

def get_reserved_btc(user_id):
    return sum(
        order['amount'] for order in LIMIT_ORDERS.values()
        if order['user_id'] == user_id and order['type'] in ['sell', 'stopsell']
    )

def create_limit_order(user_id: str, order_type: str, price: float, amount: float, usd_amount: float) -> str:

    """Create a new limit order and return its ID."""
    order_id = str(uuid4())
    LIMIT_ORDERS[order_id] = {
        'user_id': user_id,
        'type': order_type,
        'price': price,
        'amount': amount,
        'usd_amount': usd_amount,
        'created_at': datetime.now().isoformat()
    }

    save_data()
    return order_id

def cancel_limit_order(user_id: str, order_id: str) -> bool:
    """Cancel any open order (limit or stop) if it belongs to the user."""
    if order_id in LIMIT_ORDERS:
        order = LIMIT_ORDERS[order_id]
        if order['user_id'] == user_id:
            del LIMIT_ORDERS[order_id]
            save_data()
            return True
    return False



def get_user_limit_orders(user_id: str) -> List[Dict]:
    """Get all limit orders for a specific user."""
    return [{'id': k, **v} for k, v in LIMIT_ORDERS.items() if v['user_id'] == user_id]

async def process_limit_orders(context=None):
    """Check if any limit or stop orders can be executed based on current price."""
    current_price = get_btc_price()
    logger.info(f"Checking orders at current price: ${current_price:.2f}")
    executed_orders = []

    orders_to_check = sorted(LIMIT_ORDERS.items(), key=lambda x: x[1]['created_at'])

    order_type_map = {
        'buy': 'LIMIT BUY',
        'sell': 'LIMIT SELL',
        'stopbuy': 'STOP BUY',
        'stopsell': 'STOP SELL'
    }

    for order_id, order in orders_to_check:
        try:
            user_id = order['user_id']
            order_type = order['type']
            price = order['price']
            btc_amount = order['amount']
            usd_amount = btc_amount * price
            user = USERS.get(user_id)

            if not user:
                logger.warning(f"User {user_id} not found for order {order_id}")
                continue

            # Check if order conditions are met
            should_execute = (
                (order_type == 'buy' and current_price <= price) or
                (order_type == 'sell' and current_price >= price) or
                (order_type == 'stopbuy' and current_price >= price) or
                (order_type == 'stopsell' and current_price <= price)
            )

            if not should_execute:
                continue

            # Execute the trade based on order type and available funds
            if order_type in ['buy', 'stopbuy']:
                if user['usd'] >= usd_amount:
                    success, msg = execute_trade(user_id, 'buy', usd_amount, context)
                else:
                    success = False
                    msg = "❌ 🙈 Order skipped: not enough USD."

            elif order_type in ['sell', 'stopsell']:
                if user['btc'] >= btc_amount:
                    success, msg = execute_trade(user_id, 'sell', usd_amount, context, btc_amount_override=btc_amount)
                else:
                    success = False
                    msg = "❌ 🙈 Order skipped: not enough BTC."

            else:
                success = False
                msg = "❌ 🙈 Unknown order type."

            if success:
                del LIMIT_ORDERS[order_id]
                executed_orders.append(order_id)
                logger.info(f"✅ Order {order_id} executed.")

                if context:
                    order_type_label = order_type_map.get(order_type, order_type.upper())
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🐵 Your {order_type_label} order for {btc_amount:.6f} BTC was executed at ${current_price:,.2f}"
                    )

            else:
                del LIMIT_ORDERS[order_id]
                reason = "not enough USD." if order_type in ['buy', 'stopbuy'] else "not enough BTC."
                order_type_label = order_type_map.get(order_type, order_type.upper())

                logger.warning(f"{msg} (Order {order_id})")

                if context:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"❌ Your {order_type_label} order for {btc_amount:.6f} BTC at ${price:,.2f} was skipped: {reason}"
                    )

        except Exception as e:
            logger.error(f"Error processing order {order_id}: {e}")

    if executed_orders:
        save_data()
        logger.info(f"Executed orders: {executed_orders}")

    return executed_orders



@rate_limit_decorator
async def stopbuy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return

    if not context.args or len(context.args) != 2:
        await update.effective_chat.send_message("How to use:\n\n /stopbuy <BTC price> <usd amount>\n\nExample: /stopbuy 150000 3000")
        return

    try:
        price = float(context.args[0])
        usd_amount = float(context.args[1])
        btc_amount = usd_amount / price
        reserved = get_reserved_usd(user_id)
        if usd_amount + reserved > USERS[user_id]['usd']:
            await update.effective_chat.send_message("❌ 🙈 Not enough usd. (including reserved funds for active orders)")
            return

        if price <= 0 or usd_amount <= 0:
            await update.effective_chat.send_message("❌ 🙈 Price and amount must be positive.")
            return

        if USERS[user_id]['usd'] < usd_amount:
            await update.effective_chat.send_message(f"❌ 🙈 Insufficient USD. You need ${usd_amount:,.2f}.")
            return

        order_id = create_limit_order(user_id, 'stopbuy', price, btc_amount, usd_amount)

        await update.effective_chat.send_message(
            f"🛑 🙉 Stop-buy order created:\n"
            f"• Buy {btc_amount:.6f} BTC (${usd_amount:,.2f})\n" 
            f"• At price: ${price:,.2f} or higher\n\n"
            f"Availability of funds will be checked at execution.\nUse /myorders to view your active orders."
        )
    except ValueError:
        await update.effective_chat.send_message("❌ 🙈 Invalid input. Use numbers for price and amount.")

@rate_limit_decorator
async def stopsell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return

    if not context.args or len(context.args) != 2:
        await update.effective_chat.send_message("How to use:\n\n /stopsell <BTC price> <btc amount>\n\nExample: /stopsell 98000 0.5")
        return

    try:
        price = float(context.args[0])
        btc_amount = float(context.args[1])
        usd_amount = btc_amount * price
        reserved = get_reserved_btc(user_id)

        if btc_amount + reserved > USERS[user_id]['btc']:
            await update.effective_chat.send_message("❌ 🙈 Not enough BTC. (including reserved funds for active orders)")
            return

        if price <= 0 or btc_amount <= 0:
            await update.effective_chat.send_message("❌ 🙈 Price and amount must be positive.")
            return

        order_id = create_limit_order(user_id, 'stopsell', price, btc_amount, usd_amount)

        await update.effective_chat.send_message(
            f"🛑 🙉 Stop-sell order created:\n"
            f"• Sell {btc_amount:.6f} BTC (≈ ${usd_amount:,.2f})\n"
            f"• At price: ${price:,.2f} or lower\n\n"
            f"Funds will be verified at execution.\nUse /myorders to view active orders."
        )

    except ValueError:
        await update.effective_chat.send_message("❌ 🙈 Invalid input. Use numbers for price and BTC amount.")

async def handle_cancel_all_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    user_orders = [k for k, v in LIMIT_ORDERS.items() if v['user_id'] == user_id]

    if not user_orders:
        await query.edit_message_text("❌ 🙈 You have no active orders to cancel.")
        return

    for order_id in user_orders:
        del LIMIT_ORDERS[order_id]

    save_data()
    await query.edit_message_text(f"✅ Cancelled {len(user_orders)} active orders.")


@rate_limit_decorator
async def limitbuy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return

    if not context.args or len(context.args) != 2:
        await update.effective_chat.send_message("How to use:\n\n /limitbuy <BTC price> <usd amount>\n\nExample: /limitbuy 95000 3000")
        return

    try:
        price = float(context.args[0])
        usd_amount = float(context.args[1])
        btc_amount = usd_amount / price

        reserved = get_reserved_usd(user_id)
        if usd_amount + reserved > USERS[user_id]['usd']:
            await update.effective_chat.send_message("❌ 🙈 Not enough usd. (including reserved funds for active orders)")
            return
        
        if price <= 0 or usd_amount <= 0:
            await update.effective_chat.send_message("❌ 🙈 Price and amount must be positive.")
            return

        # Check if user has enough USD 
        if USERS[user_id]['usd'] < usd_amount:
            await update.effective_chat.send_message(f"❌ 🙈 Insufficient USD. You have ${USERS[user_id]['usd']:,.2f}, need ${usd_amount:,.2f}.")
            return

        # Create the limit order 
        order_id = create_limit_order(user_id, 'buy', price, btc_amount, usd_amount)

        await update.effective_chat.send_message(
            f"🐵 Limit buy order created:\n"
            f"• Buy {btc_amount:.6f} BTC (≈ ${usd_amount:,.2f})\n"
            f"• At price: ${price:,.2f}\n\n"
            f"Availability of funds will be checked at execution.\nUse /myorders to view your active orders."
        )

    except ValueError:
        await update.effective_chat.send_message("❌ 🙈 Invalid input. Use numbers for price and amount.")


@rate_limit_decorator
async def limitsell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return

    if not context.args or len(context.args) != 2:
        await update.effective_chat.send_message("How to use:\n\n /limitsell <BTC price> <btc amount>\n\nExample: /limitsell 150000 0.5")
        return

    try:
        price = float(context.args[0])
        btc_amount = float(context.args[1])
        usd_amount = btc_amount * price
        reserved = get_reserved_btc(user_id)

        if btc_amount + reserved > USERS[user_id]['btc']:
            await update.effective_chat.send_message("❌ 🙈 Not enough BTC. (including reserved funds for active orders)")
            return

        if price <= 0 or btc_amount <= 0:
            await update.effective_chat.send_message("❌ 🙈 Price and amount must be positive.")
            return

        order_id = create_limit_order(user_id, 'sell', price, btc_amount, usd_amount)

        await update.effective_chat.send_message(
            f"🐵 Limit sell order created:\n"
            f"• Sell {btc_amount:.6f} BTC (≈ ${usd_amount:,.2f})\n"
            f"• At price: ${price:,.2f}\n\n"
            f"Funds will be verified at execution.\nUse /myorders to view active orders."
        )

    except ValueError:
        await update.effective_chat.send_message("❌ 🙈 Invalid input. Use numbers for price and BTC amount.")



@rate_limit_decorator
async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return

    orders = get_user_limit_orders(user_id)
    if not orders:
        await update.effective_chat.send_message("❌ 🙈 You have no active limit or stop orders.")
        return

    current_price = get_btc_price()

    for order in orders:
        order_type_map = {
            'buy': 'LIMIT BUY',
            'sell': 'LIMIT SELL',
            'stopbuy': 'STOP BUY',
            'stopsell': 'STOP SELL'
        }
        order_type = order_type_map.get(order['type'], order['type'].upper())
        created_at = datetime.fromisoformat(order['created_at']).strftime("%Y-%m-%d %H:%M")

        diff_pct = 0
        if order['type'] == 'buy':
            diff_pct = ((current_price - order['price']) / order['price']) * 100
            status = "🐵 Executing order..." if current_price <= order['price'] else f"{diff_pct:.2f}% above"
        elif order['type'] == 'sell':
            diff_pct = ((order['price'] - current_price) / order['price']) * 100
            status = "🐵 Executing order..." if current_price >= order['price'] else f"{diff_pct:.2f}% below"
        elif order['type'] == 'stopbuy':
            diff_pct = ((order['price'] - current_price) / order['price']) * 100
            status = "🐵 Executing order..." if current_price >= order['price'] else f"{diff_pct:.2f}% below"
        elif order['type'] == 'stopsell':
            diff_pct = ((current_price - order['price']) / order['price']) * 100
            status = "🐵 Executing order..." if current_price <= order['price'] else f"{diff_pct:.2f}% above"
        else:
            status = "Unknown"

        amount_text = f"{order['amount']:.6f} BTC (${order['usd_amount']:,.2f})"

        text = (
            f"📌 {order_type} {amount_text}\n"
            f"💰 BTC Price: ${order['price']:,.2f}\n"
            f"📅 Created: {created_at}\n"
            f"📊 Status: {status}"
        )

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel Order", callback_data=f"cancelorder_{order['id']}")
        ]])

        await update.effective_chat.send_message(text, reply_markup=keyboard)

    # After all orders, send cancel all
    cancel_all_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel All Orders", callback_data="cancelall")
    ]])
    await update.effective_chat.send_message("🔚 Cancel all orders", reply_markup=cancel_all_keyboard)

async def handle_cancel_order_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    order_id = query.data.replace("cancelorder_", "")

    if cancel_limit_order(user_id, order_id):
        await query.edit_message_text("✅ Order cancelled.")
    else:
        await query.edit_message_text("❌ 🙈 Could not cancel this order.")

        


@rate_limit_decorator
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📘 *Available Commands:*\n\n"
        "👤 *Account*\n"
        "᛫ /register `<nickname>` - Register with a unique nickname\n"
        "᛫ /portfolio - View your BTC and USD balance\n"
        "᛫ /myorders - View and cancel your active orders\n"
        "᛫ /history - View your recent trades\n\n"
        "📈 *Trading (0.1% trading fee)*\n"
        "᛫ /buy  - Market buy BTC\n"
        "᛫ /sell - Market sell BTC\n"
        "᛫ /limitbuy `<price>` `<usd amount>`\n- Buy if BTC drops to target\n"
        "᛫ /stopbuy `<price>` `<usd amount>`\n- Buy if BTC rises to target\n"
        "᛫ /limitsell `<price>` `<btc amount>`\n- Sell if BTC rises to target\n"
        "᛫ /stopsell `<price>` `<btc amount>`\n- Sell if BTC drops to target\n\n"
        "🏆 *Competition*\n"
        "᛫ /leaderboard - See the top traders\n"        
        "᛫ /claimprize - Claim winnings if your PnL is $3,000+ \n(*you must use this command to claim the prize!*)\nAll users will be notified of the winner\n\n"
        "🛠 *Other*\n"
        "᛫ /news - view breaking BTC news headlines\n"
        "᛫ /price - Show current BTC price\n"
        "᛫ /chart - View BTC price chart\n"
        "᛫ /help - Show this help message\n\n"
        "*New*: Join this channel for future contest announcements: https://t.me/Goldkingcoinerscontests"
    )
    await update.effective_chat.send_message(help_text, parse_mode="Markdown")


@rate_limit_decorator
async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return

    chat_id = update.effective_chat.id
    progress_message = await context.bot.send_message(chat_id=chat_id, text="Fetching news, please wait... ⏳")

    try:
        combined_articles = []
        seen_titles = []
        seen_links = set()

        for source_name, url in RSS_FEEDS.items():
            feed = feedparser.parse(url)
            entries = feed.entries[:5]  # top 5 from each source
            for entry in entries:
                new_title = entry.title.strip()
                new_link = entry.link

                if new_link in seen_links:
                    continue  # skip if URL already seen

                # Check fuzzy similarity with titles seen
                is_duplicate = False
                for seen_title in seen_titles:
                    similarity = fuzz.ratio(new_title.lower(), seen_title.lower())
                    if similarity > 55:
                        is_duplicate = True
                        break
                if is_duplicate:
                    continue

                seen_titles.append(new_title)
                seen_links.add(new_link)

                combined_articles.append({
                    "title": escape(entry.title.replace("$", "＄")),
                    "link": new_link,
                    "source": source_name,
                    "published": entry.get('published', '')
                })

        if not combined_articles:
            await update.effective_chat.send_message("❌ 🙈 No news found at the moment.")
            await progress_message.delete()
            return

        combined_articles = combined_articles[:20]

        news_text = "📰 <b>Top Crypto News</b>:\n\n"
        for article in combined_articles:
            news_text += f"• <a href='{article['link']}'>{article['title']}</a> <i>({article['source']})</i>\n"

        await update.effective_chat.send_message(news_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Crypto news fetch error: {e}")
        await update.effective_chat.send_message("❌ 🙈 Failed to fetch news. Please try again later.")
    finally:
        await progress_message.delete()




# --- Price API ---
def get_btc_price():
    global _last_price, _last_price_time
    try:
        current_time = time.time()
        if current_time - _last_price_time < 30 and _last_price:
            return _last_price

        response = requests.get(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
            timeout=30
        )
        response.raise_for_status()

        data = response.json()
        _last_price = float(data["price"])  # Access price here
        _last_price_time = current_time
        save_data()
        return _last_price
    except requests.exceptions.RequestException as e:
        logger.error(f"Price check failed: {e}")
        if _last_price:
            return _last_price
        raise Exception("Price service unavailable. Please try again later.")

# --- Trading Logic ---
def execute_trade(user_id, action, usd_amount, context, btc_amount_override=None):
    price = get_btc_price()
    user = USERS[user_id]

    if usd_amount <= 0:
        return False, "❌ 🙈 Insufficient funds."

    if usd_amount < MIN_TRADE_AMOUNT:
        return False, f"❌ 🙈 Minimum trade amount is ${MIN_TRADE_AMOUNT:.2f}."

    fee_multiplier = 1 - TRADE_FEE

    if action == 'buy':
        if user['usd'] < usd_amount:
            return False, "❌ 🙈 Insufficient USD."

        btc_bought = (usd_amount * fee_multiplier) / price
        user['usd'] -= usd_amount
        user['btc'] += btc_bought

        user['trades'].append({
            "type": "buy",
            "btc": btc_bought,
            "usd": usd_amount,
            "price": price,
            "fee_pct": TRADE_FEE * 100,
            "timestamp": datetime.now().isoformat()
        })
        save_data()
        return True, f"🐵 Bought {btc_bought:.6f} BTC for ${usd_amount:,.2f} @ ${price:,.2f}"

    elif action == 'sell':
        price = get_btc_price()
        btc_to_sell = btc_amount_override if btc_amount_override else usd_amount / price

        if user['btc'] < btc_to_sell:
            return False, "❌ 🙈 Insufficient BTC."

        net_usd = (btc_to_sell * price) * (1 - TRADE_FEE)
        user['btc'] -= btc_to_sell
        user['usd'] += net_usd

        user['trades'].append({
            "type": "sell",
            "btc": btc_to_sell,
            "usd": net_usd,
            "price": price,
            "fee_pct": TRADE_FEE * 100,
            "timestamp": datetime.now().isoformat()
        })

        save_data()
        return True, f"🐵 Sold {btc_to_sell:.6f} BTC for ${net_usd:,.2f} @ ${price:,.2f}"


    return False, "❌ 🙈 Invalid action."




# --- Command Handlers ---
@rate_limit_decorator
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Check if user is registered
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return
            
    price = get_btc_price()
    rankings = []

    for uid, user in USERS.items():
        name = user['nickname'] or f"Trader {user['number']}"
        total_wealth = user["usd"] + (user["btc"] * price)
        pnl = total_wealth - 100000.0  # Starting capital
        rankings.append((name, total_wealth, pnl))

    rankings.sort(key=lambda x: x[1], reverse=True)
    top = rankings[:50]

    medals = ["🥇", "🥈", "🥉"]

    top_traders_text = "\n".join([
        f"{medals[i]} {name} | PnL: ${pnl:,.2f}" if i < 3 else f"{i+1}. {name} | PnL: ${pnl:,.2f}"
        for i, (name, wealth, pnl) in enumerate(top)
    ])

    await update.effective_chat.send_message(f"* * * * 🏆 PnL Leaderboard 🏆 * * * *\n\n{top_traders_text}")




@rate_limit_decorator
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return

    user = USERS[user_id]
    trades = user.get("trades", [])
    
    if not trades:
        await update.effective_chat.send_message("❌ 🙈 You have no trades yet.")
        return

    recent_trades = trades[-15:]

    recent_trades_text = f"📜 {user['nickname']}'s 15 most recent trades:\n\n"
    for trade in recent_trades:
        emoji = "📗" if trade['type'] == 'buy' else "📕"
        trade_type = trade['type'].capitalize()
        trade_btc = trade['btc']
        trade_usd = trade['usd']
        trade_price = trade['price']
        trade_time = trade['timestamp']
        recent_trades_text += f"{emoji}{trade_type}→${trade_usd:,.2f} @${trade_price:,.0f}\n"

    await update.effective_chat.send_message(recent_trades_text)
  


@rate_limit_decorator
async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if user_id not in USERS:
            await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
            return

        user = USERS[user_id]
        total_value = user["usd"] + (user["btc"] * get_btc_price())
        pnl = total_value - 100000.0
        

        display_name = user['nickname']

        if not display_name:
            display_name = f"Trader {user['number']}"

        response_text = (
            f"💰 Your Portfolio:\n\n"
            f"USD: ${user['usd']:,.1f}\n"
            f"BTC: {user['btc']:.5f} BTC\n"
            f"*Total Value: ${total_value:,.1f}*\n"
            f"📈 PnL: ${pnl:,.1f}\n"
        )
        
        await update.effective_chat.send_message(response_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Portfolio error: {e}")
        await update.effective_chat.send_message("❌ 🙈 Couldn't fetch portfolio data. Please try again later.")


@rate_limit_decorator
async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if user_id not in USERS:
            await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
            return

        response_text = (
            f"📈 Current BTC Price: ${get_btc_price():,.2f}"  # Show Status
        )
        
        await update.effective_chat.send_message(response_text)
    except Exception as e:
        logger.error(f"price: {e}")
        await update.effective_chat.send_message("❌ 🙈 Couldn't fetch data. Please try again later.")

# --- Main Entry ---
@rate_limit_decorator
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):


    await update.effective_chat.send_message(        
        "*To begin trading, use /register <your nickname here>*\n\n"
        "᛫ Use /help to see a list of available commands.\n\n"
        "current prize - 0.25 mBTC\n\n"     
        "Trading fee is 0.1%.\n\n"
        "See https://bitcointalk.org/index.php?topic=5540701.0 for Info and disclaimers\n\n"
        "*New*: Join this channel for future contest announcements: https://t.me/Goldkingcoinerscontests",    
        parse_mode="Markdown"
    )


@rate_limit_decorator
async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)  # Get the user's unique ID
    username = update.effective_user.username  # Get the user's username

    # Log the registration attempt
    logger.info(f"Attempting to register user: {username} (ID: {user_id})")

    # Check if the user is already registered
    if user_id in USERS:
        logger.warning(f"User {username} is already registered.")
        await update.effective_chat.send_message("❌ 🙈 You are already registered.\n\n Use /help for help.")
        return

    # Ensure that the user has provided a nickname
    if not context.args:
        logger.warning(f"No nickname provided for user {username} (ID: {user_id})")
        await update.effective_chat.send_message("Please use: /register <your trader nickname here>.\n"
                                                  "Example: /register goldkingcoiner")
        return

    # Get the nickname from the command arguments
    nickname = ' '.join(context.args).strip()

    # Ensure nickname uniqueness
    for user in USERS.values():
        if user.get('nickname', '').lower() == nickname.lower():
            logger.warning(f"Name {nickname} is already taken.")
            await update.effective_chat.send_message("❌ 🙈 Name already taken. Choose another.")
            return

    # Register the user
    trader_count = len(USERS) + 1
    USERS[user_id] = {
        'usd': 100000.0,
        'btc': 0.0,
        'trades': [],
        'nickname': nickname,
        'username': username,
        'number': trader_count  # ✅ comma added here

        
    }



    # Save the data after registration
    save_data()

    # Confirm successful registration
    logger.info(f"User {nickname} registered successfully with data: {USERS[user_id]}")
    await update.effective_chat.send_message(f"🐵 Registered successfully as: *{nickname}*", parse_mode="Markdown"
)


# --- Buy and Sell Handlers ---
@rate_limit_decorator
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return

    keyboard = [
        [InlineKeyboardButton("5%", callback_data='buy_5')],
        [InlineKeyboardButton("10%", callback_data='buy_10')],
        [InlineKeyboardButton("25%", callback_data='buy_25')],
        [InlineKeyboardButton("50%", callback_data='buy_50')],
        [InlineKeyboardButton("75%", callback_data='buy_75')],
        [InlineKeyboardButton("100%", callback_data='buy_100')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.effective_chat.send_message("💵 How much of your USD balance would you like to use?", reply_markup=reply_markup)

@rate_limit_decorator
async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return

    keyboard = [
        [InlineKeyboardButton("5%", callback_data='sell_5')],
        [InlineKeyboardButton("10%", callback_data='sell_10')],
        [InlineKeyboardButton("25%", callback_data='sell_25')],
        [InlineKeyboardButton("50%", callback_data='sell_50')],
        [InlineKeyboardButton("75%", callback_data='sell_75')],
        [InlineKeyboardButton("100%", callback_data='sell_100')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.effective_chat.send_message("How much of your BTC would you like to sell?", reply_markup=reply_markup)


# Generate and save chart

def fetch_btc_hourly_data():
    try:
        binance = ccxt.binance()
        ohlcv = binance.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=168)  # Last 7 days of 1-hour data

        df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])

        # Convert the timestamp to a proper DateTime format (UTC -> CET)
        df['Date'] = pd.to_datetime(df['Timestamp'], unit='ms', utc=True)
        df['Date'] = df['Date'].dt.tz_convert('Europe/Berlin')  # Assuming CET/CEST time zone

        df.set_index('Date', inplace=True)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]

    except Exception as e:
        logger.error(f"Error fetching BTC hourly data: {e}")
        raise Exception("Failed to fetch BTC data.")

# Function to generate and save the chart as an image
def generate_btc_chart(data, filename="btc_hourly_chart.png"):
    try:
        mpf.plot(data, type='candle', style='charles', title='BTC/USD 1-Hour Chart', volume=True, savefig=filename)
        logger.info(f"Chart generated and saved as {filename}")
    except Exception as e:
        logger.error(f"Error generating chart: {e}")
        raise Exception("Failed to generate chart.")

# Function to handle the /chart command in the bot
@rate_limit_decorator
async def send_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return

    chat_id = update.effective_chat.id
    filename = 'btc_hourly_chart.png'
    
    # Send a progress message while the chart is being generated
    progress_message = await context.bot.send_message(chat_id=chat_id, text="Generating chart... Please wait ⏳")
    
    try:
        # Fetch the data and generate the chart
        data = fetch_btc_hourly_data()
        generate_btc_chart(data, filename)

        # Send the generated chart image to the user
        with open(filename, 'rb') as f:
            await context.bot.send_photo(chat_id=chat_id, photo=f, caption='📉 BTC/USD 1-Hour Chart')

        # Delete the progress message after the chart is sent
        await progress_message.delete()

    except Exception as e:
        # If an error occurred, send an error message
        await context.bot.send_message(chat_id=chat_id, text=f"❌ 🙈 Error: {e}")
        await progress_message.delete()

    finally:
        # Clean up: Remove the generated chart file after sending it
        if os.path.exists(filename):
            os.remove(filename)
            
async def handle_trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    if user_id not in USERS:
        await query.edit_message_text("❌ 🙈 You need to /register first.")
        return

    try:
        action, percent_str = query.data.split('_')
        percent = int(percent_str)
        user = USERS[user_id]

        if action == 'buy':
            usd_available = user['usd']
            usd_amount = (percent / 100) * usd_available
            success, message = execute_trade(user_id, 'buy', usd_amount, context)
        elif action == 'sell':
            btc_value_in_usd = user['btc'] * get_btc_price()
            usd_amount = (percent / 100) * btc_value_in_usd
            success, message = execute_trade(user_id, 'sell', usd_amount, context)
        else:
            message = "❌ 🙈 Unknown action."

        await query.edit_message_text(message)
    except Exception as e:
        logger.error(f"Trade button error: {e}")
        await query.edit_message_text("❌ 🙈 An error occurred.")

async def process_limit_orders_callback(context: CallbackContext):
    """Background task to process limit orders periodically."""
    try:
        executed_orders = await process_limit_orders(context)

        if executed_orders:
            logger.info(f"Executed limit orders: {executed_orders}")
    except Exception as e:
        logger.error(f"Error in limit order processing task: {e}")

@rate_limit_decorator
async def claimprize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global WINNER_ID, WINNER_ANNOUNCED
    user_id = str(update.effective_user.id)

    if user_id not in USERS:
        await update.effective_chat.send_message("❌ 🙈 You need to /register first.")
        return

    user = USERS[user_id]
    price = get_btc_price()
    total_value = user["usd"] + (user["btc"] * price)
    pnl = total_value - 100000.0

    # If someone already won
    if WINNER_ID:
        if str(WINNER_ID) == user_id:
            await update.effective_chat.send_message(
                "🎉 You already claimed the winnings! You're the champion 🏆"
            )
        else:
            winner_data = USERS.get(WINNER_ID)
            winner_nickname = winner_data.get("nickname", "Unknown") if winner_data else "Unknown"
            await update.effective_chat.send_message(
                f"❌ Winnings have already been claimed by *{winner_nickname}*. The contest is over. See you next time!\n\n *New*: Join this channel for future contest announcements: https://t.me/Goldkingcoinerscontests",
                parse_mode="Markdown"
            )
        return

    # No winner yet — check eligibility
    if pnl >= 3000 and not WINNER_ANNOUNCED:
        WINNER_ID = user_id
        WINNER_ANNOUNCED = True
        winner_nickname = user.get("nickname", "A trader")
        save_data()

        await update.effective_chat.send_message(f"🎉 Congrats! 🏆 Message @Goldkingcoiner2 with your Bech32 BTC address to redeem your winnings!")

        # Broadcast with rate-limiting 
        for other_id, other_user in USERS.items():
            if other_id != user_id:
                try:
                    await context.bot.send_message(
                        chat_id=other_id,
                        text=f"🎉 {winner_nickname} has claimed the winnings with a ${pnl:,.2f} profit! The contest is over. See you next time!\n\n *New*: Join this channel for future contest announcements: https://t.me/Goldkingcoinerscontests"
                    )
                    await asyncio.sleep(3)  # 3s delay
                except Exception as e:
                    logger.warning(f"Failed to notify user {other_id}: {e}")
        else:
            await update.effective_chat.send_message(
                f"🎉 {winner_nickname} has claimed the winnings with a ${pnl:,.2f} profit! The contest is over. See you next time!\n\n *New*: Join this channel for future contest announcements: https://t.me/Goldkingcoinerscontests"
            )

    else:
        await update.effective_chat.send_message(
            f"❌ 🙈 Your current PnL is ${pnl:,.2f}. You need a PnL of $3,000 to claim the winnings."
        )



# --- Main Bot Setup ---
def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Register all handlers BEFORE polling starts
    application.add_handler(CommandHandler("news", news))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard))
    application.add_handler(CommandHandler("history", history))
    application.add_handler(CommandHandler("portfolio", portfolio))
    application.add_handler(CommandHandler("price", price))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("sell", sell))    
    application.add_handler(CommandHandler("chart", send_chart))
    application.add_handler(CommandHandler("register", register))
    application.add_handler(CommandHandler("limitbuy", limitbuy))
    application.add_handler(CommandHandler("limitsell", limitsell))
    application.add_handler(CommandHandler("myorders", my_orders))
    application.add_handler(CommandHandler("stopbuy", stopbuy))
    application.add_handler(CommandHandler("stopsell", stopsell))
    application.add_handler(CommandHandler("claimprize", claimprize))

    application.add_handler(CallbackQueryHandler(handle_cancel_order_button, pattern=r"^cancelorder_"))
    application.add_handler(CallbackQueryHandler(handle_cancel_all_button, pattern=r"^cancelall$"))
    application.add_handler(CallbackQueryHandler(handle_trade_callback, pattern=r"^(buy|sell)_\d+$"))


    
    # Start a background task to check limit orders periodically
    application.job_queue.run_repeating(
        process_limit_orders_callback, 
        interval=30.0,  # Check every 30 seconds
        first=10.0      # Start after 10 seconds
    )
    
    # 🐵 Start polling
    application.run_polling()

if __name__ == "__main__":
    main()