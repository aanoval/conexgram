"""Extract local file links from ChatGPT Desktop final responses.

ChatGPT-owned turns do not receive Conexgram's user-visible attachment
directive.  The Desktop response can still contain a local Markdown link,
for example ``[report](</Users/alday/report.pdf>)``.  This module turns those
links into safe attachment candidates; the application performs the existing
workspace/owner/size validation before sending anything to Telegram.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class ChatGPTLocalFile:
    path_text: str
    display_name: Optional[str] = None


_MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]\n]+)\]\(\s*(?:<([^>\n]+)>|([^\s)]+))\s*\)"
)
_BARE_LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])((?:/Users/|/tmp/|/private/tmp/|/Volumes/|~/)[^\s`<>\"')\]]+)"
)
_LOCAL_SCHEMES = {"file", "sandbox", "vscode"}
_FILE_WORD_RE = re.compile(
    r"\b(?:file|files|berkas|dokumen|document|attachment|lampiran|pdf|zip|gambar|image|foto|photo)\b",
    re.IGNORECASE,
)
_SEND_WORD_RE = re.compile(
    r"\b(?:send|attach|upload|share|download|kirim|kirimkan|lampirkan|unggah|bagikan|unduh)\b",
    re.IGNORECASE,
)
_DO_NOT_SEND_RE = re.compile(
    r"\b(?:don['’]?t|do not|jangan|tidak perlu|nggak perlu|tak perlu)\b[^\n]{0,40}"
    r"\b(?:send|attach|upload|share|download|kirim|lampir|unggah|bagikan|unduh)\b",
    re.IGNORECASE,
)


def should_send_local_files(user_text: str) -> bool:
    """Return true only when the user explicitly asks for a file delivery."""
    if _DO_NOT_SEND_RE.search(user_text):
        return False
    if not _SEND_WORD_RE.search(user_text) or not _FILE_WORD_RE.search(user_text):
        return False
    return True


def extract_local_files(text: str) -> list[ChatGPTLocalFile]:
    """Return unique local-file candidates in a ChatGPT final response."""
    candidates: list[ChatGPTLocalFile] = []
    seen: set[str] = set()

    for match in _MARKDOWN_LINK_RE.finditer(text):
        display_name = match.group(1).strip() or None
        target = match.group(2) or match.group(3) or ""
        path_text = _local_path_from_reference(target)
        if path_text and path_text not in seen:
            seen.add(path_text)
            candidates.append(ChatGPTLocalFile(path_text, display_name))

    for match in _BARE_LOCAL_PATH_RE.finditer(text):
        path_text = _local_path_from_reference(match.group(1))
        if path_text and path_text not in seen:
            seen.add(path_text)
            candidates.append(ChatGPTLocalFile(path_text))
    return candidates


def _local_path_from_reference(reference: str) -> Optional[str]:
    reference = reference.strip().strip("<>\"'").rstrip(".,;:")
    if not reference:
        return None
    parsed = urlparse(reference)
    if parsed.scheme:
        if parsed.scheme.lower() not in _LOCAL_SCHEMES:
            return None
        path = unquote(parsed.path)
        if parsed.scheme.lower() == "sandbox" and not path.startswith("/"):
            path = "/" + path
        return path or None
    if reference.startswith(("/", "~/")):
        return unquote(reference)
    return None
