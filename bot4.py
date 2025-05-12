import ccxt
import pandas as pd
import mplfinance as mpf
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv
import pytz
# Load token

load_dotenv("goldkingcoinersbot2.env")
TOKEN = os.getenv("BOT_TOKEN")

# Get BTC/USD hourly candles from Binance
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

# Main bot
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("chart", send_chart))
    print("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
