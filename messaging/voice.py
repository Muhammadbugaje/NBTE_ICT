"""Voice notes (Phase 9).

Upload -> validate -> normalise with ffmpeg to AAC-LC mono 16 kHz 24 kbit/s in
an .m4a container (plays in every browser incl. old Safari, and natively on
Android/iOS) -> waveform (48 buckets) -> VoiceNote row -> transcription task.

Shared by the web view and the future REST API: call `create_voice_note()`.
ffmpeg is always run through subprocess with an argument list (never a shell)
and a timeout.
"""
from __future__ import annotations

import array
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from .models import Message, VoiceNote, VoiceNotePlay

logger = logging.getLogger(__name__)

MAX_VOICE_BYTES = 20 * 1024 * 1024
WAVEFORM_BUCKETS = 48
FFMPEG_TIMEOUT = 180
PCM_RATE = 8000
OUTPUT_MIME = "audio/mp4"
NOISE_FILTERS = ["highpass=f=80", "lowpass=f=7000", "afftdn"]

ALLOWED_CONTENT_TYPES = {
    "audio/webm", "video/webm", "audio/ogg", "audio/opus", "audio/mp4", "audio/m4a",
    "audio/x-m4a", "audio/aac", "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav",
    "audio/wave", "audio/3gpp", "video/mp4",
}
ALLOWED_EXTENSIONS = {"webm", "ogg", "oga", "opus", "m4a", "mp4", "aac", "mp3", "wav", "3gp"}


def max_seconds():
    return int(getattr(settings, "VOICE_MAX_SECONDS", 900))


@lru_cache(maxsize=1)
def ffmpeg_path():
    configured = getattr(settings, "VOICE_FFMPEG_BINARY", None)
    for candidate in (configured, shutil.which("ffmpeg"), "/usr/bin/ffmpeg"):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


@lru_cache(maxsize=1)
def available_filters():
    """Names of the noise filters this ffmpeg build has."""
    binary = ffmpeg_path()
    if not binary:
        return frozenset()
    try:
        out = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True,
                             timeout=20, check=False).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            names.add(parts[1])
    return frozenset(f for f in ("highpass", "lowpass", "afftdn") if f in names)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _sniff_is_audio(head: bytes) -> bool:
    if head.startswith(b"\x1a\x45\xdf\xa3"):  # Matroska / WebM
        return True
    if head.startswith(b"OggS") or head.startswith(b"ID3"):
        return True
    if head[4:8] == b"ftyp":  # MP4 / M4A / 3GP
        return True
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return True
    if len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:  # MPEG / ADTS
        return True
    return False


def validate_upload(upload):
    if upload is None:
        raise ValidationError("Record a voice note first.")
    if upload.size > MAX_VOICE_BYTES:
        raise ValidationError("Voice notes can be at most 20 MB.")
    if upload.size < 64:
        raise ValidationError("That recording is empty.")
    content_type = (getattr(upload, "content_type", "") or "").split(";")[0].strip().lower()
    ext = os.path.splitext(upload.name or "")[1].lower().lstrip(".")
    if content_type not in ALLOWED_CONTENT_TYPES and ext not in ALLOWED_EXTENSIONS:
        raise ValidationError("That file is not a supported audio recording.")
    upload.seek(0)
    head = upload.read(16)
    upload.seek(0)
    if not _sniff_is_audio(head):
        raise ValidationError("That file is not a supported audio recording.")
    return content_type


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------
def _run(args):
    return subprocess.run(args, capture_output=True, timeout=FFMPEG_TIMEOUT, check=False)


def normalise(src_path, dst_path):
    """Transcode to AAC-LC .m4a, mono, 16 kHz, 24 kbit/s with light noise
    reduction. Retries without filters if the filter chain fails."""
    binary = ffmpeg_path()
    if not binary:
        raise RuntimeError("ffmpeg not available")
    filters = [f for f in NOISE_FILTERS if f.split("=")[0] in available_filters()]
    base = [binary, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-i", src_path, "-vn", "-map_metadata", "-1", "-ac", "1", "-ar", "16000"]
    tail = ["-c:a", "aac", "-profile:a", "aac_low", "-b:a", "24k",
            "-movflags", "+faststart", "-f", "mp4", dst_path]
    attempts = [base + ["-af", ",".join(filters)] + tail] if filters else []
    attempts.append(base + tail)
    last_error = b""
    for args in attempts:
        result = _run(args)
        if result.returncode == 0 and os.path.exists(dst_path) and os.path.getsize(dst_path) > 0:
            return dst_path
        last_error = result.stderr[-400:]
    logger.warning("ffmpeg could not normalise a voice note: %s",
                   last_error.decode("utf-8", "replace"))
    raise ValidationError("Could not read that recording. Please try again.")


def decode_pcm(path):
    """Mono signed 16-bit PCM at 8 kHz (for the waveform and duration)."""
    binary = ffmpeg_path()
    result = _run([binary, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", path,
                   "-vn", "-ac", "1", "-ar", str(PCM_RATE), "-f", "s16le", "-acodec",
                   "pcm_s16le", "pipe:1"])
    if result.returncode != 0:
        raise ValidationError("Could not read that recording.")
    samples = array.array("h")
    data = result.stdout
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if sys.byteorder == "big":  # pragma: no cover
        samples.byteswap()
    return samples


def compute_waveform(samples, buckets=WAVEFORM_BUCKETS):
    """RMS per bucket, normalised to integers 0-100 (always `buckets` long)."""
    n = len(samples)
    if n == 0:
        return [0] * buckets
    rms = []
    for i in range(buckets):
        start = i * n // buckets
        end = max(start + 1, (i + 1) * n // buckets)
        chunk = samples[start:end]
        total = 0
        for value in chunk:
            total += value * value
        rms.append(math.sqrt(total / len(chunk)) if chunk else 0.0)
    peak = max(rms) or 1.0
    return [max(0, min(100, int(round(100 * value / peak)))) for value in rms]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def create_voice_note(thread, user, upload, *, duration_ms=None, reply_to_id=None):
    """Validate, normalise and store a voice note; returns the new Message.
    Transcription is queued after the transaction commits."""
    from . import chat_services

    participant = chat_services.get_participant(thread, user)
    from .group_services import ensure_can_send

    ensure_can_send(thread, participant)
    content_type = validate_upload(upload)
    reply_to = chat_services.resolve_reply_to(thread, user, reply_to_id)
    try:
        client_ms = max(0, int(float(duration_ms or 0)))
    except (TypeError, ValueError):
        client_ms = 0
    limit_ms = max_seconds() * 1000

    binary = ffmpeg_path()
    if binary:
        with tempfile.TemporaryDirectory(prefix="voice-") as tmp:
            src = os.path.join(tmp, "input")
            dst = os.path.join(tmp, "voice.m4a")
            with open(src, "wb") as fh:
                for chunk in upload.chunks():
                    fh.write(chunk)
            normalise(src, dst)
            samples = decode_pcm(dst)
            duration = int(round(len(samples) * 1000 / PCM_RATE))
            waveform = compute_waveform(samples)
            with open(dst, "rb") as fh:
                data = fh.read()
        mime = OUTPUT_MIME
        name = "voice-note.m4a"
    else:
        logger.warning("ffmpeg not found: storing voice note as uploaded (%s)", content_type)
        upload.seek(0)
        data = upload.read()
        duration = client_ms
        waveform = [0] * WAVEFORM_BUCKETS
        mime = content_type or "application/octet-stream"
        ext = os.path.splitext(upload.name or "")[1].lower() or ".webm"
        name = f"voice-note{ext}"

    if duration > limit_ms + 2000:
        raise ValidationError(f"Voice notes can be at most {max_seconds() // 60} minutes long.")
    if duration < 300:
        raise ValidationError("That recording is too short.")

    now = timezone.now()
    with transaction.atomic():
        message = Message.objects.create(thread=thread, sender=user, body="",
                                         reply_to=reply_to, sent_at=now)
        voice = VoiceNote.objects.create(
            message=message, file=ContentFile(data, name=name), duration_ms=duration,
            size_bytes=len(data), waveform=waveform, mime=mime,
            transcript_status=VoiceNote.PENDING,
        )
        chat_services._after_post(thread, participant, now)
        transaction.on_commit(lambda: queue_transcription(voice.pk))
    return message


def queue_transcription(voice_note_id):
    from . import transcription
    from .tasks import transcribe_voice_note

    voice = VoiceNote.objects.select_related("message__sender").filter(pk=voice_note_id).first()
    if voice is None:
        return
    status = transcription.initial_status(voice)
    if status != VoiceNote.PENDING:
        transcription.set_status(voice, status)
        return
    try:
        transcribe_voice_note.delay(voice_note_id)
    except Exception:  # broker down: don't leave "Transcribing..." forever
        logger.exception("Could not queue transcription for voice note %s", voice_note_id)
        transcription.set_status(voice, VoiceNote.FAILED)


def mark_played(voice, user):
    """Record the first play by a listener (drives the unplayed dot)."""
    from . import chat_services

    chat_services.get_participant(voice.message.thread, user)
    if voice.message.sender_id == user.pk:
        return False
    _, created = VoiceNotePlay.objects.get_or_create(voice_note=voice, user=user)
    return created


def update_transcript(voice, user, text):
    """Only the sender may correct the automatic transcript."""
    if voice.message.sender_id != user.pk:
        raise ValidationError("Only the sender can edit this transcript.")
    if voice.message.is_deleted_for_everyone:
        raise ValidationError("This voice note was deleted.")
    voice.transcript = (text or "").strip()[:10000]
    voice.transcript_edited = True
    voice.transcript_status = VoiceNote.DONE
    voice.save(update_fields=["transcript", "transcript_edited", "transcript_status",
                              "updated_at"])
    voice.message.touch()
    return voice


def get_voice_for(user, voice_id):
    """VoiceNote the user may access (participants only), else Http404."""
    from django.http import Http404

    voice = VoiceNote.objects.select_related("message__thread", "message__sender").filter(
        pk=voice_id).first()
    if voice is None or not voice.can_download(user):
        raise Http404
    return voice
