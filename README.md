# tgmusicbot

A Telegram bot that files music into a local collection laid out as
`<artist>/<album>/<track>` — the layout Navidrome and Jellyfin expect.

Send it an audio file; it reads the file's own tags, falls back to the
Telegram metadata and then to the filename, and asks about anything still
missing. Nothing is written until the destination is known.

## Installation

Invoke `./run.sh` for an automatic Docker install.
Prerequisites: Docker with Compose and Buildkit.
NB: there is a nice [script](https://gist.github.com/jniltinho/bcb28a99aef33dcb5f35c297bf71e4ae)
for installing Buildkit on Debian systems.

Copy `.env.example` to `.env` and fill it in — `.env` is gitignored, so a real
token never reaches the repository. Adjust the bind mount in
`docker-compose.yml` to point at your collection.

Running without Docker:

```sh
pip install -r requirements.txt
python app.py
```

## Configuration

All configuration is environment variables, read once at startup
(`tgmusicbot/config.py`).

| Variable | Default | Meaning |
|---|---|---|
| `TG_TOKEN` | — | Bot token from @BotFather. Required. |
| `OUTPUT_FOLDER` | — | Library root. Required. |
| `TG_ALLOWED_USERS` | — | Comma-separated numeric user ids. **Required**: without it anyone who finds the bot can write to the collection. |
| `LOG_PATH` | unset | Log file; console only when unset. |
| `WORKERS` | `2` | Worker threads. Downloads never block polling. |
| `MAX_UPLOAD_MB` | `200` | Rejected above this size. |
| `PROGRESS_INTERVAL_S` | `3` | Minimum gap between progress edits (Telegram rate-limits). |
| `JOB_TTL_S` | `3600` | How long an unanswered question — and its staged bytes — survives. |

## Commands

| Command | Effect |
|---|---|
| *(send a file)* | Filed into `<artist>/<album>/<NN - title>.<ext>` |
| `/start`, `/help` | What the bot understands |
| `/status` | Library root, live jobs, worker count |

Coming: `/tags` (tag repair), `/dl` (YouTube), magnet links, rutracker search —
see `runs/2026-08-05_feature-plan.md`.

## Library layout

```
<OUTPUT_FOLDER>/
  Pink Floyd/
    Meddle/
      01 - One of These Days.flac
  .incoming/          <- staging; a scanner never sees a half-written file
```

Ingest is two-step: bytes are streamed into `.incoming` while being hashed,
then moved into place with `os.replace`. A file already present byte for byte
is reported as a duplicate and **not** rewritten; a name clash with different
content becomes `… (2).ext`.

Path components are sanitised, never trusted: `AC/DC` becomes `AC-DC`, and a
component that is only traversal (`..`) is rejected outright.

## Development

```sh
pip install -r requirements-dev.txt
pytest
```

Layout — the core knows nothing about Telegram, and nothing outside `bot/`
contains a user-facing string:

| Module | Responsibility |
|---|---|
| `config.py` | Frozen `Config.from_env()` |
| `naming.py` | Sanitising, `NN - Artist - Title` parsing. Pure functions |
| `tags.py` | Format-agnostic tag reading (ID3, Vorbis, MP4) |
| `library.py` | The only module that touches the collection on disk |
| `ingest.py` | The "a file arrived" flow, as events |
| `jobs.py` | Short job ids for `callback_data`, worker pool |
| `errors.py` / `events.py` | Typed errors and structured events — no prose |
| `bot/texts.py` | Every user-facing string, in one dict |
| `bot/render.py` | Events → HTML, progress throttling |
| `bot/handlers.py` | Telegram wiring only |

`bot/texts.py` is a plain dict rather than gettext: the bot is allowlisted to a
couple of people, so a second language is not needed yet. The boundary that
makes adding one cheap — core returns events and typed errors, never text — is
enforced by a test.
