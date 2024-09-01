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
logger.add("/tmp/log/file_{time}.log", format="{time} {level} {message}") # , compression="zip"

logger.info("Started bot!")

def handle_audio(file, artist, title):
    audio_path = os.path.join(AUDIO_PATH, artist)
    if not os.path.isdir(audio_path):
        os.makedirs(audio_path, exist_ok=True)
    audio_file = os.path.join(audio_path, title)
    with open(audio_file, 'wb') as new_file:
        new_file.write(file)
    return audio_file

# TODO append a logfile
# TODO timestamp - readable
# TODO handle existing files
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
        # print("File: ")
        # print(file_info)
        downloaded_file = bot.download_file(file_info.file_path)
        # print(downloaded_file)
        saved_path = handle_audio(downloaded_file, message.audio.performer, message.audio.file_name)
        logger.info(f"Saved as {saved_path}!")
        bot.reply_to(message, f"Saved as {saved_path}!")
        return
    print(message)
    bot.reply_to(message, "Saving failed!")

@bot.message_handler(func=lambda m: True)
def echo_all(message):
    print(message)
    bot.reply_to(message, message.text)

bot.infinity_polling()