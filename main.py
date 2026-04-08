import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8700278738:AAEg_a89GXfph7ns9XqvinVd1wGSdsRg3og")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- ДАННЫЕ ДЛЯ ПРОТОТИПА ---
CITIES = ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Краснодар", "Новосибирск"]

CATEGORIES = [
    "🏗 Строительство", "🔧 Ремонт", "🚿 Сантехника", "⚡️ Электрика",
    "🧹 Клининг", "📦 Грузоперевозки", "🛋 Сборка мебели", "🌳 Ландшафт",
    "🛠 Мастер на час", "🚪 Окна и двери", "❄️ Кондиционеры", "🎨 Дизайн"
]
CATEGORIES_PER_PAGE = 4

# --- КЛАВИАТУРЫ (Reply) ---
role_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Я заказчик"), KeyboardButton(text="Я исполнитель")]],
    resize_keyboard=True
)

main_executor_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔍 Найти задачу")],
        [KeyboardButton(text="💼 В работе"), KeyboardButton(text="Профиль")]
    ],
    resize_keyboard=True
)

main_customer_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Создать задачу"), KeyboardButton(text="Мои задачи")],
        [KeyboardButton(text="Профиль")]
    ],
    resize_keyboard=True
)

# --- ГЕНЕРАТОРЫ КЛАВИАТУР (Inline) ---
def get_cities_kb(prefix: str, include_all: bool = False):
    builder = InlineKeyboardBuilder()
    for i, city in enumerate(CITIES):
        builder.button(text=f"📍 {city}", callback_data=f"{prefix}{i}")
    builder.adjust(2)

    if include_all:
        builder.row(InlineKeyboardButton(text="🌍 Все города", callback_data=f"{prefix}all"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_task"))
    return builder.as_markup()

def get_categories_kb(prefix: str = "cat_select_", page: int = 0):
    builder = InlineKeyboardBuilder()
    start = page * CATEGORIES_PER_PAGE
    end = start + CATEGORIES_PER_PAGE

    for i, cat in enumerate(CATEGORIES[start:end]):
        real_index = start + i
        builder.row(InlineKeyboardButton(text=cat, callback_data=f"{prefix}{real_index}"))

    nav_buttons = []
    page_prefix = "tcat_page_" if prefix == "tcat_select_" else "scat_page_"
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{page_prefix}{page-1}"))
    if end < len(CATEGORIES):
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"{page_prefix}{page+1}"))

    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="Отменить ❌", callback_data="cancel_task"))
    return builder.as_markup()

# --- СТАТИЧНЫЕ КЛАВИАТУРЫ (Inline) ---
payment_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="💵 Наличные", callback_data="pay_cash")],
        [InlineKeyboardButton(text="💳 Перечисление", callback_data="pay_transfer")]
    ]
)

contact_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📱 По номеру телефона", callback_data="contact_phone")],
        [InlineKeyboardButton(text="💬 MAX / Telegram", callback_data="contact_tg")]
    ]
)

confirm_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="confirm_publish")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_task")]
    ]
)

choose_task_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="🏗 Бетонные работы (3 отклика)", callback_data="task_1")]]
)

choose_executores_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⭐️ 5.0 - Иван М. (профи)", callback_data="executor_view_1")],
        [InlineKeyboardButton(text="⭐️ 4.8 - Сергей К. (опытный)", callback_data="executor_view_2")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_tasks")]
    ]
)

executor_profile_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выбрать исполнителем", callback_data="select_confirm")],
        [InlineKeyboardButton(text="📞 Связаться", callback_data="contact_exec")],
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="task_1")],
        [InlineKeyboardButton(text="🚩 Пожаловаться", callback_data="report_exec")]
    ]
)

task_card_inline_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✅ Откликнуться (0₽)", callback_data="apply")],
        [
            InlineKeyboardButton(text="⬅️", callback_data="prev_pic"),
            InlineKeyboardButton(text="➡️", callback_data="next_pic")
        ],
        [InlineKeyboardButton(text="Назад ↩️", callback_data="back_to_search")]
    ]
)

profile_inline_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить Имя", callback_data="edit_name")],
        [
            InlineKeyboardButton(text="⭐️ Мои отзывы", callback_data="my_reviews"),
            InlineKeyboardButton(text="💳 Мой счет", callback_data="my_balance")
        ],
        [InlineKeyboardButton(text="🔄 Сменить роль", callback_data="switch_role")],
        [InlineKeyboardButton(text="📤 Поддержка", callback_data="contact_admin")]
    ]
)

# --- ХЭНДЛЕРЫ БАЗОВЫЕ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Удобная платформа для размещения задач и поиска исполнителей "
        "в сфере строительства и ремонта.\n\n"
        "Пожалуйста, выберите свою роль для начала работы\n"
        "В любой момент вы сможете изменить её в настройках профиля ⚙️",
        reply_markup=role_keyboard
    )

@dp.message(F.text == "Я исполнитель")
async def role_exec(message: types.Message):
    await message.answer("Вы вошли как Исполнитель. Здесь вы можете искать заказы.", reply_markup=main_executor_keyboard)

@dp.message(F.text == "Я заказчик")
async def role_cust(message: types.Message):
    await message.answer("Вы вошли как Заказчик. Здесь вы можете публиковать задачи.", reply_markup=main_customer_keyboard)

@dp.message(F.text == "Профиль")
async def show_profile(message: types.Message):
    user_name = message.from_user.full_name
    profile_text = (
        "👤 **Ваш личный профиль**\n"
        "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n\n"
        f"**Имя:** {user_name}\n"
        "**Статус:** ✅ Верифицирован\n"
        "📊 **Статистика:**\n"
        "⭐️ Рейтинг: 5.0 (14 отзывов)\n"
        "💼 Личный счет: 0 ₽\n"
    )
    await message.answer(text=profile_text, reply_markup=profile_inline_kb, parse_mode="Markdown")

@dp.callback_query(F.data == "switch_role")
async def switch_role(callback: types.CallbackQuery):
    await callback.message.answer("Выберите новую роль:", reply_markup=role_keyboard)
    await callback.answer()

@dp.callback_query(F.data == "cancel_task")
async def cancel_creation(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Действие отменено.", reply_markup=main_customer_keyboard)
    await callback.answer()

# --- СОЗДАНИЕ ЗАДАЧИ (ЗАКАЗЧИК) — stateless ---
@dp.message(F.text == "Создать задачу")
async def start_task_creation(message: types.Message):
    await message.answer(
        "📍 **Шаг 1:** Выберите город, в котором нужно выполнить задачу:",
        reply_markup=get_cities_kb("task_city_"),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("task_city_"))
async def process_task_city(callback: types.CallbackQuery):
    city_idx = int(callback.data.replace("task_city_", ""))
    selected_city = CITIES[city_idx]

    await callback.message.edit_text(
        f"Город: **{selected_city}**\n\n"
        "🗂 **Шаг 2:** Выберите категорию будущей задачи:\n"
        "*(Используйте кнопки Вперед/Назад для просмотра всех)*",
        reply_markup=get_categories_kb(prefix="tcat_select_", page=0),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("tcat_page_"))
async def paginate_task_categories(callback: types.CallbackQuery):
    page = int(callback.data.replace("tcat_page_", ""))
    await callback.message.edit_reply_markup(
        reply_markup=get_categories_kb(prefix="tcat_select_", page=page)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("tcat_select_"))
async def process_task_category(callback: types.CallbackQuery):
    cat_idx = int(callback.data.replace("tcat_select_", ""))
    selected_category = CATEGORIES[cat_idx]

    await callback.message.edit_text(
        f"🗂 Категория: **{selected_category}**\n\n"
        "📝 **Шаг 3:** Напишите описание задачи текстом в чат.\n"
        "*(В прототипе — нажмите кнопку ниже для демо)*",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Демо: пропустить описание", callback_data="demo_skip_desc")],
            [InlineKeyboardButton(text="Отменить ❌", callback_data="cancel_task")]
        ]),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "demo_skip_desc")
async def demo_skip_description(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "💰 **Шаг 4: Оплата**\nВыберите способ оплаты:",
        reply_markup=payment_kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("pay_"))
async def process_payment(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "📞 **Шаг 5: Связь**\nКак исполнителям с вами связаться?",
        reply_markup=contact_kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("contact_"))
async def process_contact(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "📋 **ПРЕВЬЮ ВАШЕЙ ЗАДАЧИ**\n\n"
        "📍 **Город:** Москва\n"
        "🗂 **Категория:** 🏗 Строительство\n"
        "📝 **Описание:** Демо-задача\n"
        "💳 **Оплата:** Наличные\n"
        "📞 **Связь:** Telegram\n"
        "💰 **Бюджет/Срок:** Договорная",
        reply_markup=confirm_kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "confirm_publish")
async def publish_done(callback: types.CallbackQuery):
    await callback.message.answer(
        "🚀 Задача успешно опубликована и доступна исполнителям!",
        reply_markup=main_customer_keyboard
    )
    await callback.answer()

# --- МОИ ЗАДАЧИ ---
@dp.message(F.text == "Мои задачи")
async def my_tasks(message: types.Message):
    await message.answer("Ваши активные задачи. Нажмите для просмотра откликов:", reply_markup=choose_task_keyboard)

@dp.callback_query(F.data == "task_1")
async def task_responses(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "👷 **Отклики на «Бетонные работы»**\n\nВыберите исполнителя:",
        reply_markup=choose_executores_keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_tasks")
async def back_to_tasks(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "Ваши активные задачи. Нажмите для просмотра откликов:",
        reply_markup=choose_task_keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("executor_view_"))
async def view_executor(callback: types.CallbackQuery):
    profile_text = (
        "👷 **Профиль: Иван Михайлович**\n"
        "⭐ Рейтинг: 5.0 (24 отзыва)\n"
        "🛠 Спецификация: Бетон, Фундамент\n\n"
        "💬 *'Опыт 10 лет, своя опалубка.'*"
    )
    await callback.message.edit_text(profile_text, reply_markup=executor_profile_kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "select_confirm")
async def select_confirm(callback: types.CallbackQuery):
    await callback.answer("Исполнитель назначен!", show_alert=True)
    await callback.message.edit_text("✅ **Исполнитель выбран**\n\nСвяжитесь с Иваном М. для начала работ.", parse_mode="Markdown")

# --- ПОИСК ЗАДАЧ (ИСПОЛНИТЕЛЬ) — stateless ---
@dp.message(F.text == "🔍 Найти задачу")
async def find_task_start(message: types.Message):
    await message.answer(
        "📍 Выберите город для поиска актуальных задач:",
        reply_markup=get_cities_kb("search_city_", include_all=True)
    )

@dp.callback_query(F.data.startswith("search_city_"))
async def process_search_city(callback: types.CallbackQuery):
    data_part = callback.data.replace("search_city_", "")
    if data_part == "all":
        city_name = "Все города"
    else:
        city_name = CITIES[int(data_part)]

    await callback.message.edit_text(
        f"📍 Город: **{city_name}**\n\n"
        "🗂 **Выберите категорию задачи:**\n"
        "*(Используйте кнопки Вперед/Назад для просмотра всех)*",
        reply_markup=get_categories_kb(prefix="scat_select_", page=0),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("scat_page_"))
async def paginate_search_categories(callback: types.CallbackQuery):
    page = int(callback.data.replace("scat_page_", ""))
    await callback.message.edit_reply_markup(
        reply_markup=get_categories_kb(prefix="scat_select_", page=page)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("scat_select_"))
async def process_search_category(callback: types.CallbackQuery):
    cat_idx = int(callback.data.replace("scat_select_", ""))
    selected_category = CATEGORIES[cat_idx]

    caption = (
        f"🏗 **{selected_category}**\n\n"
        f"**Описание:** Нужно выполнить работы по {selected_category.lower()}. "
        f"Подробности уточняйте.\n"
        f"📍 **Адрес:** центральный район\n"
        f"💰 **Бюджет:** договорная"
    )
    photo_url = "https://eurobeton72.ru/upload/iblock/0a5/o2gxw2n35p33h35xr9tfkfrtlzzi4ity.jpg"

    await callback.message.delete()
    await callback.message.answer_photo(
        photo=photo_url,
        caption=caption,
        reply_markup=task_card_inline_kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "apply")
async def apply_task(callback: types.CallbackQuery):
    await callback.answer("Ваш отклик отправлен заказчику!", show_alert=True)

@dp.callback_query(F.data == "back_to_search")
async def back_to_search(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        "📍 Выберите город для поиска актуальных задач:",
        reply_markup=get_cities_kb("search_city_", include_all=True)
    )
    await callback.answer()

# --- ЗАПУСК БОТА (для локальной разработки) ---
async def main():
    print("Бот запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
