"""Encryption at rest for bank session ids and tokens (PLAN.md §13).

The Fernet key comes from a secret: BAWBEE_FERNET_KEY_FILE (a mounted Docker secret) or
BAWBEE_FERNET_KEY. Without one, banks can't be linked; nothing falls back to plain text.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


class SecretsUnavailable(RuntimeError):
    pass


def _fernet(settings: Settings) -> Fernet:
    key = settings.fernet_key
    if settings.fernet_key_file is not None:
        try:
            key = settings.fernet_key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SecretsUnavailable(
                f"Can't read the encryption key at {settings.fernet_key_file}"
            ) from exc
    if not key:
        raise SecretsUnavailable(
            "Bank links need an encryption key. Set BAWBEE_FERNET_KEY_FILE "
            "(make one with `bawbee fernet-key`)."
        )
    return Fernet(key.encode())


def can_encrypt(settings: Settings) -> bool:
    try:
        _fernet(settings)
    except (SecretsUnavailable, ValueError):
        return False
    return True


def encrypt(settings: Settings, plain: str) -> str:
    return _fernet(settings).encrypt(plain.encode()).decode()


def decrypt(settings: Settings, token: str) -> str:
    try:
        return _fernet(settings).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretsUnavailable(
            "A bank link can't be read with the current encryption key. Re-link it."
        ) from exc


def new_key() -> str:
    return Fernet.generate_key().decode()
