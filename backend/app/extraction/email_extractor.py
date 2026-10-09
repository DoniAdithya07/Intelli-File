"""Saved e-mails: .eml (any mail program) and .msg (Outlook). Added 2026-10-05.

Both become a first block with the Subject/From/To/Date lines (so "the
mail from Ana about the canoe" finds it) and a block with the body: the
plain-text body when there is one, else the HTML body's visible text.
Attachments are ignored: they are separate documents, and decoding them
here would run every attachment format's parser on mail content.
"""

import email
import email.policy
from pathlib import Path

from .blocks import ExtractedBlock
from .html_extractor import html_to_text

_HEADERS = ("Subject", "From", "To", "Date")


def _message_blocks(fields: list[tuple[str, str]], body: str) -> list[ExtractedBlock]:
    subject = dict(fields).get("Subject") or None
    blocks = [ExtractedBlock(text="\n".join(f"{name}: {value}" for name, value in fields), heading=subject)]
    if body.strip():
        blocks.append(ExtractedBlock(text=body, heading=subject))
    return blocks


def _eml_body(message) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeError):  # an unknown or wrong charset label
        content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if not isinstance(content, str):
        return ""
    return html_to_text(content) if part.get_content_subtype() == "html" else content


def extract_eml(path: Path) -> list[ExtractedBlock]:
    with path.open("rb") as f:
        message = email.message_from_binary_file(f, policy=email.policy.default)
    fields = [(name, str(message[name]).strip()) for name in _HEADERS if message[name]]
    if not fields:
        raise ValueError("not an e-mail message: it has no Subject, From, To or Date")
    return _message_blocks(fields, _eml_body(message))


def extract_msg(path: Path) -> list[ExtractedBlock]:
    """Outlook keeps each property of the message in its own stream of a
    compound (OLE) file: __substg1.0_<property id><type>, type 001F for
    UTF-16 text, 001E for 8-bit text, 0102 for bytes ([MS-OXMSG])."""
    import olefile

    if not olefile.isOleFile(str(path)):
        # .msg is also the extension of Tcl and installer message catalogues
        # (plain text, found under Program Files on this PC): not mail.
        return []
    try:
        with olefile.OleFileIO(str(path)) as ole:
            def prop(property_id: str) -> str:
                for kind, encoding in (("001F", "utf-16-le"), ("001E", "cp1252")):
                    name = f"__substg1.0_{property_id}{kind}"
                    if ole.exists(name):
                        return ole.openstream(name).read().decode(encoding, "replace").rstrip("\x00").strip()
                return ""

            subject, sender, to = prop("0037"), prop("0C1A") or prop("0C1F"), prop("0E04")
            body = prop("1000")
            if not body and ole.exists("__substg1.0_10130102"):
                body = html_to_text(ole.openstream("__substg1.0_10130102").read().decode("utf-8", "replace"))
    except OSError as e:
        if e.errno is not None:
            raise  # locked or gone: the scan tries again later
        raise ValueError(f"damaged Outlook message: {e}") from e
    except (MemoryError, RecursionError):
        raise
    except Exception as e:
        # (2026-10-05) A compound file that olefile cannot follow fails in
        # many ways ("Exceeds the limit (4300 digits)...", "bytes length not
        # a multiple of item size"); all of them mean the same thing.
        raise ValueError(f"damaged Outlook message: {type(e).__name__}: {e}") from e
    fields = [(n, v) for n, v in (("Subject", subject), ("From", sender), ("To", to)) if v]
    if not fields and not body:
        raise ValueError("not an Outlook message: no subject, sender or body in it")
    return _message_blocks(fields, body)
