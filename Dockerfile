FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TOPIC_MODEL_PATH=/app/models/multilingual-minilm \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    DB_PATH=/app/data/app.db

# Сначала устанавливаем именно CPU-сборку Torch.
RUN python -m pip install --no-cache-dir \
    torch==2.15.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .

# Ограничение не позволит зависимостям заменить CPU Torch другой сборкой.
RUN echo "torch==2.15.0+cpu" > /tmp/torch-constraints.txt
RUN python -m pip install --no-cache-dir \
    -c /tmp/torch-constraints.txt \
    -r requirements.txt

# Проверяем зависимости и отсутствие поддержки CUDA в сборке.
RUN python -m pip check
RUN python -c "import torch; assert torch.version.cuda is None; print('CPU Torch:', torch.__version__)"

COPY app ./app
COPY scripts ./scripts
COPY data/sample_articles.json ./data/sample_articles.json

# Модель должна быть скачана на компьютере перед сборкой образа.
COPY models/multilingual-minilm ./models/multilingual-minilm

# Проверяем загрузку весов с диска уже при сборке.
RUN python -c "from app.matching.semantic import TopicMatcher; TopicMatcher().preload(); print('Локальная модель загружена')"

VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
