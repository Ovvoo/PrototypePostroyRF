import asyncio
import logging
import sqlite3
import os
from contextlib import contextmanager
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ─── КОНФИГУРАЦИЯ ──────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.environ.get("DB_PATH", "/tmp/mvp.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ─── БАЗА ДАННЫХ ───────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                full_name   TEXT,
                role        TEXT,
                phone       TEXT,
                bio         TEXT,
                city        TEXT,
                rating      REAL DEFAULT 5.0,
                reviews_cnt INTEGER DEFAULT 0,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS _migrations (id INTEGER PRIMARY KEY);

            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL REFERENCES users(user_id),
                city        TEXT NOT NULL,
                category    TEXT NOT NULL,
                description TEXT NOT NULL,
                payment     TEXT,
                budget      TEXT,
                contact     TEXT,
                photo_id    TEXT,
                status      TEXT DEFAULT 'open',
                executor_id INTEGER REFERENCES users(user_id),
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS responses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id     INTEGER NOT NULL REFERENCES tasks(id),
                executor_id INTEGER NOT NULL REFERENCES users(user_id),
                message     TEXT,
                status      TEXT DEFAULT 'pending',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(task_id, executor_id)
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user   INTEGER NOT NULL REFERENCES users(user_id),
                to_user     INTEGER NOT NULL REFERENCES users(user_id),
                task_id     INTEGER REFERENCES tasks(id),
                rating      INTEGER NOT NULL,
                comment     TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
    log.info("DB initialized: %s", DB_PATH)
    with get_db() as db:
        for col, typedef in [("bio", "TEXT"), ("city", "TEXT"), ("categories", "TEXT")]:
            try:
                db.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
            except Exception:
                pass


# Инициализируем БД при импорте — нужно для Vercel webhook
init_db()


# ─── ХЕЛПЕРЫ БД ────────────────────────────────────────────────────────────────
def upsert_user(user: types.User, role: str = None):
    with get_db() as db:
        exists = db.execute("SELECT role FROM users WHERE user_id=?", (user.id,)).fetchone()
        if exists:
            if role:
                db.execute(
                    "UPDATE users SET role=?, username=?, full_name=? WHERE user_id=?",
                    (role, user.username, user.full_name, user.id)
                )
        else:
            db.execute(
                "INSERT INTO users(user_id, username, full_name, role) VALUES(?,?,?,?)",
                (user.id, user.username, user.full_name, role or "customer")
            )


def get_user(user_id: int):
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def get_user_role(user_id: int) -> str:
    with get_db() as db:
        row = db.execute("SELECT role FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row[0] if row else "customer"


def set_user_phone(user_id: int, phone: str):
    with get_db() as db:
        db.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))


def create_task(customer_id, city, category, description, payment, budget, contact, photo_id) -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO tasks(customer_id,city,category,description,payment,budget,contact,photo_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (customer_id, city, category, description, payment, budget, contact, photo_id)
        )
        return cur.lastrowid


def get_open_tasks(city: str = None, category: str = None):
    with get_db() as db:
        q = (
            "SELECT t.*, u.full_name as customer_name, u.rating as customer_rating "
            "FROM tasks t JOIN users u ON t.customer_id=u.user_id "
            "WHERE t.status='open'"
        )
        params = []
        if city and city != "Все города":
            q += " AND t.city=?"
            params.append(city)
        if category:
            q += " AND t.category=?"
            params.append(category)
        q += " ORDER BY t.created_at DESC LIMIT 20"
        return db.execute(q, params).fetchall()


def get_task(task_id: int):
    with get_db() as db:
        return db.execute(
            "SELECT t.*, u.full_name as customer_name, u.phone as customer_phone,"
            " u.rating as customer_rating "
            "FROM tasks t JOIN users u ON t.customer_id=u.user_id WHERE t.id=?",
            (task_id,)
        ).fetchone()


def get_my_tasks(customer_id: int):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM tasks WHERE customer_id=? ORDER BY created_at DESC",
            (customer_id,)
        ).fetchall()


def get_task_responses(task_id: int):
    with get_db() as db:
        return db.execute(
            "SELECT r.*, u.full_name, u.rating, u.reviews_cnt, u.phone "
            "FROM responses r JOIN users u ON r.executor_id=u.user_id "
            "WHERE r.task_id=? ORDER BY r.created_at",
            (task_id,)
        ).fetchall()


def add_response(task_id: int, executor_id: int, message: str) -> bool:
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO responses(task_id, executor_id, message) VALUES(?,?,?)",
                (task_id, executor_id, message)
            )
        return True
    except sqlite3.IntegrityError:
        return False


def accept_response(task_id: int, executor_id: int):
    with get_db() as db:
        db.execute(
            "UPDATE tasks SET status='in_progress', executor_id=? WHERE id=?",
            (executor_id, task_id)
        )
        db.execute(
            "UPDATE responses SET status='accepted' WHERE task_id=? AND executor_id=?",
            (task_id, executor_id)
        )
        db.execute(
            "UPDATE responses SET status='rejected' WHERE task_id=? AND executor_id!=?",
            (task_id, executor_id)
        )


def complete_task(task_id: int):
    with get_db() as db:
        db.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))


def cancel_task_db(task_id: int):
    with get_db() as db:
        db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))


def add_review(from_user: int, to_user: int, task_id: int, rating: int, comment: str):
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO reviews(from_user,to_user,task_id,rating,comment) VALUES(?,?,?,?,?)",
            (from_user, to_user, task_id, rating, comment)
        )
        row = db.execute(
            "SELECT AVG(rating) as avg, COUNT(*) as cnt FROM reviews WHERE to_user=?",
            (to_user,)
        ).fetchone()
        db.execute(
            "UPDATE users SET rating=ROUND(?,1), reviews_cnt=? WHERE user_id=?",
            (row["avg"], row["cnt"], to_user)
        )


def has_reviewed(from_user: int, task_id: int) -> bool:
    with get_db() as db:
        return db.execute(
            "SELECT 1 FROM reviews WHERE from_user=? AND task_id=?",
            (from_user, task_id)
        ).fetchone() is not None


def executor_already_applied(task_id: int, executor_id: int) -> bool:
    with get_db() as db:
        return db.execute(
            "SELECT 1 FROM responses WHERE task_id=? AND executor_id=?",
            (task_id, executor_id)
        ).fetchone() is not None


def get_user_reviews(user_id: int, limit: int = 5, offset: int = 0):
    with get_db() as db:
        rows = db.execute(
            "SELECT r.*, u.full_name as from_name "
            "FROM reviews r JOIN users u ON r.from_user=u.user_id "
            "WHERE r.to_user=? ORDER BY r.created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset)
        ).fetchall()
        total = db.execute(
            "SELECT COUNT(*) as cnt FROM reviews WHERE to_user=?", (user_id,)
        ).fetchone()["cnt"]
        return rows, total


def get_executors(city: str = None, category: str = None):
    with get_db() as db:
        if category:
            q = (
                "SELECT DISTINCT u.* FROM users u "
                "WHERE u.role='executor' AND ("
                "  u.categories LIKE ? OR EXISTS("
                "    SELECT 1 FROM tasks t WHERE t.executor_id=u.user_id AND t.category=?"
                "  )"
                ")"
            )
            params = [f"%{category}%", category]
            if city and city != "Все города":
                q = (
                    "SELECT DISTINCT u.* FROM users u "
                    "WHERE u.role='executor' AND (u.categories LIKE ? OR EXISTS("
                    "  SELECT 1 FROM tasks t WHERE t.executor_id=u.user_id AND t.category=?"
                    ")) AND (u.city=? OR EXISTS("
                    "  SELECT 1 FROM tasks t WHERE t.executor_id=u.user_id AND t.city=?"
                    "))"
                )
                params = [f"%{category}%", category, city, city]
            q += " ORDER BY u.rating DESC LIMIT 20"
        else:
            q = "SELECT * FROM users WHERE role='executor'"
            params = []
            if city and city != "Все города":
                q += " AND city=?"
                params.append(city)
            q += " ORDER BY rating DESC LIMIT 20"
        return db.execute(q, params).fetchall()


def set_user_city(user_id: int, city: str):
    with get_db() as db:
        db.execute("UPDATE users SET city=? WHERE user_id=?", (city, user_id))


def set_user_bio(user_id: int, bio: str):
    with get_db() as db:
        db.execute("UPDATE users SET bio=? WHERE user_id=?", (bio, user_id))


def set_user_categories(user_id: int, categories: str):
    with get_db() as db:
        db.execute("UPDATE users SET categories=? WHERE user_id=?", (categories, user_id))


# ─── ДАННЫЕ ────────────────────────────────────────────────────────────────────
TOP_CITIES = ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург"]
ALL_CITIES = TOP_CITIES + [
    "Краснодар", "Новосибирск", "Нижний Новгород", "Челябинск",
    "Самара", "Уфа", "Ростов-на-Дону", "Омск", "Воронеж", "Пермь"
]
CITIES_PER_PAGE = 6

CATEGORIES = [
    "🏗 Строительство", "🔧 Ремонт", "🚿 Сантехника", "⚡️ Электрика",
    "🧹 Клининг", "📦 Грузоперевозки", "🛋 Сборка мебели", "🌳 Ландшафт",
    "🛠 Мастер на час", "🚪 Окна и двери", "❄️ Кондиционеры", "🎨 Дизайн"
]
CATEGORIES_PER_PAGE = 4

STATUS_EMOJI = {
    "open": "🟢 Открыта",
    "in_progress": "🟡 В работе",
    "done": "✅ Выполнена",
    "cancelled": "🔴 Отменена",
}


# ─── СОСТОЯНИЯ FSM ─────────────────────────────────────────────────────────────
class TaskStates(StatesGroup):
    city = State()
    category = State()
    description = State()
    payment_method = State()
    budget = State()
    contact_method = State()
    contact_number = State()
    confirmation = State()


class SearchStates(StatesGroup):
    city = State()
    category = State()


class ExecutorSearchStates(StatesGroup):
    city = State()
    category = State()


class ProfileEditStates(StatesGroup):
    bio = State()
    city = State()
    categories = State()


class ResponseStates(StatesGroup):
    message = State()


class ReviewStates(StatesGroup):
    rating = State()
    comment = State()


# ─── КЛАВИАТУРЫ ────────────────────────────────────────────────────────────────
role_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="👤 Я заказчик"), KeyboardButton(text="🔨 Я исполнитель")]],
    resize_keyboard=True
)

customer_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Создать задачу")],
        [KeyboardButton(text="📋 Мои задачи"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🔎 Найти исполнителя"), KeyboardButton(text="🔄 Сменить роль")]
    ],
    resize_keyboard=True
)

executor_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔍 Найти задачу")],
        [KeyboardButton(text="💼 Мои отклики"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🔄 Сменить роль")]
    ],
    resize_keyboard=True
)


def get_role_kb(role: str):
    return customer_kb if role == "customer" else executor_kb


def get_cities_kb(prefix: str, page: int = 0, top_only: bool = True, include_all: bool = False):
    builder = InlineKeyboardBuilder()
    if top_only:
        for city in TOP_CITIES:
            builder.button(text=f"🗺 {city}", callback_data=f"{prefix}sel_{city}")
        builder.adjust(2)
        builder.row(InlineKeyboardButton(text="🔍 Другой город", callback_data=f"{prefix}more_0"))
    else:
        start = page * CITIES_PER_PAGE
        end = start + CITIES_PER_PAGE
        for city in ALL_CITIES[start:end]:
            builder.button(text=f"🗺 {city}", callback_data=f"{prefix}sel_{city}")
        builder.adjust(2)
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}more_{page - 1}"))
        if end < len(ALL_CITIES):
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}more_{page + 1}"))
        if nav:
            builder.row(*nav)
    if include_all:
        builder.row(InlineKeyboardButton(text="🌍 Все города", callback_data=f"{prefix}sel_Все города"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


def get_categories_kb(prefix: str, page: int = 0):
    builder = InlineKeyboardBuilder()
    start = page * CATEGORIES_PER_PAGE
    end = start + CATEGORIES_PER_PAGE
    for i, cat in enumerate(CATEGORIES[start:end]):
        builder.row(InlineKeyboardButton(text=cat, callback_data=f"{prefix}sel_{start + i}"))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}pg_{page - 1}"))
    if end < len(CATEGORIES):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}pg_{page + 1}"))
    if nav:
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


contact_choice_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📱 Телефон", callback_data="contact_phone")],
    [InlineKeyboardButton(text="💬 Telegram", callback_data="contact_tg")],
])

payment_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="💵 Наличные", callback_data="pay_cash")],
    [InlineKeyboardButton(text="💳 Перевод", callback_data="pay_transfer")],
])

skip_budget_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⏭ Пропустить (по договорённости)", callback_data="budget_skip")],
])

confirm_publish_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="✅ Опубликовать", callback_data="confirm_publish")],
    [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")],
])

rating_kb = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="⭐ 1", callback_data="rate_1"),
    InlineKeyboardButton(text="⭐ 2", callback_data="rate_2"),
    InlineKeyboardButton(text="⭐ 3", callback_data="rate_3"),
    InlineKeyboardButton(text="⭐ 4", callback_data="rate_4"),
    InlineKeyboardButton(text="⭐ 5", callback_data="rate_5"),
]])


# ─── /start ────────────────────────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user = get_user(message.from_user.id)
    if user and user["role"]:
        role_name = "Заказчика" if user["role"] == "customer" else "Исполнителя"
        await message.answer(
            f"С возвращением, {message.from_user.first_name}! 👋\nВы в режиме *{role_name}*.",
            reply_markup=get_role_kb(user["role"]), parse_mode="Markdown"
        )
    else:
        upsert_user(message.from_user)
        await message.answer(
            "👋 Добро пожаловать!\n\nЭто платформа для поиска исполнителей и заказов.\nВыберите вашу роль:",
            reply_markup=role_kb
        )


# ─── РОЛИ ──────────────────────────────────────────────────────────────────────
@dp.message(F.text == "👤 Я заказчик")
async def role_customer(message: types.Message, state: FSMContext):
    await state.clear()
    upsert_user(message.from_user, "customer")
    await message.answer(
        "✅ Режим *Заказчика* активирован.\n\nСоздавайте задачи и нанимайте исполнителей.",
        reply_markup=customer_kb, parse_mode="Markdown"
    )


@dp.message(F.text == "🔨 Я исполнитель")
async def role_executor(message: types.Message, state: FSMContext):
    await state.clear()
    upsert_user(message.from_user, "executor")
    await message.answer(
        "✅ Режим *Исполнителя* активирован.\n\nИщите задачи и откликайтесь на них.",
        reply_markup=executor_kb, parse_mode="Markdown"
    )


@dp.message(F.text == "🔄 Сменить роль")
async def switch_role(message: types.Message, state: FSMContext):
    await state.clear()
    user_role = get_user_role(message.from_user.id)
    if user_role == "customer":
        await role_executor(message, state)
    else:
        await role_customer(message, state)


# ─── ПРОФИЛЬ ───────────────────────────────────────────────────────────────────
@dp.message(F.text == "👤 Профиль")
async def show_profile(message: types.Message):
    u = get_user(message.from_user.id)
    if not u:
        await message.answer("Сначала выберите роль: /start")
        return
    await send_profile_card(message, u, is_own=True)


async def send_profile_card(message_or_cb, u, is_own: bool = False, edit: bool = False):
    role_name = "Заказчик" if u["role"] == "customer" else "Исполнитель"
    stars = "⭐" * round(u["rating"] or 5)
    phone_line = f"📱 {u['phone']}" if u["phone"] else "📱 Не указан"
    bio_line = f"📄 {u['bio']}" if u["bio"] else ""
    city_line = f"📍 {u['city']}" if u["city"] else ""
    try:
        cats = u["categories"] or None
    except (IndexError, KeyError):
        cats = None
    cats_line = f"🗂 Специализации: {cats}" if cats and u["role"] == "executor" else ""

    text = (
        f"👤 *{u['full_name']}*\n"
        f"Роль: {role_name}\n"
        f"{city_line}\n"
        f"{cats_line}\n"
        f"{stars} {u['rating']} ({u['reviews_cnt']} отзывов)\n"
        f"{phone_line}\n"
        f"{bio_line}"
    ).strip()

    builder = InlineKeyboardBuilder()
    if u["reviews_cnt"] > 0:
        builder.row(InlineKeyboardButton(
            text=f"💬 Читать отзывы ({u['reviews_cnt']})",
            callback_data=f"profile_reviews_{u['user_id']}_0"
        ))
    if is_own:
        if not u["phone"]:
            builder.row(InlineKeyboardButton(text="📱 Добавить телефон", callback_data="add_phone"))
        builder.row(InlineKeyboardButton(text="✏️ Изменить о себе", callback_data="edit_bio"))
        builder.row(InlineKeyboardButton(text="🏙 Указать город", callback_data="edit_city"))
        if u["role"] == "executor":
            builder.row(InlineKeyboardButton(text="🗂 Мои специализации", callback_data="edit_categories"))

    markup = builder.as_markup() if builder._markup else None
    if edit and hasattr(message_or_cb, 'message'):
        await message_or_cb.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    elif hasattr(message_or_cb, 'answer'):
        await message_or_cb.answer(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await message_or_cb.message.answer(text, reply_markup=markup, parse_mode="Markdown")


@dp.callback_query(F.data.startswith("view_profile_"))
async def view_user_profile(callback: types.CallbackQuery):
    user_id = int(callback.data[13:])
    u = get_user(user_id)
    if not u:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await send_profile_card(callback, u, is_own=(user_id == callback.from_user.id))
    await callback.answer()


@dp.callback_query(F.data.startswith("profile_reviews_"))
async def view_profile_reviews(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    user_id = int(parts[2])
    page = int(parts[3])
    PAGE_SIZE = 3
    reviews, total = get_user_reviews(user_id, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    if not reviews:
        await callback.answer("Отзывов пока нет", show_alert=True)
        return

    lines = [f"💬 *Отзывы ({total} всего):*\n"]
    for r in reviews:
        stars = "⭐" * r["rating"]
        comment = r["comment"] or "_(без комментария)_"
        lines.append(f"{stars} от *{r['from_name']}*\n{comment}\n")

    builder = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"profile_reviews_{user_id}_{page-1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"profile_reviews_{user_id}_{page+1}"))
    if nav:
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="⬅️ К профилю", callback_data=f"view_profile_{user_id}"))

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode="Markdown"
    )
    await callback.answer()


# ─── РЕДАКТИРОВАНИЕ ПРОФИЛЯ ────────────────────────────────────────────────────
@dp.callback_query(F.data == "edit_bio")
async def ask_bio(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileEditStates.bio)
    await callback.message.answer("✏️ Напишите коротко о себе (опыт, специализация):")
    await callback.answer()


@dp.message(ProfileEditStates.bio)
async def save_bio(message: types.Message, state: FSMContext):
    set_user_bio(message.from_user.id, message.text)
    await state.clear()
    u = get_user(message.from_user.id)
    await message.answer("✅ О себе обновлено!", reply_markup=get_role_kb(u["role"]))


@dp.callback_query(F.data == "edit_city")
async def ask_profile_city(callback: types.CallbackQuery, state: FSMContext):
    u = get_user(callback.from_user.id)
    await state.set_state(ProfileEditStates.city)
    kb = get_cities_kb("pcity_")
    if u and u["city"]:
        from aiogram.utils.keyboard import InlineKeyboardBuilder as IKB
        builder = IKB.from_markup(kb)
        builder.row(InlineKeyboardButton(text=f"🗑 Убрать город ({u['city']})", callback_data="pcity_clear"))
        kb = builder.as_markup()
    await callback.message.answer("🏙 Выберите ваш город:", reply_markup=kb)
    await callback.answer()


@dp.callback_query(ProfileEditStates.city, F.data == "pcity_clear")
async def clear_profile_city(callback: types.CallbackQuery, state: FSMContext):
    set_user_city(callback.from_user.id, None)
    await state.clear()
    await callback.message.edit_text("✅ Город удалён из профиля.")
    await callback.answer()


@dp.callback_query(ProfileEditStates.city, F.data.startswith("pcity_sel_"))
async def save_profile_city(callback: types.CallbackQuery, state: FSMContext):
    city = callback.data[10:]
    set_user_city(callback.from_user.id, city)
    await state.clear()
    await callback.message.edit_text(f"✅ Город *{city}* сохранён!", parse_mode="Markdown")
    await callback.answer()


@dp.callback_query(ProfileEditStates.city, F.data.startswith("pcity_more_"))
async def profile_city_more(callback: types.CallbackQuery):
    page = int(callback.data[11:])
    await callback.message.edit_reply_markup(
        reply_markup=get_cities_kb("pcity_", page=page, top_only=False)
    )
    await callback.answer()


# ─── КАТЕГОРИИ ИСПОЛНИТЕЛЯ ─────────────────────────────────────────────────────
def get_executor_categories_kb(selected: list):
    builder = InlineKeyboardBuilder()
    for i, cat in enumerate(CATEGORIES):
        mark = "✅ " if cat in selected else ""
        builder.row(InlineKeyboardButton(
            text=f"{mark}{cat}",
            callback_data=f"ecattoggle_{i}"
        ))
    builder.row(InlineKeyboardButton(text="💾 Сохранить", callback_data="ecatsave"))
    return builder.as_markup()


@dp.callback_query(F.data == "edit_categories")
async def ask_categories(callback: types.CallbackQuery, state: FSMContext):
    u = get_user(callback.from_user.id)
    current = u["categories"].split(",") if u and u["categories"] else []
    current = [c.strip() for c in current if c.strip()]
    await state.set_state(ProfileEditStates.categories)
    await state.update_data(selected_cats=current)
    await callback.message.edit_text(
        "🗂 *Выберите свои специализации:*\n_(можно несколько)_",
        reply_markup=get_executor_categories_kb(current),
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(ProfileEditStates.categories, F.data.startswith("ecattoggle_"))
async def toggle_category(callback: types.CallbackQuery, state: FSMContext):
    idx = int(callback.data[11:])
    cat = CATEGORIES[idx]
    data = await state.get_data()
    selected = data.get("selected_cats", [])
    if cat in selected:
        selected.remove(cat)
    else:
        selected.append(cat)
    await state.update_data(selected_cats=selected)
    await callback.message.edit_reply_markup(
        reply_markup=get_executor_categories_kb(selected)
    )
    await callback.answer()


@dp.callback_query(ProfileEditStates.categories, F.data == "ecatsave")
async def save_categories(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_cats", [])
    set_user_categories(callback.from_user.id, ", ".join(selected))
    await state.clear()
    await callback.message.edit_text(
        f"✅ Специализации сохранены!\n🗂 {', '.join(selected) if selected else '—'}",
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(F.data == "add_phone")
async def ask_phone(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state("add_phone")
    await callback.message.answer(
        "Отправьте номер телефона:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True
        )
    )
    await callback.answer()


@dp.message(F.contact)
async def got_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    current = await state.get_state()
    set_user_phone(message.from_user.id, phone)
    u = get_user(message.from_user.id)
    kb = get_role_kb(u["role"]) if u else role_kb

    if current == TaskStates.contact_number:
        await state.update_data(contact=f"📱 {phone}")
        await show_preview(message, state)
    else:
        await state.clear()
        await message.answer(f"✅ Телефон {phone} сохранён!", reply_markup=kb)


# ─── СОЗДАНИЕ ЗАДАЧИ ───────────────────────────────────────────────────────────
@dp.message(F.text == "➕ Создать задачу")
async def task_start(message: types.Message, state: FSMContext):
    u = get_user(message.from_user.id)
    if not u or u["role"] != "customer":
        await message.answer("Эта функция доступна только заказчикам.\nВыберите роль: /start")
        return
    if u["city"]:
        await state.update_data(city=u["city"])
        await state.set_state(TaskStates.category)
        await message.answer(
            f"📍 Город: *{u['city']}* _(из профиля)_\n\n🗂 *Шаг 1 — Выберите категорию:*",
            reply_markup=get_categories_kb("tcat_"), parse_mode="Markdown"
        )
    else:
        await state.set_state(TaskStates.city)
        await message.answer(
            "📍 *Шаг 1 — Выберите город:*",
            reply_markup=get_cities_kb("tc_"), parse_mode="Markdown"
        )


@dp.callback_query(TaskStates.city, F.data.startswith("tc_sel_"))
async def task_city_selected(callback: types.CallbackQuery, state: FSMContext):
    city = callback.data[7:]
    await state.update_data(city=city)
    await state.set_state(TaskStates.category)
    await callback.message.edit_text(
        f"📍 Город: *{city}*\n\n🗂 *Шаг 2 из 6 — Выберите категорию:*",
        reply_markup=get_categories_kb("tcat_"), parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(TaskStates.city, F.data.startswith("tc_more_"))
async def task_city_more(callback: types.CallbackQuery):
    page = int(callback.data[8:])
    await callback.message.edit_reply_markup(
        reply_markup=get_cities_kb("tc_", page=page, top_only=False)
    )
    await callback.answer()


@dp.callback_query(TaskStates.category, F.data.startswith("tcat_sel_"))
async def task_cat_selected(callback: types.CallbackQuery, state: FSMContext):
    idx = int(callback.data[9:])
    cat = CATEGORIES[idx]
    await state.update_data(category=cat)
    await state.set_state(TaskStates.description)
    await callback.message.edit_text(
        f"🗂 Категория: *{cat}*\n\n📝 *Шаг 3 из 6 — Опишите задачу*\n_(можно прикрепить фото)_",
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(TaskStates.category, F.data.startswith("tcat_pg_"))
async def task_cat_page(callback: types.CallbackQuery):
    page = int(callback.data[8:])
    await callback.message.edit_reply_markup(reply_markup=get_categories_kb("tcat_", page=page))
    await callback.answer()


@dp.message(TaskStates.description)
async def task_description(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id if message.photo else None
    desc = message.caption if message.photo else message.text
    if not desc:
        await message.answer("Напишите описание текстом.")
        return
    await state.update_data(description=desc, photo=photo_id)
    await state.set_state(TaskStates.payment_method)
    await message.answer(
        "💰 *Шаг 4 из 6 — Способ оплаты:*",
        reply_markup=payment_kb, parse_mode="Markdown"
    )


@dp.callback_query(TaskStates.payment_method, F.data.startswith("pay_"))
async def task_payment(callback: types.CallbackQuery, state: FSMContext):
    pay = "Наличные" if callback.data == "pay_cash" else "Перевод"
    await state.update_data(payment=pay)
    await state.set_state(TaskStates.budget)
    await callback.message.edit_text(
        "💵 *Укажите бюджет и сроки:*\n"
        "_(необязательно — можно пропустить, тогда будет «По договорённости»)_",
        reply_markup=skip_budget_kb,
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(TaskStates.budget, F.data == "budget_skip")
async def task_budget_skip(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(budget="По договорённости")
    await state.set_state(TaskStates.contact_method)
    await callback.message.edit_text(
        "📞 *Как исполнитель свяжется с вами?*",
        reply_markup=contact_choice_kb, parse_mode="Markdown"
    )
    await callback.answer()


@dp.message(TaskStates.budget)
async def task_budget(message: types.Message, state: FSMContext):
    await state.update_data(budget=message.text)
    await state.set_state(TaskStates.contact_method)
    await message.answer(
        "📞 *Как исполнитель свяжется с вами?*",
        reply_markup=contact_choice_kb, parse_mode="Markdown"
    )


@dp.callback_query(TaskStates.contact_method, F.data.startswith("contact_"))
async def task_contact_choice(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "contact_tg":
        uname = f"@{callback.from_user.username}" if callback.from_user.username else "через Telegram"
        await state.update_data(contact=f"💬 {uname}")
        await state.set_state(TaskStates.confirmation)
        data = await state.get_data()
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            _build_preview(data), reply_markup=confirm_publish_kb, parse_mode="Markdown"
        )
    else:
        await state.set_state(TaskStates.contact_number)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            "📱 Отправьте номер телефона:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
                resize_keyboard=True, one_time_keyboard=True
            )
        )
    await callback.answer()


@dp.message(TaskStates.contact_number)
async def task_contact_manual(message: types.Message, state: FSMContext):
    await state.update_data(contact=f"📱 {message.text}")
    await show_preview(message, state)


def _build_preview(data: dict) -> str:
    return (
        f"📋 *ПРЕВЬЮ ЗАДАЧИ*\n\n"
        f"📍 Город: {data.get('city', '—')}\n"
        f"🗂 Категория: {data.get('category', '—')}\n"
        f"📝 Описание: {data.get('description', '—')}\n"
        f"💰 Оплата: {data.get('payment', '—')}\n"
        f"💵 Бюджет/Срок: {data.get('budget', '—')}\n"
        f"📞 Связь: {data.get('contact', '—')}"
    )


async def show_preview(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(TaskStates.confirmation)
    await message.answer(
        _build_preview(data),
        reply_markup=confirm_publish_kb,
        parse_mode="Markdown"
    )


@dp.callback_query(TaskStates.confirmation, F.data == "confirm_publish")
async def do_publish(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    task_id = create_task(
        customer_id=callback.from_user.id,
        city=data["city"],
        category=data["category"],
        description=data["description"],
        payment=data.get("payment"),
        budget=data.get("budget"),
        contact=data.get("contact"),
        photo_id=data.get("photo"),
    )
    await state.clear()
    await callback.message.edit_text(
        f"🚀 *Задача #{task_id} опубликована!*\n\nИсполнители уже могут её видеть и откликаться.",
        parse_mode="Markdown"
    )
    await callback.message.answer("Что дальше?", reply_markup=customer_kb)
    await callback.answer()


# ─── МОИ ЗАДАЧИ (ЗАКАЗЧИК) ────────────────────────────────────────────────────
def _build_my_tasks_markup(tasks):
    builder = InlineKeyboardBuilder()
    for t in tasks:
        st = STATUS_EMOJI.get(t["status"], t["status"])
        label = f"{st} #{t['id']} {t['category'][:20]}"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"mytask_{t['id']}"))
    return builder.as_markup()


@dp.message(F.text == "📋 Мои задачи")
async def my_tasks(message: types.Message):
    tasks = get_my_tasks(message.from_user.id)
    if not tasks:
        await message.answer("У вас пока нет задач. Нажмите ➕ Создать задачу!")
        return
    await message.answer(
        "📋 *Ваши задачи:*",
        reply_markup=_build_my_tasks_markup(tasks),
        parse_mode="Markdown"
    )


@dp.callback_query(F.data == "back_to_my_tasks")
async def back_to_my_tasks(callback: types.CallbackQuery):
    tasks = get_my_tasks(callback.from_user.id)
    if not tasks:
        await callback.message.edit_text("У вас пока нет задач.")
        return
    await callback.message.edit_text(
        "📋 *Ваши задачи:*",
        reply_markup=_build_my_tasks_markup(tasks),
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("mytask_"))
async def view_my_task(callback: types.CallbackQuery):
    task_id = int(callback.data[7:])
    task = get_task(task_id)
    if not task:
        await callback.answer("Задача не найдена")
        return

    responses = get_task_responses(task_id)
    builder = InlineKeyboardBuilder()

    if task["status"] == "open":
        if responses:
            builder.row(InlineKeyboardButton(
                text=f"👷 Отклики ({len(responses)})", callback_data=f"responses_{task_id}"
            ))
        builder.row(InlineKeyboardButton(text="❌ Отменить задачу", callback_data=f"cancel_task_{task_id}"))
    elif task["status"] == "in_progress":
        builder.row(InlineKeyboardButton(
            text="✅ Отметить выполненной", callback_data=f"done_task_{task_id}"
        ))
    elif task["status"] == "done" and task["executor_id"]:
        if not has_reviewed(callback.from_user.id, task_id):
            builder.row(InlineKeyboardButton(
                text="⭐ Оставить отзыв", callback_data=f"review_{task['executor_id']}_{task_id}"
            ))

    st = STATUS_EMOJI.get(task["status"], task["status"])
    text = (
        f"📋 *Задача #{task['id']}*\n"
        f"📍 {task['city']} • {task['category']}\n\n"
        f"📝 {task['description']}\n\n"
        f"💰 {task['payment'] or '—'} • 💵 {task['budget'] or '—'}\n"
        f"📊 {st}"
    )
    if task["status"] == "in_progress" and task["executor_id"]:
        exec_u = get_user(task["executor_id"])
        if exec_u:
            text += f"\n👷 Исполнитель: {exec_u['full_name']}"
            if exec_u["phone"]:
                text += f" ({exec_u['phone']})"
            builder.row(InlineKeyboardButton(
                text="👤 Профиль исполнителя", callback_data=f"view_profile_{task['executor_id']}"
            ))

    builder.row(InlineKeyboardButton(text="⬅️ Назад к задачам", callback_data="back_to_my_tasks"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@dp.callback_query(F.data.startswith("responses_"))
async def view_responses(callback: types.CallbackQuery):
    task_id = int(callback.data[10:])
    responses = get_task_responses(task_id)
    if not responses:
        await callback.answer("Откликов пока нет", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for r in responses:
        stars = "⭐" * round(r["rating"] or 5)
        label = f"{stars} {r['full_name']} ({r['reviews_cnt']} отз.)"
        builder.row(InlineKeyboardButton(
            text=label, callback_data=f"viewresp_{task_id}_{r['executor_id']}"
        ))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"mytask_{task_id}"))
    await callback.message.edit_text(
        f"👷 *Отклики на задачу #{task_id}:*",
        reply_markup=builder.as_markup(), parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("viewresp_"))
async def view_one_response(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    task_id, executor_id = int(parts[1]), int(parts[2])
    responses = get_task_responses(task_id)
    resp = next((r for r in responses if r["executor_id"] == executor_id), None)
    if not resp:
        await callback.answer("Отклик не найден")
        return

    ex_user = get_user(executor_id)
    stars = "⭐" * round(resp["rating"] or 5)
    city_line = f"📍 {ex_user['city']}" if ex_user and ex_user["city"] else ""
    try:
        ex_cats = ex_user["categories"] or None
    except (IndexError, KeyError):
        ex_cats = None
    cats_line = f"🗂 {ex_cats}" if ex_user and ex_cats else ""
    bio_line = f"📄 {ex_user['bio']}" if ex_user and ex_user["bio"] else ""
    phone_str = f"📱 {resp['phone']}" if resp["phone"] else ""

    text = (
        f"👷 *{resp['full_name']}*\n"
        f"{city_line}\n"
        f"{cats_line}\n"
        f"{stars} {resp['rating']} ({resp['reviews_cnt']} отзывов)\n"
        f"{phone_str}\n"
        f"{bio_line}\n\n"
        f"💬 *Сообщение:* {resp['message'] or '—'}\n"
    ).strip()

    reviews, total = get_user_reviews(executor_id, limit=3, offset=0)
    if reviews:
        text += f"\n\n─────────────────\n💬 *Последние отзывы ({total}):*\n"
        for rv in reviews:
            rv_stars = "⭐" * rv["rating"]
            comment = rv["comment"] or "_(без комментария)_"
            text += f"\n{rv_stars} от *{rv['from_name']}*\n{comment}\n"

    builder = InlineKeyboardBuilder()
    task = get_task(task_id)
    if task and task["status"] == "open":
        builder.row(InlineKeyboardButton(
            text="✅ Выбрать исполнителем", callback_data=f"accept_{task_id}_{executor_id}"
        ))
    if total > 3:
        builder.row(InlineKeyboardButton(
            text=f"💬 Все отзывы ({total})", callback_data=f"profile_reviews_{executor_id}_0"
        ))
    builder.row(InlineKeyboardButton(text="⬅️ Назад к откликам", callback_data=f"responses_{task_id}"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@dp.callback_query(F.data.startswith("accept_"))
async def accept_executor(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    task_id, executor_id = int(parts[1]), int(parts[2])
    accept_response(task_id, executor_id)
    exec_u = get_user(executor_id)
    task = get_task(task_id)
    await callback.message.edit_text(
        f"✅ *{exec_u['full_name']} назначен исполнителем!*\n\n"
        f"Задача #{task_id} переведена в статус «В работе».",
        parse_mode="Markdown"
    )
    try:
        customer = get_user(callback.from_user.id)
        contact_line = (
            f"📱 {customer['phone']}" if customer["phone"]
            else f"@{callback.from_user.username or 'заказчик'}"
        )
        await bot.send_message(
            executor_id,
            f"🎉 *Вас выбрали исполнителем!*\n\n"
            f"Задача #{task_id}: *{task['category']}* в {task['city']}\n"
            f"📝 {task['description'][:200]}\n\n"
            f"Контакт заказчика: {contact_line}",
            parse_mode="Markdown"
        )
    except Exception as e:
        log.warning("Could not notify executor %s: %s", executor_id, e)
    await callback.answer("Исполнитель назначен!")


@dp.callback_query(F.data.startswith("done_task_"))
async def mark_done(callback: types.CallbackQuery):
    task_id = int(callback.data[10:])
    complete_task(task_id)
    task = get_task(task_id)
    await callback.message.edit_text(
        f"✅ *Задача #{task_id} выполнена!*\n\nОставьте отзыв об исполнителе.",
        parse_mode="Markdown"
    )
    if task and task["executor_id"]:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text="⭐ Оставить отзыв", callback_data=f"review_{task['executor_id']}_{task_id}"
        ))
        await callback.message.answer("Оцените работу исполнителя:", reply_markup=builder.as_markup())
        try:
            await bot.send_message(
                task["executor_id"],
                f"🎉 *Заказчик подтвердил выполнение задачи #{task_id}!*\n\nСпасибо за работу.",
                parse_mode="Markdown"
            )
        except Exception as e:
            log.warning("Could not notify executor: %s", e)
    await callback.answer()


@dp.callback_query(F.data.startswith("cancel_task_"))
async def cancel_task_handler(callback: types.CallbackQuery):
    task_id = int(callback.data[12:])
    cancel_task_db(task_id)
    await callback.message.edit_text(f"❌ Задача #{task_id} отменена.")
    await callback.answer()


# ─── ПОИСК И ОТКЛИК (ИСПОЛНИТЕЛЬ) ─────────────────────────────────────────────
@dp.message(F.text == "🔍 Найти задачу")
async def search_start(message: types.Message, state: FSMContext):
    u = get_user(message.from_user.id)
    if not u or u["role"] != "executor":
        await message.answer("Эта функция доступна только исполнителям. /start")
        return
    await state.clear()
    city = u["city"] if u and u["city"] else "Все города"
    await show_task_list(message, city=city, category=None, user_id=message.from_user.id, edit=False)


@dp.callback_query(SearchStates.city, F.data.startswith("sc_sel_"))
async def search_city_sel(callback: types.CallbackQuery, state: FSMContext):
    city = callback.data[7:]
    await state.clear()
    await show_task_list(callback.message, city=city, category=None,
                         user_id=callback.from_user.id, edit=True)
    await callback.answer()


@dp.callback_query(SearchStates.city, F.data.startswith("sc_more_"))
async def search_city_more(callback: types.CallbackQuery):
    page = int(callback.data[8:])
    await callback.message.edit_reply_markup(
        reply_markup=get_cities_kb("sc_", page=page, top_only=False, include_all=True)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("filter_city_"))
async def filter_by_category(callback: types.CallbackQuery, state: FSMContext):
    city = callback.data[12:]
    await state.set_state(SearchStates.category)
    await state.update_data(search_city=city)
    await callback.message.edit_text(
        f"📍 {city}\n\n🗂 Выберите категорию для фильтра:",
        reply_markup=get_categories_kb("scat_")
    )
    await callback.answer()


@dp.callback_query(SearchStates.category, F.data.startswith("scat_sel_"))
async def search_cat_sel(callback: types.CallbackQuery, state: FSMContext):
    idx = int(callback.data[9:])
    cat = CATEGORIES[idx]
    data = await state.get_data()
    city = data["search_city"]
    await state.clear()
    await show_task_list(callback.message, city, cat, callback.from_user.id, edit=True)
    await callback.answer()


@dp.callback_query(SearchStates.category, F.data.startswith("scat_pg_"))
async def search_cat_page(callback: types.CallbackQuery):
    page = int(callback.data[8:])
    await callback.message.edit_reply_markup(reply_markup=get_categories_kb("scat_", page=page))
    await callback.answer()


async def show_task_list(message: types.Message, city: str, category: str | None,
                         user_id: int, edit: bool = False):
    tasks = get_open_tasks(city=city, category=category)
    builder = InlineKeyboardBuilder()

    if tasks:
        for t in tasks:
            label = f"#{t['id']} {t['city']} • {t['category'][:16]} • {t['budget'] or '—'}"
            builder.row(InlineKeyboardButton(text=label, callback_data=f"task_view_{t['id']}"))

    city_label = city if city != "Все города" else "все города"
    if category:
        builder.row(InlineKeyboardButton(
            text="❌ Убрать фильтр по категории", callback_data=f"filter_city_{city}"
        ))
        text = f"🔍 Найдено *{len(tasks)}* задач — *{category}* ({city_label}):"
    else:
        builder.row(InlineKeyboardButton(
            text="🗂 Фильтр по категории", callback_data=f"filter_city_{city}"
        ))
        text = (
            f"🔍 Все открытые задачи — *{city_label}*: *{len(tasks)} шт.*\n"
            f"_(можно выбрать категорию как фильтр)_"
        ) if tasks else f"😔 В *{city_label}* нет открытых задач."

    if city != "Все города":
        builder.row(
            InlineKeyboardButton(text="🏙 Сменить город", callback_data="tasksearch_changecity"),
            InlineKeyboardButton(text="🌍 Все города", callback_data="tasksearch_allcities"),
        )
    else:
        builder.row(InlineKeyboardButton(text="🏙 Выбрать город", callback_data="tasksearch_changecity"))

    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data == "tasksearch_allcities")
async def tasksearch_allcities(callback: types.CallbackQuery):
    await show_task_list(callback.message, city="Все города", category=None,
                         user_id=callback.from_user.id, edit=True)
    await callback.answer()


@dp.callback_query(F.data == "tasksearch_changecity")
async def tasksearch_changecity(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SearchStates.city)
    await callback.message.edit_text(
        "🏙 Выберите город для поиска задач:",
        reply_markup=get_cities_kb("sc_", include_all=True)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("task_view_"))
async def view_task_card(callback: types.CallbackQuery):
    task_id = int(callback.data[10:])
    task = get_task(task_id)
    if not task:
        await callback.answer("Задача не найдена или уже закрыта", show_alert=True)
        return
    already = executor_already_applied(task_id, callback.from_user.id)
    builder = InlineKeyboardBuilder()
    if task["status"] == "open":
        if not already:
            builder.row(InlineKeyboardButton(text="✅ Откликнуться", callback_data=f"apply_{task_id}"))
        else:
            builder.row(InlineKeyboardButton(text="✔️ Вы уже откликнулись", callback_data="noop"))
    st = STATUS_EMOJI.get(task["status"], task["status"])
    text = (
        f"📋 *Задача #{task['id']}*\n"
        f"📍 {task['city']} • {task['category']}\n\n"
        f"📝 {task['description']}\n\n"
        f"💰 Оплата: {task['payment'] or '—'}\n"
        f"💵 Бюджет/Срок: {task['budget'] or '—'}\n"
        f"📊 {st}\n"
        f"⭐ Заказчик: {task['customer_rating']}"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@dp.callback_query(F.data.startswith("apply_"))
async def apply_task(callback: types.CallbackQuery, state: FSMContext):
    task_id = int(callback.data[6:])
    await state.update_data(apply_task_id=task_id)
    await state.set_state(ResponseStates.message)
    await callback.message.answer(
        "💬 Напишите короткое сообщение заказчику (опыт, сроки, цена):\n"
        "_(отправьте «-» чтобы откликнуться без текста)_",
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.message(ResponseStates.message)
async def submit_response(message: types.Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("apply_task_id")
    if not task_id:
        await state.clear()
        return
    msg_text = "" if message.text == "-" else message.text
    ok = add_response(task_id, message.from_user.id, msg_text)
    task = get_task(task_id)
    await state.clear()
    if ok:
        await message.answer(
            f"✅ *Отклик на задачу #{task_id} отправлен!*\n\nЗаказчик получит уведомление.",
            reply_markup=executor_kb, parse_mode="Markdown"
        )
        if task:
            executor = get_user(message.from_user.id)
            try:
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(
                    text="👷 Смотреть отклики", callback_data=f"responses_{task_id}"
                ))
                await bot.send_message(
                    task["customer_id"],
                    f"🔔 *Новый отклик на задачу #{task_id}!*\n\n"
                    f"👷 {executor['full_name']}\n"
                    f"⭐ Рейтинг: {executor['rating']}\n"
                    f"💬 {msg_text or '—'}",
                    reply_markup=builder.as_markup(),
                    parse_mode="Markdown"
                )
            except Exception as e:
                log.warning("Could not notify customer: %s", e)
    else:
        await message.answer("Вы уже откликались на эту задачу.", reply_markup=executor_kb)


# ─── МОИ ОТКЛИКИ (ИСПОЛНИТЕЛЬ) ────────────────────────────────────────────────
@dp.message(F.text == "💼 Мои отклики")
async def my_responses(message: types.Message):
    with get_db() as db:
        rows = db.execute(
            "SELECT r.*, t.city, t.category, t.status as task_status, t.budget, t.customer_id "
            "FROM responses r JOIN tasks t ON r.task_id=t.id "
            "WHERE r.executor_id=? ORDER BY r.created_at DESC",
            (message.from_user.id,)
        ).fetchall()
    if not rows:
        await message.answer("Вы ещё не откликались на задачи. 🔍 Найдите подходящую!")
        return
    builder = InlineKeyboardBuilder()
    for r in rows:
        st = STATUS_EMOJI.get(r["task_status"], r["task_status"])
        label = f"{st} #{r['task_id']} {r['category'][:18]}"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"myresp_{r['task_id']}"))
    await message.answer("💼 *Ваши отклики:*", reply_markup=builder.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("myresp_"))
async def view_my_response(callback: types.CallbackQuery):
    task_id = int(callback.data[7:])
    task = get_task(task_id)
    with get_db() as db:
        resp = db.execute(
            "SELECT * FROM responses WHERE task_id=? AND executor_id=?",
            (task_id, callback.from_user.id)
        ).fetchone()
    if not task or not resp:
        await callback.answer("Не найдено")
        return
    st_task = STATUS_EMOJI.get(task["status"], task["status"])
    st_resp = {
        "pending": "⏳ Ожидает ответа",
        "accepted": "✅ Принят",
        "rejected": "❌ Отклонён"
    }.get(resp["status"], resp["status"])

    text = (
        f"📋 *Задача #{task_id}*\n"
        f"📍 {task['city']} • {task['category']}\n"
        f"📝 {task['description'][:200]}\n"
        f"💵 {task['budget'] or '—'}\n"
        f"📊 Статус задачи: {st_task}\n"
        f"📨 Статус отклика: {st_resp}"
    )
    if resp["status"] == "accepted" and task["contact"]:
        text += f"\n📞 Контакт заказчика: {task['contact']}"

    builder = InlineKeyboardBuilder()
    if task["status"] == "done" and resp["status"] == "accepted":
        if not has_reviewed(callback.from_user.id, task_id):
            builder.row(InlineKeyboardButton(
                text="⭐ Оставить отзыв о заказчике",
                callback_data=f"review_{task['customer_id']}_{task_id}"
            ))

    markup = builder.as_markup() if builder._markup else None
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    await callback.answer()


# ─── ОТЗЫВЫ ────────────────────────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("review_"))
async def start_review(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    to_user_id, task_id = int(parts[1]), int(parts[2])
    if has_reviewed(callback.from_user.id, task_id):
        await callback.answer("Вы уже оставили отзыв.", show_alert=True)
        return
    await state.set_state(ReviewStates.rating)
    await state.update_data(review_to=to_user_id, review_task=task_id)
    await callback.message.answer("⭐ Оцените от 1 до 5:", reply_markup=rating_kb)
    await callback.answer()


@dp.callback_query(ReviewStates.rating, F.data.startswith("rate_"))
async def review_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = int(callback.data[5:])
    await state.update_data(review_rating=rating)
    await state.set_state(ReviewStates.comment)
    stars = "⭐" * rating
    await callback.message.edit_text(
        f"Оценка: {stars}\n\nНапишите комментарий (или «-» для пропуска):"
    )
    await callback.answer()


@dp.message(ReviewStates.comment)
async def review_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    comment = "" if message.text == "-" else message.text
    add_review(
        from_user=message.from_user.id,
        to_user=data["review_to"],
        task_id=data["review_task"],
        rating=data["review_rating"],
        comment=comment
    )
    u = get_user(message.from_user.id)
    await state.clear()
    stars = "⭐" * data["review_rating"]
    await message.answer(
        f"✅ Отзыв сохранён! {stars}\nСпасибо за оценку.",
        reply_markup=get_role_kb(u["role"]) if u else role_kb
    )


# ─── ОБЩИЕ CALLBACK ────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "cancel")
async def cancel_cb(callback: types.CallbackQuery, state: FSMContext):
    u = get_user(callback.from_user.id)
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    kb = get_role_kb(u["role"]) if u else role_kb
    await callback.message.answer("Действие отменено.", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "noop")
async def noop(callback: types.CallbackQuery):
    await callback.answer()


# ─── ПОИСК ИСПОЛНИТЕЛЕЙ (ЗАКАЗЧИК) ────────────────────────────────────────────
@dp.message(F.text == "🔎 Найти исполнителя")
async def executor_search_start(message: types.Message, state: FSMContext):
    u = get_user(message.from_user.id)
    if not u or u["role"] != "customer":
        await message.answer("Эта функция доступна только заказчикам. /start")
        return
    city = u["city"] if u and u["city"] else None
    if city:
        await show_executor_list(message, city=city, category=None, edit=False)
    else:
        await state.set_state(ExecutorSearchStates.city)
        await message.answer(
            "🔎 В каком городе ищем исполнителя?",
            reply_markup=get_cities_kb("exs_", include_all=True)
        )


@dp.callback_query(ExecutorSearchStates.city, F.data.startswith("exs_sel_"))
async def executor_search_city_sel(callback: types.CallbackQuery, state: FSMContext):
    city = callback.data[8:]
    await state.clear()
    await show_executor_list(callback.message, city=city, category=None, edit=True)
    await callback.answer()


@dp.callback_query(ExecutorSearchStates.city, F.data.startswith("exs_more_"))
async def executor_search_city_more(callback: types.CallbackQuery):
    page = int(callback.data[9:])
    await callback.message.edit_reply_markup(
        reply_markup=get_cities_kb("exs_", page=page, top_only=False, include_all=True)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("exfilter_city_"))
async def executor_filter_by_category(callback: types.CallbackQuery, state: FSMContext):
    city = callback.data[14:]
    await state.set_state(ExecutorSearchStates.category)
    await state.update_data(ex_city=city)
    await callback.message.edit_text(
        f"📍 {city}\n\n🗂 Выберите специализацию:",
        reply_markup=get_categories_kb("excat_")
    )
    await callback.answer()


@dp.callback_query(ExecutorSearchStates.category, F.data.startswith("excat_sel_"))
async def executor_search_cat_sel(callback: types.CallbackQuery, state: FSMContext):
    idx = int(callback.data[10:])
    cat = CATEGORIES[idx]
    data = await state.get_data()
    city = data["ex_city"]
    await state.clear()
    await show_executor_list(callback.message, city=city, category=cat, edit=True)
    await callback.answer()


@dp.callback_query(ExecutorSearchStates.category, F.data.startswith("excat_pg_"))
async def executor_search_cat_page(callback: types.CallbackQuery):
    page = int(callback.data[9:])
    await callback.message.edit_reply_markup(reply_markup=get_categories_kb("excat_", page=page))
    await callback.answer()


async def show_executor_list(message: types.Message, city: str, category: str | None, edit: bool = False):
    executors = get_executors(city=city, category=category)
    builder = InlineKeyboardBuilder()

    if executors:
        for ex in executors:
            stars = "⭐" * round(ex["rating"] or 5)
            label = f"{ex['full_name']} {stars[:3]} {ex['rating']} ({ex['reviews_cnt']} отз.)"
            builder.row(InlineKeyboardButton(
                text=label, callback_data=f"view_profile_{ex['user_id']}"
            ))

    if category:
        builder.row(InlineKeyboardButton(
            text="❌ Убрать фильтр", callback_data=f"exfilter_city_{city}"
        ))
        text = f"👷 Исполнители — *{category}* ({city}): {len(executors)} чел."
    else:
        builder.row(InlineKeyboardButton(
            text="🗂 Фильтр по специализации", callback_data=f"exfilter_city_{city}"
        ))
        text = (
            f"👷 Исполнители в *{city}*: {len(executors)} чел.\n"
            f"_(можно отфильтровать по специализации)_"
        ) if executors else f"😔 В *{city}* пока нет исполнителей."

    markup = builder.as_markup() if builder._markup else None
    if edit:
        await message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await message.answer(text, reply_markup=markup, parse_mode="Markdown")


# ─── ЗАПУСК ────────────────────────────────────────────────────────────────────
async def main():
    log.info("Bot starting (polling mode)...")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
