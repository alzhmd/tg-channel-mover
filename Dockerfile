FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/app/data/chanmover.db \
    USER_SESSION=/app/data/user

RUN useradd --create-home --uid 10001 mover
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY chanmover ./chanmover
RUN mkdir -p /app/data && chown -R mover:mover /app
USER mover

# the SQLite database and the linked account's session live here: keep it on a volume
VOLUME /app/data

CMD ["python", "-m", "chanmover"]
