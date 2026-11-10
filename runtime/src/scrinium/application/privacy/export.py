"""Export of the workspace to a file, encrypted with a password by default.

It holds every table except the derived indexes (chunks, FTS5 and vectors), which are
rebuilt from the revisions, and the keyring references, which are useless outside this
machine. The text of indexed sources does go inside: that is why it is encrypted.

Encryption with nothing home-made (section 16.2): the password becomes a key with scrypt and
the content is encrypted with AES-256-GCM, which also detects any tampering. The header goes
as authenticated data: changing its parameters also makes decryption fail. The password
is stored nowhere; without it, the file cannot be opened.
"""

import base64
import gzip
import json
import os
import uuid
from collections.abc import Callable
from datetime import date, datetime
from enum import Enum
from typing import Any, Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium import __version__
from scrinium.infrastructure.sqlite.models import Base, utc_now

FORMAT: Final = "scrinium-export"
FORMAT_VERSION: Final = 1
ENCRYPTED_FORMAT: Final = "scrinium-export-encrypted"
MIN_PASSPHRASE: Final = 12
# scrypt cost: about 128 MiB and a fraction of a second per attempt, to slow down guessing.
SCRYPT_N: Final = 2**17
SCRYPT_R: Final = 8
SCRYPT_P: Final = 1
_DERIVED_TABLES: Final = frozenset({"content_chunks", "code_symbols", "extraction_tasks"})
_SECRET_COLUMNS: Final = {"source_connections": frozenset({"secret_ref"})}


class ExportError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def collect(session: Session) -> dict[str, Any]:
    """Every row, table by table, in the order they would have to be restored."""
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in Base.metadata.sorted_tables:
        if table.name in _DERIVED_TABLES:
            continue
        hidden = _SECRET_COLUMNS.get(table.name, frozenset())
        columns = [column for column in table.columns if column.name not in hidden]
        rows = session.execute(select(*columns)).mappings()
        tables[table.name] = [{key: _jsonable(value) for key, value in row.items()} for row in rows]
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "scrinium_version": __version__,
        "created_at": utc_now().isoformat(),
        "excluded": ["content_chunks y code_symbols (se regeneran)", "vectores (se regeneran)",
                     "secret_ref (credenciales del keyring de este equipo)"],
        "tables": tables,
    }  # fmt: skip


def _key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode())


def _header_bytes(header: dict[str, Any]) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode()


def encrypt(document: dict[str, Any], passphrase: str) -> bytes:
    if len(passphrase) < MIN_PASSPHRASE:
        raise ExportError("passphrase_too_short")
    salt, nonce = os.urandom(16), os.urandom(12)
    header = {
        "format": ENCRYPTED_FORMAT, "format_version": FORMAT_VERSION, "cipher": "AES-256-GCM",
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                "salt": base64.b64encode(salt).decode()},
        "nonce": base64.b64encode(nonce).decode(), "compression": "gzip",
    }  # fmt: skip
    plaintext = gzip.compress(json.dumps(document, ensure_ascii=False).encode())
    key = _key(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _header_bytes(header))
    envelope = {"header": header, "ciphertext": base64.b64encode(ciphertext).decode()}
    return json.dumps(envelope).encode()


def decrypt(
    data: bytes, passphrase: str, decompress: Callable[[bytes], bytes] = gzip.decompress
) -> dict[str, Any]:
    """Opens an encrypted file. Raises ExportError if the password is wrong or it was tampered with.

    `decompress` allows bounding the decompressed size when opening untrusted files.
    """
    try:
        envelope = json.loads(data)
        header = envelope["header"]
        kdf = header["kdf"]
        if header["format"] != ENCRYPTED_FORMAT or kdf["name"] != "scrypt":
            raise ExportError("unknown_format")
        key = _key(
            passphrase, base64.b64decode(kdf["salt"]), int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
        )
        plaintext = AESGCM(key).decrypt(
            base64.b64decode(header["nonce"]),
            base64.b64decode(envelope["ciphertext"]),
            _header_bytes(header),
        )
    except InvalidTag:
        raise ExportError("wrong_passphrase_or_modified") from None
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ExportError):
            raise
        raise ExportError("unknown_format") from None
    document: dict[str, Any] = json.loads(decompress(plaintext))
    return document


def export_workspace(session: Session, passphrase: str | None) -> tuple[bytes, bool]:
    """The file and whether it is encrypted. No password, no encryption: the UI warns first."""
    document = collect(session)
    if passphrase is None:
        return json.dumps(document, ensure_ascii=False, indent=1).encode(), False
    return encrypt(document, passphrase), True
