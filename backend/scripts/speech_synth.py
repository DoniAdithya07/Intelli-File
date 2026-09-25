"""Real speech audio for the voice tests, from the OS's own speech engine:
macOS `say` + `afconvert`, Windows System.Speech (SAPI) through PowerShell.
Both write 16 kHz mono 16-bit WAV — what the app's recorder sends.

Before 2026-09-25 only the macOS path existed, so on Windows both voice
tests skipped and Whisper was never exercised by automation there.
"""

import shutil
import subprocess
import sys
from pathlib import Path

_WINDOWS_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo 16000, ([System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen), ([System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($env:INTELLIFILE_TTS_OUT, $fmt)
$s.Speak($env:INTELLIFILE_TTS_TEXT)
$s.Dispose()
"""


def available() -> bool:
    if sys.platform == "win32":
        return shutil.which("powershell") is not None
    return shutil.which("say") is not None and shutil.which("afconvert") is not None


def synthesize_wav(text: str, out_path: Path, voice: str | None = None) -> None:
    """Speak `text` into `out_path` (a .wav). `voice` is a macOS voice name;
    Windows uses the default installed voice."""
    if sys.platform == "win32":
        import os

        env = dict(os.environ, INTELLIFILE_TTS_OUT=str(out_path), INTELLIFILE_TTS_TEXT=text)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT], check=True, capture_output=True, env=env)
        return
    aiff = out_path.with_suffix(".aiff")
    subprocess.run(["say", *(["-v", voice] if voice else []), "-o", str(aiff), text], check=True, capture_output=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(out_path)], check=True, capture_output=True)
    aiff.unlink(missing_ok=True)


def wav_to_m4a(wav_path: Path, m4a_path: Path) -> None:
    """Re-encode a WAV as AAC in an .m4a (a phone voice memo) with PyAV,
    which the app already ships — no OS tool needed."""
    import av

    with av.open(str(wav_path)) as src, av.open(str(m4a_path), "w", format="mp4") as dst:
        in_stream = src.streams.audio[0]
        out_stream = dst.add_stream("aac", rate=in_stream.rate)
        out_stream.layout = "mono"
        for frame in src.decode(in_stream):
            frame.pts = None
            for packet in out_stream.encode(frame):
                dst.mux(packet)
        for packet in out_stream.encode(None):
            dst.mux(packet)
