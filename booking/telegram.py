"""Read-only staff bot and durable delivery adapter; no third-party packages."""
import hashlib
import json
import re
import uuid
from datetime import date, datetime, time, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from booking.models import Booking, Payment, RequestThrottle, TelegramCursor, TelegramDelivery


PAGE_SIZE = 5
MAX_ATTEMPTS = 5
MENU = {
    "keyboard": [["Сегодня", "Завтра"], ["Ближайшие", "Сводка"], ["Помощь"]],
    "resize_keyboard": True,
}
COMMANDS = [
    ("start", "Открыть меню"), ("today", "Записи на сегодня"),
    ("tomorrow", "Записи на завтра"), ("upcoming", "Записи на ближайшие 7 дней"),
    ("date", "Записи на дату: /date ГГГГ-ММ-ДД"),
    ("booking", "Карточка: /booking полный номер записи"),
    ("stats", "Сводка на сегодня"), ("whoami", "Мой Telegram ID"), ("help", "Помощь"),
]
HELP = (
    "Бот студии · только просмотр\n\n"
    "/today — сегодня\n/tomorrow — завтра\n/upcoming — ближайшие 7 дней\n"
    "/date ГГГГ-ММ-ДД — выбранная дата\n/booking UUID — карточка по полному номеру\n"
    "/stats — сводка на сегодня\n/whoami — ваш Telegram ID\n\n"
    "Для следующей страницы добавьте номер: /today 2 или /date 2026-11-02 2.\n"
    "Расписание показывает подтверждённые записи и действующие резервы. "
    "Карточка по номеру доступна для любого статуса. Время московское, суммы в рублях.\n"
    "Платежи и возвраты демонстрационные. Изменения записей выполняются на сайте."
)
BOOKING_STATUS = {
    "held": "Ожидает демо-оплаты", "confirmed": "Подтверждена",
    "cancelled": "Отменена", "expired": "Время резерва истекло",
}
PAYMENT_STATUS = {
    "pending": "Ожидает демо-оплаты", "succeeded": "Демо-оплата успешна",
    "failed": "Демо-оплата не выполнена", "refunded": "Полный демо-возврат выполнен",
    "expired": "Время демо-оплаты истекло",
}
EVENT_TITLE = {
    "reserved": "Новая запись · временный резерв",
    "confirmed": "Запись подтверждена", "cancelled": "Запись отменена",
    "payment_failed": "Демо-оплата не прошла · запись отменена",
    "expired": "Резерв истёк · время снова доступно",
}


def bot_id_from_token(token):
    if not re.fullmatch(r"[1-9][0-9]*:[A-Za-z0-9_-]{20,}", token):
        raise ImproperlyConfigured("Set a valid TELEGRAM_BOT_TOKEN privately in .env.")
    return int(token.split(":", 1)[0])


class TelegramError(Exception):
    """Only fixed text/codes reach logs; Telegram may echo secrets in errors."""

    def __init__(self, code=0, retry_after=0):
        self.code = code if type(code) is int else 0
        self.retry_after = max(0, retry_after) if type(retry_after) is int else 0
        super().__init__(f"Telegram API error {self.code}" if self.code else "Telegram connection/response error")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the token-containing URL or private payload elsewhere.
        return None


class TelegramClient:
    def __init__(self, token):
        self.bot_id = bot_id_from_token(token)
        self._base_url = f"https://api.telegram.org/bot{token}/"
        self._opener = build_opener(NoRedirect())

    def call(self, method, *, request_timeout=15, **payload):
        if method not in {"getMe", "getWebhookInfo", "getUpdates", "sendMessage", "setMyCommands"}:
            raise ValueError("Unsupported Telegram method")
        request = Request(
            self._base_url + method,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with self._opener.open(request, timeout=request_timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            retry_after = 0
            try:
                body = json.load(error)
                retry_after = body.get("parameters", {}).get("retry_after", 0)
            except (ValueError, AttributeError, OSError):
                pass
            raise TelegramError(error.code, retry_after) from None
        except (URLError, OSError, ValueError):
            raise TelegramError() from None
        if not isinstance(result, dict):
            raise TelegramError()
        if result.get("ok") is not True:
            parameters = result.get("parameters") or {}
            raise TelegramError(result.get("error_code", 0), parameters.get("retry_after", 0))
        if "result" not in result:
            raise TelegramError()
        return result["result"]

    def send_message(self, chat_id, text, *, menu=False):
        payload = {"chat_id": chat_id, "text": text, "protect_content": True,
                   "link_preview_options": {"is_disabled": True}}
        if menu:
            payload["reply_markup"] = MENU
        return self.call("sendMessage", **payload)


def clean_text(value, limit=100):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:limit]


def studio_time(value):
    return timezone.localtime(value, ZoneInfo(settings.TIME_ZONE))


def booking_card(booking):
    start, end = studio_time(booking.starts_at), studio_time(booking.ends_at)
    status = booking.status
    if status == Booking.Status.HELD and booking.hold_expires_at <= timezone.now():
        status = Booking.Status.EXPIRED
    try:
        payment_status = booking.payment.status
        if status == Booking.Status.EXPIRED and payment_status == Payment.Status.PENDING:
            payment_status = Payment.Status.EXPIRED
        payment = PAYMENT_STATUS.get(payment_status, payment_status)
    except Payment.DoesNotExist:
        payment = "Нет данных"
    title = "Стрижка" if booking.service_name == "Haircut appointment" else clean_text(booking.service_name)
    lines = [
        f"Номер: {booking.pk}", f"Услуга: {title}", f"Мастер: {clean_text(booking.employee_name)}",
        f"Когда: {start:%d.%m.%Y %H:%M}–{end:%H:%M} (Москва)",
        f"Клиент: {clean_text(booking.customer.get_full_name() or booking.customer.email, 160)}",
        f"Стоимость: {booking.price_rub} ₽", f"Статус: {BOOKING_STATUS.get(status, status)}",
        f"Оплата: {payment}",
    ]
    if status == Booking.Status.HELD:
        lines.append(f"Резерв до: {studio_time(booking.hold_expires_at):%d.%m.%Y %H:%M} (Москва)")
    return "\n".join(lines)


def queue_booking_event(booking, event):
    """Called inside the booking transaction; never contacts the network."""
    if not settings.TELEGRAM_ENABLED or not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_ADMIN_IDS:
        return
    bot_id = bot_id_from_token(settings.TELEGRAM_BOT_TOKEN)
    text = f"{EVENT_TITLE[event]}\n\n{booking_card(booking)}"
    for chat_id in settings.TELEGRAM_ADMIN_IDS:
        TelegramDelivery.objects.get_or_create(
            booking=booking, event=event, bot_id=bot_id, chat_id=chat_id,
            defaults={"text": text},
        )


def deliver_pending(client, *, limit=20, now=None):
    """A row lock prevents parallel senders from processing the same row."""
    now = now or timezone.now()
    counts = {"sent": 0, "failed": 0, "retried": 0, "revoked": 0}
    if not settings.TELEGRAM_ENABLED:
        return counts
    cursor, _ = TelegramCursor.objects.get_or_create(bot_id=client.bot_id, defaults={"delivery_not_before": now})
    if cursor.delivery_not_before > now:
        return counts
    for _ in range(limit):
        with transaction.atomic():
            delivery = (TelegramDelivery.objects.select_for_update(skip_locked=True)
                        .filter(bot_id=client.bot_id, status=TelegramDelivery.Status.PENDING,
                                next_attempt_at__lte=now).first())
            if delivery is None:
                break
            if delivery.chat_id not in settings.TELEGRAM_ADMIN_IDS:
                delivery.status = TelegramDelivery.Status.REVOKED
                delivery.last_error = "Recipient access removed"
                delivery.save(update_fields=["status", "last_error"])
                counts["revoked"] += 1
                continue
            delivery.attempts += 1
            try:
                client.send_message(delivery.chat_id, delivery.text)
            except TelegramError as error:
                delivery.last_error = str(error)
                permanent = error.code in {400, 401, 403, 404}
                if error.retry_after:
                    cursor.delivery_not_before = now + timedelta(seconds=error.retry_after)
                    cursor.save(update_fields=["delivery_not_before"])
                if permanent or delivery.attempts >= MAX_ATTEMPTS:
                    delivery.status = TelegramDelivery.Status.FAILED
                    counts["failed"] += 1
                else:
                    delivery.next_attempt_at = now + timedelta(seconds=max(
                        15 * 2 ** (delivery.attempts - 1), error.retry_after,
                    ))
                    counts["retried"] += 1
            else:
                delivery.status = TelegramDelivery.Status.SENT
                delivery.sent_at = timezone.now()
                delivery.last_error = ""
                counts["sent"] += 1
            delivery.save(update_fields=["status", "attempts", "next_attempt_at", "sent_at", "last_error"])
            if delivery.last_error:
                # Stop the batch on an API error, rather than amplify an outage/flood limit.
                break
    return counts


def live_bookings(now):
    return Booking.objects.select_related("customer", "payment").filter(
        Q(status=Booking.Status.CONFIRMED) |
        Q(status=Booking.Status.HELD, hold_expires_at__gt=now)
    )


def date_bounds(day):
    zone = ZoneInfo(settings.TIME_ZONE)
    return (datetime.combine(day, time.min, tzinfo=zone),
            datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone))


def schedule(command, arguments, now):
    today = studio_time(now).date()
    if command == "/date":
        if not arguments or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", arguments[0]):
            raise ValueError
        day = date.fromisoformat(arguments[0])
        arguments = arguments[1:]
    else:
        day = today + timedelta(days=command == "/tomorrow")
    page = int(arguments[0]) if arguments else 1
    if len(arguments) > 1 or not 1 <= page <= 10000:
        raise ValueError
    start, end = date_bounds(day)
    if command == "/upcoming":
        start, end = now, date_bounds(today + timedelta(days=6))[1]
    records = live_bookings(now).filter(starts_at__gte=start, starts_at__lt=end).order_by("starts_at", "id")
    total = records.count()
    title = "Ближайшие 7 дней" if command == "/upcoming" else f"Записи на {day:%d.%m.%Y}"
    if not total:
        return f"{title}\nПодтверждённых записей и действующих резервов нет. Время московское."
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    if page > pages:
        return f"Всего страниц: {pages}. Укажите номер от 1 до {pages}."
    parts = [f"{title} · Москва\nВсего: {total} · страница {page}/{pages}"]
    for booking in records[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]:
        start_time = studio_time(booking.starts_at)
        parts.append(
            f"{start_time:%d.%m %H:%M} · {clean_text(booking.employee_name)}\n"
            f"Клиент: {clean_text(booking.customer.get_full_name() or booking.customer.email, 120)}\n"
            f"{BOOKING_STATUS[booking.status]} · {booking.price_rub} ₽\n/booking {booking.pk}"
        )
    if page < pages:
        prefix = f"/date {day.isoformat()}" if command == "/date" else command
        parts.append(f"Следующая страница: {prefix} {page + 1}")
    return "\n\n".join(parts)


def stats(now):
    day = studio_time(now).date()
    start, end = date_bounds(day)
    all_today = Booking.objects.filter(starts_at__gte=start, starts_at__lt=end)
    confirmed = all_today.filter(status=Booking.Status.CONFIRMED)
    held = all_today.filter(status=Booking.Status.HELD, hold_expires_at__gt=now)
    amount = confirmed.aggregate(total=Sum("price_rub"))["total"] or 0
    pending = TelegramDelivery.objects.filter(status=TelegramDelivery.Status.PENDING).count()
    failed = TelegramDelivery.objects.filter(status=TelegramDelivery.Status.FAILED).count()
    return (
        f"Сводка на {day:%d.%m.%Y} · Москва\n"
        f"Подтверждено: {confirmed.count()}\nДействующих резервов: {held.count()}\n"
        f"Отменено: {all_today.filter(status=Booking.Status.CANCELLED).count()}\n"
        f"Сумма подтверждённых записей: {amount} ₽ (демо)\n\n"
        f"Уведомлений в очереди: {pending}\nОшибок доставки: {failed}\n"
        "Оплата и возвраты имитируются. Это не отчёт о реальных поступлениях."
    )


def command_reply(text, now):
    aliases = {"Сегодня": "/today", "Завтра": "/tomorrow", "Ближайшие": "/upcoming",
               "Сводка": "/stats", "Помощь": "/help"}
    parts = aliases.get(text, text).split()
    if not parts:
        return HELP
    command, arguments = parts[0].split("@", 1)[0].lower(), parts[1:]
    try:
        if command in {"/today", "/tomorrow", "/date", "/upcoming"}:
            return schedule(command, arguments, now)
        if command == "/booking":
            if len(arguments) != 1:
                raise ValueError
            booking_id = uuid.UUID(arguments[0])
            booking = Booking.objects.select_related("customer", "payment").filter(pk=booking_id).first()
            return booking_card(booking) if booking else "Запись с таким номером не найдена."
        if command == "/stats":
            return stats(now)
    except (ValueError, OverflowError):
        return "Проверьте дату, номер страницы или полный UUID записи.\n\n" + HELP
    return HELP


def command_allowed(bot_id, user_id, now):
    key = hashlib.sha256(f"telegram:{bot_id}:{user_id}".encode()).hexdigest()
    with transaction.atomic():
        RequestThrottle.objects.get_or_create(key=key)
        bucket = RequestThrottle.objects.select_for_update().get(key=key)
        if now - bucket.window_start >= timedelta(minutes=1):
            bucket.window_start, bucket.count = now, 0
        if bucket.count >= 20:
            return False
        bucket.count += 1
        bucket.save(update_fields=["count", "window_start"])
        return True


def handle_update(client, update, *, identify_only=False, now=None):
    """Only Telegram-originated updates from getUpdates enter this function."""
    message = update.get("message")
    if not isinstance(message, dict):
        return
    chat, sender = message.get("chat", {}), message.get("from", {})
    user_id = sender.get("id")
    if (chat.get("type") != "private" or type(user_id) is not int or user_id <= 0
            or chat.get("id") != user_id or sender.get("is_bot")):
        return
    text = message.get("text")
    if not isinstance(text, str) or not 0 < len(text) <= 512:
        return
    text = text.strip()
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    authorized = not identify_only and settings.TELEGRAM_ENABLED and user_id in settings.TELEGRAM_ADMIN_IDS
    if not authorized and command not in {"/start", "/whoami"}:
        return
    now = now or timezone.now()
    if not command_allowed(client.bot_id, user_id, now):
        return
    if command == "/whoami" or not authorized:
        reply = f"Ваш Telegram ID: {user_id}.\nДанные записей доступны только разрешённым владельцем аккаунтам."
    else:
        reply = command_reply(text, now)
    client.send_message(user_id, reply, menu=authorized)
