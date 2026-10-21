# frontend/Dockerfile

FROM nginx:1.27-alpine

# Обычный frontend никогда не открывает серверный Review-канал.
ENV PDRD_REVIEW_PROXY_KEY=""
# Ключ доверенного frontend proxy передаётся только серверным окружением.
ENV PDRD_FRONTEND_PROXY_KEY=""

COPY nginx.conf /etc/nginx/templates/default.conf.template

COPY src /usr/share/nginx/html

# Права рабочей копии не должны закрывать статику от непривилегированного nginx worker.
# Читаемость проверяется при сборке, включая модули, блокирующие запуск авторизации.
RUN find /usr/share/nginx/html -type d -exec chmod 755 {} + \
    && find /usr/share/nginx/html -type f -exec chmod 644 {} + \
    && chmod 644 /etc/nginx/templates/default.conf.template \
    && su -s /bin/sh nginx -c 'for asset in index.html js/app.js js/features/auth/session.js js/features/review/controller.js js/features/analysis/visualization.js css/blocks/review-resize.css; do test -r "/usr/share/nginx/html/$asset" || exit 1; done'

EXPOSE 80
