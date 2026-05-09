FROM python:3.11-slim

# ── System deps: Chromium + ChromeDriver for Selenium ──────────────────────
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python env vars ────────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# ── Python deps (production only) ──────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Project files ──────────────────────────────────────────────────────────
COPY . .

RUN mkdir -p data/bronze data/silver data/gold logs

CMD ["python", "src/pipeline.py"]