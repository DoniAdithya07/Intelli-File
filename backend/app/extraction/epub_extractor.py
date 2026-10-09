"""E-book (.epub) extraction. Added 2026-10-05.

An EPUB is a zip of XHTML chapters. The package file (named by
META-INF/container.xml) lists them in reading order (the "spine"); each
chapter becomes one block, its <title> the block's section, its text read
by the HTML reader (scripts and styles dropped, nothing rendered). When
the package file can't be followed, the chapters are read in zip order.
The zip-bomb check in extractor.py has already looked at the archive.
"""

import html
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote

from .blocks import ExtractedBlock
from .html_extractor import html_to_text

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# (2026-10-05) Looked for in the head only: a chapter full of unclosed
# "<title" made the lazy match rescan to the end once per tag.
_TITLE_SEARCH_CHARS = 4096
_XML_ENCODING = re.compile(rb"""<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._-]+)""")
_CHAPTER_SUFFIXES = (".xhtml", ".html", ".htm")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _resolve(base: str, href: str) -> str:
    """A spine/encryption href as a zip member name (2026-10-05): without its
    #fragment, and "/OEBPS/x.xhtml" taken from the zip's root, not the
    package folder."""
    href = unquote(href.split("#", 1)[0])
    if href.startswith("/"):
        return posixpath.normpath(href.lstrip("/"))
    return posixpath.normpath(posixpath.join(base, href))


def _spine(archive: zipfile.ZipFile) -> list[str]:
    """Chapter paths in reading order; [] when the package can't be read."""
    try:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        opf_path = next(e.get("full-path") for e in container.iter() if _local(e.tag) == "rootfile")
        opf = ET.fromstring(archive.read(opf_path))
    except (KeyError, StopIteration, ET.ParseError):
        return []
    base = posixpath.dirname(opf_path)
    hrefs = {e.get("id"): e.get("href") for e in opf.iter() if _local(e.tag) == "item"}
    order = [hrefs.get(e.get("idref")) for e in opf.iter() if _local(e.tag) == "itemref"]
    return [_resolve(base, h) for h in order if h]


def _encrypted(archive: zipfile.ZipFile) -> set[str]:
    """Members META-INF/encryption.xml lists (DRM; also obfuscated fonts,
    which are not chapters). Only a book that has this file can be DRM."""
    try:
        root = ET.fromstring(archive.read("META-INF/encryption.xml"))
    except (KeyError, ET.ParseError):
        return set()
    return {_resolve("", e.get("URI")) for e in root.iter() if _local(e.tag) == "CipherReference" and e.get("URI")}


def _decode(data: bytes) -> str:
    """A chapter's text in its own encoding (2026-10-05: windows-1252 and
    UTF-16 books were reported as DRM): the byte-order mark, else the XML
    declaration, else UTF-8, else Latin-1 (which never fails)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    declared = _XML_ENCODING.match(data[:200])
    for encoding in ([declared.group(1).decode("ascii")] if declared else []) + ["utf-8"]:
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("latin-1")


def extract_epub(path: Path) -> list[ExtractedBlock]:
    blocks = []
    unreadable = 0
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        # A spine whose entries name no file in the book is no spine: zip order instead.
        chapters = [n for n in _spine(archive) if n in names] or [n for n in archive.namelist() if n.lower().endswith(_CHAPTER_SUFFIXES)]
        encrypted = _encrypted(archive)
        for name in chapters:
            if name in encrypted:  # DRM-protected: ciphertext, not text
                unreadable += 1
                continue
            raw = _decode(archive.read(name))
            text = html_to_text(raw)
            if text:
                title = _TITLE.search(raw, 0, _TITLE_SEARCH_CHARS)
                section = html.unescape(title.group(1)).strip() or None if title else None
                blocks.append(ExtractedBlock(text=text, section=section))
    if not blocks and unreadable:
        raise ValueError("the book's chapters are encrypted (DRM), so its text cannot be read")
    return blocks
