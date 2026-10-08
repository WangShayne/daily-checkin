FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHECKIN_DATA_DIR=/data \
    TZ=Asia/Shanghai \
    CHECKIN_USER=admin \
    CHECKIN_PASSWORD=changeme \
    CHECKIN_DISPLAY=:99 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    procps \
    psmisc \
    xvfb \
    x11vnc \
    novnc \
    fonts-liberation \
    fonts-noto-cjk \
    ca-certificates \
    curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e . \
    && playwright install --with-deps chromium

RUN mkdir -p /data

EXPOSE 4567

VOLUME ["/data"]

# shm helpful for Chromium; also set in compose
CMD ["uvicorn", "checkin.web.app:app", "--host", "0.0.0.0", "--port", "4567"]
