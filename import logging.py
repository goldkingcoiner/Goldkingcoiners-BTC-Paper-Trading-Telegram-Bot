import time
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Replace with your bot's token
BOT_TOKEN = '7766197856:AAGWDy1TCbmvmpIEY0YEEXbQ1mYmLofAhTU'

# Set up logging to get detailed error messages
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Command handler to test response time
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    start_time = time.time()  # Record start time
    await update.message.reply_text('Pong!')  # Send a message to the user
    end_time = time.time()  # Record end time

    # Calculate the response time
    response_time = round((end_time - start_time) * 1000, 2)  # Convert to milliseconds
    await update.message.reply_text(f"Response time: {response_time} ms")

def main():
    """Start the bot."""
    # Create Application and pass in the bot's token
    application = Application.builder().token(BOT_TOKEN).build()

    # Register the /ping command handler
    application.add_handler(CommandHandler("ping", ping))

    # Start the Bot
    application.run_polling()

if __name__ == '__main__':
    main()
