# 3.14 matches the runtime the test suite runs on. If that tag is unavailable,
# 3.13-slim works too — nothing here needs 3.14 specifically, and every
# dependency is pure Python (no wheels to compile).
FROM python:3.14-slim

LABEL org.opencontainers.image.title="tgmusicbot" \
      org.opencontainers.image.description="Telegram bot that files music into an artist/album/track library" \
      org.opencontainers.image.source="https://github.com/NetBUG/tgmusicbot" \
      org.opencontainers.image.licenses="MIT"

# ffmpeg is what makes the WebM/Opus -> Ogg remux possible. Without it the
# format selector quietly falls back to m4a, which works but never yields the
# original Opus stream. Alpine was dropped: it has no ffmpeg in the base image
# and musl buys nothing here, since no dependency is compiled.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    XDG_CACHE_HOME=/tmp/cache

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app.py ./
COPY tgmusicbot ./tgmusicbot

# Fail the build, not the first message: a missing ffmpeg or a dependency that
# did not install is worth finding out here.
RUN ffmpeg -version > /dev/null \
    && ffprobe -version > /dev/null \
    && python -c "import telebot, mutagen, requests, yt_dlp, loguru" \
    && python -c "from tgmusicbot.bot.handlers import BotApp; from tgmusicbot.config import Config" \
    && python -c "\
from tgmusicbot.sources.youtube import YouTubeSource, FORMAT_WITH_FFMPEG; \
assert YouTubeSource(ffmpeg_path='/usr/bin/ffmpeg').format_selector == FORMAT_WITH_FFMPEG"

# uid 1000 on purpose: the media NFS export squashes to anonuid=1000, so the
# bot's writes and the export agree on ownership.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin tgmusic
USER tgmusic

# telebot stops cleanly on KeyboardInterrupt; the default SIGTERM just kills it
# mid-download and leaves a staged file behind (swept on the next start).
STOPSIGNAL SIGINT

ENTRYPOINT ["python", "app.py"]
