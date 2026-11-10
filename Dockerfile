FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY data/sample_articles.json ./data/sample_articles.json

# База лежит в /app/data — эту папку монтируем как том, чтобы данные переживали перезапуск
ENV DB_PATH=/app/data/app.db
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
