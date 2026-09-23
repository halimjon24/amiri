"""
Telegram-бот для шашлычной «Сихкабоби Амири»
Функции:
- Меню и цены
- Выбор из 3 филиалов
- Доставка / самовывоз
- Приём заказа
- Имя и телефон клиента
- Итоговая сумма
- Уведомление сотруднику
- Сохранение заказов в JSON
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

# ───────────────────────── НАСТРОЙКИ ─────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

ORDERS_FILE = Path(__file__).parent / "orders.json"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ───────────────────────── ДАННЫЕ ─────────────────────────

MENU = {
    "lamb": {
        "name": "Шашлык из баранины",
        "price": 550,
        "emoji": "🥩",
        "desc": "Отборная мякоть с жирком, маринад с пряностями",
    },
    "beef": {
        "name": "Шашлык из говядины",
        "price": 520,
        "emoji": "🍖",
        "desc": "Нежные кусочки говядины в фирменном маринаде",
    },
    "chicken": {
        "name": "Окорочка",
        "price": 380,
        "emoji": "🍗",
        "desc": "Сочные куриные окорочка на мангале",
    },
    "lyulya": {
        "name": "Люля на мангале",
        "price": 450,
        "emoji": "🔥",
        "desc": "Ручной фарш с пряностями, жарится на углях",
    },
}

BRANCHES = {
    "1": {
        "name": "Филиал №1",
        "address": "19 мкр",
        "phone": "+992 (92) 029-77-00",
        "hours": "00:00 – 23:59",
        "map_url": "https://maps.app.goo.gl/zY88cp5FLujtGdg79?g_st=atm",
    },
    "2": {
        "name": "Филиал №2",
        "address": "Унджи",
        "phone": "+992 (92) 995-70-00",
        "hours": "08:00 – 23:00",
        "map_url": "https://maps.app.goo.gl/ep9JAucFbcY6m2Kq5?g_st=atm",
    },
    "3": {
        "name": "Филиал №3",
        "address": "Спутник",
        "phone": "+992 (11) 770-77-00",
        "hours": "08:00 – 23:00",
        "map_url": "https://maps.app.goo.gl/zY88cp5FLujtGdg79?g_st=atm",
    },
}


# ───────────────────────── МОДЕЛИ ─────────────────────────

@dataclass
class CartItem:
    key: str
    name: str
    price: int
    qty: int = 1

    @property
    def total(self) -> int:
        return self.price * self.qty


@dataclass
class Order:
    user_id: int
    username: Optional[str]
    branch_id: str
    delivery_type: str  # "pickup" | "delivery"
    items: list = field(default_factory=list)
    customer_name: str = ""
    customer_phone: str = ""
    address: str = ""  # только для доставки
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def total(self) -> int:
        return sum(CartItem(**i).total if isinstance(i, dict) else i.total for i in self.items)

    def to_dict(self) -> dict:
        return asdict(self)


# ───────────────────────── FSM ─────────────────────────

class OrderStates(StatesGroup):
    choosing_branch = State()
    choosing_delivery = State()
    browsing_menu = State()
    entering_name = State()
    entering_phone = State()
    entering_address = State()  # только доставка
    confirming = State()


# ───────────────────────── ХРАНЕНИЕ ─────────────────────────

def load_orders() -> list:
    if ORDERS_FILE.exists():
        try:
            return json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_order(order: Order) -> None:
    orders = load_orders()
    orders.append(order.to_dict())
    ORDERS_FILE.write_text(json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8")


# ───────────────────────── КЛАВИАТУРЫ ─────────────────────────

def main_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="📋 Меню")
    builder.button(text="🛒 Заказать")
    builder.button(text="📍 Филиалы")
    builder.button(text="📞 Позвонить")
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)


def branches_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for bid, b in BRANCHES.items():
        builder.button(text=f"{b['name']} — {b['address']}", callback_data=f"branch:{bid}")
    builder.adjust(1)
    return builder.as_markup()


def delivery_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏪 Самовывоз", callback_data="delivery:pickup")
    builder.button(text="🚗 Доставка", callback_data="delivery:delivery")
    builder.button(text="« Назад", callback_data="back:branches")
    builder.adjust(2, 1)
    return builder.as_markup()


def menu_kb(cart: dict[str, CartItem] | None = None) -> InlineKeyboardMarkup:
    cart = cart or {}
    builder = InlineKeyboardBuilder()
    for key, item in MENU.items():
        qty = cart.get(key)
        label = f"{item['emoji']} {item['name']} — {item['price']} ₽"
        if qty:
            label = f"{item['emoji']} {item['name']} ×{qty.qty} — {item['price']} ₽"
        builder.button(text=label, callback_data=f"item:{key}")
    builder.button(text="🛒 Корзина / Оформить", callback_data="cart:view")
    builder.button(text="« Отмена", callback_data="cancel")
    builder.adjust(1)
    return builder.as_markup()


def item_qty_kb(key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➖", callback_data=f"qty:{key}:-1")
    builder.button(text="➕", callback_data=f"qty:{key}:+1")
    builder.button(text="« К меню", callback_data="menu:back")
    builder.adjust(2, 1)
    return builder.as_markup()


def cart_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Оформить заказ", callback_data="cart:checkout")
    builder.button(text="🍽 Добавить ещё", callback_data="menu:back")
    builder.button(text="🗑 Очистить", callback_data="cart:clear")
    builder.button(text="« Отмена", callback_data="cancel")
    builder.adjust(1)
    return builder.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить заказ", callback_data="order:confirm")
    builder.button(text="❌ Отменить", callback_data="cancel")
    builder.adjust(1)
    return builder.as_markup()


def call_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for bid, b in BRANCHES.items():
        builder.button(text=f"📞 {b['name']}: {b['phone']}", callback_data=f"call:{bid}")
    builder.adjust(1)
    return builder.as_markup()


def branches_info_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for bid, b in BRANCHES.items():
        builder.button(text=f"📍 {b['name']}", callback_data=f"info:{bid}")
    builder.adjust(1)
    return builder.as_markup()


# ───────────────────────── ХЕЛПЕРЫ ─────────────────────────

def cart_from_data(data: dict) -> dict[str, CartItem]:
    raw = data.get("cart", {})
    return {k: CartItem(**v) for k, v in raw.items()}


def cart_to_data(cart: dict[str, CartItem]) -> dict:
    return {k: asdict(v) for k, v in cart.items()}


def format_cart(cart: dict[str, CartItem]) -> str:
    if not cart:
        return "Корзина пуста."
    lines = []
    total = 0
    for item in cart.values():
        lines.append(f"• {item.name} ×{item.qty} = {item.total} ₽")
        total += item.total
    lines.append(f"\n<b>Итого: {total} ₽</b>")
    return "\n".join(lines)


def format_order_text(order: Order) -> str:
    branch = BRANCHES[order.branch_id]
    dtype = "Самовывоз" if order.delivery_type == "pickup" else "Доставка"
    lines = [
        f"🧾 <b>Новый заказ</b>",
        f"🕐 {order.created_at}",
        f"",
        f"📍 <b>{branch['name']}</b>",
        f"   {branch['address']}",
        f"🚚 {dtype}",
    ]
    if order.delivery_type == "delivery" and order.address:
        lines.append(f"🏠 Адрес клиента: {order.address}")
    lines.append("")
    lines.append("<b>Состав:</b>")
    for i in order.items:
        item = CartItem(**i) if isinstance(i, dict) else i
        lines.append(f"• {item.name} ×{item.qty} = {item.total} ₽")
    lines.append(f"\n💰 <b>Сумма: {order.total()} ₽</b>")
    lines.append("")
    lines.append(f"👤 {order.customer_name}")
    lines.append(f"📱 {order.customer_phone}")
    if order.username:
        lines.append(f"✈️ @{order.username}")
    return "\n".join(lines)


# ───────────────────────── РОУТЕР ─────────────────────────

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🔥 <b>Сихкабоби Амири</b>\n"
        "Шашлык на углях • Три филиала\n\n"
        "Выберите действие в меню ниже или нажмите <b>🛒 Заказать</b>.",
        reply_markup=main_kb(),
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
@router.message(F.text.casefold() == "отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Заказ отменён.", reply_markup=main_kb())


# ─── Меню (просмотр) ───

@router.message(F.text == "📋 Меню")
async def show_menu(message: Message):
    text = "<b>📋 Наше меню</b>\n\n"
    for item in MENU.values():
        text += (
            f"{item['emoji']} <b>{item['name']}</b> — <b>{item['price']} ₽</b>\n"
            f"   <i>{item['desc']}</i>\n\n"
        )
    text += "Чтобы заказать — нажмите <b>🛒 Заказать</b>"
    await message.answer(text, parse_mode="HTML", reply_markup=main_kb())


# ─── Филиалы ───

@router.message(F.text == "📍 Филиалы")
async def show_branches(message: Message):
    await message.answer(
        "📍 <b>Наши филиалы</b>\nВыберите, чтобы увидеть адрес и часы работы:",
        reply_markup=branches_info_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("info:"))
async def branch_info(callback: CallbackQuery):
    bid = callback.data.split(":")[1]
    b = BRANCHES[bid]
    text = (
        f"📍 <b>{b['name']}</b>\n\n"
        f"🏠 {b['address']}\n"
        f"🕒 {b['hours']}\n"
        f"📞 <code>{b['phone']}</code>\n\n"
        f"<i>Нажмите на номер, чтобы скопировать</i>"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🗺 Открыть карту", url=b["map_url"])
    kb.adjust(1)
    await callback.message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    await callback.answer()

# ─── Позвонить ───

@router.message(F.text == "📞 Позвонить")
async def call_menu(message: Message):
    await message.answer(
        "📞 Выберите филиал, чтобы позвонить:",
        reply_markup=call_kb(),
    )


@router.callback_query(F.data.startswith("call:"))
async def call_branch(callback: CallbackQuery):
    bid = callback.data.split(":")[1]
    b = BRANCHES[bid]
    await callback.message.answer(
        f"📞 <b>{b['name']}</b>\n\n"
        f"Номер: <code>{b['phone']}</code>\n\n"
        f"<i>Нажмите на номер, чтобы скопировать и позвонить</i>",
        parse_mode="HTML",
    )
    await callback.answer()

# ─── Заказ: старт ───

@router.message(F.text == "🛒 Заказать")
async def start_order(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(OrderStates.choosing_branch)
    await message.answer(
        "📍 <b>Выберите филиал</b>",
        reply_markup=branches_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("branch:"), OrderStates.choosing_branch)
async def choose_branch(callback: CallbackQuery, state: FSMContext):
    bid = callback.data.split(":")[1]
    await state.update_data(branch_id=bid, cart={})
    await state.set_state(OrderStates.choosing_delivery)
    b = BRANCHES[bid]
    await callback.message.edit_text(
        f"📍 Выбран: <b>{b['name']}</b>\n{b['address']}\n\n"
        f"Как удобнее получить заказ?",
        reply_markup=delivery_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "back:branches")
async def back_to_branches(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OrderStates.choosing_branch)
    await callback.message.edit_text(
        "📍 <b>Выберите филиал</b>",
        reply_markup=branches_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delivery:"), OrderStates.choosing_delivery)
async def choose_delivery(callback: CallbackQuery, state: FSMContext):
    dtype = callback.data.split(":")[1]
    await state.update_data(delivery_type=dtype)
    await state.set_state(OrderStates.browsing_menu)
    label = "Самовывоз" if dtype == "pickup" else "Доставка"
    await callback.message.edit_text(
        f"🚚 Способ: <b>{label}</b>\n\n"
        f"🍽 <b>Выберите блюда</b>\n"
        f"Нажмите на блюдо, чтобы добавить / изменить количество:",
        reply_markup=menu_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Меню в заказе ───

@router.callback_query(F.data.startswith("item:"), OrderStates.browsing_menu)
async def pick_item(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":")[1]
    data = await state.get_data()
    cart = cart_from_data(data)
    if key not in cart:
        m = MENU[key]
        cart[key] = CartItem(key=key, name=m["name"], price=m["price"], qty=1)
    await state.update_data(cart=cart_to_data(cart))
    item = cart[key]
    await callback.message.edit_text(
        f"{MENU[key]['emoji']} <b>{item.name}</b>\n"
        f"{MENU[key]['desc']}\n\n"
        f"Цена: {item.price} ₽\n"
        f"Количество: <b>{item.qty}</b>\n"
        f"Сумма: <b>{item.total} ₽</b>",
        reply_markup=item_qty_kb(key),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("qty:"), OrderStates.browsing_menu)
async def change_qty(callback: CallbackQuery, state: FSMContext):
    _, key, delta = callback.data.split(":")
    delta = int(delta)
    data = await state.get_data()
    cart = cart_from_data(data)
    if key not in cart:
        m = MENU[key]
        cart[key] = CartItem(key=key, name=m["name"], price=m["price"], qty=0)
    cart[key].qty = max(0, cart[key].qty + delta)
    if cart[key].qty == 0:
        del cart[key]
        await state.update_data(cart=cart_to_data(cart))
        await callback.message.edit_text(
            "🍽 <b>Выберите блюда</b>",
            reply_markup=menu_kb(cart),
            parse_mode="HTML",
        )
    else:
        await state.update_data(cart=cart_to_data(cart))
        item = cart[key]
        await callback.message.edit_text(
            f"{MENU[key]['emoji']} <b>{item.name}</b>\n"
            f"{MENU[key]['desc']}\n\n"
            f"Цена: {item.price} ₽\n"
            f"Количество: <b>{item.qty}</b>\n"
            f"Сумма: <b>{item.total} ₽</b>",
            reply_markup=item_qty_kb(key),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "menu:back", OrderStates.browsing_menu)
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = cart_from_data(data)
    await callback.message.edit_text(
        "🍽 <b>Выберите блюда</b>\n"
        "Нажмите на блюдо, чтобы добавить / изменить количество:",
        reply_markup=menu_kb(cart),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cart:view", OrderStates.browsing_menu)
async def view_cart(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = cart_from_data(data)
    if not cart:
        await callback.answer("Корзина пуста — добавьте блюда", show_alert=True)
        return
    await callback.message.edit_text(
        f"🛒 <b>Ваш заказ</b>\n\n{format_cart(cart)}",
        reply_markup=cart_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cart:clear", OrderStates.browsing_menu)
async def clear_cart(callback: CallbackQuery, state: FSMContext):
    await state.update_data(cart={})
    await callback.message.edit_text(
        "Корзина очищена.\n\n🍽 <b>Выберите блюда</b>",
        reply_markup=menu_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cart:checkout", OrderStates.browsing_menu)
async def checkout(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = cart_from_data(data)
    if not cart:
        await callback.answer("Корзина пуста", show_alert=True)
        return
    await state.set_state(OrderStates.entering_name)
    await callback.message.edit_text(
        f"🛒 {format_cart(cart)}\n\n"
        f"👤 Как вас зовут?",
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Данные клиента ───

@router.message(OrderStates.entering_name)
async def enter_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Введите имя (минимум 2 символа).")
        return
    await state.update_data(customer_name=name)
    await state.set_state(OrderStates.entering_phone)
    kb = ReplyKeyboardBuilder()
    kb.button(text="📱 Отправить номер", request_contact=True)
    kb.button(text="❌ Отмена")
    kb.adjust(1)
    await message.answer(
        "📱 Укажите номер телефона\n"
        "Можно нажать кнопку ниже или написать вручную:",
        reply_markup=kb.as_markup(resize_keyboard=True),
    )


@router.message(OrderStates.entering_phone, F.contact)
async def enter_phone_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    await state.update_data(customer_phone=phone)
    await after_phone(message, state)


@router.message(OrderStates.entering_phone, F.text)
async def enter_phone_text(message: Message, state: FSMContext):
    if message.text and message.text.strip() in ("❌ Отмена", "Отмена"):
        await state.clear()
        await message.answer("Заказ отменён.", reply_markup=main_kb())
        return
    phone = (message.text or "").strip()
    digits = "".join(c for c in phone if c.isdigit() or c == "+")
    if len(digits) < 10:
        await message.answer("Введите корректный номер телефона.")
        return
    await state.update_data(customer_phone=phone)
    await after_phone(message, state)


async def after_phone(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("delivery_type") == "delivery":
        await state.set_state(OrderStates.entering_address)
        await message.answer(
            "🏠 Укажите адрес доставки:",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await show_confirmation(message, state)


@router.message(OrderStates.entering_address)
async def enter_address(message: Message, state: FSMContext):
    address = (message.text or "").strip()
    if len(address) < 5:
        await message.answer("Введите полный адрес доставки.")
        return
    await state.update_data(address=address)
    await show_confirmation(message, state)


async def show_confirmation(message: Message, state: FSMContext):
    data = await state.get_data()
    cart = cart_from_data(data)
    branch = BRANCHES[data["branch_id"]]
    dtype = "Самовывоз" if data["delivery_type"] == "pickup" else "Доставка"

    text = (
        f"✅ <b>Проверьте заказ</b>\n\n"
        f"📍 {branch['name']}\n"
        f"   {branch['address']}\n"
        f"🚚 {dtype}\n"
    )
    if data.get("address"):
        text += f"🏠 {data['address']}\n"
    text += f"\n{format_cart(cart)}\n\n"
    text += f"👤 {data['customer_name']}\n"
    text += f"📱 {data['customer_phone']}"

    await state.set_state(OrderStates.confirming)
    await message.answer(text, reply_markup=confirm_kb(), parse_mode="HTML")


@router.callback_query(F.data == "order:confirm", OrderStates.confirming)
async def confirm_order(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    cart = cart_from_data(data)

    order = Order(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        branch_id=data["branch_id"],
        delivery_type=data["delivery_type"],
        items=[asdict(i) for i in cart.values()],
        customer_name=data["customer_name"],
        customer_phone=data["customer_phone"],
        address=data.get("address", ""),
    )

    save_order(order)
    text = format_order_text(order)

    # Уведомление админам / сотрудникам
    notified = 0
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
            notified += 1
        except Exception as e:
            logger.warning("Не удалось отправить админу %s: %s", admin_id, e)

    await state.clear()
    await callback.message.edit_text(
        f"🎉 <b>Заказ принят!</b>\n\n"
        f"{format_cart(cart)}\n\n"
        f"Мы свяжемся с вами для подтверждения.\n"
        f"Спасибо, что выбрали <b>Сихкабоби Амири</b>! 🔥",
        parse_mode="HTML",
    )
    await callback.message.answer("Главное меню:", reply_markup=main_kb())
    await callback.answer()

    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS пуст — заказ сохранён, но никому не отправлен")


@router.callback_query(F.data == "cancel")
async def cancel_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Заказ отменён.")
    await callback.message.answer("Главное меню:", reply_markup=main_kb())
    await callback.answer()


# ───────────────────────── ЗАПУСК ─────────────────────────

async def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "Укажите BOT_TOKEN в файле .env\n"
            "Скопируйте .env.example → .env и вставьте токен от @BotFather"
        )

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Бот запущен. Админы: %s", ADMIN_IDS or "не заданы")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
