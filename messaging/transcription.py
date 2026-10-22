"""Voice-note transcription backends (Phase 9).

Choose with settings.VOICE_TRANSCRIBE_BACKEND:
  ""               auto - faster_whisper when importable, else "none"
  "faster_whisper" local Whisper on CPU (pip install faster-whisper)
  "none"           transcription off: notes are marked "unavailable"
  "dummy"          deterministic fake text (tests / demos)

A backend's `transcribe(path)` returns (text, language, confidence|None).
"""
from __future__ import annotations

import importlib.util
import logging
import math
import threading

from django.conf import settings
from django.utils import timezone

from .models import VoiceNote

logger = logging.getLogger(__name__)

_model_lock = threading.Lock()
_whisper_model = None


def faster_whisper_installed():
    return importlib.util.find_spec("faster_whisper") is not None


def backend_name():
    name = (getattr(settings, "VOICE_TRANSCRIBE_BACKEND", "") or "").strip().lower()
    if not name:
        return "faster_whisper" if faster_whisper_installed() else "none"
    if name == "faster_whisper" and not faster_whisper_installed():
        return "none"
    return name


def _sender_allows(user):
    # The notification-preference model belongs to another developer; read it
    # defensively so this works before and after it exists.
    return getattr(getattr(user, "notification_preference", None),
                   "transcribe_voice_notes", True)


def initial_status(voice):
    """Status a new note starts with: pending, disabled or unavailable."""
    if not _sender_allows(voice.message.sender):
        return VoiceNote.DISABLED
    if backend_name() not in ("faster_whisper", "dummy"):
        return VoiceNote.UNAVAILABLE
    return VoiceNote.PENDING


def set_status(voice, status):
    voice.transcript_status = status
    voice.save(update_fields=["transcript_status", "updated_at"])
    voice.message.touch()


class DummyBackend:
    def transcribe(self, path, duration_ms=0):
        seconds = max(1, round((duration_ms or 0) / 1000))
        return (f"Dummy transcript of a {seconds} second voice note.", "en", 0.9)


class FasterWhisperBackend:
    def _model(self):
        global _whisper_model
        with _model_lock:
            if _whisper_model is None:
                from faster_whisper import WhisperModel  # optional dependency

                _whisper_model = WhisperModel(
                    getattr(settings, "VOICE_WHISPER_MODEL", "small") or "small",
                    device="cpu", compute_type="int8",
                )
            return _whisper_model

    def transcribe(self, path, duration_ms=0):
        segments, info = self._model().transcribe(path, beam_size=1, vad_filter=True)
        parts, logprobs = [], []
        for segment in segments:
            parts.append(segment.text.strip())
            if getattr(segment, "avg_logprob", None) is not None:
                logprobs.append(segment.avg_logprob)
        confidence = None
        if logprobs:
            confidence = round(max(0.0, min(1.0, math.exp(sum(logprobs) / len(logprobs)))), 3)
        return (" ".join(p for p in parts if p).strip(), getattr(info, "language", "") or "",
                confidence)


def get_backend():
    name = backend_name()
    if name == "dummy":
        return DummyBackend()
    if name == "faster_whisper":
        return FasterWhisperBackend()
    return None


def transcribe(voice):
    """Run the configured backend for one VoiceNote and store the result."""
    status = initial_status(voice)
    if status != VoiceNote.PENDING:
        set_status(voice, status)
        return voice
    if voice.transcript_edited:
        return voice  # never overwrite the sender's own correction
    backend = get_backend()
    if backend is None:
        set_status(voice, VoiceNote.UNAVAILABLE)
        return voice
    try:
        with voice.file.storage.open(voice.file.name, "rb") as fh:
            path = getattr(fh, "name", None)
            if not path:  # non-filesystem storage: spool to a temp file
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
                    tmp.write(fh.read())
                    path = tmp.name
            text, language, confidence = backend.transcribe(path, voice.duration_ms)
    except Exception:
        logger.exception("Transcription failed for voice note %s", voice.pk)
        set_status(voice, VoiceNote.FAILED)
        return voice
    voice.refresh_from_db()
    if voice.transcript_edited:
        return voice
    voice.transcript = text or ""
    voice.transcript_language = (language or "")[:12]
    voice.transcript_confidence = confidence
    voice.transcript_status = VoiceNote.DONE if text else VoiceNote.FAILED
    voice.transcribed_at = timezone.now()
    voice.save(update_fields=["transcript", "transcript_language", "transcript_confidence",
                              "transcript_status", "transcribed_at", "updated_at"])
    voice.message.touch()
    return voice
