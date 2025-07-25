import sqlite3
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.error import TelegramError
from datetime import datetime
import asyncio
from aiohttp import web

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Set in Koyeb environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Set in Koyeb environment variables
CHANNEL_ID = os.getenv("CHANNEL_ID", "@Powerbank_Earning_Websites")  # Replace with your channel username
ADMIN_IDS = [6972264549]  # Replace with your Telegram ID
PORT = int(os.getenv("PORT", 8443))  # Default port for Koyeb

# Database setup
def init_db():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    
    # Create users table with upi_id
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            joined_channel INTEGER DEFAULT 0,
            balance INTEGER DEFAULT 0,
            referrer_id INTEGER,
            upi_id TEXT
        )
    ''')
    # Add upi_id column if it doesn't exist
    try:
        c.execute('ALTER TABLE users ADD COLUMN upi_id TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists
    # Create tasks table
    c.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            payment_price INTEGER NOT NULL,
            question TEXT NOT NULL
        )
    ''')
    # Create user tasks completion table
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_tasks (
            user_id INTEGER,
            task_id INTEGER,
            completed INTEGER DEFAULT 0,
            pending INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, task_id)
        )
    ''')
    # Create task responses table
    c.execute('''
        CREATE TABLE IF NOT EXISTS task_responses (
            user_id INTEGER,
            task_id INTEGER,
            response TEXT,
            PRIMARY KEY (user_id, task_id)
        )
    ''')
    # Create announcements table
    c.execute('''
        CREATE TABLE IF NOT EXISTS announcements (
            announcement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Create withdrawals table
    c.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            withdrawal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER NOT NULL,
            upi_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# Check if user is subscribed to the channel
async def is_user_subscribed(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        chat_member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except TelegramError:
        return False

# Save user to database
def save_user(user_id: int, username: str, referrer_id: int = None):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    existing_user = c.fetchone()
    if existing_user:
        c.execute('UPDATE users SET username = ? WHERE user_id = ?', (username, user_id))
    else:
        c.execute('''
            INSERT INTO users (user_id, username, referrer_id)
            VALUES (?, ?, ?)
        ''', (user_id, username, referrer_id))
    conn.commit()
    conn.close()

# Get user data
def get_user(user_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id, username, joined_channel, balance, referrer_id, upi_id FROM users WHERE user_id = ?', (user_id,))
    user = c.fetchone()
    conn.close()
    return user

# Update user channel join status
def update_channel_status(user_id: int, joined: bool):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        UPDATE users SET joined_channel = ? WHERE user_id = ?
    ''', (1 if joined else 0, user_id))
    conn.commit()
    conn.close()

# Set or update UPI ID
def set_upi_id(user_id: int, upi_id: str):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('UPDATE users SET upi_id = ? WHERE user_id = ?', (upi_id, user_id))
    conn.commit()
    conn.close()

# Add bonus to user
def add_bonus(user_id: int, amount: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        UPDATE users SET balance = balance + ? WHERE user_id = ?
    ''', (amount, user_id))
    conn.commit()
    conn.close()

# Deduct balance from user
def deduct_balance(user_id: int, amount: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?
    ''', (amount, user_id, amount))
    rows_affected = c.rowcount
    conn.commit()
    conn.close()
    return rows_affected > 0

# Remove balance (admin action)
def remove_balance(user_id: int, amount: int):
    return deduct_balance(user_id, amount)

# Get referrals
def get_referrals(user_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id, username FROM users WHERE referrer_id = ?', (user_id,))
    referrals = c.fetchall()
    conn.close()
    return referrals

# Add task
def add_task(title: str, description: str, payment_price: int, question: str):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('INSERT INTO tasks (title, description, payment_price, question) VALUES (?, ?, ?, ?)', 
              (title, description, payment_price, question))
    conn.commit()
    conn.close()

# Remove task
def remove_task(task_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('DELETE FROM tasks WHERE task_id = ?', (task_id,))
    c.execute('DELETE FROM user_tasks WHERE task_id = ?', (task_id,))
    c.execute('DELETE FROM task_responses WHERE task_id = ?', (task_id,))
    conn.commit()
    conn.close()

# Get tasks
def get_tasks():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT task_id, title, description, payment_price, question FROM tasks')
    tasks = c.fetchall()
    conn.close()
    return tasks

# Mark task as pending
def mark_task_pending(user_id: int, task_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO user_tasks (user_id, task_id, pending)
        VALUES (?, ?, 1)
    ''', (user_id, task_id))
    conn.commit()
    conn.close()

# Mark task as completed
def mark_task_completed(user_id: int, task_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        UPDATE user_tasks SET completed = 1, pending = 0
        WHERE user_id = ? AND task_id = ?
    ''', (user_id, task_id))
    conn.commit()
    conn.close()

# Decline task
def decline_task(user_id: int, task_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('DELETE FROM user_tasks WHERE user_id = ? AND task_id = ?', (user_id, task_id))
    c.execute('DELETE FROM task_responses WHERE user_id = ? AND task_id = ?', (user_id, task_id))
    conn.commit()
    conn.close()

# Save task response
def save_task_response(user_id: int, task_id: int, response: str):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO task_responses (user_id, task_id, response)
        VALUES (?, ?, ?)
    ''', (user_id, task_id, response))
    conn.commit()
    conn.close()

# Get pending tasks for user
def get_pending_tasks(user_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        SELECT t.task_id, t.title, t.description, t.payment_price
        FROM tasks t
        JOIN user_tasks ut ON t.task_id = ut.task_id
        WHERE ut.user_id = ? AND ut.pending = 1
    ''', (user_id,))
    tasks = c.fetchall()
    conn.close()
    return tasks

# Get completed tasks for user
def get_completed_tasks(user_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        SELECT t.task_id, t.title, t.description, t.payment_price
        FROM tasks t
        JOIN user_tasks ut ON t.task_id = ut.task_id
        WHERE ut.user_id = ? AND ut.completed = 1
    ''', (user_id,))
    tasks = c.fetchall()
    conn.close()
    return tasks

# Add announcement
def add_announcement(message: str):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('INSERT INTO announcements (message) VALUES (?)', (message,))
    conn.commit()
    conn.close()

# Delete announcement
def delete_announcement(announcement_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('DELETE FROM announcements WHERE announcement_id = ?', (announcement_id,))
    conn.commit()
    conn.close()

# Get announcements
def get_announcements():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT announcement_id, message, timestamp FROM announcements ORDER BY TIMESTAMP DESC')
    announcements = c.fetchall()
    conn.close()
    return announcements

# Get all user IDs
def get_all_users():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM users')
    users = c.fetchall()
    conn.close()
    return [user[0] for user in users]

# Get total user count
def get_user_count():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    count = c.fetchone()[0]
    conn.close()
    return count

# Add withdrawal request
def add_withdrawal(user_id: int, amount: int, upi_id: str):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO withdrawals (user_id, amount, upi_id, status)
        VALUES (?, ?, ?, 'pending')
    ''', (user_id, amount, upi_id))
    conn.commit()
    conn.close()

# Approve withdrawal
def approve_withdrawal(withdrawal_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        UPDATE withdrawals SET status = 'approved'
        WHERE withdrawal_id = ?
    ''', (withdrawal_id,))
    conn.commit()
    conn.close()

# Decline withdrawal
def decline_withdrawal(withdrawal_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        UPDATE withdrawals SET status = 'declined'
        WHERE withdrawal_id = ?
    ''', (withdrawal_id,))
    conn.commit()
    conn.close()

# Get withdrawal history
def get_withdrawal_history(user_id: int):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        SELECT withdrawal_id, amount, upi_id, status, timestamp
        FROM withdrawals WHERE user_id = ? ORDER BY timestamp DESC
    ''', (user_id,))
    history = c.fetchall()
    conn.close()
    return history

# Get pending withdrawals
def get_pending_withdrawals():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        SELECT w.withdrawal_id, w.user_id, w.amount, w.upi_id, w.timestamp, u.username
        FROM withdrawals w
        JOIN users u ON w.user_id = u.user_id
        WHERE w.status = 'pending' ORDER BY w.timestamp DESC
    ''')
    withdrawals = c.fetchall()
    conn.close()
    return withdrawals

# Main menu keyboard
def main_menu():
    keyboard = [
        [
            InlineKeyboardButton("📣 Invite Friends", callback_data='refer'),
            InlineKeyboardButton("📋 Start Tasks", callback_data='tasks'),
        ],
        [
            InlineKeyboardButton("📊 Your Progress", callback_data='insights'),
            InlineKeyboardButton("👤 My Account", callback_data='account'),
        ],
        [
            InlineKeyboardButton("⏳ Task Status", callback_data='pending_completed'),
            InlineKeyboardButton("📢 Updates", callback_data='announcements'),
        ],
        [
            InlineKeyboardButton("💸 Cash Out", callback_data='withdraw'),
            InlineKeyboardButton("ℹ️ About Us", callback_data='about'),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

# Admin menu keyboard
def admin_menu():
    keyboard = [
        [
            InlineKeyboardButton("👥 Manage Users", callback_data='admin_users'),
            InlineKeyboardButton("➕ Create Task", callback_data='admin_add_task'),
        ],
        [
            InlineKeyboardButton("➖ Delete Task", callback_data='admin_remove_task'),
            InlineKeyboardButton("📢 Post Update", callback_data='admin_announcement'),
        ],
        [
            InlineKeyboardButton("💸 Adjust Balance", callback_data='admin_remove_balance'),
            InlineKeyboardButton("🗑️ Clear Update", callback_data='admin_delete_announcement'),
        ],
        [
            InlineKeyboardButton("📤 Withdrawal Requests", callback_data='admin_withdraw_requests'),
            InlineKeyboardButton("📋 Task Approvals", callback_data='admin_task_requests'),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

# Task selection keyboard
def task_selection_menu():
    tasks = get_tasks()
    keyboard = [[InlineKeyboardButton(f"🔹 {title} ({price} points)", callback_data=f'task_{task_id}')] for task_id, title, _, price, _ in tasks]
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')])
    return InlineKeyboardMarkup(keyboard) if tasks else None

# Task detail keyboard with submit button
def task_complete_button(task_id: int):
    keyboard = [
        [InlineKeyboardButton("✅ Start Task", callback_data=f'complete_{task_id}')],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]
    ]
    return InlineKeyboardMarkup(keyboard)

# Approve/Decline task buttons
def task_action_buttons(user_id: int, task_id: int):
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f'approve_task_{user_id}_{task_id}'),
            InlineKeyboardButton("❌ Decline", callback_data=f'decline_task_{user_id}_{task_id}'),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# Withdraw menu keyboard
def withdraw_menu(upi_id: str = None):
    keyboard = [
        [InlineKeyboardButton("💰 Request Cash Out", callback_data='request_withdrawal')],
        [InlineKeyboardButton("📜 View History", callback_data='withdrawal_history')],
        [InlineKeyboardButton(f"{'🔄 Update' if upi_id else '💳 Set'} UPI ID", callback_data='set_upi_id')],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]
    ]
    return InlineKeyboardMarkup(keyboard)

# Withdrawal confirmation keyboard
def withdrawal_confirmation_buttons(withdrawal_id: int):
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data=f'confirm_withdrawal_{withdrawal_id}'),
            InlineKeyboardButton("❌ Cancel", callback_data='cancel_withdrawal')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# Approve/Decline withdrawal buttons
def withdrawal_action_buttons(withdrawal_id: int):
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f'approve_withdrawal_{withdrawal_id}'),
            InlineKeyboardButton("❌ Decline", callback_data=f'decline_withdrawal_{withdrawal_id}')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# Start command handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    referrer_id = int(args[0]) if args else None

    # Save user to database
    save_user(user.id, user.username, referrer_id)

    # Notify referrer if exists and user is new
    if referrer_id:
        referrer = get_user(referrer_id)
        user_exists = get_user(user.id)
        if referrer and user_exists[4] == referrer_id:
            await context.bot.send_message(
                referrer_id,
                f"🎉 Awesome! Your friend @{user.username} just joined via your referral link! 🚀"
            )

    # Check if user is admin
    if user.id in ADMIN_IDS:
        await update.message.reply_text(
            "🎉 Welcome back, Admin! Take control with the admin panel below: ⚙️",
            reply_markup=admin_menu()
        )
        return

    # Check channel subscription for non-admins
    if await is_user_subscribed(context, user.id):
        update_channel_status(user.id, True)
        user_data = get_user(user.id)
        if user_data[4]:
            add_bonus(user_data[4], 0)
            await context.bot.send_message(
                user_data[4],
                f"🎊 Great news! Your referral @{user.username} joined {CHANNEL_ID}! Now you will recieve 50% of his earnings ! 💰 Keep inviting! 🚀"
            )
        welcome_message = (
            f"🎉 Hey @{user.username}, welcome to the party! 🎈\n"
            f"Join {CHANNEL_ID} and start earning rewards with exciting tasks, referrals, and more! 💸\n"
            f"Let's dive in—choose an option below! 👇"
        )
        await update.message.reply_text(welcome_message, reply_markup=main_menu())
    else:
        keyboard = [[InlineKeyboardButton("📢 Join Channel Now", url=f"https://t.me/{CHANNEL_ID[1:]}")]]
        await update.message.reply_text(
            f"🚀 Unlock amazing rewards by joining {CHANNEL_ID}! Click below to get started! 🎉",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.job_queue.run_once(check_subscription, 30, data={'user_id': user.id})

# Check subscription job
async def check_subscription(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data['user_id']
    if await is_user_subscribed(context, user_id):
        user = get_user(user_id)
        update_channel_status(user_id, True)
        if user[4]:
            add_bonus(user[4], 0)
            await context.bot.send_message(
                user[4],
                f"🎊 Your referral @{user[1]} just joined {CHANNEL_ID}! Now you will receive 50% of his earnings ! 💰 Keep spreading the word! 🚀"
            )
        welcome_message = (
            f"🎉 Welcome aboard, @{user[1]}! 🎈\n"
            f"You're now part of {CHANNEL_ID}! Start earning rewards with fun tasks and referrals! 💸\n"
            f"Pick an option below to begin! 👇"
        )
        await context.bot.send_message(user_id, welcome_message, reply_markup=main_menu())
    else:
        await context.bot.send_message(
            user_id,
            f"🚀 Join {CHANNEL_ID} to unlock exciting rewards! Click below to join now! 🎉",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel Now", url=f"https://t.me/{CHANNEL_ID[1:]}")]])
        )

# Callback query handler
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # Admins bypass all restrictions
    if user_id in ADMIN_IDS:
        if query.data == 'admin_users':
            conn = sqlite3.connect('bot.db')
            c = conn.cursor()
            c.execute('SELECT user_id, username, balance FROM users')
            users = c.fetchall()
            user_count = get_user_count()
            conn.close()
            message = f"👥 User Dashboard (Total: {user_count}):\n"
            for uid, username, balance in users:
                message += f"ID: {uid}, @{username}, Balance: {balance} points 💰\n"
            message += "\n💡 Update balance: /setbalance <user_id> <amount>\n💡 Deduct balance: /removebalance <user_id> <amount>"
            await query.message.edit_text(message, reply_markup=admin_menu())

        elif query.data == 'admin_add_task':
            await query.message.edit_text(
                "➕ Ready to add a new task? Send: /add_task <title> | <description> | <payment_price> | <question>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
            )

        elif query.data == 'admin_remove_task':
            tasks = get_tasks()
            if not tasks:
                await query.message.edit_text(
                    "🚫 No tasks available to remove.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )
                return
            message = "📋 Available Tasks:\n"
            for task_id, title, desc, price, question in tasks:
                message += f"Task {task_id}: {title} ({price} points) 💸\n{desc}\n\n"
            message += "💡 Send: /remove_task <task_id> to delete a task."
            await query.message.edit_text(
                message,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
            )

        elif query.data == 'admin_announcement':
            await query.message.edit_text(
                "📢 Want to share an update? Send: /announcement <message>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
            )

        elif query.data == 'admin_remove_balance':
            await query.message.edit_text(
                "💸 Adjust a user's balance: /removebalance <user_id> <amount>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
            )

        elif query.data == 'admin_delete_announcement':
            announcements = get_announcements()
            if not announcements:
                await query.message.edit_text(
                    "🚫 No announcements to delete.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )
                return
            message = "📢 Current Announcements:\n"
            for ann_id, msg, timestamp in announcements:
                message += f"ID: {ann_id}\n{msg}\n📅 Posted: {timestamp}\n\n"
            message += "💡 Send: /deleteannouncement <announcement_id> to remove."
            await query.message.edit_text(
                message,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
            )

        elif query.data == 'admin_withdraw_requests':
            withdrawals = get_pending_withdrawals()
            if not withdrawals:
                await query.message.edit_text(
                    "🚫 No pending withdrawal requests.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )
                return
            message = "💸 Pending Withdrawal Requests:\n"
            for wid, uid, amount, upi_id, timestamp, username in withdrawals:
                message += f"ID: {wid}, User: @{username} (ID: {uid})\n💰 Amount: {amount} Rs\n💳 UPI ID: {upi_id}\n📅 Posted: {timestamp}\n\n"
                keyboard = withdrawal_action_buttons(wid)
                await context.bot.send_message(
                    user_id,
                    f"💸 New Withdrawal Request:\nUser: @{username} (ID: {uid})\nAmount: {amount} Rs\nUPI ID: {upi_id}\n📅 Posted: {timestamp}\nTake action below! 👇",
                    reply_markup=keyboard
                )
            await query.message.edit_text(
                message,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
            )

        elif query.data == 'admin_task_requests':
            tasks = get_tasks()
            pending_tasks = []
            for task in tasks:
                task_id = task[0]
                c = sqlite3.connect('bot.db').cursor()
                c.execute('''
                    SELECT ut.user_id, tr.response, u.username
                    FROM user_tasks ut
                    JOIN task_responses tr ON ut.user_id = tr.user_id AND ut.task_id = tr.task_id
                    JOIN users u ON ut.user_id = u.user_id
                    WHERE ut.task_id = ? AND ut.pending = 1
                ''', (task_id,))
                pending_tasks.extend([(task_id, task[1], task[3], task[4], user_id, response, username) 
                                     for user_id, response, username in c.fetchall()])
                c.close()
            if not pending_tasks:
                await query.message.edit_text(
                    "🚫 No pending task submissions to review.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )
                return
            message = "📋 Pending Task Submissions:\n"
            for task_id, title, price, question, task_user_id, response, username in pending_tasks:
                message += f"Task {task_id}: {title} ({price} points) 💸\nUser: @{username} (ID: {task_user_id})\nResponse: {response}\n\n"
                keyboard = task_action_buttons(task_user_id, task_id)
                await context.bot.send_message(
                    query.from_user.id,
                    f"📋 New Task Submission:\nUser: @{username} (ID: {task_user_id})\nTask {task_id}: {title} ({price} points)\nResponse: {response}\nTake action below! 👇",
                    reply_markup=keyboard
                )
            await query.message.edit_text(
                message,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
            )

        elif query.data.startswith('approve_withdrawal_'):
            try:
                withdrawal_id = int(query.data.replace('approve_withdrawal_', ''))
                conn = sqlite3.connect('bot.db')
                c = conn.cursor()
                c.execute('''
                    SELECT w.user_id, w.amount, w.upi_id, u.username
                    FROM withdrawals w
                    JOIN users u ON w.user_id = u.user_id
                    WHERE w.withdrawal_id = ? AND w.status = 'pending'
                ''', (withdrawal_id,))
                withdrawal = c.fetchone()
                conn.close()
                if not withdrawal:
                    await query.message.edit_text(
                        "🚫 No pending withdrawal request found for this ID.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                    )
                    return
                user_id, amount, upi_id, username = withdrawal
                approve_withdrawal(withdrawal_id)
                await context.bot.send_message(
                    user_id,
                    f"🎉 Your withdrawal request (ID: {withdrawal_id}) of {amount} Rs to {upi_id} has been approved! 🎊 Funds are on their way! 🚀"
                )
                await query.message.edit_text(
                    f"✅ Withdrawal ID {withdrawal_id} of {amount} Rs approved for @{username} (UPI: {upi_id}).",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )
            except (ValueError, sqlite3.Error) as e:
                logger.error(f"Error approving withdrawal: {e}")
                await query.message.edit_text(
                    "❌ Error processing withdrawal. Please try again or contact support.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )

        elif query.data.startswith('decline_withdrawal_'):
            try:
                withdrawal_id = int(query.data.replace('decline_withdrawal_', ''))
                conn = sqlite3.connect('bot.db')
                c = conn.cursor()
                c.execute('''
                    SELECT w.user_id, w.amount, w.upi_id, u.username
                    FROM withdrawals w
                    JOIN users u ON w.user_id = u.user_id
                    WHERE w.withdrawal_id = ? AND w.status = 'pending'
                ''', (withdrawal_id,))
                withdrawal = c.fetchone()
                conn.close()
                if not withdrawal:
                    await query.message.edit_text(
                        "🚫 No pending withdrawal request found for this ID.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                    )
                    return
                user_id, amount, upi_id, username = withdrawal
                decline_withdrawal(withdrawal_id)
                add_bonus(user_id, amount)
                await context.bot.send_message(
                    user_id,
                    f"⚠️ Your withdrawal request (ID: {withdrawal_id}) of {amount} Rs to {upi_id} was declined. {amount} points have been refunded to your balance. Try again or contact support! 📞"
                )
                await query.message.edit_text(
                    f"❌ Withdrawal ID {withdrawal_id} of {amount} Rs declined for @{username} (UPI: {upi_id}). Points refunded.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )
            except (ValueError, sqlite3.Error) as e:
                logger.error(f"Error declining withdrawal: {e}")
                await query.message.edit_text(
                    "❌ Error processing withdrawal. Please try again or contact support.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )

        elif query.data.startswith('approve_task_'):
            try:
                parts = query.data.replace('approve_task_', '').split('_')
                task_user_id = int(parts[0])
                task_id = int(parts[1])
                tasks = get_tasks()
                task = next((t for t in tasks if t[0] == task_id), None)
                if not task:
                    await query.message.edit_text(
                        "🚫 Task not found.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                    )
                    return
                user = get_user(task_user_id)
                mark_task_completed(task_user_id, task_id)
                task_price = task[3]
                add_bonus(task_user_id, task_price)
                task_title = task[1]
                await context.bot.send_message(
                    task_user_id,
                    f"🎉 Woohoo! Your submission for Task {task_id}: {task_title} has been approved! 🎊 +{task_price} points added to your balance! Keep rocking it! 🚀"
                )
                referrer_id = user[4]
                if referrer_id:
                    referrer_bonus = int(task_price * 0.5)  # Changed from 0.2 to 0.5 for 50% bonus
                    add_bonus(referrer_id, referrer_bonus)
                    referrer = get_user(referrer_id)
                    await context.bot.send_message(
                        referrer_id,
                        f"🎊 Your referral @{user[1]} smashed Task {task_id}: {task_title}! You earned {referrer_bonus} points (50% of task reward)! 💰 Keep inviting! 🚀"
                    )
                await query.message.edit_text(
                    f"✅ Task {task_id}: {task_title} approved for @{user[1]}. +{task_price} points awarded.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )
            except (ValueError, sqlite3.Error) as e:
                logger.error(f"Error approving task: {e}")
                await query.message.edit_text(
                    "❌ Error processing task approval. Please try again or contact support.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )

        elif query.data.startswith('decline_task_'):
            try:
                parts = query.data.replace('decline_task_', '').split('_')
                task_user_id = int(parts[0])
                task_id = int(parts[1])
                tasks = get_tasks()
                task = next((t for t in tasks if t[0] == task_id), None)
                if not task:
                    await query.message.edit_text(
                        "🚫 Task not found.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                    )
                    return
                user = get_user(task_user_id)
                task_title = task[1]
                decline_task(task_user_id, task_id)
                await context.bot.send_message(
                    task_user_id,
                    f"⚠️ Your submission for Task {task_id}: {task_title} was declined. Please review the requirements and try again! 📝 Contact support if you need help."
                )
                await query.message.edit_text(
                    f"❌ Task {task_id}: {task_title} declined for @{user[1]}.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )
            except (ValueError, sqlite3.Error) as e:
                logger.error(f"Error declining task: {e}")
                await query.message.edit_text(
                    "❌ Error processing task decline. Please try again or contact support.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin", callback_data='back_admin')]])
                )

        elif query.data == 'back_admin':
            await query.message.edit_text(
                "⚙️ Admin Panel: Manage users, tasks, and withdrawals with ease! Choose an option: 👇",
                reply_markup=admin_menu()
            )
        return

    # Non-admins require channel join
    user = get_user(user_id)
    if not user or not user[2]:
        await query.message.edit_text(
            f"🚀 Join {CHANNEL_ID} to unlock exciting rewards! Click below to join now! 🎉",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel Now", url=f"https://t.me/{CHANNEL_ID[1:]}")]])
        )
        return

    if query.data == 'refer':
        referral_link = f"https://t.me/{context.bot.username}?start={user_id}"
        await query.message.edit_text(
            f"🎉 Invite your friends and earn big! Share this link and earn 50% of his earnings:\n{referral_link}\n"
            f"💰 Get lifetime rewards per friend who joins {CHANNEL_ID} and 50% of their task rewards! 🚀 Start sharing now!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
        )

    elif query.data == 'tasks':
        keyboard = task_selection_menu()
        if not keyboard:
            await query.message.edit_text(
                "🚫 No tasks available right now. Check back soon for exciting opportunities! 🎉",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
            )
            return
        await query.message.edit_text("📋 Choose a task to start earning rewards! 💸", reply_markup=keyboard)

    elif query.data.startswith('task_'):
        task_id = int(query.data.split('_')[1])
        tasks = get_tasks()
        task = next((t for t in tasks if t[0] == task_id), None)
        if not task:
            await query.message.edit_text(
                "🚫 Task not found. Try another one! 📝",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
            )
            return
        task_id, title, desc, price, _ = task
        message = (
            f"📋 Task {task_id}: {title}\n"
            f"📝 Description: {desc}\n"
            f"💰 Reward: {price} points\n"
            f"Ready to start? Click below! 👇"
        )
        await query.message.edit_text(message, reply_markup=task_complete_button(task_id))

    elif query.data.startswith('complete_'):
        task_id = int(query.data.split('_')[1])
        tasks = get_tasks()
        task = next((t for t in tasks if t[0] == task_id), None)
        if not task:
            await query.message.edit_text(
                "🚫 Task not found. Try another one! 📝",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
            )
            return
        context.user_data['awaiting_response'] = task_id
        await query.message.edit_text(
            f"📝 Task Question: {task[4]}\n"
            f"Please send your response as a text message to submit! 🚀",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
        )

    elif query.data == 'insights':
        referrals = get_referrals(user_id)
        completed_tasks = get_completed_tasks(user_id)
        message = "📊 Your Progress Snapshot:\n"
        message += f"👥 Total Referrals: {len(referrals)}\n"
        if referrals:
            message += "Your Referrals:\n"
            for ref_id, ref_username in referrals:
                ref_tasks = get_completed_tasks(ref_id)
                message += f"@{ref_username}: {len(ref_tasks)} tasks completed 🎉\n"
        message += f"\n✅ Completed Tasks: {len(completed_tasks)}\n"
        message += "Keep earning and inviting to climb the leaderboard! 🚀"
        await query.message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
        )

    elif query.data == 'account':
        user = get_user(user_id)
        referrals = get_referrals(user_id)
        completed_tasks = get_completed_tasks(user_id)
        message = (
            f"👤 Your Account Overview:\n"
            f"💼 Username: @{user[1]}\n"
            f"💰 Balance: {user[3]} points\n"
            f"💳 UPI ID: {user[5] if user[5] else 'Not set'}\n"
            f"👥 Referrals: {len(referrals)}\n"
            f"✅ Completed Tasks: {len(completed_tasks)}\n"
            f"Keep rocking it! 🚀 Check your options below:"
        )
        await query.message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
        )

    elif query.data == 'pending_completed':
        pending_tasks = get_pending_tasks(user_id)
        completed_tasks = get_completed_tasks(user_id)
        message = "⏳ Your Task Status:\n"
        if pending_tasks:
            message += "\n🔄 Pending Tasks:\n"
            for task_id, title, desc, price in pending_tasks:
                message += f"Task {task_id}: {title} ({price} points) 💸\n{desc}\n\n"
        else:
            message += "\n🔄 No Pending Tasks.\n"
        if completed_tasks:
            message += "\n✅ Completed Tasks:\n"
            for task_id, title, desc, price in completed_tasks:
                message += f"Task {task_id}: {title} ({price} points) 🎉\n{desc}\n\n"
        else:
            message += "\n✅ No Completed Tasks."
        message += "Ready for more? Check tasks now! 👇"
        await query.message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
        )

    elif query.data == 'announcements':
        announcements = get_announcements()
        if not announcements:
            await query.message.edit_text(
                "🚫 No updates right now. Stay tuned for exciting news! 📢",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
            )
            return
        message = "📢 Latest Updates:\n"
        for ann_id, msg, timestamp in announcements:
            message += f"ID: {ann_id}\n{msg}\n📅 Posted: {timestamp}\n\n"
        message += "Stay in the loop! Check back for more updates! 🚀"
        await query.message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
        )

    elif query.data == 'withdraw':
        user = get_user(user_id)
        message = (
            f"💸 Cash Out Your Earnings:\n"
            f"💰 Minimum withdrawal: 15 Rs\n"
            f"📈 Current balance: {user[3]} points\n"
            f"💳 UPI ID: {user[5] if user[5] else 'Not set'}\n"
            f"Ready to withdraw? Choose an option below! 👇"
        )
        await query.message.edit_text(
            message,
            reply_markup=withdraw_menu(user[5])
        )

    elif query.data == 'set_upi_id':
        context.user_data['awaiting_upi_id'] = True
        await query.message.edit_text(
            f"💳 Please provide your {'updated ' if user[5] else ''}UPI ID to cash out! 🚀",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
        )

    elif query.data == 'request_withdrawal':
        user = get_user(user_id)
        if user[3] < 15:
            await query.message.edit_text(
                "⚠️ Not enough points! You need at least 15 points to withdraw. Keep earning! 💪",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
            )
            return
        if not user[5]:
            await query.message.edit_text(
                "💳 Please set your UPI ID to proceed with withdrawals.",
                reply_markup=withdraw_menu(user[5])
            )
            return
        amount = 15
        if deduct_balance(user_id, amount):
            add_withdrawal(user_id, amount, user[5])
            conn = sqlite3.connect('bot.db')
            c = conn.cursor()
            c.execute('SELECT withdrawal_id FROM withdrawals WHERE user_id = ? AND upi_id = ? AND amount = ?', 
                     (user_id, user[5], amount))
            withdrawal_id = c.fetchone()[0]
            conn.close()
            await query.message.edit_text(
                f"💸 Confirm Your Withdrawal:\n"
                f"💰 Amount: {amount} Rs\n"
                f"💳 UPI ID: {user[5]}\n"
                f"Please confirm or cancel below! 👇",
                reply_markup=withdrawal_confirmation_buttons(withdrawal_id)
            )
        else:
            await query.message.edit_text(
                "⚠️ Insufficient balance for withdrawal. Earn more points! 💪",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
            )

    elif query.data.startswith('confirm_withdrawal_'):
        withdrawal_id = int(query.data.replace('confirm_withdrawal_', ''))
        conn = sqlite3.connect('bot.db')
        c = conn.cursor()
        c.execute('''
            SELECT w.user_id, w.amount, w.upi_id, u.username
            FROM withdrawals w
            JOIN users u ON w.user_id = u.user_id
            WHERE w.withdrawal_id = ?
        ''', (withdrawal_id,))
        withdrawal = c.fetchone()
        conn.close()
        if not withdrawal:
            await query.message.edit_text(
                "🚫 Withdrawal request not found.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
            )
            return
        user_id, amount, upi_id, username = withdrawal
        for admin_id in ADMIN_IDS:
            await context.bot.send_message(
                admin_id,
                f"💸 New Withdrawal Request:\nUser: @{username} (ID: {user_id})\nAmount: {amount} Rs\nUPI ID: {upi_id}\nTake action below! 👇",
                reply_markup=withdrawal_action_buttons(withdrawal_id)
            )
        await query.message.edit_text(
            f"🎉 Your withdrawal request for {amount} Rs to {upi_id} has been submitted! We'll notify you once it's approved! 🚀",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
        )

    elif query.data == 'cancel_withdrawal':
        user = get_user(user_id)
        add_bonus(user_id, 15)
        await query.message.edit_text(
            f"⚠️ Withdrawal cancelled. 15 points have been refunded to your balance! 💰 Try again anytime!",
            reply_markup=withdraw_menu(user[5])
        )

    elif query.data == 'withdrawal_history':
        history = get_withdrawal_history(user_id)
        if not history:
            await query.message.edit_text(
                "🚫 No withdrawal history yet. Start earning and cash out! 💸",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
            )
            return
        message = "📜 Your Withdrawal History:\n"
        for wid, amount, upi_id, status, timestamp in history:
            message += f"ID: {wid}\n💰 Amount: {amount} Rs\n💳 UPI ID: {upi_id}\n📅 Posted: {timestamp}\n📌 Status: {status.capitalize()}\n\n"
        message += "Ready to cash out more? Head to Withdraw! 👇"
        await query.message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
        )

    elif query.data == 'about':
        message = (
            f"ℹ️ About Us:\n"
            f"Welcome to our awesome bot! 🚀 Earn rewards by completing fun tasks and inviting friends to join {CHANNEL_ID}! 🎉\n"
            f"Key Features:\n"
            f"📢 Join {CHANNEL_ID} to unlock all features.\n"
            f"💰 Earn 50% of their task rewards who joined via your link!\n"
            f"📋 Complete tasks to earn points, pending admin approval.\n"
            f"💸 Withdraw earnings (min. 15 Rs) via UPI after setting your UPI ID.\n"
            f"📊 Track your progress, withdrawals, and more!\n"
            f"📢 Stay updated with the latest announcements.\n"
            f"Start exploring now! 👇"
        )
        await query.message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
        )

    elif query.data == 'back':
        user = get_user(user_id)
        await query.message.edit_text(
            f"🎉 Hey @{user[1]}, ready to earn more? Pick an option below! 👇",
            reply_markup=main_menu()
        )

# Add task command (admin only)
async def add_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        args = ' '.join(context.args).split('|')
        if len(args) != 4:
            raise ValueError
        title, description, payment_price, question = [arg.strip() for arg in args]
        payment_price = int(payment_price)
        add_task(title, description, payment_price, question)
        await update.message.reply_text(f"🎉 Task '{title}' added successfully! Users can start earning now! 🚀")
    except (ValueError, IndexError):
        await update.message.reply_text(
            "💡 Usage: /add_task <title> | <description> | <payment_price> | <question>"
        )

# Add announcement command (admin only)
async def announcement_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        message = ' '.join(context.args)
        if not message:
            raise ValueError
        add_announcement(message)
        user_ids = get_all_users()
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for user_id in user_ids:
            try:
                await context.bot.send_message(
                    user_id,
                    f"📢 Big Update!\n{message}\n📅 Posted: {current_time}\nStay tuned for more! 🚀"
                )
            except TelegramError:
                logger.warning(f"Failed to send announcement to user {user_id}")
        await update.message.reply_text("🎉 Announcement posted and sent to all users! 🚀")
    except ValueError:
        await update.message.reply_text("💡 Usage: /announcement <message>")

# Delete announcement command (admin only)
async def delete_announcement_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        announcement_id = int(context.args[0])
        delete_announcement(announcement_id)
        await update.message.reply_text(f"✅ Announcement {announcement_id} deleted successfully!")
    except (IndexError, ValueError):
        await update.message.reply_text("💡 Usage: /deleteannouncement <announcement_id>")

# Remove balance command (admin only)
async def remove_balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        if remove_balance(user_id, amount):
            await update.message.reply_text(f"✅ {amount} points deducted from user {user_id}'s balance.")
        else:
            await update.message.reply_text(f"⚠️ Failed: User {user_id} has insufficient balance.")
    except (IndexError, ValueError):
        await update.message.reply_text("💡 Usage: /removebalance <user_id> <amount>")

# Complete task response and UPI ID handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    # Admin-specific handling
    if user_id in ADMIN_IDS:
        await update.message.reply_text(
            "⚙️ Admin Panel: Use /add_task, /remove_task, /setbalance, /removebalance, /announcement, or /deleteannouncement to manage the bot! 👇",
            reply_markup=admin_menu()
        )
        return

    # Non-admins require channel join
    if not user or not user[2]:
        await update.message.reply_text(
            f"🚀 Join {CHANNEL_ID} to unlock exciting rewards! Click below to join now! 🎉",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel Now", url=f"https://t.me/{CHANNEL_ID[1:]}")]])
        )
        return

    # Handle task response
    if 'awaiting_response' in context.user_data:
        task_id = context.user_data['awaiting_response']
        tasks = get_tasks()
        task = next((t for t in tasks if t[0] == task_id), None)
        if not task:
            await update.message.reply_text(
                "🚫 Task not found. Try another one! 📝",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
            )
            del context.user_data['awaiting_response']
            return

        response = update.message.text
        save_task_response(user_id, task_id, response)
        mark_task_pending(user_id, task_id)
        task_title, task_desc, task_price, task_question = task[1], task[2], task[3], task[4]
        for admin_id in ADMIN_IDS:
            await context.bot.send_message(
                admin_id,
                f"📋 New Task Submission:\nUser: @{user[1]} (ID: {user_id})\nTask {task_id}: {task_title} ({task_price} points) 💸\nQuestion: {task_question}\nResponse: {response}\nTake action below! 👇",
                reply_markup=task_action_buttons(user_id, task_id)
            )
        await update.message.reply_text(
            f"🎉 Your submission for Task {task_id}: {task_title} has been sent for review! We'll notify you once it's approved! 🚀",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
        )
        del context.user_data['awaiting_response']
        return

    # Handle UPI ID input
    if 'awaiting_upi_id' in context.user_data:
        upi_id = update.message.text.strip()
        if not upi_id:
            await update.message.reply_text(
                "⚠️ Invalid UPI ID! Please provide a valid UPI ID to cash out. 💳",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data='back')]])
            )
            return
        set_upi_id(user_id, upi_id)
        await update.message.reply_text(
            f"🎉 UPI ID set to {upi_id}! You're ready to cash out your earnings! 💸 Choose an option below:",
            reply_markup=withdraw_menu(upi_id)
        )
        del context.user_data['awaiting_upi_id']

# Set balance command (admin only)
async def set_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        conn = sqlite3.connect('bot.db')
        c = conn.cursor()
        c.execute('UPDATE users SET balance = ? WHERE user_id = ?', (amount, user_id))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Balance updated for user {user_id} to {amount} points! 💰")
    except (IndexError, ValueError):
        await update.message.reply_text("💡 Usage: /setbalance <user_id> <amount>")

# Remove task command (admin only)
async def remove_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        task_id = int(context.args[0])
        remove_task(task_id)
        await update.message.reply_text(f"✅ Task {task_id} removed successfully! 🚀")
    except (IndexError, ValueError):
        await update.message.reply_text("💡 Usage: /remove_task <task_id>")

# Delete announcement command (admin only)
async def delete_announcement_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        announcement_id = int(context.args[0])
        delete_announcement(announcement_id)
        await update.message.reply_text(f"✅ Announcement {announcement_id} deleted successfully! 🚀")
    except (IndexError, ValueError):
        await update.message.reply_text("💡 Usage: /deleteannouncement <announcement_id>")

# Remove balance command (admin only)
async def remove_balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        if remove_balance(user_id, amount):
            await update.message.reply_text(f"✅ {amount} points deducted from user {user_id}'s balance! 💰")
        else:
            await update.message.reply_text(f"⚠️ Failed: User {user_id} has insufficient balance.")
    except (IndexError, ValueError):
        await update.message.reply_text("💡 Usage: /removebalance <user_id> <amount>")

# Error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

# Webhook handler
async def webhook(request):
    app = request.app['telegram_app']
    update = Update.de_json(await request.json(), app.bot)
    await app.process_update(update)
    return web.Response()

async def main():
    init_db()
    application = Application.builder().token(BOT_TOKEN).build()

    # Initialize the application
    await application.initialize()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(CommandHandler("add_task", add_task_cmd))
    application.add_handler(CommandHandler("announcement", announcement_cmd))
    application.add_handler(CommandHandler("deleteannouncement", delete_announcement_cmd))
    application.add_handler(CommandHandler("setbalance", set_balance))
    application.add_handler(CommandHandler("remove_task", remove_task_cmd))
    application.add_handler(CommandHandler("removebalance", remove_balance_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    # Set up webhook
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL environment variable not set")
        return
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    logger.info(f"Webhook set to {WEBHOOK_URL}/webhook")

    # Set up web server
    web_app = web.Application()
    web_app['telegram_app'] = application
    web_app.router.add_post('/webhook', webhook)
    
    # Start web server
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

    # Keep the application running
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
