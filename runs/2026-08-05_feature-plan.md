# tgmusicbot — Feature plan: download sources, tag repair, library layout
Date: 2026-08-05
Status: in progress

## Goal

Turn tgmusicbot from a "save forwarded file" bot into a media-library manager over a
tree organised as `<artist>/<album>/<track>`. Five requested features:

1. Find a track on YouTube by name, download with yt-dlp
2. If not found — search rutracker
3. Download a torrent by magnet link; if it contains audio, file it into the library
4. Repair ID3 tags for a subdirectory (album or artist) — broken encodings, wrong
   names; derive track number / artist / title / album, with the *directory name*
   as an optional source of truth (user's choice, not automatic)
5. A file sent to the bot lands in the library

## Decisions taken (2026-08-05)

| Question | Answer |
|---|---|
| Torrent backend | **qBittorrent** via WebAPI. Its own deployment is a separate task. |
| YouTube audio format | Research first (see Findings) → **keep original, no re-encode** |
| rutracker | Account available, credentials will be provided |
| Scope now | **Plan only, no code** |

---

## Research & Findings

### Current code (`app.py`, 68 lines)

Only feature 5 is implemented, and only partially: files go to
`OUTPUT_FOLDER/<performer>/<file_name>` — **two levels, not three**. There is no
album level at all, so the required `artist/album/track` layout does not exist yet.

Bugs found:

| # | Location | Problem |
|---|---|---|
| 1 | `app.py:56` | `message.audio.performer` / `.file_name` may be `None` → `os.path.join(path, None)` → `TypeError`, unhandled, user gets no reply at all |
| 2 | `app.py:57` | Inverted ternary. `overwritten = counter != 0` (a name clash happened). On clash the bot says "Saved as …"; on a clean save it says "Already exists: saved as …" |
| 3 | `app.py:31-40` | The "same file" branch `break`s, but lines 39-40 rewrite the file anyway — a full redundant write to a (network) disk |
| 4 | `app.py:36` | `title.replace(".", "_0.")` replaces **every** dot: `01. Intro.mp3` → `01_0. Intro_0.mp3`. Needs `os.path.splitext` |
| 5 | `app.py:25` | No sanitisation. `performer` is client-supplied: `/`, `..`, control chars → writes outside the library (path traversal) |
| 6 | `app.py:50` | `document` is declared in `content_types` but only `message.audio` is handled → an mp3 sent as a file falls through to "Saving failed!" |
| 7 | `app.py:32` | Dedup compares `len(file)` — whole file held in RAM. Tolerable for Bot API (≤20 MB), unusable for yt-dlp / torrent paths |
| 8 | `app.py:59` | `parse_mode="MARKDOWN"` plus a manual `replace('_','\\_')`. Paths containing `*`, `[`, `` ` `` break the markup |
| 9 | `app.py:64-67` | `echo_all` swallows all text — direct conflict with "text = search query" |
| 10 | — | No allowlist. Anyone who finds the bot can write to the library. With features 1-3 this becomes genuinely dangerous |
| 11 | `app.py:69` | `infinity_polling()` handles messages inline — a yt-dlp or torrent job blocks the bot for minutes |

Infrastructure:

- **`.env` is tracked in git** (commit `d2f98f8`). Verified: it currently holds only
  the placeholder `%YOUR_TG_TOKEN_HERE%`, so nothing has leaked — but `.gitignore:125`
  does not apply to an already-tracked file, so the first real token would be committed.
  → `git rm --cached .env`, ship `.env.example`.
- `requirements.txt`: `telebot==0.0.5` is a **different project** (installs the module
  `telebot_router`); `pyTelegramBotAPI` only arrives as its transitive dependency.
  Verified against PyPI metadata: `Requires-Dist: pyTelegramBotAPI`. It works by
  accident. → pin `pyTelegramBotAPI` directly.
- `Dockerfile`: `python:3.12-alpine` — no ffmpeg (required by yt-dlp for audio
  extraction/remux). → `python:3.13-slim` + `apt-get install ffmpeg`.
- `docker-compose.yml`: `ports: 4001:80` — nothing listens on 80, dead config;
  `version: "3"` is obsolete.
- `run.sh`: `sudo mkdir` without `-p` fails on re-run.

### Opus playback — Navidrome and Jellyfin

**Both can play Opus. No transcoding pipeline is needed.**

- **Navidrome** serves MP3, FLAC, AAC, OGG, **Opus**, WMA, WAV, AIFF and anything
  else ffmpeg handles, and can transcode on the fly per user/player if a client
  needs it.
- **Jellyfin** lists Opus as supported. Direct play is client-dependent:
  - ✅ Chrome, Edge, Firefox, Android, Android TV, iOS, Swiftfin, Roku, Kodi, Desktop
  - ⚠️ Safari: only Opus inside `.caf`
  - ⚠️ iOS 17 / macOS 14+: stereo Opus in MP4; iOS 18.4 / macOS 15.4+: Opus in OGG
  - FLAC and MP3 direct-play everywhere (MP3 mono on Chrome is a known
    false-negative that transcodes to AAC)

Consequence for the plan: **store the original stream, do not re-encode.** YouTube
`bestaudio` is usually Opus in a WebM container — WebM audio is poorly recognised by
music clients, so **remux (not re-encode) into an Ogg `.opus` container**; lossless
container change, zero quality loss. Apple clients are the only weak spot, and
Navidrome transcodes on the fly for them.

Tagging consequence: `.opus`/`.ogg` carry **Vorbis comments**, `.m4a` carries MP4
atoms, not ID3. The tag module must therefore be format-agnostic from day one —
which it needs anyway, since the existing library contains FLAC.

### k3s integration (`gh://NetBUG/k3s`, branch `rebuild-2026`)

Read at commit `78ffbde`. The relevant architecture is *already decided in that repo*,
and it favours this bot staying where it is.

**The governing principle** — `docs/en/06-navidrome-media.md`:
> "Heavier media workloads (SMB server, Nextcloud, **transcoding bots**) can stay
> *off-cluster* on the media node; the cluster only mounts what it needs."

and `runs/20260627-1400-reinstalling-sowilo.md` states outright:
> `| tgmusicbot | оставляем на Docker (вне scope) |`

So: **tgmusicbot stays on the media node (sowilo) as Docker, writing directly to the
local collection. Navidrome/Jellyfin run in k3s and mount the same tree read-only
over NFS.** The bot is the single writer; the players are readers. That is a clean
split and no k3s manifest for tgmusicbot is needed.

Current state of the pieces:

| Service | State in `rebuild-2026` | Bearing on this plan |
|---|---|---|
| **navidrome** | Manifests **exist** (`apps/navidrome/`), but `navidrome/ks.yaml` is **commented out** in `apps/kustomization.yaml` — blocked on milestone M6, the media NFS export, which does not exist yet | Consumer of the library. `ND_MUSICFOLDER: /music`, expects `artist/album/track` — matches this bot's target layout |
| **jellyfin** | **No manifests at all.** Only mentioned in `runs/20260627-1400-reinstalling-sowilo.md`: stopped on sowilo, no data, config in `~/compose/jellyfin` | Needs its own spec. Its music library must mirror Navidrome's — same NFS export, same RO mount |
| **qBittorrent** | **No manifests.** Separate spec, per decision above | Feature 3 depends on it: needs a reachable WebAPI URL + credentials, and its completed-downloads directory must be visible to the bot |
| **media NFS export** | `infrastructure/config/media-nfs-pv.yaml` PV `media-music-ro`, `ReadOnlyMany`, `mountOptions: [nfsvers=4.1, ro]`. `volumeHandle` points at placeholder `192.168.122.134/export/media/music` with a `TODO` — real media node not wired | See constraints below |

**Constraints this imposes — the important part:**

1. **The NFS export is `ro` by design**, at the export level:
   `/export/media 192.168.5.0/24(ro,no_subtree_check,all_squash,anonuid=1000,anongid=1000)`.
   The doc calls this "the strongest guarantee for the music collection", and adds that
   a future RW share "gets its *own* export line with `rw` and a dedicated subtree".
   → The bot **must not** try to write through this export. It writes to the local
   filesystem on the media node. This reinforces the off-cluster placement.
2. **Path mismatch to resolve.** The M6 doc binds `/srv/music` into
   `/export/media/music`, but the real collection — and this bot's compose bind — is
   `/media/Magic/netbug/Music/ARTISTS` (7.3 TB mdraid `md0` on sowilo). One of the two
   must move or the bind mount must be repointed before M6 works.
3. **Placeholder IP.** The PV points at `192.168.122.134`; the media node is sowilo.
   Must be corrected as part of M6, not here.
4. **Export CIDR mismatch.** The export line allows `192.168.5.0/24`; the cluster and
   this VM live on `192.168.122.0/24`. Also an M6 item — flagging it because a wrong
   CIDR looks like a broken mount, not a config error.
5. **Scan latency.** `ND_SCANSCHEDULE: "@every 24h"` — files added by the bot would
   be invisible to Navidrome for up to a day. → the bot should trigger a rescan
   (Subsonic `/rest/startScan`) after a successful ingest. Optional, behind config;
   no-op when the URL is unset.
6. `all_squash,anonuid=1000,anongid=1000` on the export and `user: 1000:1000` in the
   bot's compose agree — keep writing as uid 1000 so both sides see consistent ownership.

**Items to hand to the k3s repo (not to be done in this repo):**

- M6: create the real export, fix the server IP, fix the CIDR, resolve the
  `/srv/music` vs `/media/Magic/netbug/Music/ARTISTS` path, then re-enable
  `navidrome/ks.yaml` in `apps/kustomization.yaml`
- New spec: **qBittorrent** — WebAPI endpoint reachable from the bot, credentials via
  SOPS, completed-downloads path shared with the bot
- New spec: **Jellyfin** — music library on the same RO NFS export as Navidrome, so
  both see an identical `artist/album/track` tree

---

## Plan

### Phase 0 — foundation (nothing else can be built without it)

Split the monolith per the workspace convention (core logic decoupled from I/O,
config in a frozen dataclass with `from_env()`):

```
app.py                  # thin entrypoint: Config.from_env() -> wiring
tgmusicbot/
  config.py             # @dataclass(frozen=True) Config.from_env()
  library.py            # MediaLibrary: artist/album/track, sanitize, dedup, ingest(stream)
  naming.py             # parse "NN - Artist - Title", normalisation
  tags.py               # TagReader / TagFixer on mutagen (ID3 + Vorbis + MP4)
  sources/base.py       # Protocol: search(q) -> [Candidate]; fetch(c, dest) -> Path
  sources/youtube.py
  sources/rutracker.py
  torrents.py           # QBittorrentClient (WebAPI)
  errors.py             # typed domain errors — the only thing core raises
  events.py             # Progress / Question / Done — structured, no human text
  jobs.py               # JobRegistry: short ids, queue, workers, pending questions
  bot/handlers.py       # I/O and formatting only
  bot/texts.py          # flat key -> str catalogue; the only module with UI strings
tests/
```

- `MediaLibrary` is the only module that knows about the filesystem. Three levels:
  `<root>/<artist>/<album>/<NN - title>.<ext>`. `sanitize()`: reject `/` and `..`,
  strip control chars, cap at 255 bytes, trim trailing dot/space (matters for the
  SMB clients that also serve this tree).
- Dedup: size first, then sha256 — never a blind rewrite (fixes bug 3).
- Long jobs move to a worker pool; `time.monotonic` for timings; clock injected.
- Allowlist `TG_ALLOWED_USERS` as a decorator on every handler (fixes 10).
- Replace `echo_all` with explicit commands plus a real fallback (fixes 9).
- Unit tests for `library` / `naming` / `tags` against `tmp_path`, no Telegram.

#### UI / i18n groundwork (added 2026-08-05)

The wording, keyboards and a second language are deliberately deferred to Phase 1,
when `/tags` produces the first genuinely multi-step dialogue. Three items are *not*
deferred, because they are architecture rather than presentation and become expensive
to retrofit:

1. **Core never returns human-facing text.** `library` / `naming` / `tags` / `sources`
   return dataclasses and raise typed errors from `errors.py`
   (`AlbumUnknown`, `DuplicateFile`, `UnsafeName`, `SourceUnavailable`, …).
   Rendering happens only in `bot/`. This is what makes localisation a one-module
   change later instead of a sweep through every module.
2. **`callback_data` is capped at 64 bytes**, so every interactive flow already
   planned (5 YouTube candidates, the four `/tags` buttons, the which-album prompt,
   torrent cancel) must address state by a **short job id**, not by text. Hence
   `JobRegistry` allocates ids and owns pending questions from day one; buttons carry
   `<action>:<job_id>[:<option_idx>]`. Bolting ids on after `jobs.py` exists would
   mean rewriting the worker pool and every handler.
3. **Progress is an event, not a string.** Core emits `Progress(job_id, done, total)`
   / `Question(job_id, options)` / `Done(job_id, result)`; the ≥3 s throttle and
   `edit_message_text` live in `bot/`. Otherwise throttling ends up duplicated in the
   yt-dlp hook and the qBittorrent poller.

`bot/texts.py` is a plain `dict[str, str]` for now — no gettext, no `.po`. The bot is
allowlisted to one or two users, so a second language is YAGNI; keeping the strings in
one place costs nothing and preserves the option.

**Feature 5 is fully closed here**: bugs 1-8, a working `document` handler, and a
follow-up prompt asking which album when the tags do not supply one.

### Phase 1 — ID3 / tag repair (feature 4)

- `/tags <path>` shows current vs proposed tags with buttons:
  `Apply` / `From directory name` / `From file names` / `Cancel` — the user picks the
  source of truth, as requested.
- Encoding repair: the common case is cp1251 bytes decoded as latin-1 →
  `s.encode('latin-1').decode('cp1251')`; pick the candidate by the share of
  characters in the expected alphabet. Always show a before → after diff.
- Track number from the leading `NN` in the filename or from existing `TRCK`;
  write as `n/total`.
- Write ID3v2.4/UTF-8 with `id3v1=0`; Vorbis comments for `.opus`/`.ogg`/`.flac`;
  MP4 atoms for `.m4a`.
- Dry-run by default. Back up the old tags to JSON alongside so there is a rollback.

### Phase 2 — YouTube (feature 1)

- `/dl Artist - Album - Title` (explicit input, because YouTube metadata is
  unreliable) → `ytsearch5:` → five candidates as buttons (title, channel, duration).
- yt-dlp **as a library**, not a subprocess: `bestaudio`, then remux to Ogg `.opus`
  with no re-encode (see Findings). ffmpeg required in the image.
- Progress hook into an edited message, throttled to ≥3 s (otherwise Telegram
  rate-limits).
- Download to temp, then `MediaLibrary.ingest()` — no half-written files ever appear
  in the library.
- Filters: duration 30 s…20 min; drop live/cover unless explicitly asked.

### Phase 3 — torrents via qBittorrent (feature 3)

Built **before** rutracker: the engine can be tested with any magnet, and feature 2
is merely a producer of magnets.

- `QBittorrentClient` over WebAPI: login (cookie cached), `torrents/add` with the
  magnet, poll metadata.
- Once metadata arrives, list files **before** downloading; keep only audio
  (`.flac .mp3 .m4a .ogg .opus .wav .ape .wv`) and set `priority=0` on the rest via
  `torrents/filePrio`.
- On completion `ingest_album()`: if the release is shaped `Artist - Album (Year)`,
  file it automatically; otherwise ask.
- Limits: max size, max duration, a cancel button.
- Depends on the separate qBittorrent deployment spec — until it exists, develop
  against a local qBittorrent container.

### Phase 4 — rutracker (feature 2)

Deliberately last: no public API, login required, HTML parsing, captcha and ISP
blocking are both plausible.

- Session login with credentials from `.env`; cookies cached on disk.
- Search → parse the results table (title, size, seeders, format) → filter
  lossless/mp3 → buttons → extract `magnet:` from the topic page → hand to Phase 3.
- Graceful degradation is mandatory: not logged in / captcha must produce an explicit
  message, never a silent "nothing found".

### Phase 5 — packaging

- Dockerfile → `python:3.13-slim` + ffmpeg
- `git rm --cached .env`, add `.env.example`
- README in both languages: commands, env vars, library layout
- Drop the dead `ports` and obsolete `version` from compose
- Optional Navidrome rescan trigger after ingest (see k3s constraint 5)

---

## Result

**Phase 0 implemented (2026-08-05).** The monolith is gone; feature 5 is closed and
the UI/i18n groundwork above is in place. 107 unit tests, no Telegram needed to run
them.

| File | Contents |
|---|---|
| `tgmusicbot/config.py` | frozen `Config.from_env()`; `TG_ALLOWED_USERS` mandatory |
| `tgmusicbot/errors.py` | typed errors, each with a `code` and structured `params` |
| `tgmusicbot/events.py` | `Progress` / `Question` / `Done` / `Failed`, `Option` |
| `tgmusicbot/naming.py` | `sanitize`, `split_extension`, `parse_track_filename` |
| `tgmusicbot/tags.py` | `TrackTags`, `read_tags` over mutagen `easy` (ID3/Vorbis/MP4) |
| `tgmusicbot/library.py` | `stage()` → `ingest_staged()`, dedup, clash renaming |
| `tgmusicbot/ingest.py` | the arrival flow: ask for each missing field in turn |
| `tgmusicbot/jobs.py` | `JobRegistry` (short ids), callback codec, `WorkerPool` |
| `tgmusicbot/bot/{texts,render,handlers}.py` | strings, rendering + throttle, wiring |
| `tests/` | 107 tests: naming, library, jobs, ingest, config, render, i18n boundary |

Audit bugs closed: 1 (missing `performer`/`file_name` → now asked for, not a crash),
2 (inverted ternary → explicit `IngestStatus`), 3 (redundant rewrite → hash compare,
duplicate is a no-op), 4 (`replace(".", …)` → `split_extension`), 5 (no sanitising →
`naming.sanitize`, traversal test), 6 (`document` declared but unhandled), 7 (whole
file in RAM → streamed hashing), 8 (Markdown escaping → HTML mode + `html.escape`),
9 (`echo_all` → text is an answer to a pending question, else help), 10 (no allowlist
→ decorator on every handler), 11 (blocking polling → `WorkerPool`).

Two Phase 5 items were pulled forward because Phase 0 cannot run without them:
`requirements.txt` now pins `pyTelegramBotAPI` directly (the `telebot==0.0.5` pin was
a different project), and `.env` is untracked (`git rm --cached`) with a `.env.example`
shipped. README rewritten: env vars, commands, layout, module map.

Verified: full suite green; a stubbed end-to-end run through `WorkerPool` covers
file → "no artist tag" → typed answer → "no album tag" → typed answer → saved, plus
the `Singles` button path and rejection of a non-allowlisted user. Not verified
against a live Telegram token.

**Phase 1 implemented (2026-08-05).** Tag repair, 180 tests total.

| File | Contents |
|---|---|
| `tgmusicbot/encoding.py` | mojibake repair with a scorer that refuses false positives |
| `tgmusicbot/tagfix.py` | `Source` (tags/directory/filename), `Proposal`, `apply()` + JSON backup |
| `tgmusicbot/tagservice.py` | `/tags` as a job; buttons carry only the job id |
| `tags.write_tags` | ID3v2.4/UTF-8 with `v1=0`, Vorbis, MP4; `n/total` |
| `naming.parse_album_dirname` | `"Pink Floyd - Meddle (1971)"` → artist/album/year |
| `library.resolve` | user path → path proven to be inside the root |

The scorer is the interesting part. Three signals separate mojibake from
ordinary text: letters outside ASCII-plus-Cyrillic, scripts mixed inside one
word, and capitals in the middle of a word. A candidate must beat the original
by a margin before it is proposed. This is what stops the classic false
positive: `Sigur Rós` round-trips into the entirely plausible `Sigur Rуs`
(Cyrillic *у*), and the mixed-script penalty rejects it. Verified against
`Motörhead`, `Blue Öyster Cult`, `Beyoncé`, `Erik Satie – Gymnopédie No.1`,
`AC/DC` — all untouched — and both mojibake forms of `Пинк Флойд` — both
repaired.

Deviations from the plan, both deliberate:

- **`.tags-backup.json` lives in the target directory, not "alongside" each
  file**, and is an append-only list of runs with `before`/`after` per track.
  One dotfile per album beats one per track, and scanners skip dotfiles.
- **No `--dry-run` flag.** The proposal *is* the dry run: `propose()` cannot
  write, and only the Apply button reaches `apply()`. A flag would have been a
  second way to express the same thing.

Real-file round-trips are tested without fixtures or ffmpeg (neither is on this
box): a bare MPEG-1 Layer III stream and a bare FLAC STREAMINFO block are
synthesised in-process, which is enough for mutagen to attach tags to.

Verified: full suite green; a stubbed end-to-end `/tags` run over two real MP3s
tagged `Ïèíê Ôëîéä` produced the diff, the four buttons, and after Apply the
files read back as `Пинк Флойд` with `total_tracks=2` and a backup written.
Still not verified against a live Telegram token.

**Fixes from the first live run (2026-08-05).**

* `logsetup.py` — telebot answered every long-polling read timeout with two full
  tracebacks (the exception, then the same traceback again as a separate record).
  Transient failures now collapse to one line, `telebot: reconnecting to Telegram
  (read timeout)`, repeats of the same reason are suppressed for 60 s and the next
  line says how many were swallowed. Genuine errors pass through untouched.
* `bot/download.py` — the progress message could only ever read `download... ?%`:
  `TeleBot.download_file` returns the whole file as `bytes` and reports nothing on
  the way. The file is now streamed through `ProgressStream` straight into
  `MediaLibrary.stage()`, so there is a real percentage
  (`Downloading ████████░░░░ 67% of 3.0 MB`) and the file never sits in RAM whole.
  A network failure becomes `SourceUnavailable`, not a bare traceback.
* The stage name was a raw internal word (`download`) leaking into the UI; it is a
  catalogue key now, like everything else the user sees.

**Phase 2 implemented (2026-08-05).** `/dl` against YouTube, 262 tests total.

| File | Contents |
|---|---|
| `sources/base.py` | `Candidate` / `Fetched` / `Source` protocol — rutracker will fit it |
| `sources/youtube.py` | yt-dlp as a library: search, filters, remux policy, progress hook |
| `dlservice.py` | `/dl` as a job; `parse_query` splits `Artist - Album - Title` |
| `ingest.py` | now writes tags into the file (see below) |
| `library.py` | `rename_staged`, `restat`; `sweep_incoming` also clears dead `dl-*` dirs |

Verified live against real YouTube: `/dl Pink Floyd - Meddle - One of These Days`
offered five results, downloaded 5.5 MB of m4a and filed it as
`Pink Floyd/Meddle/One of These Days.m4a`; a second identical `/dl` was reported as
a duplicate rather than saved as `… (2)`.

Two problems the live run exposed, both fixed:

1. **The file landed with no tags at all.** Placement is a directory tree; a player
   reads tags. Untagged, the track shows up in Navidrome as Unknown Artist no matter
   how tidy the path is. `IngestService` now writes the placement metadata into the
   file. Existing tags are never overwritten — only gaps are filled — unless the
   caller passes `prefer_hint`, which `/dl` does, because what the user typed beats
   whatever a YouTube upload claims.
2. **Tagging broke dedup.** Writing tags changes the bytes, so a hash taken while
   staging no longer matches the filed file, and re-downloading the same track would
   have landed as `… (2)`. Tags are therefore written to the *staged* file and the
   hash recomputed (`restat`) before the move. Staged files are also renamed to their
   real extension first, because mutagen sniffs by filename as well as content and an
   MP3 with no ID3 block scores zero when the name ends in `.part`.

Deviation from the plan, deliberate: **no re-encode and no unconditional remux.** The
plan said "remux WebM into Ogg"; the implementation makes that conditional
(`webm>ogg`), so an m4a download is passed through untouched. Without ffmpeg — this
sandbox has none — the format selector refuses WebM outright rather than filing a
container that music clients mishandle.

Also fixed: `quiet: True` does not silence yt-dlp's download bar, which was writing
`[download] 42%` to stdout; that needs `noprogress: True`.

**Link handling added on request (2026-08-05), beyond the original plan.**

A YouTube link sent as a plain message is now handled like a sent file: no command,
no search step. The flow is inspect → propose → confirm:

* `sources/youtube.py` — `find_url()` recognises `watch?v=`, `youtu.be`, `shorts/`,
  `live/`, `embed/`, `m.`/`music.` hosts and links embedded in a sentence, and
  normalises all of them to `watch?v=<id>`. That normalisation matters: a
  `music.youtube.com` link carries `list=`, and yt-dlp would fetch the whole playlist.
* `sources/youtube.py` — `inspect()` returns metadata without downloading, resolving
  the format selector so the *extension* is known in advance and the proposed path can
  be shown in full. An unplayable link therefore fails before any bytes move.
* `naming.clean_video_title()` — strips upload decoration in both languages:
  `(Official Audio)`, `[4K]`, `(Remastered 2011)`, `(официальный клип)`,
  `(премьера клипа, 2002)`, `(текст песни)`. Only known noise is removed; brackets
  holding part of a real name are left alone (`Aphex Twin - #3 (Rhubarb)`).
* `dlservice.py` — `start_url()` proposes `Artist/Album/Title.ext` and asks
  **Yes, save / Specify path / Cancel**, as requested. A two-part title leaves the
  album unknown, so `Singles` is proposed rather than asking another question. With no
  artist in the title the channel is used — for a `… - Topic` auto-upload that is
  exactly the artist name.
* `parse_target()` accepts a slash path, the dash form, or a single word (taken as the
  album). A typed path still goes through `sanitize()`, so `../../etc/passwd` is
  refused rather than followed.
* Precedence in `_answer_text`: a link wins over a pending question. A link is
  unmistakably a new request, and no answer to any of our questions looks like a URL.

Verified live on the requested link `watch?v=Q8WJz-DmPVg`: the bot proposed
`МакSим/Singles/Знаешь ли ты.m4a`, downloaded 3.8 MB only after Yes, and the filed
file reads back with artist/album/title set. 315 tests.

**Container image + ffmpeg (2026-08-05), Phase 5 pulled forward.**

`Dockerfile` is now `python:3.14-slim` + ffmpeg (Alpine dropped: no ffmpeg in base,
and musl buys nothing when every dependency is pure Python). Also: build-time sanity
checks (`ffmpeg -version`, importing every dependency and `BotApp`) so a broken image
fails at build rather than on the first message; `STOPSIGNAL SIGINT`, because telebot
stops cleanly on `KeyboardInterrupt` while the default SIGTERM kills it mid-download;
uid 1000 to match the NFS export's `anonuid`; OCI labels; `.dockerignore` that keeps
`.env`, `media/`, `logs/` and the venv out of the build context.

`docker-compose.yml`: `version:` and the dead `ports: 4001:80` removed, `init: true`
added (yt-dlp spawns ffmpeg, and PID 1 python does not reap), library path
parameterised via `LIBRARY_PATH` so the same file serves this sandbox and sowilo.
`run.sh` is idempotent (`mkdir -p`), refuses to start without `.env`, and picks
whichever compose implementation is installed.

**Image built and running (podman 5.7.0, rootless).** 619 MB, `python:3.14-slim`
(123 MB) plus ffmpeg 7.1.5 and the dependency tree. Verified:

| Check | Result |
|---|---|
| build-time assertions | pass (they are `RUN` steps, so the build would have failed) |
| `podman run` with no env | exits 2, logs `TG_TOKEN: is not set` |
| identity inside | `uid=1000(tgmusic)`, Python 3.14.6, yt-dlp 2026.07.04 |
| WebM→Ogg remux **inside the image** | `ogg`/`opus`, audio MD5 identical to the source |
| writing into the bind-mounted library | `ingest.created /media/Container Probe/…`, host sees `netbug:netbug` |
| live run | `Started polling` as @MusicNetbugBot; the container is now the live bot |

**One real difference between runtimes, found the hard way.** Rootless podman maps
container uid 1000 to a *subuid*, so the bind-mounted library was not writable and
yt-dlp died with `Permission denied: '/media/….part'`. Fix: `--userns=keep-id`, added
to compose as `userns_mode: ${USERNS_MODE:-}` — empty by default so Docker (where
container uid 1000 really is host uid 1000, and which is what sowilo runs) is
unaffected, and set to `keep-id` in this sandbox's `.env`.

No compose implementation is installed here (`podman compose` finds no provider), so
`run.sh` itself is still unexercised; the equivalent `podman run` is in the README.

**The ffmpeg remux branch is now verified end to end** — the one thing Phase 2 could
not test. ffmpeg was installed as a static build into `~/.local/bin` (no root needed;
`Config.from_env` picks it up off `PATH`). A 40 s Opus-in-WebM file was synthesised,
served over localhost and fetched through `YouTubeSource`:

| | |
|---|---|
| container before → after | `matroska,webm` → `ogg` |
| audio codec | `opus` → `opus` |
| audio stream MD5 | `12469b5587efd706a8e07c4481b5a45e` → **identical** |

So the remux is a pure container change, exactly as the plan required. The local-HTTP
route was necessary because **YouTube now answers this IP with "Sign in to confirm
you're not a bot"** — all six extractor clients tried (`tv`, `android_vr`,
`web_safari`, `web_embedded`, `ios`, `mweb`) either hit the same wall, returned no
formats, or reported DRM. Our own error handling behaved: a typed `SourceUnavailable`
with a one-line reason, nothing written, no partial file.

Two knobs added for that, both defaulting to yt-dlp's own behaviour:
`YT_COOKIES_FILE` (a Netscape cookie jar — the documented fix) and
`YT_PLAYER_CLIENTS` (comma-separated extractor clients). 333 tests.

Note for the sowilo deployment: `media/` moved to `/home/netbug/media` on this VM and
`.env` follows it; the real collection is still `/media/Magic/netbug/Music/ARTISTS`
behind `LIBRARY_PATH`.

Next: Phase 3 (torrents via qBittorrent).

Open questions:
- rutracker credentials still to be provided
- qBittorrent WebAPI endpoint + credentials pending its own deployment task
- M6 path decision (`/srv/music` vs `/media/Magic/netbug/Music/ARTISTS`) belongs to
  the k3s repo but blocks the Navidrome-sees-bot-output loop
