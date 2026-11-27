# Multi-Stage Production Dockerfile for MyCA Backend API
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final Runtime Image
FROM python:3.12-slim AS runner

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged application user & persistent storage directory
RUN groupadd -g 1001 myca && \
    useradd -u 1001 -g myca -m -s /bin/bash myca && \
    mkdir -p /app/storage && \
    chown -R myca:myca /app

# Copy dependencies from builder
COPY --from=builder /root/.local /home/myca/.local
ENV PATH=/home/myca/.local/bin:$PATH
ENV PYTHONPATH=/app

# Copy application and migration code
COPY --chown=myca:myca . /app

USER myca

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
