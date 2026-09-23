FROM python:3.14-slim AS collector

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collector/ ./collector/
COPY config.json .
COPY cron.py .

# ──────────────────────────────────────────────
FROM nginx:alpine AS web

# Copiar los archivos estáticos del dashboard
COPY web/ /usr/share/nginx/html/web/

# Configurar Nginx para servir el dashboard
RUN echo 'server { \
    listen 80; \
    server_name localhost; \
    root /usr/share/nginx/html; \
    index web/index.html; \
    location / { \
        try_files $uri $uri/ /web/index.html; \
    } \
}' > /etc/nginx/conf.d/default.conf

# ──────────────────────────────────────────────
FROM python:3.14-slim AS final

WORKDIR /app

COPY --from=collector /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=web /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf
COPY --from=web /usr/share/nginx/html/web /app/web

# Instalar nginx y supervisor
RUN apk add --no-cache nginx supervisor

# Copiar archivos Python del collector
COPY requirements.txt .
COPY collector/ ./collector/
COPY config.json .
COPY cron.py .
COPY serve.py .

# Configurar supervisor para correr Nginx + Python server
RUN mkdir -p /var/log/supervisor

COPY <<'EOF' /etc/supervisor/conf.d/supervisord.conf
[supervisord]
nodaemon=true
logfile=/dev/null
logfile_maxbytes=0

[program:nginx]
command=nginx -g "daemon off;"
autostart=true
autorestart=true
stderr_logfile=/dev/fd/2
stdout_logfile=/dev/fd/1

[program:python-server]
command=python serve.py 8089
autostart=true
autorestart=true
priority=10
stderr_logfile=/dev/fd/2
stdout_logfile=/dev/fd/1
EOF

EXPOSE 8089

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
