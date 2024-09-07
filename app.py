"""
    Entrypoint for telegram bot for saving media files
"""
import os
import sys

from loguru import logger
import telebot

AUDIO_PATH = os.environ.get("OUTPUT_FOLDER", "../data/")
TOKEN = os.environ.get("TG_TOKEN", None)

if not TOKEN:
    logger.error("You must pass the Telegram bot token as TG_TOKEN environment variable! See https://t.me/botfather to obtain it")
    sys.exit(-1)

bot = telebot.TeleBot(TOKEN, parse_mode="MARKDOWN") # You can set parse_mode by default. HTML or MARKDOWN

# See https://github.com/Delgan/loguru?tab=readme-ov-file#easier-file-logging-with-rotation-retention-compression
logger.add("tgmusicbot.log", format="{time:YYYY-MM-DD HH:mm} {level} {message}") # , compression="zip", rotation="10 MB"

logger.info("Started bot!")

def handle_audio(file, artist, title):
    audio_path = os.path.join(AUDIO_PATH, artist)
    if not os.path.isdir(audio_path):
        os.makedirs(audio_path, exist_ok=True)
    audio_file = os.path.join(audio_path, title)
    counter = 0
    filename = title
    while os.path.exists(audio_file):
        if os.path.getsize(audio_file) == len(file):
            logger.info(f"File {audio_file} already exists but it's the same file!")
            break
        logger.warning(f"File {audio_file} already exists!")
        filename = title.replace(".", "_" + str(counter) + ".")
        audio_file = os.path.join(audio_path, filename)
        counter += 1
    with open(audio_file, 'wb') as new_file:
        new_file.write(file)
    return audio_file, counter != 0

# TODO handle other types and say "sorry"?

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Howdy, how are you doing?")

# Handles all sent documents and audio files
@bot.message_handler(content_types=['document', 'audio'])
def handle_docs_audio(message):
    # See https://github.com/eternnoir/pyTelegramBotAPI/blob/master/examples/download_file_example.py
    if message.audio:
        file_info = bot.get_file(message.audio.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        saved_path, overwritten = handle_audio(downloaded_file, message.audio.performer, message.audio.file_name)
        response_msg = f"Saved as {saved_path}!" if overwritten else f"Already exists: saved as {saved_path}!"
        logger.info(response_msg)
        bot.reply_to(message, response_msg.replace('_', '\\_'))
        return
    logger.error(message)
    bot.reply_to(message, "Saving failed!")

@bot.message_handler(func=lambda m: True)
def echo_all(message):
    print(message)
    bot.reply_to(message, message.text)

bot.infinity_polling()