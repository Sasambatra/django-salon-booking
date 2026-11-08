# Подготовка публичного размещения

**Русский** · [English](DEPLOYMENT.md)

Проект подготовлен для локального просмотра. Публичного сервера, домена,
реального SMTP и платёжного провайдера нет. Development server не предназначен
для публичной эксплуатации.

## Что настроить

1. Хост, домен, DNS и HTTPS.
2. Production WSGI/ASGI-сервер и выдачу static-файлов.
3. PostgreSQL с отдельными runtime/migration/test-ролями. Runtime не нужен CREATEDB.
4. Новый deployment secret, allowed hosts, `DJANGO_DEBUG=false`, SMTP и реального отправителя.
5. Настоящего владельца с проверенным email и MFA; отключение bootstrap-доступа.
6. Reverse proxy, перезаписывающий forwarded headers; только реальные trusted ranges и закрытый origin.
7. Edge-лимиты, мониторинг, хранение резервных копий и проверенное восстановление.

HSTS subdomains/preload включают осознанно, после HTTPS всех затронутых имён.
Локальная проверка настроек не подтверждает внешний TLS или доставку email.

## Проверки среды размещения

```bash
python manage.py check --deploy
python manage.py migrate --check
python manage.py collectstatic --noinput
```

Они проверяют настройки/миграции и собирают static, но не настраивают инфраструктуру.
До открытия доступа проверьте email регистрации/восстановления, MFA, владельца,
расписание, успех/отказ/истечение/возврат и оба языка через реальный HTTPS-домен.
Проверьте журналы и лимиты. Оплату обозначайте как симуляцию.
Реальный провайдер требует отдельной конфигурации и проверенных callback.

## Дополнительный Telegram worker

Настройте приватный токен/allowlist, один `run_telegram_bot` для базы,
наблюдение за pending/failed и срок хранения клиентских данных в очереди.
Нужен исходящий HTTPS к api.telegram.org, публичный webhook не требуется.
Проверьте разрешённые/запрещённые аккаунты и восстановление с реальным ботом.
Fake-transport тесты не создают BotFather-бота и не обеспечивают хостинг.
[Инструкция](TELEGRAM.md).

## Копии и восстановление

`backup_database` создаёт приватный custom-format pg_dump. Для размещения нужны
копии вне сервера, retention, права доступа и сохранение приватных ключей MFA.
Сначала восстановите в другую базу, проверьте схему и записи/оплаты, затем
переключайте приложение. Локальная проба восстановления не заменяет проверку хоста.

## GitHub

`.github/workflows/checks.yml` готовит Python 3.14 и PostgreSQL 18, устанавливает
закреплённые пакеты и проверяет исходники, переводы, миграции и тесты. Его
временные учётные данные не связаны с `.env`. О результатах hosted CI судят
по фактическому запуску, а не наличию workflow.

[Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/) ·
[PostgreSQL в GitHub Actions](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers).
