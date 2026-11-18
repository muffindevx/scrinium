"""Export of the workspace to a file, encrypted with a password by default.

It holds every table except the derived indexes (chunks, FTS5 and vectors), which are
rebuilt from the revisions, and the keyring references, which are useless outside this
machine. The text of indexed sources does go inside: that is why it is encrypted.

Encryption with nothing home-made: the password becomes a key with scrypt and
the content is encrypted with AES-256-GCM, which also detects any tampering. The header goes
as authenticated data: changing its parameters also makes decryption fail. The password
is stored nowhere; without it, the file cannot be opened.
"""

import base64
import gzip
import json
import os
import uuid
import zlib
from collections.abc import Callable, Iterator
from datetime import date, datetime
from enum import Enum
from typing import Any, Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
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
_DERIVED_TABLES: Final = frozenset(
    {"content_chunks", "code_symbols", "code_edges", "extraction_tasks", "ai_cache",
     # Recomputed after a restore: coverage's decisions and the queue of pending work.
     "revision_decisions", "jobs"}
)  # fmt: skip
_SECRET_COLUMNS: Final = {
    "source_connections": frozenset({"secret_ref"}),
    # A provider's key stays in this machine's keyring; restored, it must be typed again.
    "model_providers": frozenset({"secret_ref"}),
}


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
        "excluded": ["content_chunks, code_symbols, code_edges y ai_cache (se regeneran)",
                     "vectores (se regeneran)",
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


# --- Streaming (R1) ------------------------------------------------------------------------
# The same file as `export_workspace`, written piece by piece: rows are read in batches, the
# JSON is produced table by table, and when encrypted it is gzipped and encrypted with
# incremental AES-GCM (the same cipher and tag as `encrypt`, so `decrypt` opens it). Memory stays
# flat whatever the size of the workspace.

_BATCH: Final = 500
_CHUNK: Final = 64 * 1024


def _json_chunks(session: Session) -> Iterator[str]:
    yield (
        '{"format": ' + json.dumps(FORMAT) + ', "format_version": ' + str(FORMAT_VERSION)
        + ', "scrinium_version": ' + json.dumps(__version__)
        + ', "created_at": ' + json.dumps(utc_now().isoformat())
        + ', "excluded": ' + json.dumps(
            ["content_chunks, code_symbols, code_edges y ai_cache (se regeneran)",
             "vectores (se regeneran)",
             "secret_ref (credenciales del keyring de este equipo)"], ensure_ascii=False)
        + ', "tables": {'
    )  # fmt: skip
    first_table = True
    for table in Base.metadata.sorted_tables:
        if table.name in _DERIVED_TABLES:
            continue
        hidden = _SECRET_COLUMNS.get(table.name, frozenset())
        columns = [column for column in table.columns if column.name not in hidden]
        yield ("" if first_table else ", ") + json.dumps(table.name) + ": ["
        first_table = False
        first_row = True
        result = session.execute(select(*columns).execution_options(yield_per=_BATCH))
        for row in result.mappings():
            data = {key: _jsonable(value) for key, value in row.items()}
            yield ("" if first_row else ", ") + json.dumps(data, ensure_ascii=False)
            first_row = False
        yield "]"
    yield "}}"


def _batched(pieces: Iterator[str]) -> Iterator[bytes]:
    buffer: list[str] = []
    size = 0
    for piece in pieces:
        buffer.append(piece)
        size += len(piece)
        if size >= _CHUNK:
            yield "".join(buffer).encode()
            buffer, size = [], 0
    if buffer:
        yield "".join(buffer).encode()


def stream_export(session: Session, passphrase: str | None) -> Iterator[bytes]:
    """The export as a stream of bytes. Check the passphrase first: errors cannot be raised
    once the response has started."""
    plain = _batched(_json_chunks(session))
    if passphrase is None:
        yield from plain
        return
    salt, nonce = os.urandom(16), os.urandom(12)
    header = {
        "format": ENCRYPTED_FORMAT, "format_version": FORMAT_VERSION, "cipher": "AES-256-GCM",
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                "salt": base64.b64encode(salt).decode()},
        "nonce": base64.b64encode(nonce).decode(), "compression": "gzip",
    }  # fmt: skip
    key = _key(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(_header_bytes(header))
    compressor = zlib.compressobj(9, zlib.DEFLATED, 31)  # 31: gzip framing
    yield ('{"header": ' + json.dumps(header) + ', "ciphertext": "').encode()
    pending = b""

    def encoded(data: bytes, final: bool = False) -> bytes:
        nonlocal pending
        pending += data
        cut = len(pending) if final else len(pending) - len(pending) % 3
        out, pending = base64.b64encode(pending[:cut]), pending[cut:]
        return out

    for chunk in plain:
        yield encoded(encryptor.update(compressor.compress(chunk)))
    tail = encryptor.update(compressor.flush()) + encryptor.finalize()
    yield encoded(tail + encryptor.tag, final=True)
    yield b'"}'


def check_passphrase(passphrase: str | None) -> None:
    if passphrase is not None and len(passphrase) < MIN_PASSPHRASE:
        raise ExportError("passphrase_too_short")
