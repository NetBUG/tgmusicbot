"""Telegram wiring: I/O and formatting only.

Every long operation is handed to the worker pool, so polling never stalls
behind a download.  Every reply is rendered from an event through
``bot.texts`` — nothing below this package knows what a sentence looks like.
"""

from __future__ import annotations

import functools
import io
import mimetypes
from collections.abc import Callable
from pathlib import Path

import telebot
from loguru import logger

from ..config import Config
from ..events import Done, Event, Failed, Progress, Question
from ..ingest import IngestService
from ..jobs import Job, JobRegistry, WorkerPool, parse_callback
from ..library import MediaLibrary
from ..tags import TrackTags
from . import render, texts


class BotApp:
    def __init__(
        self,
        config: Config,
        *,
        library: MediaLibrary | None = None,
        registry: JobRegistry | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.config = config
        self.library = library or MediaLibrary(config.library_root)
        self.registry = registry or JobRegistry(ttl_s=config.job_ttl_s)
        self.service = IngestService(self.library, self.registry)
        self.bot = telebot.TeleBot(config.token, parse_mode="HTML")
        self.throttle = render.ProgressThrottle(
            config.progress_interval_s,
            **({"clock": clock} if clock else {}),
        )
        self.pool = WorkerPool(
            self.registry, self._on_event, workers=config.workers
        )
        self._register()

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        self.library.sweep_incoming(self.config.job_ttl_s)
        self.pool.start()
        logger.info(
            "polling; library={} workers={}",
            self.library.root,
            self.config.workers,
        )
        self.bot.infinity_polling()

    # -- event rendering ---------------------------------------------------

    def _on_event(self, event: Event) -> None:
        try:
            job = self.registry.get(event.job_id)
        except Exception:  # noqa: BLE001 — purged job: nothing to draw on
            return

        if isinstance(event, Progress) and not self.throttle.should_send(job.id):
            return

        text = render.render(event)
        markup = render.keyboard(event) if isinstance(event, Question) else None

        if isinstance(event, (Done, Failed)):
            self.throttle.forget(job.id)

        if job.message_id is None:
            sent = self.bot.send_message(job.chat_id, text, reply_markup=markup)
            job.message_id = sent.message_id
            return
        try:
            self.bot.edit_message_text(
                text, job.chat_id, job.message_id, reply_markup=markup
            )
        except Exception as error:  # noqa: BLE001 — "message is not modified" etc.
            logger.debug("edit failed ({}), sending a new message", error)
            sent = self.bot.send_message(job.chat_id, text, reply_markup=markup)
            job.message_id = sent.message_id

    # -- handlers ----------------------------------------------------------

    def _allowed(self, handler):
        @functools.wraps(handler)
        def wrapper(message_or_call):
            user = getattr(message_or_call, "from_user", None)
            if user is None or user.id not in self.config.allowed_users:
                logger.warning("rejected user {}", getattr(user, "id", None))
                return None
            return handler(message_or_call)

        return wrapper

    def _register(self) -> None:
        bot = self.bot

        @bot.message_handler(commands=["start"])
        @self._allowed
        def on_start(message):
            bot.reply_to(
                message,
                texts.t("cmd.start", root=render.escape(self.library.root)),
            )

        @bot.message_handler(commands=["help"])
        @self._allowed
        def on_help(message):
            bot.reply_to(message, texts.t("cmd.help"))

        @bot.message_handler(commands=["status"])
        @self._allowed
        def on_status(message):
            bot.reply_to(
                message,
                texts.t(
                    "cmd.status",
                    root=render.escape(self.library.root),
                    jobs=len(self.registry),
                    workers=self.config.workers,
                ),
            )

        @bot.message_handler(content_types=["audio", "document", "voice"])
        @self._allowed
        def on_file(message):
            self._submit_file(message)

        @bot.callback_query_handler(func=lambda call: True)
        @self._allowed
        def on_callback(call):
            self._answer_callback(call)

        @bot.message_handler(func=lambda message: True)
        @self._allowed
        def on_text(message):
            self._answer_text(message)

    # -- flows -------------------------------------------------------------

    def _submit_file(self, message) -> None:
        media = message.audio or message.document or message.voice
        if media is None:
            self.bot.reply_to(message, texts.t("error.unsupported_format", extension=""))
            return

        job = self.registry.create(
            "ingest", message.chat.id, message.from_user.id, file_id=media.file_id
        )
        hint = TrackTags(
            artist=getattr(media, "performer", None),
            title=getattr(media, "title", None),
        )
        self.pool.submit(
            job,
            lambda j, emit: self._download_and_ingest(j, media, hint, emit),
        )

    def _download_and_ingest(self, job: Job, media, hint: TrackTags, emit):
        emit(Progress(job.id, "download"))
        info = self.bot.get_file(media.file_id)
        payload = self.bot.download_file(info.file_path)
        filename = _filename_for(media, info.file_path)
        emit(Progress(job.id, "store"))
        return self.service.start(
            job,
            io.BytesIO(payload),
            filename,
            hint,
            max_bytes=self.config.max_upload_bytes,
        )

    def _answer_callback(self, call) -> None:
        self.bot.answer_callback_query(call.id)
        try:
            callback = parse_callback(call.data)
            job, option = self.registry.answer(callback.job_id, callback.option or 0)
        except Exception:  # noqa: BLE001 — expired or malformed button
            self.bot.send_message(call.message.chat.id, texts.t("error.job_unknown"))
            return
        self.pool.submit(job, lambda j, emit: self.service.answer_option(j, option))

    def _answer_text(self, message) -> None:
        pending = self.registry.waiting_for_text(message.chat.id)
        answer = (message.text or "").strip()
        if pending is None or not answer:
            self.bot.reply_to(message, texts.t("cmd.help"))
            return
        # clears the question first, so a second message cannot answer it twice
        job = self.registry.answer_text(pending.id, answer)
        self.pool.submit(job, lambda j, emit: self.service.resume(j, answer))


def _filename_for(media, telegram_path: str) -> str:
    """Telegram often omits ``file_name``; rebuild something parseable."""
    name = getattr(media, "file_name", None)
    if name:
        return name
    stem = " - ".join(
        part
        for part in (getattr(media, "performer", None), getattr(media, "title", None))
        if part
    )
    extension = Path(telegram_path or "").suffix.lower()
    if not extension:
        extension = mimetypes.guess_extension(getattr(media, "mime_type", "") or "") or ""
    return (stem or "audio") + extension


def build_app(config: Config) -> BotApp:
    return BotApp(config)
