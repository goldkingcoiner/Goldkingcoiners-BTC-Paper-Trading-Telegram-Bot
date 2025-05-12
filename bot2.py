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
from telegram.ext import Updater, CallbackContext
from functools import wraps
# --- Configuration ---
# Rate limit decorator
load_dotenv("goldkingcoinersbot2.env")
TOKEN = os.getenv("BOT_TOKEN")
TRADE_FEE = 0.001  # 0.05%
# Store timestamps of user commands
user_last_interaction = {}

# Define the cooldown period in seconds (e.g., 2 seconds)
COOLDOWN_TIME = 1
MIN_TRADE_AMOUNT = 1.0  # minimum USD value for any trade

# --- Data Management ---
DATA_FILE = 'bot5_data.json'
ORDERS = []

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
                await update.message.reply_text("⚠️ You're sending commands too quickly. Please wait a moment.")
                return  # Prevent the command from executing if it's too fast

        # Update the last interaction time for the user
        user_last_interaction[user_id] = current_time

        # Call the original function (the command handler)
        return await func(update, context, *args, **kwargs)

    return wrapper

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

USERS, _last_price, _last_price_time = load_data()
  

@rate_limit_decorator
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = ( 

        "🔥 Welcome to the Goldkingcoiner's\n trading challenge❗🔥\n\n"
        
        "🤔 How to use this bot:\n\n"
        "Press the menu buttons or type the commands: \n"
        
        "☞ /leaderboard - show top traders by PnL\n"
        "☞ /chart - show 1-hour BTC chart\n"
        "☞ /buy - buy Bitcoin (0.1 percent fee)\n" 
        "☞ /sell - sell Bitcoin (0.1 percent fee)\n"
        "☞ /portfolio - view portfolio\n"        
        "☞ /trades - view trading history\n"
        "☞ /price - BTC price\n"                
        "☞ /start - start bot.\n"
        "☞ /register <nickname> - register your nickname\n"
        "☞ /help - help\n\n"

        "The winner (top PnL) is declared at the end of the competition.\n"
        "ANN: https://bitcointalk.org/index.php?topic=5540701 \n\n"
        
        f"*Note:\n\n" 
        
        "All trades are simulated and for learning purposes only.\n"        
        "I can change the rules and conditions of this competition at any time. User data is deleted after the challenge is over.\n" 
    )
    await update.message.reply_text(help_text)

# --- Price API ---
def get_btc_price():
    global _last_price, _last_price_time
    try:
        current_time = time.time()
        if current_time - _last_price_time < 30 and _last_price:
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
def execute_trade(user_id, action, usd_amount, context):
    price = get_btc_price()
    user = USERS[user_id]

    if usd_amount <= 0:
        return False, "❌ You can't trade a negative or zero USD amount."

    if usd_amount < MIN_TRADE_AMOUNT:
        return False, f"❌ Minimum trade amount is ${MIN_TRADE_AMOUNT:.2f}."

    fee_multiplier = 1 - TRADE_FEE

    if action == 'buy':
        if user['usd'] < usd_amount:
            return False, "❌ Insufficient USD."

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
        return True, f"✅ Bought {btc_bought:.6f} BTC for ${usd_amount:,.2f} @ ${price:,.2f}"

    elif action == 'sell':
        btc_to_sell = usd_amount / price
        if user['btc'] < btc_to_sell:
            return False, "❌ Insufficient BTC."

        if usd_amount < MIN_TRADE_AMOUNT:
            return False, f"❌ Minimum trade amount is ${MIN_TRADE_AMOUNT:.6f}."

        net_usd = usd_amount * fee_multiplier
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
        return True, f"✅ Sold {btc_to_sell:.6f} BTC for ${net_usd:,.2f} @ ${price:,.2f}"

    return False, "❌ Invalid action."




# --- Command Handlers ---
@rate_limit_decorator
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    await update.message.reply_text(f"* * * * 🏆 Leaderboard 🏆 * * * *\n\n{top_traders_text}")




# Posting trade updates to the group in execute_trade function
@rate_limit_decorator
async def trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.message.reply_text("⚠️ You need to /register first. Use /help for a list of commands.")
        return

    user = USERS[user_id]
    trades = user.get("trades", [])
    
    if not trades:
        await update.message.reply_text("❌ You have no trades yet.")
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

    await update.message.reply_text(recent_trades_text)
  


@rate_limit_decorator
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


@rate_limit_decorator
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
@rate_limit_decorator
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)  # Get the user's ID    
    username = update.effective_user.username  # Get the user's username
    if user_id in USERS:
        logger.warning(f"User {username} is already registered.")
        await update.message.reply_text("Welcome to the Goldkingcoiner's trading challenge❗🔥\n\n⚠️ You are already registered.\n\n Use /help for help.")
        return

    await update.message.reply_text(
        "🔥Welcome to the Goldkingcoiner's trading challenge❗🔥\n\n"
        "To begin trading, register with:\n"
        "`/register <your nickname>`\n\n"
        "Important: Register in this thread too: https://bitcointalk.org/index.php?topic=5542315.0\n"        
        "Use /help to see available commands.",
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
@rate_limit_decorator
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.message.reply_text("⚠️ You need to /register first.")
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
    await update.message.reply_text("💵 How much of your USD balance would you like to use?", reply_markup=reply_markup)

@rate_limit_decorator
async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.message.reply_text("⚠️ You need to /register first.")
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
    await update.message.reply_text("How much of your BTC would you like to sell?", reply_markup=reply_markup)



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
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Error: {e}")
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
        await query.edit_message_text("⚠️ You need to /register first.")
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
            message = "❌ Unknown action."

        await query.edit_message_text(message)
    except Exception as e:
        logger.error(f"Trade button error: {e}")
        await query.edit_message_text("❌ An error occurred.")

            
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
    application.add_handler(CallbackQueryHandler(handle_trade_callback))
    application.add_handler(CommandHandler("register", register))
    # ✅ Start polling LAST
    application.run_polling()
if __name__ == "__main__":
    main()