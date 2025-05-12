import os
import logging
import json
import time
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
# --- Configuration ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('goldkingcoinersbot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv("goldkingcoinersbot.env")
TOKEN = os.getenv("BOT_TOKEN")
STARTING_BTC = 10.0
TRADE_FEE = 0.001  # 0.1%

# --- Data Management ---
DATA_FILE = 'bot_data.json'

USERS = {}
ORDERS = []  # Limit orders (removed limit functionality, but kept the list for possible future use)
_last_price = None
_last_price_time = 0

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📝 *Available Commands:* \n\n"
        "/start - Start interacting with the bot and see your balance.\n"
        "/register <your_name> - Register with the bot.\n"
        "/leaderboard - View the top traders and their profits.\n"
        "/trades - View the most recent trades from all users.\n"
        "/buy <amount> - Buy a certain amount of BTC with your USD balance. 0.1% trading fee.\n"
        "/sell <amount> - Sell a certain amount of BTC for USD. 0.1% trading fee.\n"
        "/portfolio - View your portfolio with BTC and USD balance.\n"
        "/help - Show this help message.\n\n"
        "💡 *Note*: All trades are simulated and for learning purposes."
    )
    await update.message.reply_text(help_text)

def save_data():
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump({
                'users': USERS,
                'price_data': {
                    'last_price': _last_price,
                    'last_price_time': _last_price_time
                }
            }, f)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def load_data():
    try:
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            return (
                data.get('users', {}),
                data.get('price_data', {}).get('last_price', None),
                data.get('price_data', {}).get('last_price_time', 0)
            )
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, None, 0

USERS, _last_price, _last_price_time = load_data()

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
    except Exception as e:
        logger.error(f"Price check failed: {e}")
        if _last_price:
            return _last_price
        raise Exception("Price service unavailable")

# --- Trading Logic ---
def execute_trade(user_id, action, amount, context):
    price = get_btc_price()
    user = USERS[user_id]

    if amount <= 0:
        return False, "❌ You can't trade a negative or zero amount of BTC."

    if action == 'buy':
        cost = amount * price * (1 + TRADE_FEE)
        if user['usd'] < cost:
            return False, "❌ Insufficient USD."
        user['usd'] -= cost
        user['btc'] += amount
        user['trades'].append({"type": "buy", "btc": amount, "price": price, "timestamp": datetime.now().isoformat()})
        
        # Send trade update to the group
        community_chat_id = "-1002659773587"  # Replace with actual group chat ID
        context.bot.send_message(
            chat_id=community_chat_id,
            text=f"{user['username']} just bought {amount:.6f} BTC @ ${price:,.2f}"
        )

        return True, f"✅ Bought {amount:.6f} BTC for ${cost:,.2f}"

    elif action == 'sell':
        if user['btc'] < amount:
            return False, "❌ Insufficient BTC."
        proceeds = amount * price * (1 - TRADE_FEE)
        user['btc'] -= amount
        user['usd'] += proceeds
        user['trades'].append({"type": "sell", "btc": amount, "price": price, "timestamp": datetime.now().isoformat()})
        
        # Send trade update to the group
        community_chat_id = "-1002659773587"  # Replace with actual group chat ID
        context.bot.send_message(
            chat_id=community_chat_id,
            text=f"{user['username']} just sold {amount:.6f} BTC @ ${price:,.2f}"
        )

        return True, f"✅ Sold {amount:.6f} BTC for ${proceeds:,.2f}"

    return False, "❌ Invalid action."

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id

        if user_id in USERS:
            username = USERS[user_id]['username']
            welcome_text = (
                f"👋 Welcome back, *{username}*!\n\n"
                "You're already registered.\n"
                "Use the commands below to start trading."
            )
        else:
            welcome_text = (
                r"💎 *GoldKing Coiners Trading Bot* 💎" + "\n\n"
                r"Trade Bitcoin with simulated funds." + "\n"
                r"Starting balance: 10 BTC" + "\n\n"
                r"📌 Use /register <your_name> to begin."
            )

        await update.message.reply_text(
            welcome_text,
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Start error: {e}")
        await update.message.reply_text("⚠️ Bot is temporarily unavailable. Please try again later.")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = get_btc_price()  # Get the current BTC price
    rankings = []

    for uid, user in USERS.items():
        # Calculate the total wealth: USD balance + BTC holdings at current price
        total_wealth = user["usd"] + (user["btc"] * price)
        
        # Calculate the unrealized P&L relative to the starting point (100 BTC at the start)
        starting_value = STARTING_BTC * price  # The value of the starting 100 BTC in USD
        pnl_absolute = total_wealth - starting_value  # Absolute profit/loss (in USD)
        
        # Calculate the P&L percentage
        pnl_percentage = (pnl_absolute / starting_value) * 100  # P&L as a percentage
        
        # Add user and their P&L info to the rankings list
        rankings.append((user["username"], pnl_absolute, pnl_percentage))

    # Sort users by P&L in descending order (highest P&L first)
    rankings.sort(key=lambda x: x[1], reverse=True)
    
    top = rankings[:10]  # Show top 10 traders

    # Format the leaderboard displaying P&L in dollars and percentage
    top_traders_text = "\n".join([f"{i+1}. {name} - ${pnl:,.2f} ({pnl_percentage:+.2f}%)" for i, (name, pnl, pnl_percentage) in enumerate(top)])

    # Send leaderboard to the Telegram group
    community_chat_id = "-1002659773587"  # Replace with actual group chat ID
    await context.bot.send_message(
        chat_id=community_chat_id,
        text=f"🏆 Top Traders by P&L (Relative to Starting Point):\n{top_traders_text}"
    )

    # Respond to the user
    await update.message.reply_text(f"🏆 Top Traders by P&L (Relative to Starting Point):\n{top_traders_text}")

# Posting trade updates to the group in execute_trade function

async def trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recent_trades_text = ""
    for user in USERS.values():
        if user["trades"]:
            recent_trades_text += f"\n{user['username']}'s Recent Trades:\n"
            for trade in user["trades"][-5:]:  # Show last 5 trades for example
                trade_type = trade['type'].capitalize()
                trade_btc = trade['btc']
                trade_price = trade['price']
                trade_time = trade['timestamp']
                recent_trades_text += f"• {trade_type} {trade_btc:.6f} BTC @ ${trade_price:,.2f} on {trade_time}\n"

    if recent_trades_text:
        await update.message.reply_text(f"📜 Recent Trades:\n{recent_trades_text}")
    else:
        await update.message.reply_text("❌ No trades have been made yet.")

async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if user_id not in USERS:
            await update.message.reply_text("⚠️ You need to /register before using this command.")
            return

        user = USERS[user_id]
        total_value = user["usd"] + (user["btc"] * get_btc_price())  # Show live BTC price

        response_text = (
            f"📊 {user['username']}'s Portfolio\n"
            f"• 💵 USD: ${user['usd']:,.2f}\n"
            f"• ₿ BTC: {user['btc']:.6f}\n"
            f"• 💰 Total Value: ${total_value:,.2f}\n"
            f"📈 Current BTC Price: ${get_btc_price():,.2f}"  # Show current BTC price
        )
        
        await update.message.reply_text(response_text)
    except Exception as e:
        logger.error(f"Portfolio error: {e}")
        await update.message.reply_text("❌ Couldn't fetch portfolio data. Please try again later.")

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id

        if user_id in USERS:
            username = USERS[user_id]["username"]
            await update.message.reply_text(
                f"⚠️ You're already registered as *{username}*.\n"
                "Use /reset if you want to start over.",
                parse_mode="Markdown"
            )
            return

        if not context.args:
            await update.message.reply_text("Usage: /register <your_name>")
            return

        new_username = " ".join(context.args).strip()[:32]

        # Prevent duplicate usernames
        for existing_user in USERS.values():
            if existing_user["username"].lower() == new_username.lower():
                await update.message.reply_text("❌ That username is already taken. Please choose a different name.")
                return

        USERS[user_id] = {
            "username": new_username,
            "usd": 0.0,
            "btc": 10.0,  # Starting balance in BTC
            "trades": [],
            "joined": datetime.now().isoformat()
        }
        save_data()

        await update.message.reply_text(
            f"✅ Registered as {new_username} with 10 BTC.\n"
            "Use /help to see available commands."
        )
    except Exception as e:
        logger.error(f"Register error: {e}")
        await update.message.reply_text("❌ Registration failed. Please try again.")

# --- Buy and Sell Handlers ---
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if user_id not in USERS:
            await update.message.reply_text("⚠️ You need to /register first.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /buy <amount>")
            return

        amount = float(context.args[0])

        success, message = execute_trade(user_id, 'buy', amount, context)

        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Buy error: {e}")
        await update.message.reply_text("❌ Error occurred while trying to buy BTC. Please try again.")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if user_id not in USERS:
            await update.message.reply_text("⚠️ You need to /register first.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /sell <amount>")
            return

        amount = float(context.args[0])

        success, message = execute_trade(user_id, 'sell', amount, context)

        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Sell error: {e}")
        await update.message.reply_text("❌ Error occurred while trying to sell BTC. Please try again.")

# --- Reset and Delete Commands ---
async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in USERS:
        await update.message.reply_text("⚠️ You're not registered yet. Use /register <name> to get started.")
        return

    USERS[user_id]["usd"] = 0.0
    USERS[user_id]["btc"] = 10.0
    USERS[user_id]["trades"] = []
    USERS[user_id]["joined"] = datetime.now().isoformat()
    save_data()

    await update.message.reply_text("🔁 Your account has been reset to the starting balance (10 BTC, $0 USD).")

async def delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in USERS:
        await update.message.reply_text("⚠️ No account found to delete.")
        return

    keyboard = [
        [InlineKeyboardButton("✅ Confirm Delete", callback_data="confirm_delete")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚠️ Are you sure you want to *permanently delete* your account?\nThis cannot be undone.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "confirm_delete":
        if user_id in USERS:
            del USERS[user_id]
            save_data()
            await query.edit_message_text("✅ Your account has been permanently deleted.")
        else:
            await query.edit_message_text("❌ Your account was not found.")
    elif query.data == "cancel_delete":
        await query.edit_message_text("🚫 Account deletion cancelled.")
    else:
        await query.edit_message_text("❓ Invalid selection.")

async def delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in USERS:
        await update.message.reply_text("⚠️ No account found to delete.")
        return

    # Confirmation step
    keyboard = [
        [InlineKeyboardButton("✅ Confirm Delete", callback_data="confirm_delete")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚠️ Are you sure you want to permanently delete your account? This cannot be undone.",
        reply_markup=reply_markup
    )

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "confirm_delete":
        if user_id in USERS:
            del USERS[user_id]
            save_data()
            await query.edit_message_text("✅ Your account has been permanently deleted.")
        else:
            await query.edit_message_text("❌ Your account was not found.")
    elif query.data == "cancel_delete":
        await query.edit_message_text("🚫 Account deletion cancelled.")

# --- Main Bot Setup ---
def main():
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard))
    application.add_handler(CommandHandler("trades", trades))
    application.add_handler(CommandHandler("portfolio", portfolio))
    application.add_handler(CommandHandler("register", register))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("sell", sell))    
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("delete_account", delete_account))
    application.add_handler(CallbackQueryHandler(delete_callback))
if __name__ == "__main__":
    main()