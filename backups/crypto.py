"""Optional encryption of backup artefacts (Master Plan §22).

Uses AES-256-GCM from the ``cryptography`` package, in 4 MiB chunks so large
media archives never have to fit in memory. The key comes only from the
``BACKUP_ENCRYPTION_KEY`` environment variable.

File format: ``NBTEBK1\\n`` then, per chunk, a 12-byte nonce, a 4-byte
big-endian ciphertext length and the ciphertext (with its tag). The chunk
index and a "last chunk" flag are bound in as associated data, so chunks
cannot be reordered, dropped or truncated without the decryption failing.

Key format: 32 random bytes, base64 (url-safe or standard). Generate one with
``python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"``.
Any other non-empty string is accepted and stretched with SHA-256, but a
random key is strongly recommended. KEEP A COPY OF THE KEY OFF THE SERVER:
encrypted backups cannot be restored without it.
"""
import base64
import binascii
import hashlib
import os
import struct

ENV_KEY = "BACKUP_ENCRYPTION_KEY"
MAGIC = b"NBTEBK1\n"
CHUNK = 4 * 1024 * 1024


class EncryptionUnavailable(Exception):
    pass


def library_available():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except Exception:
        return False
    return True


def key_is_set():
    return bool(os.environ.get(ENV_KEY, "").strip())


def status(setting_on):
    """(will_encrypt, reason) for the page and the run."""
    if not setting_on:
        return False, "Encryption is turned off in the backup settings."
    if not library_available():
        return False, "The cryptography package is not installed on this server, so backups are not encrypted."
    if not key_is_set():
        return False, f"{ENV_KEY} is not set in the server's .env, so backups are not encrypted."
    return True, "Backups are encrypted with AES-256-GCM before they leave the server."


def _key():
    raw = os.environ.get(ENV_KEY, "").strip()
    if not raw:
        raise EncryptionUnavailable(f"{ENV_KEY} is not set.")
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            padded = raw + "=" * (-len(raw) % 4)
            data = decoder(padded)
        except (binascii.Error, ValueError):
            continue
        if len(data) == 32:
            return data
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _aad(index, last):
    return struct.pack(">QB", index, 1 if last else 0)


def encrypt_file(src, dst):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aes = AESGCM(_key())
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(MAGIC)
        index = 0
        chunk = fin.read(CHUNK)
        while True:
            nxt = fin.read(CHUNK)
            last = not nxt
            nonce = os.urandom(12)
            ct = aes.encrypt(nonce, chunk, _aad(index, last))
            fout.write(nonce + struct.pack(">I", len(ct)) + ct)
            if last:
                break
            chunk, index = nxt, index + 1


def decrypt_file(src, dst):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aes = AESGCM(_key())
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        if fin.read(len(MAGIC)) != MAGIC:
            raise ValueError("Not an encrypted NBTE backup file.")
        index = 0
        header = fin.read(16)
        while header:
            if len(header) != 16:
                raise ValueError("The encrypted file is truncated.")
            nonce, (length,) = header[:12], struct.unpack(">I", header[12:])
            ct = fin.read(length)
            if len(ct) != length:
                raise ValueError("The encrypted file is truncated.")
            nxt = fin.read(16)
            fout.write(aes.decrypt(nonce, ct, _aad(index, not nxt)))
            header, index = nxt, index + 1
