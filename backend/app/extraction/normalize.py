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
