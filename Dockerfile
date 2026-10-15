FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg — чтобы читать радиопоток, libopus — чтобы кодировать звук для Discord
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot ./bot
COPY playlists ./playlists

# Отдельный пользователь без прав администратора; в /app/data бот хранит настройки
RUN useradd --system --uid 10001 radio \
    && mkdir -p /app/data \
    && chown 10001 /app/data
USER radio

CMD ["python", "-m", "bot"]
