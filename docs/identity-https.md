<!-- docs/identity-https.md -->

# HTTPS для локального PDRD

## Статус и изученная инфраструктура

Это предложение настройки. Слушатель TLS, запись DNS и сертификат пока не
установлены. Текущие изменения auth/user/admin можно проверить и собрать
отдельно; вход из браузера принимается после подготовки HTTPS.

Проверены `compose.yaml`, `frontend/Dockerfile`, `frontend/nginx.conf`,
`scripts/up.sh`, `scripts/check-stack.sh`, настройки Auth/Gateway и документы
соседнего репозитория shared infrastructure.

По выводу пользователя с aistation от 2026-10-05:

- frontend работает на `192.168.55.3:8080 → 80`;
- порт 443 свободен, Nginx на хосте не установлен;
- `pdrd.itcneoterm.local` пока не разрешается;
- auth/user/admin доступны внутри сети приложения;
- shared infrastructure обслуживает модели, RabbitMQ, n8n и Open WebUI;
  слушатель TLS для PDRD там не настроен.

## Предлагаемая схема

```text
Браузер
  └─ HTTPS pdrd.itcneoterm.local:443
       └─ существующий frontend / Nginx
            └─ API Gateway в app-net
                 ├─ auth-service ─ LDAPS ─ Active Directory
                 ├─ user-service
                 └─ admin-service
```

TLS завершается в Nginx существующего frontend. Добавляется порт 443,
сертификат и закрытый ключ монтируются только в frontend для чтения.
Распределение ответственности сервисов, app-net, базы и общая инфраструктура
сохраняются. Текущие маршруты `/api/` и служебные заголовки остаются в том же
Nginx; `X-Real-IP` сохраняет адрес клиента, `X-Forwarded-Proto` принимает `https`.

Диагностический порт 8080 следует перенести на `127.0.0.1`. Проверки
`scripts/check-stack.sh` используют его для чтения готовности и состояния сессии.
Публичный вход, регистрация и передача пароля AD выполняются через HTTPS.
На HTTP-странице браузерный клиент прекращает отправку пароля до запроса.

Если дальнейшая проверка обнаружит существующий корпоративный HTTPS-прокси,
точку завершения TLS следует согласовать с его владельцем до настройки.
Текущий вывод сервера такого прокси не показывает.

## DNS и сертификат корпоративного CA

1. В корпоративном DNS создать A-запись
   `pdrd.itcneoterm.local → 192.168.55.3`.
2. Проверить разрешение имени на сервере и на рабочей станции.
   Суффикс `.local` требует убедиться, что клиент действительно использует
   корпоративный DNS для этого имени.
3. Выпустить отдельный сертификат веб-сервера:
   SAN `DNS:pdrd.itcneoterm.local`, назначение `Server Authentication`.
   Шаблон и выдачу подтверждает администратор корпоративного CA.
4. Доверенная цепочка CA должна быть установлена на рабочих станциях и Linux.
5. Подготовить PEM-файлы: `pdrd-fullchain.pem` (сертификат PDRD, затем
   промежуточные сертификаты) и `pdrd-tls.key` (его закрытый ключ).

Сертификат LDAPS контроллера не подходит для HTTPS-имени PDRD.
`ops/certificates/ad-ca.pem` остаётся публичной цепочкой доверия AD;
закрытый ключ веб-сервера предназначен только для frontend.
Файлы окружения в `ops/certificates/` уже исключены из Git.

Пример создания запроса сертификата на Linux; эта команда записывает ключ
в игнорируемый каталог и не отправляет его в CA:

```bash
umask 077
openssl req -new -newkey rsa:3072 -nodes -keyout ops/certificates/pdrd-tls.key -out ops/certificates/pdrd-tls.csr -subj "/CN=pdrd.itcneoterm.local" -addext "subjectAltName=DNS:pdrd.itcneoterm.local"
```

В CA передаётся `pdrd-tls.csr`. Итоговый сертификат нужно проверить:
SAN, срок действия, назначение веб-сервера, совпадение с ключом и цепочку доверия.

## Минимальные изменения после согласования схемы

В существующем блоке `server` файла `frontend/nginx.conf` добавить:

```nginx
listen 443 ssl;
server_name pdrd.itcneoterm.local;
ssl_certificate /run/pdrd-tls/fullchain.pem;
ssl_certificate_key /run/pdrd-tls/private.key;
ssl_protocols TLSv1.2 TLSv1.3;
```

Существующий `listen 80` сохраняется для закрытой диагностики. Значение
`server_name _` заменяется указанным именем. Все существующие locations,
таймауты, лимит загрузки и служебные заголовки сохраняются.
Перед включением TLS следует добавить запрет передавать изменяющие запросы
на HTTP-слушателе, сохранив диагностические GET-запросы.

Для frontend в Compose добавить публикацию `192.168.55.3:443:443` и
два подключения файлов только для чтения:

```yaml
volumes:
  - ./ops/certificates/pdrd-fullchain.pem:/run/pdrd-tls/fullchain.pem:ro
  - ./ops/certificates/pdrd-tls.key:/run/pdrd-tls/private.key:ro
```

В закрытом `.env` установить `FRONTEND_BIND_IP=127.0.0.1`.
Файлы сертификата должны существовать до запуска контейнера.
Перед пересозданием frontend требуется проверка `nginx -t`.

Эти изменения Nginx/Compose описаны для проверки и пока не применены в коде.
Обязательные параметры Auth/Gateway/email согласует готовый HTTPS-скрипт:

```bash
python3 scripts/configure_auth_origin.py https://pdrd.itcneoterm.local
```

Он задаёт три публичных адреса и `AUTH_SERVICE_HTTP__COOKIE_SECURE=true`.
Настройки stage/prod требуют HTTPS; CSRF и точный Origin обязательны.

## Приёмка после установки TLS

```bash
getent hosts pdrd.itcneoterm.local
openssl s_client -connect pdrd.itcneoterm.local:443 -servername pdrd.itcneoterm.local -verify_hostname pdrd.itcneoterm.local -verify_return_error </dev/null
curl --fail --show-error https://pdrd.itcneoterm.local/
```

Проверки выполняются с проверкой доверия сертификату.
Затем проверить через frontend: локальный admin, AD-вход, повторный вход
с тем же user_id, смену AD-пароля, роли, отзыв старой сессии и выход.
Командная приёмка также обращается к HTTPS:

```bash
python3 scripts/check_auth_runtime.py --transport-url https://pdrd.itcneoterm.local --public-origin https://pdrd.itcneoterm.local --login admin --expect-admin
python3 scripts/check_auth_runtime.py --transport-url https://pdrd.itcneoterm.local --public-origin https://pdrd.itcneoterm.local --login i.mein
```

Вторая команда предполагает, что i.mein ещё не получил административную роль.
После назначения этой роли проверка запускается с `--expect-admin`.

Основание для настроек TLS: [документация Nginx](https://nginx.org/en/docs/http/configuring_https_servers.html).
Выдача сертификата зависит от [шаблонов корпоративного CA](https://learn.microsoft.com/en-us/windows-server/identity/ad-cs/manage-certificate-templates).
