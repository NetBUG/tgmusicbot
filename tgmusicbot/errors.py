"""Typed domain errors.

The core never formats a message for a human: it raises one of these, and
``bot/`` turns ``error.code`` into a string via ``bot.texts``.  Keeping the
wording out of here is what makes localisation a one-module change.
"""


class TgMusicError(Exception):
    """Base class for every error the core raises deliberately."""

    code = "error.generic"

    @property
    def params(self) -> dict[str, object]:
        """Values a message template may interpolate."""
        return {}


class ConfigError(TgMusicError):
    code = "error.config"

    def __init__(self, variable: str, reason: str):
        super().__init__(f"{variable}: {reason}")
        self.variable = variable
        self.reason = reason

    @property
    def params(self) -> dict[str, object]:
        return {"variable": self.variable, "reason": self.reason}


class UnsafeName(TgMusicError):
    """A path component could not be made safe (empty, ``.``, ``..``)."""

    code = "error.unsafe_name"

    def __init__(self, value: object):
        super().__init__(repr(value))
        self.value = value

    @property
    def params(self) -> dict[str, object]:
        return {"value": self.value}


class MetadataMissing(TgMusicError):
    """A field required to place the file in the library is absent."""

    code = "error.metadata_missing"

    def __init__(self, field: str):
        super().__init__(field)
        self.field = field

    @property
    def params(self) -> dict[str, object]:
        return {"field": self.field}


class ArtistUnknown(MetadataMissing):
    code = "error.artist_unknown"

    def __init__(self) -> None:
        super().__init__("artist")


class AlbumUnknown(MetadataMissing):
    code = "error.album_unknown"

    def __init__(self) -> None:
        super().__init__("album")


class TitleUnknown(MetadataMissing):
    code = "error.title_unknown"

    def __init__(self) -> None:
        super().__init__("title")


class UnsupportedFormat(TgMusicError):
    code = "error.unsupported_format"

    def __init__(self, extension: str):
        super().__init__(extension)
        self.extension = extension

    @property
    def params(self) -> dict[str, object]:
        return {"extension": self.extension}


class PathNotFound(TgMusicError):
    code = "error.path_not_found"

    def __init__(self, path: str):
        super().__init__(path)
        self.path = path

    @property
    def params(self) -> dict[str, object]:
        return {"path": self.path}


class NoAudioFiles(PathNotFound):
    code = "error.no_audio_files"


class TooLarge(TgMusicError):
    code = "error.too_large"

    def __init__(self, size: int, limit: int):
        super().__init__(f"{size} > {limit}")
        self.size = size
        self.limit = limit

    @property
    def params(self) -> dict[str, object]:
        return {"size": self.size, "limit": self.limit}


class SourceUnavailable(TgMusicError):
    """A download source (YouTube, rutracker, qBittorrent) cannot be reached."""

    code = "error.source_unavailable"

    def __init__(self, source: str, reason: str = ""):
        super().__init__(f"{source}: {reason}" if reason else source)
        self.source = source
        self.reason = reason

    @property
    def params(self) -> dict[str, object]:
        return {"source": self.source, "reason": self.reason}


class JobUnknown(TgMusicError):
    """A callback referenced a job id that expired or never existed."""

    code = "error.job_unknown"

    def __init__(self, job_id: str):
        super().__init__(job_id)
        self.job_id = job_id

    @property
    def params(self) -> dict[str, object]:
        return {"job_id": self.job_id}


class NotAllowed(TgMusicError):
    code = "error.not_allowed"

    def __init__(self, user_id: int):
        super().__init__(str(user_id))
        self.user_id = user_id

    @property
    def params(self) -> dict[str, object]:
        return {"user_id": self.user_id}
