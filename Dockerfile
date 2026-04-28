FROM python:3.12-slim

# Dependências do sistema para Playwright + Tesseract + OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-por \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependências Python primeiro (cache de layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instala Playwright + Chromium (usado pelo DeepSeek browser e FastDL fallback)
RUN playwright install chromium --with-deps

# Copia o código
COPY . .

# Cria diretório de dados persistentes
RUN mkdir -p /data/downloads /data/logs

# Variáveis de ambiente padrão (sobrescritas pelo docker-compose por cliente)
ENV BOT_DATA_DIR=/data
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["python", "panel.py"]
