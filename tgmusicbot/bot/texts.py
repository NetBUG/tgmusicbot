"""The only module in the project that contains user-facing wording.

A flat ``dict`` on purpose: the bot is allowlisted to one or two people, so
gettext and a second language would be ceremony without a user.  What matters
is that every string lives here, so adding a language later means adding a
second dict and a lookup — not a sweep through the core.
"""

from __future__ import annotations

CATALOGUE: dict[str, str] = {
    # -- commands ----------------------------------------------------------
    "cmd.start": (
        "Music library bot.\n\n"
        "Send me an audio file and it lands in <code>{root}</code> as "
        "<code>artist/album/track</code>.\n\n"
        "/help — what I understand"
    ),
    "cmd.help": (
        "<b>What works now</b>\n"
        "• send an audio file or a document — I read its tags and file it\n"
        "• if a tag is missing, I ask for it; just reply with the value\n"
        "• /status — queue and library root\n\n"
        "<b>Coming</b>: /tags, /dl, magnet links, rutracker search."
    ),
    "cmd.status": (
        "Library: <code>{root}</code>\nLive jobs: {jobs}\nWorkers: {workers}"
    ),
    # -- ingest ------------------------------------------------------------
    "ingest.received": "Got <code>{filename}</code>, working…",
    "ingest.progress": "{stage}… {percent}%",
    "ingest.created": "Saved:\n<code>{path}</code>",
    "ingest.renamed": (
        "A different file already had that name. Saved as:\n<code>{path}</code>"
    ),
    "ingest.duplicate": "Already in the library, byte for byte:\n<code>{path}</code>",
    # -- questions ---------------------------------------------------------
    "ask.artist": (
        "No artist tag on <code>{filename}</code>.\nReply with the artist name:"
    ),
    "ask.album": (
        "No album tag on <code>{filename}</code>.\n"
        "Reply with the album name, or pick:"
    ),
    "ask.title": (
        "No usable title for <code>{filename}</code>.\nReply with the track title:"
    ),
    "ask.album.singles": "Singles",
    "ask.cancel": "Cancel",
    "ask.cancelled": "Cancelled. Nothing was written.",
    # -- outcomes ----------------------------------------------------------
    "done.generic": "Done.",
    # -- errors ------------------------------------------------------------
    "error.generic": "Something went wrong: {reason}",
    "error.config": "Configuration problem: {variable} {reason}",
    "error.unsafe_name": "That name cannot be used as a folder: {value}",
    "error.metadata_missing": "Missing tag: {field}",
    "error.artist_unknown": (
        "No artist tag and none in the filename — I do not know where to put this."
    ),
    "error.album_unknown": "No album tag.",
    "error.title_unknown": "No title, and the filename gives me nothing to use.",
    "error.unsupported_format": "<code>{extension}</code> is not an audio format I file.",
    "error.too_large": "Too big: {size} bytes, limit is {limit}.",
    "error.source_unavailable": "{source} is unreachable: {reason}",
    "error.job_unknown": "That request expired — send the file again.",
    "error.not_allowed": "Not for you.",
}


def t(key: str, /, **params: object) -> str:
    """Render a catalogue entry. An unknown key is a bug, so it is loud."""
    template = CATALOGUE.get(key)
    if template is None:
        return f"[{key}]"
    try:
        return template.format(**params)
    except KeyError as missing:
        return f"[{key}: missing {missing.args[0]}]"
