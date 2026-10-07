FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHECKIN_DATA_DIR=/data \
    TZ=Asia/Shanghai \
    CHECKIN_USER=admin \
    CHECKIN_PASSWORD=changeme

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

RUN mkdir -p /data

EXPOSE 4567

VOLUME ["/data"]

CMD ["uvicorn", "checkin.web.app:app", "--host", "0.0.0.0", "--port", "4567"]
