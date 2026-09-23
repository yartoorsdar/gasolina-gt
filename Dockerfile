FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collector/ ./collector/
COPY data/ ./data/
COPY web/ ./web/
COPY serve.py .
COPY config.json .

# Expose the port for the dashboard
EXPOSE 8089

# Start the server (serves static files + runs collectors)
CMD ["python", "serve.py"]
