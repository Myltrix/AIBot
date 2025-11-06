import telebot
import json
import sqlite3
import logging
import google.generativeai as genai
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ai_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

GEMINI_API_KEY = "API"
bot = telebot.TeleBot('TOKEN')

try:
    if GEMINI_API_KEY and GEMINI_API_KEY != "YOUR_GEMINI_API_KEY_HERE":
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")
        logger.info("Gemini модель успешно инициализирована")
    else:
        logger.warning("Gemini API ключ не установлен или установлен демо-ключ")
        model = None
except Exception as e:
    logger.error(f"Ошибка инициализации Gemini: {e}")
    model = None

def init_db():
    conn = sqlite3.connect('quiz_bot.db', check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            private_chat_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            messages TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            question TEXT,
            response TEXT,
            liked BOOLEAN DEFAULT 0,
            used_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    conn.commit()
    return conn

db_connection = init_db()

user_chat_sessions = {}
pending_ai_responses = {}

def get_or_create_user(user_id, username, first_name, last_name, private_chat_id=None):
    cursor = db_connection.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, private_chat_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, last_name, private_chat_id))
    db_connection.commit()

def get_chat_session(user_id):
    if user_id not in user_chat_sessions:
        cursor = db_connection.cursor()
        cursor.execute('''
            SELECT messages FROM chat_sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1
        ''', (user_id,))
        result = cursor.fetchone()

        if result:
            messages = json.loads(result[0])
        else:
            messages = []

        user_chat_sessions[user_id] = messages

    return user_chat_sessions[user_id]

def save_chat_session(user_id, messages):
    cursor = db_connection.cursor()
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO chat_sessions (user_id, messages, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, json.dumps(messages)))
        db_connection.commit()
        user_chat_sessions[user_id] = messages
    except Exception as e:
        logger.error(f"Ошибка при сохранении чат-сессии: {e}")

def clear_chat_session(user_id):
    cursor = db_connection.cursor()
    try:
        cursor.execute('DELETE FROM chat_sessions WHERE user_id = ?', (user_id,))
        db_connection.commit()
        if user_id in user_chat_sessions:
            del user_chat_sessions[user_id]
    except Exception as e:
        logger.error(f"Ошибка при очистке чат-сессии: {e}")

def get_saved_ai_response(user_id, question):
    cursor = db_connection.cursor()
    cursor.execute('''
        SELECT response FROM ai_responses 
        WHERE user_id = ? AND question = ? AND liked = 1
        ORDER BY used_count DESC, created_at DESC
        LIMIT 1
    ''', (user_id, question))
    result = cursor.fetchone()
    return result[0] if result else None

def save_ai_response(user_id, question, response, liked=True):
    cursor = db_connection.cursor()
    try:
        cursor.execute('''
            INSERT INTO ai_responses (user_id, question, response, liked, used_count)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, question, response, liked, 1 if liked else 0))
        db_connection.commit()
        logger.info(f"Ответ ИИ сохранен для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении ответа ИИ: {e}")

def increment_ai_response_usage(response_id):
    cursor = db_connection.cursor()
    try:
        cursor.execute('''
            UPDATE ai_responses SET used_count = used_count + 1 WHERE id = ?
        ''', (response_id,))
        db_connection.commit()
    except Exception as e:
        logger.error(f"Ошибка при обновлении счетчика использования: {e}")

def query_gemini(user_id, question):
    try:
        saved_response = get_saved_ai_response(user_id, question)
        if saved_response:
            logger.info(f"Использован сохраненный ответ для пользователя {user_id}")
            return f"💾 *Ответ из сохраненных:*\n\n{saved_response}"

        if model is None:
            return "❌ AI сервис временно недоступен.\n\nПожалуйста, проверьте настройки API ключа Gemini."

        messages = get_chat_session(user_id)

        chat_history = []
        for msg in messages[-10:]:
            if msg['role'] == 'user':
                chat_history.append({"role": "user", "parts": [msg['content']]})
            else:
                chat_history.append({"role": "model", "parts": [msg['content']]})

        chat_history.append({"role": "user", "parts": [question]})

        def generate_response():
            response = model.generate_content(chat_history)
            return response.text.strip()

        with ThreadPoolExecutor() as executor:
            future = executor.submit(generate_response)
            reply = future.result(timeout=30)

        if reply:
            messages.append({"role": "user", "content": question})
            messages.append({"role": "assistant", "content": reply})

            if len(messages) > 20:
                messages = messages[-20:]

            save_chat_session(user_id, messages)
            return reply
        else:
            return "❌ Не удалось получить ответ от AI. Ответ пустой или некорректный."

    except Exception as e:
        logger.error(f"Ошибка Gemini API: {str(e)}")

        error_msg = str(e).lower()

        if "quota" in error_msg or "billing" in error_msg:
            return "❌ Превышена квота API или проблема с биллингом. Проверьте настройки Google AI Studio."
        elif "safety" in error_msg or "blocked" in error_msg:
            return "❌ Запрос был заблокирован системой безопасности. Попробуйте переформулировать вопрос."
        elif "api key" in error_msg:
            return "❌ Проблема с API ключом. Проверьте корректность ключа Gemini."
        elif "network" in error_msg or "connection" in error_msg:
            return "❌ Проблема с сетью. Проверьте интернет-соединение."
        elif "timeout" in error_msg:
            return "❌ Время ожидания ответа истекло. Попробуйте еще раз."
        else:
            return f"❌ Произошла ошибка при обращении к AI: {str(e)}\n\nПопробуйте переформулировать вопрос или повторить позже."

def create_keyboard(main_menu=False):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)

    if main_menu:
        buttons = ["🤖 Задать вопрос AI", "🧹 Очистить историю", "❓ Помощь"]
        markup.add(*buttons)

    return markup

def create_feedback_keyboard():
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton("👍 Понравился", callback_data="feedback_like"),
        telebot.types.InlineKeyboardButton("👎 Не понравился", callback_data="feedback_dislike")
    )
    return markup

@bot.message_handler(commands=['start'])
def handle_start(message):
    chat_type = 'private' if message.chat.type == 'private' else 'group'
    user_id = message.from_user.id

    get_or_create_user(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        private_chat_id=message.chat.id if chat_type == 'private' else None
    )

    welcome_text = (
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        "Я - AI помощник на базе Google Gemini! 🤖\n"
        "Задай мне ЛЮБОЙ вопрос, и я постараюсь на него ответить!\n\n"
        "Я помню контекст нашего разговора и могу учиться на твоих оценках.\n\n"
        "Просто напиши свой вопрос или выбери действие из меню:"
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=create_keyboard(main_menu=True), parse_mode='Markdown')

@bot.message_handler(commands=['ai', 'help', 'clear'])
def handle_ai_commands(message):
    chat_type = 'private' if message.chat.type == 'private' else 'group'
    user_id = message.from_user.id

    get_or_create_user(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        private_chat_id=message.chat.id if chat_type == 'private' else None
    )

    if message.text.startswith('/ai'):
        ai_command(message)
    elif message.text.startswith('/help'):
        help_command(message)
    elif message.text.startswith('/clear'):
        clear_history_command(message)

def ai_command(message):
    bot.send_message(
        message.chat.id,
        "🤖 *Режим AI Помощника*\n\n"
        "Задайте ЛЮБОЙ вопрос, и я постараюсь на него ответить! 🚀\n"
        "Я помню контекст нашего разговора.\n\n"
        "Примеры вопросов:\n"
        "• Объясни квантовую физику простыми словами\n"
        "• Расскажи о истории Древнего Рима\n"
        "• Помоги написать код на Python\n\n"
        "Жду ваш вопрос...",
        parse_mode='Markdown'
    )

def help_command(message):
    help_text = (
        "📖 *Помощь по AI помощнику*\n\n"
        
        "🤖 *Как использовать:*\n"
        "• Просто напиши любой вопрос в чат\n"
        "• Я отвечу на него используя Google Gemini AI\n"
        "• Я помню контекст последних сообщений\n\n"
        
        "💡 *Особенности:*\n"
        "• Контекстная память (помню до 20 последних сообщений)\n"
        "• Сохранение понравившихся ответов\n"
        "• Обучение на основе ваших оценок\n"
        "• Поддержка различных тем и вопросов\n\n"
        
        "⚡ *Команды:*\n"
        "• /start - перезапустить бота\n"
        "• /ai - активировать режим AI\n"
        "• /clear - очистить историю разговора\n"
        "• /help - показать эту справку\n\n"
        
        "🎯 *Советы:*\n"
        "• Будь конкретен в вопросах\n"
        "• Используй 👍/👎 для оценки ответов\n"
        "• Очищай историю если хочешь начать новый диалог\n\n"
        
        "*Задавай любой вопрос - я готов помочь!* 🚀"
    )
    bot.send_message(message.chat.id, help_text, parse_mode='Markdown')

def clear_history_command(message):
    user_id = message.from_user.id
    clear_chat_session(user_id)
    bot.send_message(
        message.chat.id,
        "🧹 История разговора очищена! Начинаем новый диалог!",
        reply_markup=create_keyboard(main_menu=True)
    )

@bot.message_handler(func=lambda message: message.text in [
    "🤖 Задать вопрос AI", "🧹 Очистить историю", "❓ Помощь"
])
def handle_menu_buttons(message):
    chat_type = 'private' if message.chat.type == 'private' else 'group'
    user_id = message.from_user.id

    get_or_create_user(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        private_chat_id=message.chat.id if chat_type == 'private' else None
    )

    if message.text == "🤖 Задать вопрос AI":
        ai_command(message)
    elif message.text == "🧹 Очистить историю":
        clear_history_command(message)
    elif message.text == "❓ Помощь":
        help_command(message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('feedback_'))
def handle_feedback(call):
    user_id = call.from_user.id
    message_id = call.message.message_id
    
    if call.data == 'feedback_like':
        if user_id in pending_ai_responses and message_id in pending_ai_responses[user_id]:
            question, response = pending_ai_responses[user_id][message_id]
            save_ai_response(user_id, question, response, liked=True)
            
            del pending_ai_responses[user_id][message_id]
            
            bot.answer_callback_query(call.id, "✅ Ответ сохранен! Буду использовать его в будущем.")
            bot.edit_message_text(
                f"🤖 *AI Ответ:*\n\n{response}\n\n✅ *Ответ сохранен в базу данных*",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown'
            )
        else:
            bot.answer_callback_query(call.id, "⚠️ Информация об ответе не найдена")
    
    elif call.data == 'feedback_dislike':
        if user_id in pending_ai_responses and message_id in pending_ai_responses[user_id]:
            question, old_response = pending_ai_responses[user_id][message_id]
            
            del pending_ai_responses[user_id][message_id]
            
            bot.answer_callback_query(call.id, "🔄 Генерирую новый ответ...")
            
            new_response = query_gemini(user_id, question)
            
            sent_message = bot.send_message(
                call.message.chat.id,
                f"🤖 *AI Ответ (обновленный):*\n\n{new_response}",
                parse_mode='Markdown',
                reply_markup=create_feedback_keyboard()
            )
            
            if user_id not in pending_ai_responses:
                pending_ai_responses[user_id] = {}
            pending_ai_responses[user_id][sent_message.message_id] = (question, new_response)
            
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    if message.text and message.text.startswith('/'):
        return

    chat_type = 'private' if message.chat.type == 'private' else 'group'
    user_id = message.from_user.id
    chat_id = message.chat.id

    get_or_create_user(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        private_chat_id=chat_id if chat_type == 'private' else None
    )

    bot.send_chat_action(chat_id, 'typing')
    ai_response = query_gemini(user_id, message.text)
    
    sent_message = bot.send_message(
        chat_id, 
        f"🤖 *AI Ответ:*\n\n{ai_response}", 
        parse_mode='Markdown',
        reply_markup=create_feedback_keyboard()
    )
    
    if user_id not in pending_ai_responses:
        pending_ai_responses[user_id] = {}
    pending_ai_responses[user_id][sent_message.message_id] = (message.text, ai_response)

def check_gemini_availability():
    try:
        if model:
            response = model.generate_content("Привет! Ответь 'OK' если ты работаешь.")
            return response.text is not None
        return False
    except:
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("🤖 AI Бот запускается...")

    gemini_available = check_gemini_availability()

    if gemini_available:
        print("✅ Gemini AI активен и готов к работе!")
    else:
        print("❌ Gemini AI недоступен. Проверьте API ключ и настройки.")

    print("🤖 AI бот готов к работе! Используйте /start")
    print("=" * 50)

    try:
        bot.polling(none_stop=True, interval=0, timeout=60)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        print(f"❌ Произошла ошибка: {e}")
