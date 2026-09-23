FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collector/ ./collector/
COPY config.json .
COPY cron.py .
COPY serve.py .
COPY web/ ./web/

EXPOSE 8089

CMD ["python", "-u", "serve.py"]
