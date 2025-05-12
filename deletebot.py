from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
import asyncio

BOT_TOKEN = "7845947446:AAE4axPU8akBGgTmP97fr2cfR1ioycRkFWE"
TRADING_BOT_USER_ID = 7766197856  # User ID of the trading bot whose messages should be deleted
DELAY_SECONDS = 1  # Delay before deleting messages

async def delete_trading_bot_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    # Log all messages to verify content
    print(f"Received message: {message.text}")
    
    # Check if the message is a reply to another message
    if message.reply_to_message:
        print(f"Message is a reply to message ID: {message.reply_to_message.message_id}")

    # Only delete if the message is from the trading bot and is a reply to a user's message
    if message and message.from_user and message.from_user.id == TRADING_BOT_USER_ID:
        if message.reply_to_message:  # Check if it's a reply
            await asyncio.sleep(DELAY_SECONDS)  # Delay before deleting
            try:
                await context.bot.delete_message(chat_id=message.chat_id, message_id=message.message_id)
                print(f"Deleted message {message.message_id} from trading bot.")
            except Exception as e:
                print(f"Error deleting message: {e}")
        else:
            print(f"Message from the trading bot is not a reply. Skipping deletion.")

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add handler to handle all messages and check for the condition
    app.add_handler(MessageHandler(filters.ALL, delete_trading_bot_message))
    
    print("Bot is running...")
    app.run_polling()
