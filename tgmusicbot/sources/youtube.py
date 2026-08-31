"""YouTube via yt-dlp, used as a library rather than a subprocess.

Audio is never re-encoded. With ffmpeg available the WebM/Opus stream is
*remuxed* into an Ogg container (`webm>ogg`, a conditional remux, so an m4a
download is left alone) — a container change costs nothing and music clients
recognise `.ogg` where they mishandle `.webm`. Without ffmpeg the format
selector simply refuses WebM and takes m4a instead, because a `.webm` file has
no business being in the library.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from ..errors import SourceUnavailable
from ..tags import AUDIO_EXTENSIONS
from .base import Candidate, Fetched, ProgressCallback

NAME = "youtube"

MIN_DURATION_S = 30
MAX_DURATION_S = 20 * 60

# With ffmpeg: take the best audio whatever it is, remux WebM into Ogg after.
FORMAT_WITH_FFMPEG = "bestaudio/best"
# Without: only containers the library accepts as-is.
FORMAT_NO_FFMPEG = (
    "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio[ext=ogg]/"
    "bestaudio[ext=opus]/bestaudio[ext=flac]"
)
REMUX = "webm>ogg"

_LIVE_STATUSES = frozenset({"is_live", "is_upcoming", "post_live"})
_LIVE_WORDS = ("live", "concert", "tour", "лайв", "концерт")
_COVER_WORDS = ("cover", "karaoke", "remix", "кавер", "караоке")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


class YouTubeSource:
    name = NAME

    def __init__(
        self,
        *,
        min_duration_s: int = MIN_DURATION_S,
        max_duration_s: int = MAX_DURATION_S,
        ffmpeg_path: str | None = None,
        cookies_file: str | None = None,
        player_clients: tuple[str, ...] = (),
        ydl_factory: Callable[[dict], object] | None = None,
    ):
        self._min_duration_s = min_duration_s
        self._max_duration_s = max_duration_s
        self._ffmpeg_path = ffmpeg_path
        self._cookies_file = cookies_file
        self._player_clients = player_clients
        self._ydl_factory = ydl_factory or _default_factory

    def _options(self, **extra) -> dict:
        """Options every call shares.

        ``cookies_file`` and ``player_clients`` exist because YouTube answers
        datacentre IPs with "Sign in to confirm you're not a bot" — exported
        cookies are the documented fix, and a different player client sometimes
        sidesteps it. Both default to off, i.e. to yt-dlp's own behaviour.
        """
        options: dict = {
            "quiet": True,
            "no_warnings": True,
            # `quiet` does not cover the download bar — it goes to stdout separately
            "noprogress": True,
            "noplaylist": True,
        }
        if self._cookies_file:
            options["cookiefile"] = self._cookies_file
        if self._player_clients:
            options["extractor_args"] = {
                "youtube": {"player_client": list(self._player_clients)}
            }
        return options | extra

    # -- search ------------------------------------------------------------

    def search(self, query: str, limit: int = 5) -> list[Candidate]:
        """Ask for more than we need, because filtering throws results away."""
        options = self._options(skip_download=True, extract_flat="in_playlist")
        entries = self._extract(options, f"ytsearch{limit * 3}:{query}")
        allowed = _Intent(query)

        candidates: list[Candidate] = []
        for entry in entries:
            candidate = _to_candidate(entry)
            if candidate is None or not self._acceptable(candidate, entry, allowed):
                continue
            candidates.append(candidate)
            if len(candidates) == limit:
                break
        return candidates

    def _acceptable(self, candidate: Candidate, entry: dict, intent: "_Intent") -> bool:
        if entry.get("live_status") in _LIVE_STATUSES and not intent.wants_live:
            return False
        if candidate.duration_s is not None:
            if candidate.duration_s < self._min_duration_s:
                return False
            if candidate.duration_s > self._max_duration_s and not intent.wants_live:
                return False
        if intent.rejects(candidate.title):
            return False
        return True

    def inspect(self, url: str) -> Candidate:
        """Metadata for one video, without downloading it.

        Resolving the format here is deliberate: if no acceptable audio exists
        the user finds out before anything is downloaded, and the extension is
        known in advance so the proposed path can be shown in full.
        """
        options = self._options(skip_download=True, format=self.format_selector)
        entries = self._extract(options, url)
        if not entries:
            raise SourceUnavailable(self.name, "no video at that link")
        candidate = _to_candidate(entries[0])
        if candidate is None:
            raise SourceUnavailable(self.name, "no video at that link")
        return candidate

    # -- fetch -------------------------------------------------------------

    def fetch(
        self,
        candidate: Candidate,
        destination: Path,
        on_progress: ProgressCallback | None = None,
    ) -> Fetched:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)

        options = self._options(
            format=self.format_selector,
            outtmpl=str(destination / "%(id)s.%(ext)s"),
            retries=3,
        )
        if on_progress is not None:
            options["progress_hooks"] = [_hook(on_progress)]
        if self._ffmpeg_path:
            options["ffmpeg_location"] = self._ffmpeg_path
            options["postprocessors"] = [
                {"key": "FFmpegVideoRemuxer", "preferedformat": REMUX}
            ]

        self._extract(options, candidate.url, download=True)

        path = _downloaded_file(destination, candidate.id)
        if path is None:
            raise SourceUnavailable(self.name, "nothing was downloaded")
        return Fetched(path=path, candidate=candidate)

    @property
    def format_selector(self) -> str:
        return FORMAT_WITH_FFMPEG if self._ffmpeg_path else FORMAT_NO_FFMPEG

    # -- internals ---------------------------------------------------------

    def _extract(self, options: dict, target: str, *, download: bool = False):
        try:
            with self._ydl_factory(options) as ydl:  # type: ignore[attr-defined]
                info = ydl.extract_info(target, download=download)
        except SourceUnavailable:
            raise
        except Exception as error:  # noqa: BLE001 — yt-dlp raises a whole zoo
            raise SourceUnavailable(self.name, _reason(error)) from error
        if info is None:
            return []
        return info.get("entries") or [info]


class _Intent:
    """What the user's wording says they will accept."""

    def __init__(self, query: str):
        words = {word.lower() for word in _WORD.findall(query)}
        self.wants_live = bool(words & set(_LIVE_WORDS))
        self._allowed = words

    def rejects(self, title: str) -> bool:
        lowered = title.lower()
        return any(
            word in lowered and word not in self._allowed for word in _COVER_WORDS
        )


def _to_candidate(entry: dict) -> Candidate | None:
    video_id = entry.get("id")
    title = entry.get("title")
    if not video_id or not title:
        return None
    duration = entry.get("duration")
    extension = entry.get("ext")
    return Candidate(
        id=str(video_id),
        title=str(title),
        url=entry.get("webpage_url") or entry.get("url") or f"https://youtu.be/{video_id}",
        source=NAME,
        uploader=entry.get("uploader") or entry.get("channel"),
        duration_s=int(duration) if isinstance(duration, (int, float)) else None,
        extension=f".{extension}" if extension else None,
    )


_URL = re.compile(
    r"""https?://
        (?:www\.|m\.|music\.)?
        (?:
            youtube\.com/(?:watch\?(?:[^\s]*&)?v=|shorts/|live/|embed/|v/)
          | youtu\.be/
        )
        (?P<id>[\w-]{11})""",
    re.IGNORECASE | re.VERBOSE,
)


def find_url(text: str | None) -> str | None:
    """The canonical watch URL for the first YouTube link in ``text``.

    Normalising to ``watch?v=`` drops playlist and timestamp parameters, which
    would otherwise make yt-dlp fetch a whole playlist instead of one track.
    """
    if not text:
        return None
    match = _URL.search(text)
    if match is None:
        return None
    return f"https://www.youtube.com/watch?v={match.group('id')}"


def _hook(on_progress: ProgressCallback):
    def hook(status: dict) -> None:
        if status.get("status") != "downloading":
            return
        done = status.get("downloaded_bytes") or 0
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        on_progress(int(done), int(total) if total else None)

    return hook


def _downloaded_file(destination: Path, video_id: str) -> Path | None:
    """The extension is only known after the (optional) remux."""
    matches = [
        path
        for path in destination.glob(f"{video_id}.*")
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_size)


def _default_factory(options: dict):
    from yt_dlp import YoutubeDL

    return YoutubeDL(options)


def _reason(error: Exception) -> str:
    text = str(error).replace("ERROR: ", "")
    return text.split("\n", 1)[0][:160] or type(error).__name__
