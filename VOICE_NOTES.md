# Voice notes in chat (Phase 9)

Staff can record a voice note in any chat (hold the mic button, or click it to
start and press Send). This page covers how the audio is stored and how the
optional automatic transcript is set up.

## Format

Browsers record different formats (Chrome/Edge/Firefox: Opus in WebM;
Safari: AAC in MP4). The server converts every upload with **ffmpeg** to one
format:

| Setting | Value |
|---|---|
| Codec / container | AAC-LC in `.m4a` (`audio/mp4`) |
| Channels / sample rate | mono, 16 kHz |
| Bit rate | 24 kbit/s |
| Clean-up | `highpass=f=80,lowpass=f=7000,afftdn` (skipped if the ffmpeg build lacks a filter) |

AAC in MP4 plays in every current browser, in old Safari, and natively on
Android and iOS, so the mobile app can use the same file.

**Size:** about **180 KB per minute** (24 kbit/s ≈ 3 KB/s). A 15-minute note
(the maximum, `VOICE_MAX_SECONDS = 900`) is about 2.7 MB. Uploads are limited to
20 MB before conversion.

The file lives in restricted storage (`PROTECTED_MEDIA_ROOT/messaging/voice/`)
and is served only to the chat's participants by `/messages/voice/<id>/audio/`
(with HTTP Range support for seeking; through `X-Accel-Redirect` when
`USE_X_ACCEL_REDIRECT=True`). Voice notes do not expire. "Delete for everyone"
erases the audio.

With each note the server also stores the duration, the size, and a 48-bar
waveform (computed from the decoded audio).

ffmpeg must be installed on the server (`apt install ffmpeg`). If it is
missing, the original recording is stored as uploaded, with the duration the
browser reported, and a warning is logged.

## Automatic transcripts (optional)

Transcription runs in the Celery task `messaging.transcribe_voice_note` after
the upload is saved. The backend is chosen with `VOICE_TRANSCRIBE_BACKEND`:

| Value | Meaning |
|---|---|
| *(empty, default)* | `faster_whisper` if the package is installed, otherwise `none` |
| `faster_whisper` | Local Whisper on the CPU (no audio leaves the server) |
| `none` | Off. Notes show "Transcript unavailable" |
| `dummy` | Fixed fake text, for tests and demos only |

`faster-whisper` is **not** in `requirements.txt`, because it is optional and
large. To turn it on:

```bash
source /path/to/venv/bin/activate
pip install faster-whisper

# Download the model once, as the user the Celery worker runs as.
# The model is cached in ~/.cache/huggingface.
python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
```

Then set the variables in the environment (`.env`) and restart the Celery worker:

```
VOICE_TRANSCRIBE_BACKEND=faster_whisper
VOICE_WHISPER_MODEL=small          # or tiny / base / medium, or a local folder path
```

For servers without internet access, copy a downloaded model folder (for
example `faster-whisper-small`) to the server and set `VOICE_WHISPER_MODEL` to
that folder's full path.

**CPU cost (int8, one worker):**

| Model | RAM | Speed |
|---|---|---|
| `tiny` / `base` | ~0.5 GB | several times faster than real time; weaker accuracy, especially for accented English, Hausa and Yoruba words |
| `small` (default) | ~1 GB | about real time to 3× faster on a modern 4-core CPU (a 1-minute note takes 20–60 s) |
| `medium` | ~2.5 GB | slower than real time on most servers; not recommended without a GPU |

Run transcription on a dedicated Celery queue or worker with
`--concurrency=1` so that it never slows down normal requests. The language is
detected automatically. The sender can correct the transcript, and a correction
is never overwritten.

Each sender can opt out through `notification_preference.transcribe_voice_notes`
(when that preference exists). Their notes are then marked "disabled".

**To turn transcripts off:** set `VOICE_TRANSCRIBE_BACKEND=none`, or uninstall
`faster-whisper`, then restart the workers. Existing transcripts are kept.

In development, Celery runs tasks inline (`CELERY_EAGER=True`), so an upload
waits for its transcription. Use `VOICE_TRANSCRIBE_BACKEND=dummy` or `none`
locally if that is too slow.
