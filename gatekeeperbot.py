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
import pytz
import asyncio

# --- Configuration ---

def load_data():
    global USERS, _last_price, _last_price_time
    try:
        # Check if the file exists and has data
        if os.path.exists(DATA_FILE) and os.path.getsize(DATA_FILE) > 0:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                logger.info(f"Data loaded successfully: {data}")
                USERS = data.get('users', {})
                _last_price = data.get('price_data', {}).get('last_price', None)
                _last_price_time = data.get('price_data', {}).get('last_price_time', 0)
                logger.info(f"Loaded USERS: {USERS}")
        else:
            # If the file is empty or doesn't exist, initialize data
            logger.warning(f"Data file {DATA_FILE} is empty or doesn't exist. Initializing new data.")
            USERS = {}
            _last_price = None
            _last_price_time = 0
            save_data()  # Save the initialized data
            logger.info("Initialized new data file with empty structure.")
        
        return USERS, _last_price, _last_price_time
    
    except (FileNotFoundError, json.JSONDecodeError) as e:
        # If any error occurs, initialize data and log the issue
        logger.error(f"Error loading data: {e}")
        USERS = {}
        _last_price = None
        _last_price_time = 0
        save_data()  # Save the initialized data in case of error
        logger.info("Initialized new data file with empty structure.")
        return USERS, _last_price, _last_price_time

def save_data():
    try:
        with open(DATA_FILE, 'w') as f:
            data = {
                'users': USERS,
                'price_data': {
                    'last_price': _last_price,
                    'last_price_time': _last_price_time
                }
            }
            json.dump(data, f)
            logger.info(f"Data saved successfully: {data}")  # Log the data being saved
    except Exception as e:
        logger.error(f"Error saving data: {e}")
logger = logging.getLogger(__name__)

load_dotenv("goldkingcoinersbot2.env")
TOKEN = os.getenv("BOT_TOKEN")
TRADE_FEE = 0.0005  # 0.05%


# --- Data Management ---
DATA_FILE = 'bot5_data.json'
USERS, _last_price, _last_price_time = load_data()
ORDERS = []  

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = ( 

        "🔥Welcome to the Goldkingcoiner's trading challenge❗🔥\n\n"
        
        "🤔 How to use this bot:\n\n"
        "Press the menu buttons or type the commands: \n"
        
        "☞ /leaderboard - top traders\n"
        "☞ /chart - 1-hour BTC chart\n"
        "☞ /buy - buy Bitcoin\n" 
        "☞ /sell - sell Bitcoin\n"
        "☞ /portfolio - view portfolio\n"        
        "☞ /trades - view trading history\n"
        "☞ /price - BTC price\n"                
        "☞ /start - start bot.\n"
        "☞ /register <nickname> - register your nickname\n"
        "☞ /help - help\n\n"

        "The winner (top PnL) is declared at the end of the competition.\n"
        "❗Important: Register in this thread: https://bitcointalk.org/index.php?topic=5542315.0"
        f"\n*Note:\n" 
        
        "All trades are simulated and for learning purposes only.\n"        
        "I can change the rules and conditions of this competition at any time. User data is deleted after the contest is over.\n" 
    )
    await update.message.reply_text(help_text)

# --- Price API ---
def get_btc_price():
    global _last_price, _last_price_time
    try:
        current_time = time.time()
        if current_time - _last_price_time < 60 and _last_price:
            return _last_price

        response = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
            timeout=10
        )
        response.raise_for_status()
        _last_price = float(response.json()["bitcoin"]["usd"])
        _last_price_time = current_time
        save_data()
        return _last_price
    except requests.exceptions.RequestException as e:
        logger.error(f"Price check failed: {e}")
        if _last_price:
            return _last_price
        raise Exception("Price service unavailable. Please try again later.")

# --- Trading Logic ---
# --- Trading Logic ---
def execute_trade(user_id, action, usd_amount, context):
    price = get_btc_price()
    user = USERS[user_id]

    if usd_amount <= 0:
        return False, "❌ You can't trade a negative or zero USD amount."

    fee_multiplier = 1 - TRADE_FEE  # 0.999 for 0.05% fee

    if action == 'buy':
        if user['usd'] < usd_amount:
            return False, "❌ Insufficient USD."

        btc_bought = (usd_amount * fee_multiplier) / price  # Apply fee here

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
        return True, f"✅ Bought {btc_bought:.6f} BTC for ${usd_amount:,.2f} @ ${price:,.2f} (Fee: 0.05%)"

    elif action == 'sell':
        btc_to_sell = usd_amount / price
        if user['btc'] < btc_to_sell:
            return False, "❌ Insufficient BTC."

        net_usd = usd_amount * fee_multiplier  # Fee applied to proceeds

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
        return True, f"✅ Sold {btc_to_sell:.6f} BTC for ${net_usd:,.2f} @ ${price:,.2f} (Fee: 0.05%)"

    return False, "❌ Invalid action."



# --- Command Handlers ---

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = get_btc_price()
    rankings = []

    for uid, user in USERS.items():
        name = user['nickname'] or f"Trader {user['number']}"
        total_wealth = user["usd"] + (user["btc"] * price)
        pnl = total_wealth - 100000.0  # Assuming everyone starts with $100000
        rankings.append((name, total_wealth, pnl))

    rankings.sort(key=lambda x: x[1], reverse=True)
    top = rankings[:50]

    top_traders_text = "\n".join([
        f"{i+1}. {name} | 💰 ${wealth:,.2f} | 📈 PnL: ${pnl:,.2f}"
        for i, (name, wealth, pnl) in enumerate(top)
    ])
    await update.message.reply_text(f"* * * * 🏆 Leaderboard 🏆 * * * *\n{top_traders_text}")



# Posting trade updates to the group in execute_trade function

async def trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)  # Get the user's ID
    if user_id not in USERS:
        await update.message.reply_text("⚠️ You need to /register first. Use /help for a list of commands.")
        return

    user = USERS[user_id]
    trades = user.get("trades", [])  # Get the list of trades for the user
    
    # If there are no trades, send a message
    if not trades:
        await update.message.reply_text("❌ You have no trades yet.")
        return
    
    # Get the last 25 trades (or fewer if the user has less than 25 trades)
    recent_trades = trades[-15:]

    # Build the response text for recent trades
    recent_trades_text = f"📜 {user['nickname']}'s 15 most recent trades:\n\n"
    for trade in recent_trades:
        trade_type = trade['type'].capitalize()  # Buy/Sell
        trade_btc = trade['btc']
        trade_price = trade['price']
        trade_time = trade['timestamp']
        recent_trades_text += f"• {trade_type} {trade_btc*trade_price:.2f} USD @ ${trade_price:,.2f} on {trade_time}\n\n"

    await update.message.reply_text(recent_trades_text)



async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if user_id not in USERS:
            await update.message.reply_text("⚠️ You need to /register first. Use /help for a list of commands.")
            return

        user = USERS[user_id]
        total_value = user["usd"] + (user["btc"] * get_btc_price())

        display_name = user['nickname']

        if not display_name:
            display_name = f"Trader {user['number']}"

        response_text = (
            f"📊 {display_name}'s Portfolio\n"
            f"• 💵 USD: ${user['usd']:,.2f}\n"
            f"•   ₿ BTC: {user['btc']:.6f}\n"
            f"• 💰 Total Value: ${total_value:,.2f}\n"
        )
        
        await update.message.reply_text(response_text)
    except Exception as e:
        logger.error(f"Portfolio error: {e}")
        await update.message.reply_text("❌ Couldn't fetch portfolio data. Please try again later.")



async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if user_id not in USERS:
            await update.message.reply_text("⚠️ You need to /register first. Use /help for a list of commands.")
            return

        response_text = (
            f"📈 Current BTC Price: ${get_btc_price():,.2f}"  # Show current BTC price
        )
        
        await update.message.reply_text(response_text)
    except Exception as e:
        logger.error(f"price: {e}")
        await update.message.reply_text("❌ Couldn't fetch data. Please try again later.")

# --- Main Entry ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)  # Get the user's ID    
    username = update.effective_user.username  # Get the user's username
    if user_id in USERS:
        logger.warning(f"User {username} is already registered.")
        await update.message.reply_text("Welcome to the Goldkingcoiner's trading challenge❗🔥\n\n⚠️ You are already registered.\n\n Use /help for help.")
        return

    await update.message.reply_text(
        "🔥Welcome to the Goldkingcoiner's trading challenge❗🔥\n\n"
        "To begin, register like this:\n"
        "`/register <your nickname>`\n\n"
        "Important: Register in this thread too: https://bitcointalk.org/index.php?topic=5542315.0\n"
        "Use /help to see available commands.",
        parse_mode="Markdown"
    )



async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)  # Get the user's unique ID
    username = update.effective_user.username  # Get the user's username

    # Log the registration attempt
    logger.info(f"Attempting to register user: {username} (ID: {user_id})")

    # Check if the user is already registered
    if user_id in USERS:
        logger.warning(f"User {username} is already registered.")
        await update.message.reply_text("⚠️ You are already registered.\n\n Use /help for help.")
        return

    # Ensure that the user has provided a nickname
    if not context.args:
        logger.warning(f"No nickname provided for user {username} (ID: {user_id})")
        await update.message.reply_text("Please use: /register <nickname>")
        return

    # Get the nickname from the command arguments
    nickname = ' '.join(context.args).strip()

    # Ensure nickname uniqueness
    for user in USERS.values():
        if user.get('nickname', '').lower() == nickname.lower():
            logger.warning(f"Nickname {nickname} is already taken.")
            await update.message.reply_text("❌ Nickname already taken. Choose another.")
            return

    # Register the user
    trader_count = len(USERS) + 1
    USERS[user_id] = {
        'usd': 100000.0,
        'btc': 0.0,
        'trades': [],
        'nickname': nickname,
        'username': username,
        'number': trader_count
    }

    # Save the data after registration
    save_data()

    # Confirm successful registration
    logger.info(f"User {nickname} registered successfully with data: {USERS[user_id]}")
    await update.message.reply_text(f"✅ Registered successfully as: *{nickname}*", parse_mode="Markdown")


# --- Buy and Sell Handlers ---
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.message.reply_text("🤔 The contest has not started yet.\n Come back later...\n\n")
        return

    await update.message.reply_text("🤔 The contest has not started yet.\n Come back later...\n\n")


async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.message.reply_text("🤔 The contest has not started yet.\n Come back later...\n\n")

        return

    await update.message.reply_text("🤔 The contest has not started yet.\n Come back later...\n\n")




def fetch_btc_hourly_data():
    binance = ccxt.binance()
    ohlcv = binance.fetch_ohlcv('BTC/USD', timeframe='1h', limit=168)  # Last 7 days

    df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
    
    # Convert UTC to CET
    df['Date'] = pd.to_datetime(df['Timestamp'], unit='ms', utc=True)
    df['Date'] = df['Date'].dt.tz_convert('Europe/Berlin')  # CET/CEST

    df.set_index('Date', inplace=True)
    return df[['Open', 'High', 'Low', 'Close', 'Volume']]

# Generate and save chart
def generate_chart(data, filename):
    mpf.plot(data, type='candle', style='charles',
             title='BTC/USD Hourly', volume=True, savefig=filename)

# Telegram bot command
async def send_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    filename = 'btc_hourly.png'

    try:
        data = fetch_btc_hourly_data()
        generate_chart(data, filename)

        with open(filename, 'rb') as f:
            await context.bot.send_photo(chat_id=chat_id, photo=f, caption='📉 BTC/USD Hourly Chart')

    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Error: {e}")

    finally:
        if os.path.exists(filename):
            os.remove(filename)
            

            
# --- Main Bot Setup ---
# --- Main Bot Setup ---
def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Register all handlers BEFORE polling starts
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard))
    application.add_handler(CommandHandler("trades", trades))
    application.add_handler(CommandHandler("portfolio", portfolio))
    application.add_handler(CommandHandler("price", price))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("sell", sell))    
    application.add_handler(CommandHandler("chart", send_chart))
    application.add_handler(CommandHandler("register", register))
    # ✅ Start polling LAST
    application.run_polling()
if __name__ == "__main__":
    main()

