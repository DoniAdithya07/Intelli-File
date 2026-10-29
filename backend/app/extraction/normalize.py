import codecs
import re
import unicodedata


def normalize_text(text: str) -> str:
    """Unicode-normalize and collapse excess whitespace so near-identical
    formatting differences don't fragment otherwise-identical content.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_text_file(path) -> str:
    """A text file's content: UTF-32/UTF-16 when it starts with that byte-order
    mark, else UTF-8 (BOM dropped), else Windows-1252 — the encoding of the
    non-UTF-8 text files real Windows users have; it never fails."""
    data = path.read_bytes()
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return data.decode("utf-32", "replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")
