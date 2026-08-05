"""Configuration, read once from the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

DEFAULT_WORKERS = 2
DEFAULT_MAX_UPLOAD_MB = 200
DEFAULT_PROGRESS_INTERVAL_S = 3.0


@dataclass(frozen=True, slots=True)
class Config:
    token: str
    library_root: Path
    allowed_users: frozenset[int]
    log_path: Path | None = None
    workers: int = DEFAULT_WORKERS
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_MB * 1024 * 1024
    progress_interval_s: float = DEFAULT_PROGRESS_INTERVAL_S
    job_ttl_s: float = 3600.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env

        token = _require(env, "TG_TOKEN").strip()
        if token.startswith("%") or ":" not in token:
            raise ConfigError("TG_TOKEN", "does not look like a bot token")

        root = Path(_require(env, "OUTPUT_FOLDER")).expanduser()

        allowed = _parse_user_ids(env.get("TG_ALLOWED_USERS", ""))
        if not allowed:
            raise ConfigError(
                "TG_ALLOWED_USERS",
                "must list at least one numeric Telegram user id",
            )

        log_raw = env.get("LOG_PATH", "").strip()
        return cls(
            token=token,
            library_root=root,
            allowed_users=allowed,
            log_path=Path(log_raw).expanduser() if log_raw else None,
            workers=_positive_int(env, "WORKERS", DEFAULT_WORKERS),
            max_upload_bytes=_positive_int(env, "MAX_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB)
            * 1024
            * 1024,
            progress_interval_s=_positive_float(
                env, "PROGRESS_INTERVAL_S", DEFAULT_PROGRESS_INTERVAL_S
            ),
            job_ttl_s=_positive_float(env, "JOB_TTL_S", 3600.0),
        )


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise ConfigError(name, "is not set")
    return value


def _parse_user_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError:
            raise ConfigError("TG_ALLOWED_USERS", f"{chunk!r} is not a number") from None
    return frozenset(ids)


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(name, f"{raw!r} is not an integer") from None
    if value <= 0:
        raise ConfigError(name, "must be positive")
    return value


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(name, f"{raw!r} is not a number") from None
    if value <= 0:
        raise ConfigError(name, "must be positive")
    return value
