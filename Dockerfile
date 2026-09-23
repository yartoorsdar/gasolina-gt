FROM python:3.14-slim

WORKDIR /app

# Instalar dependencias del sistema para Playwright Chromium
RUN apt-get update && apt-get install -y \
    --no-install-recommends \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar Chromium para Playwright (headless)
RUN playwright install chromium

COPY collector/ ./collector/
COPY config.json .
COPY cron.py .
COPY serve.py .
COPY web/ ./web/

EXPOSE 8089

CMD ["python", "-u", "serve.py"]
