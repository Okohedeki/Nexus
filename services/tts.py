"""Local TTS via Kokoro (kokoro-onnx). Free, runs on CPU, no API key."""

import asyncio
import logging
import os
import re
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

_DEFAULT_VOICE = os.environ.get("PODCAST_VOICE", "bm_george")
_kokoro = None


def _models_dir() -> Path:
    base = os.environ.get("KG_DB_PATH") or os.path.join(os.getcwd(), "data", "knowledge.db")
    data_dir = Path(base).parent
    models = data_dir / "models"
    models.mkdir(parents=True, exist_ok=True)
    return models


def _download(url: str, dest: Path):
    if dest.exists():
        return
    logger.info("Downloading %s → %s", url, dest)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)


def _get_kokoro():
    global _kokoro
    if _kokoro is not None:
        return _kokoro
    from kokoro_onnx import Kokoro
    models = _models_dir()
    model_path = models / "kokoro-v1.0.onnx"
    voices_path = models / "voices-v1.0.bin"
    _download(_MODEL_URL, model_path)
    _download(_VOICES_URL, voices_path)
    _kokoro = Kokoro(str(model_path), str(voices_path))
    return _kokoro


def _clean_for_tts(text: str) -> str:
    """Strip markdown + script markers so they aren't read aloud."""
    # Markdown images/links must run BEFORE generic bracket-stripping
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[SECTION:\s*([^\]]+)\]", r"\1.", text, flags=re.I)
    text = re.sub(r"\[HOST[^\]]*\]:?", "", text, flags=re.I)
    text = re.sub(r"\[[^\]]+\]", "", text)  # remaining bracket stage directions
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chunks(text: str, max_chars: int = 1000):
    """Split into paragraph-ish chunks under max_chars, respecting sentence ends."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}" if buf else p
            continue
        if buf:
            yield buf
            buf = ""
        if len(p) <= max_chars:
            buf = p
            continue
        # Paragraph too long — split on sentences
        sentences = re.split(r"(?<=[.!?])\s+", p)
        for s in sentences:
            if len(buf) + len(s) + 1 <= max_chars:
                buf = f"{buf} {s}" if buf else s
            else:
                if buf:
                    yield buf
                buf = s
    if buf:
        yield buf


def _synthesize_blocking(text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
    kokoro = _get_kokoro()
    cleaned = _clean_for_tts(text)
    if not cleaned:
        raise ValueError("No speakable text after cleanup")
    pieces = []
    sample_rate = 24000
    gap = np.zeros(int(sample_rate * 0.35), dtype=np.float32)
    for chunk in _chunks(cleaned):
        audio, sr = kokoro.create(chunk, voice=voice, speed=speed, lang="en-us")
        sample_rate = sr
        pieces.append(audio.astype(np.float32))
        pieces.append(gap)
    if not pieces:
        raise ValueError("Empty synthesis result")
    return np.concatenate(pieces), sample_rate


async def synthesize_to_file(
    text: str,
    out_path: Path | str,
    voice: str = _DEFAULT_VOICE,
    speed: float = 1.0,
) -> Path:
    """Synthesize `text` → WAV file at `out_path`. Returns the path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    audio, sr = await asyncio.to_thread(_synthesize_blocking, text, voice, speed)
    await asyncio.to_thread(sf.write, str(out_path), audio, sr)
    return out_path


def list_voices() -> list[str]:
    try:
        return list(_get_kokoro().get_voices())
    except Exception:
        return []
