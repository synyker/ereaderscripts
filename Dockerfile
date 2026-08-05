FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

# supercronic runs cron jobs as an unprivileged foreground process, which is
# what a container wants; system cron needs a daemon and swallows stdout.
ARG SUPERCRONIC_VERSION=v0.2.29
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64 /usr/local/bin/supercronic
RUN chmod +x /usr/local/bin/supercronic

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client rsync \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY crontab /app/crontab

ENTRYPOINT ["python", "/app/fetch_news.py"]
CMD []
