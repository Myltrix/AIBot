import asyncio
import logging
import google.generativeai as genai
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message

GEMINI_API_KEY = "API"
TOKEN = "TOKEN"

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

logging.basicConfig(level=logging.INFO)

bot = Bot(TOKEN)
dp = Dispatcher()

user_memory = {}

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_memory[message.from_user.id] = []
    await message.answer("👋 Привет! Я бот с ИИ Gemini. Запоминаю наш разговор, так что можешь общаться со мной как с человеком 😉")

@dp.message(F.text)
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_text = message.text.strip()

    if user_id not in user_memory:
        user_memory[user_id] = []

    user_memory[user_id].append({"role": "user", "content": user_text})

    await bot.send_chat_action(message.chat.id, "typing")

    try:
        chat = []
        for msg in user_memory[user_id][-10:]:
            if msg["role"] == "user":
                chat.append({"role": "user", "parts": [msg["content"]]})
            else:
                chat.append({"role": "model", "parts": [msg["content"]]})

        chat.append({"role": "user", "parts": [user_text]})

        def generate():
            response = model.generate_content(chat)
            return response.text.strip()

        ai_reply = await asyncio.to_thread(generate)

        user_memory[user_id].append({"role": "model", "content": ai_reply})

        await message.answer(ai_reply)

    except Exception as e:
        logging.error(f"Ошибка Gemini: {e}")
        await message.answer(f"⚠️ Ошибка при обращении к ИИ: {e}")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
