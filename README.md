# tgmusicbot

A Telegram bot that files music into a local collection laid out as
`<artist>/<album>/<track>` — the layout Navidrome and Jellyfin expect.

Send it an audio file; it reads the file's own tags, falls back to the
Telegram metadata and then to the filename, and asks about anything still
missing. Nothing is written until the destination is known.

## Installation

Invoke `./run.sh`: it creates the log directory, builds and starts the
container. Prerequisites: Docker with Compose v2, or podman-compose — the
script picks whichever is installed. Buildkit is no longer required (the cache
mount it needed is gone).

**Rootless podman:** set `USERNS_MODE=keep-id` in `.env`. Rootless podman maps
container uid 1000 to a *subuid* on the host, so a bind mount owned by host uid
1000 is not writable without it — the symptom is
`Permission denied: '/media/….part'`. Under Docker container uid 1000 *is* host
uid 1000, so leave it empty.

Without any compose implementation, the equivalent of the compose file is:

```sh
podman build -t tgmusicbot:latest .
podman run -d --name tgmusicbot --userns=keep-id --init \
  --env-file .env -e OUTPUT_FOLDER=/media \
  -e LOG_PATH=/var/log/tgmusicbot/tgmusicbot.log \
  -v /home/netbug/media:/media:z \
  -v ./logs:/var/log/tgmusicbot:z \
  tgmusicbot:latest
```

Copy `.env.example` to `.env` and fill it in — `.env` is gitignored, so a real
token never reaches the repository. Point `LIBRARY_PATH` at your collection;
compose bind-mounts it at `/media` and overrides `OUTPUT_FOLDER` accordingly, so
the same `.env` works for a container and for a direct run.

The image is `python:3.14-slim` plus **ffmpeg**, which is what enables the
WebM/Opus → Ogg remux; without it the bot falls back to m4a. It runs as uid
1000 to match the `anonuid=1000` squash on the media NFS export.

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
| `SEARCH_LIMIT` | `5` | How many YouTube results to offer. |
| `MIN_DURATION_S` | `30` | Shorter results are dropped. |
| `MAX_DURATION_S` | `1200` | Longer results are dropped unless the query asks for live. |
| `FFMPEG_PATH` | auto | Found on `PATH` when unset. Enables the WebM → Ogg remux. |
| `YT_COOKIES_FILE` | unset | Netscape cookie jar; see below. |
| `YT_PLAYER_CLIENTS` | unset | Comma-separated yt-dlp extractor clients, e.g. `web_safari,ios`. |
| `LIBRARY_PATH` | `/home/netbug/media` | Host path compose mounts at `/media`. Not read by the bot. |
| `LOG_DIR` | `/var/log/tgmusicbot` | Host path compose mounts for logs. Not read by the bot. |

## Commands

| Command | Effect |
|---|---|
| *(send a file)* | Filed into `<artist>/<album>/<NN - title>.<ext>` |
| *(send a YouTube link)* | Read the title, propose a path, ask before saving |
| `/dl Artist - Album - Title` | Search YouTube, pick a result, file the audio |
| `/tags <path>` | Repair the tags of an album or a whole artist |
| `/start`, `/help` | What the bot understands |
| `/status` | Library root, live jobs, worker count |

Coming: magnet links, rutracker search — see `runs/2026-08-05_feature-plan.md`.

## Downloading from YouTube

`/dl Pink Floyd - Meddle - One of These Days` searches, shows up to five
results with channel and duration, and files whichever you pick. Two parts
(`/dl Pink Floyd - Time`) also work — the album becomes a question.

The metadata comes from what you typed, never from the video title: channel
names and YouTube titles are far too unreliable to file a library by. Since a
YouTube download carries no tags at all, the tags are written into the file
after it lands — a player reads tags, not paths.

### Sending a link

A YouTube link on its own is treated exactly like a sent file — no command
needed. The bot reads the video title, works out where the track would go and
asks first:

```
МакSим - Знаешь ли ты
Maksim

Save the audio as:
МакSим/Singles/Знаешь ли ты.m4a ?

[ Yes, save ] [ Specify path ] [ Cancel ]
```

Nothing is downloaded until you agree. **Specify path** (or simply replying
with text) accepts either form:

| Reply | Result |
|---|---|
| `Artist/Album/Title` | all three replaced |
| `Artist/Album` | title kept from the video |
| `Artist - Album - Title` | same as the slash form |
| `Meddle` | taken as the album — the field a proposal gets wrong most often |

A title of the form `Artist - Album - Track` is used as-is; `Artist - Track`
leaves the album unknown, so `Singles` is proposed. When the title has no
artist at all, the channel name is used — for a "… - Topic" auto-upload that is
exactly the artist. Upload decoration is stripped in both languages:
`(Official Audio)`, `[4K]`, `(официальный клип)`, `(премьера клипа, 2002)`.

Links are normalised to `watch?v=<id>`, which drops `list=` and `t=`
parameters — otherwise yt-dlp would happily fetch an entire playlist.

Filtered out: live streams, anything under 30 s or over 20 min, and
cover/karaoke/remix versions — unless your query asks for them (`/dl … live`
keeps live results).

**Audio is never re-encoded.** With ffmpeg present the WebM/Opus stream is
remuxed into an Ogg container (`webm>ogg` — a conditional remux, so an m4a
download is left untouched). Without ffmpeg the format selector simply refuses
WebM and takes m4a, because a `.webm` file has no business being in a music
library.

### When YouTube says "confirm you're not a bot"

Datacentre and VPS addresses get this sooner or later, and it applies to the
whole IP, not to one video. Two knobs, both off by default:

* `YT_COOKIES_FILE` — a Netscape cookie jar exported from a logged-in browser.
  This is yt-dlp's documented fix and the one that actually works.
* `YT_PLAYER_CLIENTS` — e.g. `web_safari,ios`. Different extractor clients are
  gated differently, so one of them sometimes still answers.

The failure surfaces as a plain message (`youtube is unreachable: Sign in to
confirm…`), not a traceback — nothing is written and no partial file is left.

## Tag repair

`/tags Pink Floyd/Meddle` (or `/tags Pink Floyd` for every album at once) shows
a before → after diff and four buttons. **Nothing is written until you press
Apply** — the proposal is the dry run.

| Button | Who is the source of truth |
|---|---|
| Apply | accept what is shown |
| From directory name | the folders decide artist and album |
| From file names | the filenames decide track number, artist and title |
| Cancel | nothing happens |

Text repair covers the two mistakes that account for nearly all the damage in
an old collection: cp1251 bytes read as latin-1 (`Пинк` → `Ïèíê`) and utf-8
bytes read as cp1251 (`РџРёРЅРє`). A candidate is only proposed when it scores
clearly better than the original, so correct text is left alone — `Sigur Rós`
round-trips into a plausible-looking `Sigur Rуs`, and the scorer rejects it.

ID3 is written as v2.4/UTF-8 with no v1 block, because a stale latin-1 v1 tag
is how mojibake survives a repair. FLAC/Ogg get Vorbis comments, `.m4a` gets
MP4 atoms. Track numbers are written as `n/total`.

Before writing, the previous tags are appended to `.tags-backup.json` in the
target directory — a dotfile, so scanners ignore it.

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
| `tags.py` | Format-agnostic tag read/write (ID3, Vorbis, MP4) |
| `encoding.py` | Mojibake detection and repair, with the scoring that avoids false positives |
| `library.py` | The only module that touches the collection on disk |
| `ingest.py` | The "a file arrived" flow, as events; writes tags into the file |
| `sources/` | `Source` protocol and the YouTube implementation over yt-dlp |
| `dlservice.py` | `/dl` as a job: search → pick → fetch → hand over to ingest |
| `tagfix.py` | Proposals and their application, plus the JSON backup |
| `tagservice.py` | `/tags` as a job: propose → pick a source → apply |
| `jobs.py` | Short job ids for `callback_data`, worker pool |
| `errors.py` / `events.py` | Typed errors and structured events — no prose |
| `bot/texts.py` | Every user-facing string, in one dict |
| `bot/render.py` | Events → HTML, progress throttling |
| `bot/handlers.py` | Telegram wiring only |

`bot/texts.py` is a plain dict rather than gettext: the bot is allowlisted to a
couple of people, so a second language is not needed yet. The boundary that
makes adding one cheap — core returns events and typed errors, never text — is
enforced by a test.
