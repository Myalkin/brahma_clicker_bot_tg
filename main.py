import threading
import time
import telebot
from telebot import types
import psycopg2
from psycopg2 import pool
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
DB_NAME = os.getenv('DB_NAME')
DB_HOST = os.getenv('DB_HOST')
DB_USER = os.getenv('DB_USER')
DB_PASS = os.getenv('DB_PASS')
DB_PORT = os.getenv('DB_PORT')

bot = telebot.TeleBot(BOT_TOKEN, threaded= True, num_threads=32)
user_locks = {}
user_cache = {}
cache_lock = threading.Lock()
dirty_users = set()
dirty_lock = threading.Lock()
click_buffer = {}
buffer_lock = threading.Lock()

try:
    connection_pool = pool.SimpleConnectionPool(
        1, 10,
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )
    print("Пул соединений создан")
except Exception as e:
    print(f"Ошибка создания пула: {e}")
    connection_pool = None

def get_db_conn():
    if connection_pool:
        try:
            return connection_pool.getconn()
        except:
            return psycopg2.connect(
                host=DB_HOST, port=DB_PORT, user=DB_USER, 
                password=DB_PASS, database=DB_NAME
            )
    else:
        return psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, 
            password=DB_PASS, database=DB_NAME
        )

def return_db_conn(conn):
    if connection_pool and conn:
        try:
            connection_pool.putconn(conn)
        except:
            conn.close()
    elif conn:
        conn.close()

def init_database():
    conn = None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id BIGINT PRIMARY KEY, 
                username VARCHAR(255), 
                clicks BIGINT DEFAULT 0, 
                rank VARCHAR(50) DEFAULT 'Ноги Брахмы'
            )
        """)
        conn.commit()
        cur.close()
        print("База данных инициализирована")
    except Exception as e:
        print(f"Ошибка инициализации БД: {e}")
    finally:
        if conn:
            return_db_conn(conn)

def get_or_create_user(user_id, username):
    with cache_lock:
        if user_id in user_cache:
            return user_cache[user_id]
    
    conn = None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute('SELECT id, username, clicks, rank FROM users WHERE id = %s', (user_id,))
        user = cur.fetchone()
        
        if not user:
            cur.execute("""
                INSERT INTO users(id, username, clicks, rank) 
                VALUES (%s, %s, %s, %s) 
                RETURNING id, username, clicks, rank
            """, (user_id, username, 0, 'Ноги Брахмы'))
            conn.commit()
            user = cur.fetchone()
        
        cur.close()
        
        with cache_lock:
            user_cache[user_id] = user
        
        return user
    except Exception as e:
        print(f"Ошибка get_or_create_user: {e}")
        return None
    finally:
        if conn:
            return_db_conn(conn)

def calculate_rank(clicks):
    if clicks is None:
        clicks = 0
    ranks = [
        (0, "Залупа Брахмы"),
        (1, "Ноги Брахмы"),
        (10, "Колени Брахмы"),
        (30, "Бедра Брахмы"),
        (60, "Торс Брахмы"),
        (100, "Грудь Брахмы"),
        (200, "Плечи Брахмы"),
        (400, "Шея Брахмы"),
        (600, "Лицо Брахмы"),
        (1000, "Голова Брахмы")
    ]
    for threshold, rank_name in reversed(ranks):
        if clicks >= threshold:
            return rank_name
    return "Залупа Брахмы"

def leaderboard(limit = 10):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("select * from users order by clicks desc limit %s", (limit,))
    lb = cur.fetchall()
    cur.close()
    return_db_conn(conn)
    return lb


def get_user_stat(user_id):
    with cache_lock:
        if user_id in user_cache:
            return user_cache[user_id]
    
    conn = None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, username, clicks, rank FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        cur.close()
        
        if user:
            with cache_lock:
                user_cache[user_id] = user
        
        return user
    except Exception as e:
        print(f"Ошибка get_user_stat: {e}")
        return None
    finally:
        if conn:
            return_db_conn(conn)

def batch_click_save():
    while True:

        time.sleep(2)

        with dirty_lock:
            if not dirty_users:
                continue

            users_to_save = list(dirty_users)
            dirty_users.clear()

        data = []

        with cache_lock:
            for user_id in users_to_save:
                if user_id in user_cache:
                    u = user_cache[user_id]
                    data.append((u[2], u[3], u[0]))

        if not data:
            continue

        conn = None

        try:
            conn = get_db_conn()
            cur = conn.cursor()

            cur.executemany("""
                UPDATE users
                SET clicks = %s, rank = %s
                WHERE id = %s
            """, data)

            conn.commit()
            cur.close()

            print(f"Сохранено {len(data)} пользователей")

        except Exception as e:
            print("Ошибка batch save:", e)

        finally:
            if conn:
                return_db_conn(conn)

threading.Thread(target=batch_click_save, daemon=True).start()
def get_user_lock(user_id):
    lock = user_locks.get(user_id)

    if not lock:
        lock = threading.Lock()
        user_locks[user_id] = lock

    return lock
@bot.message_handler(commands=['start'])
def start_command(message):
    get_or_create_user(message.from_user.id, message.from_user.username)
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton('Начать кликать'))
    markup.add(types.KeyboardButton('Моя статистика'))
    markup.add(types.KeyboardButton('Лидерборд'))
    
    bot.send_message(
        message.chat.id,
        f'Привет {message.from_user.first_name}!\nЭто кликер для становления из ног Брахмы до головы Брахмы',
        reply_markup=markup
    )

@bot.message_handler(func=lambda message: message.text == 'Начать кликать')
def start_game_handler(message):
    get_or_create_user(message.from_user.id, message.from_user.username)
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton('КЛИК', callback_data='click'))
    
    bot.send_message(
        message.chat.id,
        'Кликай по кнопке чтобы зарабатывать очки',
        reply_markup=markup
    )

@bot.message_handler(func=lambda message: message.text == 'Моя статистика')
def show_stat_handler(message):
    user = get_user_stat(message.from_user.id)
    
    if user:
        user_id, username, clicks, rank = user
        stats = f"Кликов: {clicks}, Ранг: {rank}"
        bot.send_message(message.chat.id, stats)
    else:
        bot.send_message(message.chat.id, 'Статистика не найдена. Начни игру!')

@bot.message_handler(func=lambda message: message.text == 'Лидерборд')
def show_leaderboard(message):
    lb = leaderboard()
    if not lb:
        bot.send_message(message.chat.id, 'Лидерборд пуст')
        return
    text = 'Лидерборд топ 10\n\n'
    for i, user in enumerate(lb, start=1):
        user_id, username, clicks, rank = user
        if username:
            name = f"@{username}"
        else:
            name = f"id: {user_id}"
        text += f"{i}. {name}\n"
        text += f"кликов: {clicks}\n"
        text += f"ранг: {rank}\n"
    bot.send_message(message.chat.id, text)       


@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):

    if call.data != 'click':
        return

    user_id = call.from_user.id
    username = call.from_user.username

    lock = get_user_lock(user_id)

    with lock:

        if user_id not in user_cache:

            user = get_or_create_user(user_id, username)

            if not user:
                return

            user_cache[user_id] = user

        old_data = user_cache[user_id]

        new_clicks = old_data[2] + 1
        new_rank = calculate_rank(new_clicks)

        user_cache[user_id] = (user_id, username, new_clicks, new_rank)
        with dirty_lock:
            dirty_users.add(user_id)

    try:
        bot.answer_callback_query(call.id, f"+1 клики: {new_clicks}, ранг: {new_rank}")
    except:
        pass

if __name__ == '__main__':
    init_database()
    print("Бот запущен...")
    print("Нажми Ctrl+C для остановки")
    
    try:
        bot.infinity_polling(timeout=20, long_polling_timeout=20)
    except Exception as e:
        print(f"Ошибка: {e}")
        time.sleep(5)