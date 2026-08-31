"""Telegram wiring: I/O and formatting only.

Every long operation is handed to the worker pool, so polling never stalls
behind a download.  Every reply is rendered from an event through
``bot.texts`` — nothing below this package knows what a sentence looks like.
"""

from __future__ import annotations

import functools
import mimetypes
from collections.abc import Callable
from pathlib import Path

import telebot
from loguru import logger

from ..config import Config
from ..dlservice import DownloadService
from ..events import Done, Event, Failed, Progress, Question
from ..ingest import IngestService
from ..jobs import Job, JobRegistry, WorkerPool, parse_callback
from ..library import MediaLibrary
from ..naming import clean_artist
from ..sources.youtube import YouTubeSource, find_url
from ..tags import TrackTags
from ..tagservice import TagService
from . import render, texts
from .download import open_telegram_file

STAGE_DOWNLOAD = "stage.download"
STAGE_SEARCH = "stage.search"
STAGE_INSPECT = "stage.inspect"


class BotApp:
    def __init__(
        self,
        config: Config,
        *,
        library: MediaLibrary | None = None,
        registry: JobRegistry | None = None,
        clock: Callable[[], float] | None = None,
        source=None,
    ):
        self.config = config
        self.library = library or MediaLibrary(config.library_root)
        self.registry = registry or JobRegistry(ttl_s=config.job_ttl_s)
        self.service = IngestService(self.library, self.registry)
        self.tags = TagService(self.library, self.registry)
        self.downloads = DownloadService(
            self.library,
            self.registry,
            self.service,
            source
            or YouTubeSource(
                min_duration_s=config.min_duration_s,
                max_duration_s=config.max_duration_s,
                ffmpeg_path=config.ffmpeg_path,
                cookies_file=config.yt_cookies_file,
                player_clients=config.yt_player_clients,
            ),
            limit=config.search_limit,
        )
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
        markup = None
        if isinstance(event, Question):
            markup = render.keyboard(event)
            text = self._with_context(job, event, text)

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

    def _with_context(self, job: Job, event: Question, text: str) -> str:
        """A question about a list or a diff is useless without the list."""
        if event.key == "ask.tags.confirm" and job.payload.get("proposal"):
            return f"{render.proposal_table(job.payload['proposal'])}\n\n{text}"
        if event.key == "ask.dl.choose" and job.payload.get("candidates"):
            return f"{text}\n\n{render.candidate_list(job.payload['candidates'])}"
        return text

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

        @bot.message_handler(commands=["dl"])
        @self._allowed
        def on_dl(message):
            self._submit_dl(message)

        @bot.message_handler(commands=["tags"])
        @self._allowed
        def on_tags(message):
            self._submit_tags(message)

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
            # Telegram passes on whatever the file claims, "P!nk - Topic" included
            artist=clean_artist(getattr(media, "performer", None)),
            title=getattr(media, "title", None),
        )
        self.pool.submit(
            job,
            lambda j, emit: self._download_and_ingest(j, media, hint, emit),
        )

    def _download_and_ingest(self, job: Job, media, hint: TrackTags, emit):
        emit(Progress(job.id, STAGE_DOWNLOAD, 0, getattr(media, "file_size", None)))
        info = self.bot.get_file(media.file_id)
        filename = _filename_for(media, info.file_path)

        response, stream = open_telegram_file(
            self.config.token,
            info.file_path,
            lambda done, total: emit(Progress(job.id, STAGE_DOWNLOAD, done, total)),
            expected_size=getattr(media, "file_size", None),
        )
        try:
            return self.service.start(
                job,
                stream,
                filename,
                hint,
                max_bytes=self.config.max_upload_bytes,
            )
        finally:
            response.close()

    def _submit_dl(self, message) -> None:
        query = _argument(message.text)
        url = find_url(query)
        if url:
            self._submit_url(message, url)
            return
        if not query:
            self.bot.reply_to(message, texts.t("cmd.dl.usage"))
            return
        job = self.registry.create("dl", message.chat.id, message.from_user.id)
        self.pool.submit(job, lambda j, emit: self._search_and_ask(j, query, emit))

    def _submit_url(self, message, url: str) -> None:
        """A link on its own is a request, exactly like a sent file."""
        job = self.registry.create("dl", message.chat.id, message.from_user.id)
        self.pool.submit(job, lambda j, emit: self._inspect_and_ask(j, url, emit))

    def _search_and_ask(self, job: Job, query: str, emit):
        emit(Progress(job.id, STAGE_SEARCH))
        return self.downloads.start(job, query)

    def _inspect_and_ask(self, job: Job, url: str, emit):
        emit(Progress(job.id, STAGE_INSPECT))
        return self.downloads.start_url(job, url)

    def _submit_tags(self, message) -> None:
        path = _argument(message.text)
        if not path:
            self.bot.reply_to(message, texts.t("cmd.tags.usage"))
            return
        job = self.registry.create("tags", message.chat.id, message.from_user.id)
        self.pool.submit(job, lambda j, emit: self.tags.start(j, path))

    def _answer_callback(self, call) -> None:
        self.bot.answer_callback_query(call.id)
        try:
            callback = parse_callback(call.data)
            job, option = self.registry.answer(callback.job_id, callback.option or 0)
        except Exception:  # noqa: BLE001 — expired or malformed button
            self.bot.send_message(call.message.chat.id, texts.t("error.job_unknown"))
            return
        service = self._service_for(job)
        self.pool.submit(job, lambda j, emit: service.answer_option(j, option, emit))

    def _service_for(self, job: Job):
        if job.kind == "tags":
            return self.tags
        if job.kind == "dl":
            return self.downloads
        return self.service

    def _answer_text(self, message) -> None:
        answer = (message.text or "").strip()

        # a link is unmistakably a new request, even mid-dialogue
        url = find_url(answer)
        if url:
            self._submit_url(message, url)
            return

        pending = self.registry.waiting_for_text(message.chat.id)
        if pending is None or not answer:
            self.bot.reply_to(message, texts.t("cmd.help"))
            return
        # clears the question first, so a second message cannot answer it twice
        job = self.registry.answer_text(pending.id, answer)
        service = self._service_for(job)
        self.pool.submit(job, lambda j, emit: service.resume(j, answer, emit))


def _argument(text: str | None) -> str:
    """Everything after the command word."""
    _, _, argument = (text or "").partition(" ")
    return argument.strip()


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
