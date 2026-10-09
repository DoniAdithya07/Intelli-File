"""More file types (2026-10-05): every new format, end to end.

Builds a sample of each new type on the fly, indexes them all into a
throwaway index through the real folder scan (the same path the app
uses), and checks that search finds each one by a word that exists only
inside it:

  documents   .odt .ods .odp .epub .eml .msg .doc .xls .ppt
  photos      .heic .heif .tif .tiff (CLIP photo search + thumbnails;
              OCR reads every page of a multi-page TIFF)
  videos      .3gp .wmv .mts .m2ts .mpg .mpeg (keyframes, the right
              moment, the frame at that moment)
  audio       .wma .aac .opus (spoken words found when Windows' speech
              engine can make a recording; decoded either way)

and that a damaged file of each new kind is reported as failed with a
reason while everything around it is still indexed.

The old binary Office files (.doc/.xls/.ppt/.msg) are written by a small
compound-file writer below (Office's "OLE" container): enough of each
format for the readers, built from the published specifications
([MS-CFB], [MS-DOC], [MS-PPT], [MS-XLS], [MS-OXMSG]). When real files
saved by Microsoft Office are available they are read too: point
INTELLIFILE_REAL_OFFICE_DIR at a folder with real_report.doc,
real_budget.xls and real_pitch.ppt (made by
.local/work/make_real_office_fixtures.ps1); without it that check skips.

Needs backend/requirements-dev.txt (pillow-heif, only to WRITE the test
.heic: the app itself reads HEIC with pi-heif, which cannot write).

Run with:  backend\\venv\\Scripts\\python.exe backend\\scripts\\prototype_more_formats.py
"""

import io
import os
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.embeddings.clip_model import ClipModel  # noqa: E402
from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.extraction import ocr  # noqa: E402
from app.extraction.extractor import extract_document  # noqa: E402
from app.extraction.spreadsheet_extractor import MAX_SPREADSHEET_ROWS  # noqa: E402
from app.files.discovery import (  # noqa: E402
    AUDIO_EXTENSIONS, CODE_EXTENSIONS, IMAGE_EXTENSIONS, MAX_DOCUMENT_FILE_BYTES, SUPPORTED_EXTENSIONS,
    VIDEO_EXTENSIONS, size_cap,
)
from app.indexing import Indexer, VisualIndexer, index_folder  # noqa: E402
from app.indexing.folder_scan import RECENT_FAILURES  # noqa: E402
from app.indexing.visual_indexer import decode_frame_at, extract_keyframes  # noqa: E402
from app.agent.library import library_answer  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.search.query_parsing import parse_query  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402
from app.thumbnails import generate_thumbnail  # noqa: E402
from app.transcription import Transcriber  # noqa: E402
from app.transcription.audio_io import load_audio_as_mono_16k  # noqa: E402
from app.visual_search import VisualSearchService  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
TEXT_MODEL_DIR = default_model_dir(BACKEND / "models")
CLIP_MODEL_DIR = BACKEND / "models" / "clip-vit-base-patch16"
WHISPER_DIR = BACKEND / "models" / "whisper-base.en"
REAL_OFFICE_DIR = Path(os.environ.get("INTELLIFILE_REAL_OFFICE_DIR") or BACKEND.parent / ".local" / "tmp" / "office-fixtures")

NEW_DOCUMENTS = {".odt", ".ods", ".odp", ".epub", ".eml", ".msg", ".doc", ".xls", ".ppt"}
NEW_IMAGES = {".heic", ".heif", ".tif", ".tiff"}
NEW_VIDEOS = {".3gp", ".wmv", ".mts", ".m2ts", ".mpg", ".mpeg"}
NEW_AUDIO = {".wma", ".aac", ".opus"}


# ---------------------------------------------------------------- OpenDocument, EPUB, e-mail

ODF_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"'
)


def write_odf(path: Path, mimetype: str, body: str, manifest_extra: str = "") -> None:
    """An OpenDocument package as LibreOffice writes it: `mimetype` first
    and stored, a manifest, and the content in content.xml."""
    content = f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {ODF_NS} office:version="1.3"><office:body>{body}</office:body></office:document-content>'
    manifest = ('<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                f'<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml">{manifest_extra}</manifest:file-entry></manifest:manifest>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), mimetype, compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/manifest.xml", manifest)
        z.writestr("content.xml", content)


def make_odt(path: Path) -> None:
    write_odf(path, "application/vnd.oasis.opendocument.text", (
        "<office:text>"
        '<text:h text:outline-level="1">Expedition Journal</text:h>'
        '<text:h text:outline-level="2">Day one</text:h>'
        "<text:p>We spotted a <text:span>narwhal</text:span> near<text:s/>the ice shelf.</text:p>"
        "<text:list><text:list-item><text:p>Pack the thermal blanket</text:p></text:list-item></text:list>"
        "<table:table><table:table-row><table:table-cell><text:p>Item</text:p></table:table-cell><table:table-cell><text:p>Qty</text:p></table:table-cell></table:table-row>"
        "<table:table-row><table:table-cell><text:p>Sled dogs</text:p></table:table-cell><table:table-cell><text:p>12</text:p></table:table-cell></table:table-row></table:table>"
        "</office:text>"))


def make_ods(path: Path) -> None:
    filler_rows = MAX_SPREADSHEET_ROWS + 500
    write_odf(path, "application/vnd.oasis.opendocument.spreadsheet", (
        "<office:spreadsheet>"
        '<table:table table:name="Harvest">'
        "<table:table-row><table:table-cell><text:p>crop</text:p></table:table-cell><table:table-cell><text:p>tonnes</text:p></table:table-cell></table:table-row>"
        '<table:table-row><table:table-cell><text:p>rhubarb</text:p></table:table-cell><table:table-cell office:value-type="float" office:value="7"><text:p>7</text:p></table:table-cell></table:table-row>'
        f'<table:table-row table:number-rows-repeated="{filler_rows}"><table:table-cell><text:p>filler row</text:p></table:table-cell></table:table-row>'
        # LibreOffice pads a sheet to its full size with repeated empty rows/cells: must cost nothing.
        '<table:table-row table:number-rows-repeated="1048000"><table:table-cell table:number-columns-repeated="16384"/></table:table-row>'
        "</table:table>"
        '<table:table table:name="Notes"><table:table-row><table:table-cell><text:p>Call the greenhouse supplier</text:p></table:table-cell></table:table-row></table:table>'
        "</office:spreadsheet>"))


def make_odp(path: Path) -> None:
    def page(name, title, body, notes=""):
        notes_xml = f"<presentation:notes><draw:frame><draw:text-box><text:p>{notes}</text:p></draw:text-box></draw:frame></presentation:notes>" if notes else ""
        return (f'<draw:page draw:name="{name}">'
                f'<draw:frame presentation:class="title"><draw:text-box><text:p>{title}</text:p></draw:text-box></draw:frame>'
                f'<draw:frame presentation:class="outline"><draw:text-box><text:p>{body}</text:p></draw:text-box></draw:frame>'
                f"{notes_xml}</draw:page>")
    write_odf(path, "application/vnd.oasis.opendocument.presentation", (
        "<office:presentation>"
        + page("page1", "Observatory Budget", "Two new telescopes", "Mention the comet survey")
        + page("page2", "Timeline", "First light in autumn")
        + "</office:presentation>"))


def make_epub(path: Path) -> None:
    """Chapters stored in the zip in the WRONG order; the spine says the
    reading order, which is what the blocks must follow."""
    container = ('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>'
           '<item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>'
           '<item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>'
           '<item id="css" href="style.css" media-type="text/css"/>'
           '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')

    def chapter(title, text):
        return (f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title>'
                f"<style>p {{ color: red }}</style></head><body><h1>{title}</h1><p>{text}</p><script>alert('x')</script></body></html>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/text/ch2.xhtml", chapter("The Return", "The albatross flew home at dawn."))
        z.writestr("OEBPS/text/ch1.xhtml", chapter("The Voyage", "A clockmaker boarded the schooner in Lisbon."))
        z.writestr("OEBPS/style.css", "body { margin: 0 }")


def make_eml(path: Path, html_only: bool = False) -> None:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = "Invoice for the canoe rental" if not html_only else "Newsletter: gardening tips"
    msg["From"] = "Ana Silva <ana@example.invalid>"
    msg["To"] = "Bo Chen <bo@example.invalid>"
    msg["Date"] = "Mon, 05 Oct 2026 09:30:00 +0000"
    if html_only:
        msg.set_content("<html><body><p>Prune the <b>wisteria</b> in late winter.</p><script>track()</script></body></html>", subtype="html")
    else:
        msg.set_content("Hello Bo,\nthe canoe rental for the weekend is 85 euros, payable on arrival.\nAna")
        msg.add_alternative("<p>HTML twin that must not be preferred over the plain text</p>", subtype="html")
        msg.add_attachment(b"%PDF-1.4 attachment bytes mentioning porcupine", maintype="application", subtype="pdf", filename="porcupine.pdf")
    path.write_bytes(bytes(msg))


# ---------------------------------------------------------------- old binary Office files (OLE)

def write_cfb(path: Path, streams: dict[str, bytes]) -> None:
    """A minimal compound file ([MS-CFB] version 3, 512-byte sectors): a
    root storage holding flat streams. Every stream is padded to at least
    4096 bytes so all of them live in normal sectors (smaller ones would
    belong in the mini stream, which this writer does not build); the
    readers under test ignore bytes past the data they are told about."""
    sec, endchain, free, fatsect, nostream = 512, 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD, 0xFFFFFFFF
    items = sorted(streams.items(), key=lambda kv: (len(kv[0]), kv[0].upper()))
    datas = [(name, data.ljust(max(4096, -(-len(data) // sec) * sec), b"\0")) for name, data in items]
    dir_secs = -(-(len(datas) + 1) // 4)
    stream_secs = sum(len(d) // sec for _, d in datas)
    fat_secs = 1
    while 128 * fat_secs < fat_secs + dir_secs + stream_secs:
        fat_secs += 1
    fat = [fatsect] * fat_secs

    def chain(n):
        start = len(fat)
        fat.extend(start + i + 1 if i < n - 1 else endchain for i in range(n))
        return start

    dir_start = chain(dir_secs)
    starts = [chain(len(d) // sec) for _, d in datas]
    fat += [free] * (fat_secs * 128 - len(fat))

    def entry(name, kind, child, right, start, size):
        raw = name.encode("utf-16-le") + b"\0\0"
        return (raw.ljust(64, b"\0") + struct.pack("<HBB", len(raw) if name else 0, kind, 1) + struct.pack("<III", nostream, right, child)
                + b"\0" * 16 + b"\0" * 4 + b"\0" * 16 + struct.pack("<IQ", start, size))

    entries = [entry("Root Entry", 5, 1 if datas else nostream, nostream, endchain, 0)]
    for i, ((name, data), start) in enumerate(zip(datas, starts)):
        entries.append(entry(name, 2, nostream, i + 2 if i + 1 < len(datas) else nostream, start, len(data)))
    entries += [entry("", 0, nostream, nostream, 0, 0)] * (dir_secs * 4 - len(entries))
    difat = [i for i in range(fat_secs)] + [free] * (109 - fat_secs)
    header = (b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\0" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\0" * 6
              + struct.pack("<IIIIIIIII", 0, fat_secs, dir_start, 0, 4096, endchain, 0, endchain, 0) + struct.pack("<109I", *difat))
    body = struct.pack(f"<{len(fat)}I", *fat) + b"".join(entries) + b"".join(d for _, d in datas)
    path.write_bytes(header + body)


def make_doc(path: Path, encrypted: bool = False) -> None:
    """Word 97-2003: a FIB, the text in two pieces (one 8-bit cp1252, one
    UTF-16 for Greek), a table (cell marks), a hyperlink field, and the
    piece table (CLX) in the 1Table stream."""
    piece1 = ("Lighthouse ledger\rThe zeppelin budget for caf\xe9 visits\r"
              "Item\x07Cost\x07\x07Walrus feed\x07450\x07\x07"
              "\x13 HYPERLINK \"https://example.invalid\" \x14marmalade site\x15\r")
    piece2 = "Greek: Καλημέρα octopus\r"
    text_at = 1024
    p1 = piece1.encode("cp1252")
    p2_at = text_at + len(p1) + (len(p1) % 2)
    word = bytearray(p2_at + len(piece2) * 2)
    struct.pack_into("<HHHHHHH", word, 0, 0xA5EC, 0x00C1, 0, 0x0409, 0, 0x0200 | (0x0100 if encrypted else 0), 0x00BF)
    struct.pack_into("<H", word, 0x20, 14)                 # csw
    struct.pack_into("<H", word, 0x3E, 22)                 # cslw
    struct.pack_into("<I", word, 0x4C, len(piece1) + len(piece2))  # ccpText
    struct.pack_into("<H", word, 0x98, 0x5D)               # cbRgFcLcb
    word[text_at:text_at + len(p1)] = p1
    word[p2_at:] = piece2.encode("utf-16-le")
    cps = [0, len(piece1), len(piece1) + len(piece2)]
    pcds = [(text_at * 2) | 0x40000000, p2_at]
    plc = struct.pack("<3I", *cps) + b"".join(struct.pack("<HIH", 0, fc, 0) for fc in pcds)
    clx = b"\x01" + struct.pack("<H", 2) + b"\0\0" + b"\x02" + struct.pack("<I", len(plc)) + plc  # a Prc to skip, then the Pcdt
    struct.pack_into("<II", word, 0x9A + 33 * 8, 0, len(clx))  # fcClx, lcbClx
    write_cfb(path, {"WordDocument": bytes(word), "1Table": clx})


def rec(rtype: int, data: bytes = b"", ver: int = 0, inst: int = 0) -> bytes:
    """One [MS-PPT] record: header (version/instance, type, length) + data."""
    return struct.pack("<HHI", (inst << 4) | ver, rtype, len(data)) + data


def make_ppt(path: Path, repeated_refs: int = 0, header_token: int = 0xE391C05F) -> None:
    """PowerPoint 97-2003, older style: slide text in the document's
    SlideListWithText; slide 1 also has a text box in its own slide
    container. The slide containers are stored out of order and a master
    full of placeholder text sits first: the reader must follow the
    persist directory, keep presentation order and skip masters.
    `repeated_refs` more slide entries all naming slide 1 (a hostile file);
    `header_token` 0xF3D1C4DF marks an encrypted presentation."""
    def persist(ref):
        return rec(0x03F3, struct.pack("<IIIII", ref, 0, 0, 256 + ref, 0))
    def chars(s):
        return rec(0x0FA0, s.encode("utf-16-le"))
    def bytes_atom(s):
        return rec(0x0FA8, s.encode("cp1252"))
    slwt = rec(0x0FF0, persist(1) + rec(0x0F9F, struct.pack("<I", 0)) + bytes_atom("Lighthouse plan")
               + rec(0x0F9F, struct.pack("<I", 1)) + chars("Ünïcode zebra crossing")
               + persist(2) + rec(0x0F9F, struct.pack("<I", 0)) + bytes_atom("Second slide about the quokka")
               + persist(1) * repeated_refs, ver=0xF)
    document = rec(0x03E8, slwt, ver=0xF)
    master = rec(0x03F8, rec(0x040C, bytes_atom("Click to edit Master title style"), ver=0xF), ver=0xF)
    slide1 = rec(0x03EE, rec(0x040C, rec(0xF002, rec(0xF00D, chars("Textbox walrus"), ver=0xF), ver=0xF), ver=0xF), ver=0xF)
    slide2 = rec(0x03EE, rec(0x03EF, b"\0" * 24), ver=0xF)
    stream = bytearray(master)
    offsets = {}
    for name, blob in (("slide2", slide2), ("slide1", slide1), ("doc", document)):
        offsets[name] = len(stream)
        stream += blob
    persist_dir_at = len(stream)
    stream += rec(0x1772, struct.pack("<IIII", (3 << 20) | 1, offsets["slide1"], offsets["slide2"], offsets["doc"]))
    user_edit_at = len(stream)
    stream += rec(0x0FF5, struct.pack("<IHBBIIIIHH", 0, 0, 0, 3, 0, persist_dir_at, 3, 4, 0, 0))
    current_user = rec(0x0FF6, struct.pack("<IIIHHBBH", 0x14, header_token, user_edit_at, 0, 0x03F4, 3, 0, 0))
    write_cfb(path, {"PowerPoint Document": bytes(stream), "Current User": current_user})


def biff(rtype: int, data: bytes = b"") -> bytes:
    return struct.pack("<HH", rtype, len(data)) + data


def make_xls(path: Path, sheets: list[tuple[str, list]] | None = None) -> None:
    """Excel 97-2003 (BIFF8): a workbook globals substream naming the
    sheets, then each sheet with text (LABEL) and number (NUMBER) cells."""
    def cell(r, c, v):
        if isinstance(v, str):
            return biff(0x0204, struct.pack("<HHHHB", r, c, 0, len(v), 0) + v.encode("latin-1"))
        return biff(0x0203, struct.pack("<HHHd", r, c, 0, v))
    sheets = sheets or [("Ledger", [(0, 0, "item"), (0, 1, "cost"), (1, 0, "kumquat crates"), (1, 1, 42.0), (2, 1, 2.5)])]
    bof_globals = biff(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0, 1997, 0, 6))
    bodies = [biff(0x0809, struct.pack("<HHHHII", 0x0600, 0x0010, 0, 1997, 0, 6)) + b"".join(cell(*x) for x in cells) + biff(0x000A) for _, cells in sheets]
    def globals_with(offsets):
        boundsheets = b"".join(biff(0x0085, struct.pack("<IHBB", at, 0, len(name), 0) + name.encode("latin-1")) for at, (name, _) in zip(offsets, sheets))
        return bof_globals + biff(0x0042, struct.pack("<H", 1200)) + boundsheets + biff(0x000A)
    at, offsets = len(globals_with([0] * len(sheets))), []  # the sheets follow the globals
    for body in bodies:
        offsets.append(at)
        at += len(body)
    write_cfb(path, {"Workbook": globals_with(offsets) + b"".join(bodies)})


def make_msg(path: Path) -> None:
    """An Outlook .msg: its properties as UTF-16 string streams."""
    def prop(tag, text):
        return f"__substg1.0_{tag}001F", text.encode("utf-16-le")
    write_cfb(path, dict([
        prop("0037", "Ferry timetable change"),
        prop("0C1A", "Harbour Office"),
        prop("0E04", "All Staff"),
        prop("1000", "From Monday the ferry to the islands leaves at 07:40, carrying the cormorant survey team."),
    ]))


# ---------------------------------------------------------------- photos, video, audio

def shape_image(colour: str, shape: str, size=(512, 512)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    w, h = size
    box = [w * 0.2, h * 0.12, w * 0.8, h * 0.88] if shape != "circle" or w == h else [w * 0.3, h * 0.1, w * 0.7, h * 0.9]
    (ImageDraw.Draw(image).ellipse if shape == "circle" else ImageDraw.Draw(image).rectangle)(box, fill=colour)
    return image


def text_page(lines: list[str]) -> Image.Image:
    image = Image.new("RGB", (1600, 900), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("arial.ttf", 44)
    for i, line in enumerate(lines):
        draw.text((70, 70 + i * 70), line, fill="black", font=font)
    return image


def make_heic(path: Path) -> None:
    import pillow_heif  # dev only: writes the test file; the app reads HEIC with pi-heif

    pillow_heif.from_pillow(shape_image("red", "circle")).save(str(path), quality=90)


# (container format, video codec, frame size, extra muxer options) per extension.
VIDEO_FORMATS = {
    ".3gp": ("3gp", "h263", (352, 288), {}),
    ".wmv": ("asf", "wmv2", (640, 360), {}),
    ".mts": ("mpegts", "libx264", (640, 360), {"mpegts_m2ts_mode": "1"}),
    ".m2ts": ("mpegts", "libx264", (640, 360), {"mpegts_m2ts_mode": "1"}),
    ".mpg": ("mpeg", "mpeg1video", (640, 360), {}),
    ".mpeg": ("mpeg", "mpeg2video", (640, 360), {}),
}


def size_of(ext: str) -> tuple[int, int]:
    return VIDEO_FORMATS[ext][2]


def make_video(path: Path) -> None:
    """3 s of a red circle, then 3 s of a blue square, at 25 fps (MPEG-1/2
    only allow standard frame rates) with a keyframe every half second."""
    import av

    fmt, codec, size, options = VIDEO_FORMATS[path.suffix]
    scenes = [shape_image("red", "circle", size), shape_image("blue", "square", size)]
    with av.open(str(path), mode="w", format=fmt, options=options) as container:
        stream = container.add_stream(codec, rate=25)
        stream.width, stream.height, stream.pix_fmt = size[0], size[1], "yuv420p"
        stream.gop_size = 12
        for image in scenes:
            for _ in range(75):
                for packet in stream.encode(av.VideoFrame.from_image(image)):
                    container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


# (container format, audio codec, sample rate) per extension.
AUDIO_FORMATS = {".wma": ("asf", "wmav2", 44100), ".aac": ("adts", "aac", 44100), ".opus": ("ogg", "libopus", 48000)}


def transcode_audio(wav: Path, out: Path) -> None:
    import av

    fmt, codec, rate = AUDIO_FORMATS[out.suffix]
    with av.open(str(wav)) as src, av.open(str(out), mode="w", format=fmt) as dst:
        stream = dst.add_stream(codec, rate=rate, layout="mono")
        stream.bit_rate = 64000  # WMA's encoder has no default
        for frame in src.decode(audio=0):
            frame.pts = None
            for packet in stream.encode(frame):
                dst.mux(packet)
        for packet in stream.encode(None):
            dst.mux(packet)


def make_tone_wav(path: Path, seconds: float = 2.0) -> None:
    import math
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(int(16000 * seconds))))


# ---------------------------------------------------------------- checks

def texts(blocks) -> str:
    return "\n".join(b.text for b in blocks)


def main() -> int:
    for needed in (TEXT_MODEL_DIR / "model.onnx", CLIP_MODEL_DIR / "onnx"):
        if not needed.exists():
            print(f"Model not found: {needed} — run the scripts/download_*.py scripts first.")
            return 1

    work = Path(tempfile.mkdtemp(prefix="intellifile_formats_"))
    try:
        # --- 1. Discovery: every new type is picked up; .ts stays TypeScript; document caps ---
        assert NEW_DOCUMENTS <= SUPPORTED_EXTENSIONS, NEW_DOCUMENTS - SUPPORTED_EXTENSIONS
        assert NEW_IMAGES <= IMAGE_EXTENSIONS and NEW_VIDEOS <= VIDEO_EXTENSIONS and NEW_AUDIO <= AUDIO_EXTENSIONS
        assert NEW_AUDIO <= SUPPORTED_EXTENSIONS, "audio is transcribed through extract_document"
        assert ".ts" in CODE_EXTENSIONS and ".ts" not in VIDEO_EXTENSIONS, ".ts is TypeScript, never an MPEG transport stream"
        assert all(size_cap(e) == MAX_DOCUMENT_FILE_BYTES for e in NEW_DOCUMENTS), {e: size_cap(e) for e in NEW_DOCUMENTS}
        print("1. Discovery: all new types supported, .ts stays code, new documents capped at 50 MB: OK")

        # --- 1b. Search filters and collection questions know the new types ---
        def keeps(query, path):
            return parse_query(query).filters.matches(path, 1, 0.0)
        assert keeps("type:tif scans", "C:/s/a.tiff") and keeps("type:tiff scans", "C:/s/a.tif") and not keeps("type:tif x", "C:/s/a.png")
        assert keeps("type:heic x", "C:/p/a.HEIF") and keeps("type:mpg x", "C:/v/a.mpeg") and keeps("type:mts x", "C:/v/a.m2ts")
        assert keeps("type:email canoe", "C:/m/a.msg") and keeps("type:emails canoe", "C:/m/a.eml") and not keeps("type:email x", "C:/m/a.pdf")
        assert keeps("type:document x", "C:/d/a.odt") and keeps("type:image x", "C:/p/a.heic") and keeps("type:video x", "C:/v/a.wmv") and keeps("type:audio x", "C:/a/a.opus")
        lib = ["C:/m/a.eml", "C:/m/b.msg", "C:/d/c.xls", "C:/d/d.ppt", "C:/d/e.odp", "C:/p/f.heic", "C:/v/g.mts", "C:/a/h.opus", "C:/b/i.epub"]
        for question, answer in (("how many emails do I have", "2 emails"), ("are there any spreadsheets", "Yes: 1 spreadsheet (c.xls)"),
                                 ("how many presentations", "2 presentations"), ("any e-books?", "Yes: 1 e-book (i.epub)"),
                                 ("are there any photos", "Yes: 1 photo (f.heic)"), ("any videos", "Yes: 1 video (g.mts)"),
                                 ("do I have any audio files", "Yes: 1 audio file (h.opus)")):
            got = library_answer(question, lib)
            assert got and got.startswith(answer), f"{question!r} -> {got!r}"
        print("1b. type: filters (two spellings of one format, type:email, the kind tabs) and collection questions cover the new types: OK")

        docs = work / "docs"
        docs.mkdir()
        builders = {
            "journal.odt": make_odt, "harvest.ods": make_ods, "observatory.odp": make_odp, "voyage.epub": make_epub,
            "canoe.eml": make_eml, "newsletter.eml": lambda p: make_eml(p, html_only=True), "ferry.msg": make_msg,
            "ledger.doc": make_doc, "ledger.xls": make_xls, "plan.ppt": make_ppt,
        }
        for name, build in builders.items():
            build(docs / name)

        # --- 2. Structure of what each reader returns ---
        odt = extract_document(docs / "journal.odt")
        narwhal = next(b for b in odt if "narwhal" in b.text)
        assert narwhal.section == "Expedition Journal" and narwhal.heading == "Day one", narwhal
        assert "narwhal near the ice shelf" in narwhal.text, "text:s is a space"
        assert any("Sled dogs | 12" in b.text for b in odt) and any("thermal blanket" in b.text for b in odt), odt
        ods = extract_document(docs / "harvest.ods")
        assert [b.section for b in ods] == ["Harvest", "Notes"], ods
        assert "rhubarb 7" in ods[0].text and len(ods[0].text.splitlines()) == MAX_SPREADSHEET_ROWS, len(ods[0].text.splitlines())
        odp = extract_document(docs / "observatory.odp")
        assert [(b.page_number, b.heading) for b in odp] == [(1, "Observatory Budget"), (2, "Timeline")], odp
        assert "telescopes" in odp[0].text and "comet survey" in odp[0].text, "slide text and speaker notes"
        epub = extract_document(docs / "voyage.epub")
        assert [b.section for b in epub] == ["The Voyage", "The Return"], f"spine order expected, got {[b.section for b in epub]}"
        assert "clockmaker" in epub[0].text and "alert" not in texts(epub) and "color" not in texts(epub), epub
        eml = extract_document(docs / "canoe.eml")
        assert eml[0].heading == "Invoice for the canoe rental" and "From: Ana Silva" in eml[0].text and "Date:" in eml[0].text, eml[0]
        assert "85 euros" in texts(eml) and "HTML twin" not in texts(eml) and "porcupine" not in texts(eml), "plain body preferred, attachments ignored"
        html_eml = extract_document(docs / "newsletter.eml")
        assert "Prune the wisteria in late winter" in texts(html_eml) and "track()" not in texts(html_eml), html_eml
        msg = extract_document(docs / "ferry.msg")
        assert msg[0].heading == "Ferry timetable change" and "Harbour Office" in msg[0].text and "cormorant" in texts(msg), msg
        doc = texts(extract_document(docs / "ledger.doc"))
        for expected in ("Lighthouse ledger", "zeppelin budget for café visits", "Item | Cost", "Walrus feed | 450", "marmalade site", "Καλημέρα octopus"):
            assert expected in doc, f"{expected!r} missing from {doc!r}"
        assert "HYPERLINK" not in doc and "\x07" not in doc and "\x13" not in doc, f"field codes / cell marks must go: {doc!r}"
        xls = extract_document(docs / "ledger.xls")
        assert xls[0].section == "Ledger" and "kumquat crates 42" in xls[0].text and "2.5" in xls[0].text, xls
        ppt = extract_document(docs / "plan.ppt")
        assert [b.page_number for b in ppt] == [1, 2], ppt
        assert "Lighthouse plan" in ppt[0].text and "Ünïcode zebra crossing" in ppt[0].text and "Textbox walrus" in ppt[0].text, ppt[0]
        assert "quokka" in ppt[1].text and "Master" not in texts(ppt), ppt
        print("2. Readers: headings/sections (odt), sheets + row cap + repeated empty rows (ods), slides + notes (odp, ppt),"
              " spine order (epub), headers + plain body (eml/msg), pieces + fields + tables (doc), sheets (xls): OK")

        # --- 3. Zip bombs and protected files are refused with a reason ---
        bomb = work / "bomb.odt"
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            z.writestr("content.xml", b" " * (5 * 1024 * 1024))
        refused = []
        for path in (bomb, bomb.with_suffix(".epub")):
            if path.suffix == ".epub":
                shutil.copy(bomb, path)
            try:
                extract_document(path)
            except ValueError as e:
                refused.append(str(e))
        assert len(refused) == 2 and all("zip bomb" in r for r in refused), refused
        locked = work / "locked.odt"
        write_odf(locked, "application/vnd.oasis.opendocument.text", "<office:text><text:p>x</text:p></office:text>",
                  '<manifest:encryption-data manifest:checksum-type="SHA1"/>')
        locked_doc = work / "locked.doc"
        make_doc(locked_doc, encrypted=True)
        for path in (locked, locked_doc):
            try:
                extract_document(path)
                raise AssertionError(f"{path.name} is password-protected and must be refused")
            except ValueError as e:
                assert "password" in str(e), e
        print("3. Zip-bomb .odt/.epub refused; password-protected .odt and .doc refused with a reason: OK")
        for check in HOSTILE_CHECKS:  # 3b-3m: hostile and odd files (2026-10-05 review)
            check(work)

        # --- 4. Real files saved by Microsoft Office, when available ---
        real = {"real_report.doc": ["Quarterly Zanzibar Report", "flamingo budget", "Αθήνα", "Walrus feed | 450", "marmalade website", "pelican harbour"],
                "real_budget.xls": ["kumquat crates 42", "aardvark insurance"],
                "real_pitch.ppt": ["Tangerine Market Size", "Pelican Roadmap"]}
        if all((REAL_OFFICE_DIR / n).exists() for n in real):
            for name, words in real.items():
                blocks = extract_document(REAL_OFFICE_DIR / name)
                got = texts(blocks)
                missing = [w for w in words if w not in got]
                assert not missing, f"{name}: {missing} missing from {got[:400]!r}"
                assert "HYPERLINK" not in got and "Click to edit" not in got, got
            pages = [b.page_number for b in extract_document(REAL_OFFICE_DIR / "real_pitch.ppt")]
            assert pages == [1, 2], pages
            print(f"4. Real Office 97-2003 files saved by Microsoft Office ({', '.join(real)}) read correctly: OK")
        else:
            print(f"4. (no real Office files in {REAL_OFFICE_DIR}: skipped)")

        # --- 5. Damaged files of every new document type: failed with a reason, the rest indexed ---
        broken = {
            "broken.odt": b"PK\x03\x04 not a zip", "broken.ods": b"PK\x03\x04 nor this", "broken.odp": os.urandom(300),
            "broken.epub": b"PK\x03\x04 truncated", "broken.eml": bytes(range(256)) * 10, "broken.msg": write_and_read_empty_ole(work),
            "broken.doc": os.urandom(2000), "broken.xls": os.urandom(2000), "broken.ppt": b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + os.urandom(1000),
        }
        for name, data in broken.items():
            (docs / name).write_bytes(data)
        # Media lives in the same folder: one scan, like a real Pictures/Music folder.
        media = docs
        make_heic(media / "red circle.heic")
        shutil.copy(media / "red circle.heic", media / "red circle copy.heif")
        tiff_pages = [shape_image("red", "circle"), text_page(["Scanned delivery note", "Consignment of saffron for the bakery", "Signed by the warehouse manager"])]
        tiff_pages[0].save(media / "scan.tiff", save_all=True, append_images=tiff_pages[1:])
        shape_image("red", "circle").save(media / "single.tif")
        (media / "broken.heic").write_bytes(b"\0\0\0\x18ftypheic" + os.urandom(500))
        for ext in VIDEO_FORMATS:
            make_video(media / f"clip{ext}")
        (media / "broken.wmv").write_bytes(os.urandom(4000))

        # Keyframes decode in every new container, and times start at 0 like a player shows them.
        for ext in VIDEO_FORMATS:
            frames = extract_keyframes(media / f"clip{ext}")
            stamps = [t for t, _ in frames]
            assert len(frames) >= 6 and stamps[0] < 0.5 and 5.0 < stamps[-1] < 6.5, f"{ext}: {stamps}"
            # The thumbnail of every moment shows that moment, the last one included (MPEG
            # program/transport streams seek past the requested time unless stepped back).
            for t in stamps:
                r, _, b = decode_frame_at(media / f"clip{ext}", round(t, 2)).convert("RGB").getpixel((size_of(ext)[0] // 2, size_of(ext)[1] // 2))
                assert (r > 150 and b < 100) if t < 2.9 else (b > 150 and r < 100), f"{ext}: frame at {t:.2f} s shows the wrong scene {(r, b)}"
        print("5. PyAV decodes .3gp (H.263), .wmv (WMV2), .mts/.m2ts (H.264 in BDAV), .mpg (MPEG-1), .mpeg (MPEG-2);"
              " times start at 0; the frame at every keyframe time is that moment's: OK")

        made_speech = False
        try:
            import speech_synth
            made_speech = speech_synth.available() and (WHISPER_DIR / "onnx").exists()
        except Exception:
            made_speech = False
        spoken = {".wma": ("The pelican flew over the harbour at sunrise.", "pelican"),
                  ".aac": ("Remember to water the cactus every second week.", "cactus"),
                  ".opus": ("The volcano tour starts at nine in the morning.", "volcano")}
        for ext, (sentence, _) in spoken.items():
            wav = work / f"speech{ext}.wav"
            if made_speech:
                speech_synth.synthesize_wav(sentence, wav)
            else:
                make_tone_wav(wav)
            transcode_audio(wav, media / f"memo{ext}")
            seconds = len(load_audio_as_mono_16k(media / f"memo{ext}")) / 16000
            assert 1.0 < seconds < 15, f"{ext} decoded to {seconds:.2f} s"
        (media / "broken.opus").write_bytes(b"OggS" + os.urandom(800))
        print("6. PyAV decodes .wma (WMA v2), .aac (ADTS), .opus (Ogg Opus) to 16 kHz mono: OK")

        # --- 7. One real folder scan over everything; search finds each file by its own word ---
        text_model = EmbeddingModel(TEXT_MODEL_DIR)
        clip_model = ClipModel(CLIP_MODEL_DIR)
        vector_store = LanceDBVectorStore(str(work / "vectors"))
        keyword_store = KeywordStore(work / "keyword.db")
        record_store = FileRecordStore(work / "files.db")
        transcriber = Transcriber(WHISPER_DIR) if (WHISPER_DIR / "onnx").exists() else None
        indexer = Indexer(text_model, vector_store, keyword_store, record_store, transcriber=transcriber)
        visual_indexer = VisualIndexer(clip_model, vector_store, record_store)
        search = SearchService(text_model, vector_store, keyword_store, record_store)
        visual = VisualSearchService(clip_model, vector_store, record_store)
        RECENT_FAILURES.clear()
        index_folder(indexer, str(docs), visual_indexer=visual_indexer)
        failures = {Path(f["path"]).name: f["error"] for f in RECENT_FAILURES}

        expected_failed = set(broken) | {"broken.wmv"} | ({"broken.opus"} if transcriber else set())
        assert expected_failed <= set(failures), f"not reported as failed: {expected_failed - set(failures)}; failures: {failures}"
        assert all(failures[n] for n in expected_failed)
        # A parser library's own words never reach the Status screen (2026-10-05: "Exceeds the limit (4300 digits)...").
        assert failures["broken.ppt"] == "The file is damaged or not a valid PowerPoint file.", failures["broken.ppt"]
        unexpected = set(failures) - expected_failed - ({"memo.wma", "memo.aac", "memo.opus"} if transcriber is None else set())
        assert not unexpected, {n: failures[n] for n in unexpected}
        assert all(h["filename"] != "broken.heic" for h in visual.search("a red circle", top_k=30, apply_cutoff=False)), "a damaged photo is not a photo"
        print("7. Damaged files reported as failed with a reason, nothing else failed: OK")
        for name in sorted(expected_failed):
            print(f"   {name}: {failures[name][:90]}")

        words = {"journal.odt": "narwhal ice shelf", "harvest.ods": "rhubarb", "observatory.odp": "telescopes comet survey",
                 "voyage.epub": "clockmaker schooner", "canoe.eml": "canoe rental", "newsletter.eml": "wisteria",
                 "ferry.msg": "cormorant survey ferry", "ledger.doc": "zeppelin budget", "ledger.xls": "kumquat crates",
                 "plan.ppt": "quokka"}
        for name, query in words.items():
            hits = search.search(query, top_k=3)
            assert hits and hits[0]["filename"] == name, f"{query!r} -> {[h['filename'] for h in hits]}"
        print(f"8. Text search finds each new document by its own words ({len(words)} files): OK")

        for name in ("red circle.heic", "red circle copy.heif", "scan.tiff", "single.tif"):
            assert record_store.get_by_path(str(media / name)).indexed, name
        top = [h["filename"] for h in visual.search("a red circle", top_k=20, apply_cutoff=False)]
        for name in ("red circle.heic", "red circle copy.heif", "scan.tiff", "single.tif"):
            assert name in top[:12], f"{name} not among the red circles: {top}"
        for name in ("red circle.heic", "scan.tiff"):
            thumb = Image.open(io.BytesIO(generate_thumbnail(media / name, max_size=96)))
            assert thumb.format == "JPEG" and max(thumb.size) == 96, (name, thumb.size)
        print("9. HEIC/HEIF/TIF/TIFF photos indexed, found by 'a red circle', thumbnails render: OK")

        if ocr.available():
            hits = search.search("saffron consignment delivery note", top_k=3)
            assert hits and hits[0]["filename"] == "scan.tiff", f"page 2 of the TIFF must be read: {[h['filename'] for h in hits]}"
            print("10. OCR reads every page of a multi-page TIFF (text on page 2 found): OK")
        else:
            print("10. (Windows OCR not available: multi-page TIFF OCR skipped)")

        for ext in VIDEO_FORMATS:
            name = f"clip{ext}"
            hit = next((h for h in visual.search("a blue square", top_k=30, apply_cutoff=False) if h["filename"] == name), None)
            assert hit is not None, f"{name} not found by 'a blue square'"
            t = hit["timestamp_offset_seconds"]
            assert 3.0 <= t < 6.5, f"{name}: blue square matched at {t} s, expected the second scene (3-6 s)"
            frame = Image.open(io.BytesIO(generate_thumbnail(media / name, timestamp_offset_seconds=t, max_size=96)))
            r, _, b = frame.convert("RGB").getpixel((frame.width // 2, frame.height // 2))
            assert frame.format == "JPEG" and b > 150 and r < 100, f"{name}: the frame at {t} s must show the blue square, centre pixel is {(r, b)}"
        print(f"11. Videos ({', '.join(VIDEO_FORMATS)}) found by 'a blue square' at the right moment; frame at that moment renders: OK")

        if transcriber is None:
            print("12. (Whisper model not installed: audio transcription skipped, decoding checked in 6)")
        elif not made_speech:
            print("12. (no speech engine to make a recording: transcription skipped, decoding checked in 6)")
        else:
            for ext, (_, word) in spoken.items():
                hits = search.search(word, top_k=3)
                assert hits and hits[0]["filename"] == f"memo{ext}", f"{word!r} -> {[h['filename'] for h in hits]}"
            print("12. Spoken words find the .wma, .aac and .opus recordings: OK")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def write_and_read_empty_ole(work: Path) -> bytes:
    """A valid compound file with no message in it (a .msg that isn't one)."""
    path = work / "empty_ole.bin"
    write_cfb(path, {"Something Else": b"\x01\x02"})
    return path.read_bytes()


# ---------------------------------------------------------------- hostile and odd files (2026-10-05 review)
# Each check below failed before its fix. They need no model, so they run
# on their own too:  python -c "import prototype_more_formats as m; m.run_hostile_checks()"

def make_overlapping_doc(path: Path, repeats: int = 4000) -> None:
    """A .doc whose piece table lists the same 4,000 characters 4,000 times
    (16 M characters from a 5 KB text): overlapping pieces are legal bytes."""
    text = ("overlap " * 500).encode("cp1252")
    text_at = 1024
    word = bytearray(text_at + len(text))
    struct.pack_into("<HHHHHHH", word, 0, 0xA5EC, 0x00C1, 0, 0x0409, 0, 0x0200, 0x00BF)
    struct.pack_into("<H", word, 0x20, 14)
    struct.pack_into("<H", word, 0x3E, 22)
    struct.pack_into("<I", word, 0x4C, len(text))
    struct.pack_into("<H", word, 0x98, 0x5D)
    word[text_at:] = text
    cps = [0] + [len(text) - 1, 1] * repeats + [len(text) - 1]  # every other piece is 1..3999 again
    plc = struct.pack(f"<{len(cps)}I", *cps) + struct.pack("<HIH", 0, (text_at * 2) | 0x40000000, 0) * (len(cps) - 1)
    clx = b"\x02" + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<II", word, 0x9A + 33 * 8, 0, len(clx))
    write_cfb(path, {"WordDocument": bytes(word), "1Table": clx})


def check_old_office(work: Path) -> None:
    from app.extraction.legacy_office_extractor import _clean_word_text

    make_overlapping_doc(work / "overlap.doc")
    try:
        got = extract_document(work / "overlap.doc")
        raise AssertionError(f"overlapping pieces must be refused, got {sum(len(b.text) for b in got):,} characters")
    except ValueError as e:
        assert "damaged" in str(e), e
    print("3b. .doc whose pieces overlap (16 M characters from 4 KB) refused as damaged: OK")

    make_ppt(work / "amplified.ppt", repeated_refs=3000)
    pages = [b.page_number for b in extract_document(work / "amplified.ppt")]
    assert pages == [1, 2], f"3,000 entries naming one slide must read it once: {len(pages)} slides"
    make_ppt(work / "encrypted.ppt", header_token=0xF3D1C4DF)
    try:
        extract_document(work / "encrypted.ppt")
        raise AssertionError("an encrypted .ppt (Current User token F3D1C4DF) must be refused")
    except ValueError as e:
        assert "password" in str(e), e
    print("3c. .ppt: repeated slide entries read once; encrypted presentation refused as password-protected: OK")

    cleaned = _clean_word_text("Intro \x13 PAGE \x14 3 \x15 middle \x13 TOC the rest of the letter, never closed")
    assert "Intro" in cleaned and "middle" in cleaned and "the rest of the letter" in cleaned and "PAGE" not in cleaned, repr(cleaned)
    print("3d. .doc field left open at the end: the text after it is kept: OK")

    big = [(0, 0, "bigsheet header"), (60000, 200, "far corner")]  # 60,001 x 201 = 12 M cells
    make_xls(work / "big.xls", [("Huge", big), ("Small", [(0, 0, "ostrich ledger")])])
    blocks = extract_document(work / "big.xls")
    assert [b.section for b in blocks] == ["Small"] and "ostrich ledger" in blocks[0].text, blocks
    print("3e. .xls sheet of 12 M cells skipped, the other sheet read: OK")


def check_msg(work: Path) -> None:
    damaged = work / "damaged.msg"
    damaged.write_bytes(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + bytes(range(256)) * 4)
    try:
        extract_document(damaged)
        raise AssertionError("a .msg with an OLE header that does not parse must fail")
    except ValueError as e:
        assert "damaged Outlook message" in str(e), e
    catalogue = work / "tcl.msg"
    catalogue.write_text("::msgcat::mcset de Open Oeffnen\n")
    assert extract_document(catalogue) == [], "a Tcl/installer message catalogue is not mail"
    print("3f. .msg: damaged Outlook message reported as such; a non-mail .msg stays silently empty: OK")


def write_epub(path: Path, opf_items: list[tuple[str, str]], files: dict[str, bytes], encryption: str | None = None) -> None:
    container = ('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    manifest = "".join(f'<item id="{i}" href="{h}" media-type="application/xhtml+xml"/>' for i, h in opf_items)
    spine = "".join(f'<itemref idref="{i}"/>' for i, _ in opf_items)
    opf = f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>{manifest}</manifest><spine>{spine}</spine></package>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        if encryption is not None:
            z.writestr("META-INF/encryption.xml", encryption)
        z.writestr("OEBPS/content.opf", opf)
        for name, data in files.items():
            z.writestr(name, data)


def chapter_bytes(title: str, text: str, encoding: str = "utf-8") -> bytes:
    declaration = f'<?xml version="1.0" encoding="{encoding}"?>'
    return (declaration + f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head><body><p>{text}</p></body></html>').encode(encoding)


def check_epub(work: Path) -> None:
    # Spine hrefs with #fragments and absolute /OEBPS/ paths; a legacy-encoded chapter.
    write_epub(work / "odd.epub", [("a", "text/one.xhtml#start"), ("b", "/OEBPS/text/two.xhtml"), ("c", "text/three.xhtml")], {
        "OEBPS/text/one.xhtml": chapter_bytes("One", "The lamplighter climbed the tower."),
        "OEBPS/text/two.xhtml": chapter_bytes("Two", "A gondola drifted past the bakery."),
        "OEBPS/text/three.xhtml": chapter_bytes("Three", "Caf\xe9 cr\xe8me with the harbourmaster.", "windows-1252"),
    })
    blocks = extract_document(work / "odd.epub")
    assert [b.section for b in blocks] == ["One", "Two", "Three"], [b.section for b in blocks]
    assert "Café crème" in blocks[2].text, blocks[2].text
    # A spine none of whose entries exist: the chapters are still read, in zip order.
    write_epub(work / "lost_spine.epub", [("x", "missing.xhtml")], {"OEBPS/chapter.xhtml": chapter_bytes("Only", "The orchard keeper's diary.")})
    assert "orchard keeper" in texts(extract_document(work / "lost_spine.epub"))
    # UTF-16 chapter, and a book with no encryption.xml whose only chapter is not UTF-8: never "DRM".
    write_epub(work / "utf16.epub", [("u", "u.xhtml")], {"OEBPS/u.xhtml": b"\xff\xfe" + chapter_bytes("Wide", "Saffron fields at dusk.", "utf-16")[2:]})
    assert "Saffron fields" in texts(extract_document(work / "utf16.epub"))
    write_epub(work / "latin.epub", [("l", "l.xhtml")], {"OEBPS/l.xhtml": "<html><body><p>Bj\xf6rk na \xedsland</p></body></html>".encode("latin-1")})
    assert "Björk" in texts(extract_document(work / "latin.epub"))
    # Real DRM: encryption.xml lists the chapter, whose bytes are ciphertext.
    encryption = ('<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
                  '<enc:EncryptedData><enc:CipherData><enc:CipherReference URI="OEBPS/secret.xhtml"/></enc:CipherData></enc:EncryptedData></encryption>')
    write_epub(work / "drm.epub", [("s", "secret.xhtml")], {"OEBPS/secret.xhtml": bytes(range(256)) * 8}, encryption)
    try:
        extract_document(work / "drm.epub")
        raise AssertionError("a book whose chapters are listed in encryption.xml is DRM-protected")
    except ValueError as e:
        assert "DRM" in str(e), e
    # A <title> regex bounded to the head: 30,000 unclosed <title tags (each
    # rescanned the whole chapter: quadratic) return at once.
    import time
    write_epub(work / "titles.epub", [("t", "t.xhtml")], {"OEBPS/t.xhtml": b"<html><body><p>Kestrel notes</p>" + b"<title" * 30_000 + b"</body></html>"})
    started = time.perf_counter()
    assert "Kestrel notes" in texts(extract_document(work / "titles.epub"))
    assert time.perf_counter() - started < 1.0, f"title search took {time.perf_counter() - started:.1f} s"
    print("3g. EPUB: #fragment and /absolute spine paths, lost spine -> zip order, cp1252/UTF-16/latin-1 chapters read,"
          " DRM only when encryption.xml lists the chapter, <title> search bounded: OK")


def check_html_and_odf(work: Path) -> None:
    from app.extraction.html_extractor import html_to_text

    text = html_to_text("<html><head><title>Tab name</title><body><p>The ferret ate the biscuit.</p></body></html>")
    assert "ferret ate the biscuit" in text, repr(text)
    print("3h. HTML with an unclosed <head>: the body is still read: OK")

    nested = ("<office:spreadsheet><table:table table:name=\"Outer\"><table:table-row><table:table-cell><text:p>outer cell</text:p>"
              "<table:table table:name=\"Inner\"><table:table-row><table:table-cell><text:p>pangolin</text:p></table:table-cell></table:table-row></table:table>"
              "</table:table-cell></table:table-row></table:table></office:spreadsheet>")
    write_odf(work / "nested.ods", "application/vnd.oasis.opendocument.spreadsheet", nested)
    blocks = extract_document(work / "nested.ods")
    assert texts(blocks).count("pangolin") == 1 and [b.section for b in blocks] == ["Outer"], blocks
    hostile = work / "entities.odt"
    with zipfile.ZipFile(hostile, "w") as z:
        z.writestr("content.xml", f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa">]><office:document-content {ODF_NS}><office:body><office:text><text:p>&a;</text:p></office:text></office:body></office:document-content>')
    import base64
    huge_manifest = work / "manifest.odt"
    write_odf(huge_manifest, "application/vnd.oasis.opendocument.text", "<office:text><text:p>x</text:p></office:text>",
              "<!--" + base64.b64encode(os.urandom(1_200_000)).decode() + "-->")
    for path, reason in ((hostile, "DOCTYPE"), (huge_manifest, "manifest")):
        try:
            extract_document(path)
            raise AssertionError(f"{path.name} must be refused")
        except ValueError as e:
            assert reason in str(e), e
    print("3i. ODS nested table indexed once; content.xml with a DOCTYPE and an oversized manifest refused: OK")


def check_media_limits(work: Path) -> None:
    import logging

    import av

    from app.indexing import visual_indexer

    # A long all-black video: the fallback that samples one frame a second
    # must seek to its sample times, not decode every frame of the film.
    path = work / "black.mpg"
    with av.open(str(path), mode="w", format="mpeg") as container:
        stream = container.add_stream("mpeg1video", rate=25)
        stream.width, stream.height, stream.pix_fmt, stream.gop_size = 64, 48, "yuv420p", 25
        black = av.VideoFrame.from_image(Image.new("RGB", (64, 48), "black"))
        for _ in range(25 * 120):
            for packet in stream.encode(black):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    decoded = {"frames": 0}
    real_open = av.open

    class Counting:
        def __init__(self, inner):
            self._inner = inner
        def __getattr__(self, name):
            return getattr(self._inner, name)
        def __enter__(self):
            self._inner.__enter__()
            return self
        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)
        def decode(self, *a, **k):
            for frame in self._inner.decode(*a, **k):
                decoded["frames"] += 1
                yield frame

    av.open = lambda *a, **k: Counting(real_open(*a, **k))
    try:
        frames = visual_indexer.extract_keyframes(path, max_frames=10, every_seconds=1.0)
    finally:
        av.open = real_open
    assert frames and frames[-1][0] > 60, [t for t, _ in frames]
    assert decoded["frames"] < 800, f"{decoded['frames']} of 3,000 frames decoded for 10 samples"
    print(f"3j. Video fallback sampling seeks: {decoded['frames']} of 3,000 frames decoded for {len(frames)} samples: OK")

    # Multi-page TIFF: a page over the pixel cap is skipped, not converted.
    pages = [Image.new("L", (200, 100), 255), Image.new("L", (3000, 3000), 255)]
    pages[0].save(work / "pages.tiff", save_all=True, append_images=pages[1:])
    seen = []
    real_ocr, real_cap = ocr._ocr_image, Image.MAX_IMAGE_PIXELS
    ocr._ocr_image = lambda engine, p: (seen.append(p.name), "")[1]
    Image.MAX_IMAGE_PIXELS = 5_000_000
    try:
        ocr._ocr_pages_with_pillow(None, work / "pages.tiff")
    finally:
        ocr._ocr_image, Image.MAX_IMAGE_PIXELS = real_ocr, real_cap
    assert seen == ["p0.png"], f"the 9 MP page is over the 5 MP cap and must be skipped: {seen}"
    print("3k. TIFF OCR: a page over the pixel cap is skipped: OK")

    # Capture-date fallbacks say why in the debug log.
    from app.extraction.image_extractor import extract_captured_at

    records = []
    handler = logging.Handler(logging.DEBUG)
    handler.emit = records.append
    root = logging.getLogger("app")
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        not_media = work / "not_media.jpg"
        not_media.write_text("plain words")
        extract_captured_at(not_media)
        broken_video = work / "broken.mp4"
        broken_video.write_bytes(bytes(64))
        visual_indexer.video_captured_at(broken_video)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    logged = [r for r in records if r.exc_info]
    assert len(logged) == 2, [r.getMessage() for r in records]
    print("3l. Photo/video capture-date fallbacks logged at debug with the reason: OK")


def check_thumbnail_log(work: Path) -> None:
    import logging
    from types import SimpleNamespace

    from app.routes.files import thumbnail_endpoint

    broken = work / "broken thumb.png"
    broken.write_bytes(b"\x89PNG not really")
    state = SimpleNamespace(indexer=SimpleNamespace(file_record_store=SimpleNamespace(get_by_path=lambda p: SimpleNamespace(deleted=False))))
    records = []
    handler = logging.Handler(logging.WARNING)
    handler.emit = records.append
    logging.getLogger("app").addHandler(handler)
    try:
        response = thumbnail_endpoint(str(broken), SimpleNamespace(app=SimpleNamespace(state=state)))
    finally:
        logging.getLogger("app").removeHandler(handler)
    assert response.status_code == 422 and any(r.exc_info for r in records), [r.getMessage() for r in records]
    print("3m. A thumbnail that cannot be made is logged with its reason (still a 422): OK")


HOSTILE_CHECKS = (check_old_office, check_msg, check_epub, check_html_and_odf, check_media_limits, check_thumbnail_log)


def run_hostile_checks(only: str | None = None) -> None:
    work = Path(tempfile.mkdtemp(prefix="intellifile_hostile_"))
    try:
        for check in HOSTILE_CHECKS:
            if only is None or check.__name__ == only:
                check(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
