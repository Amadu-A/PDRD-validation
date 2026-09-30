# frontend/Dockerfile

FROM nginx:1.27-alpine

# Обычный frontend никогда не открывает серверный Review-канал.
ENV PDRD_REVIEW_PROXY_KEY=""

COPY nginx.conf /etc/nginx/templates/default.conf.template

COPY src /usr/share/nginx/html

EXPOSE 80
