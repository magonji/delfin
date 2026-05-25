"""
Low-level reader/writer for the Financisto native ``.backup`` format.

Verified against the official source
(``ru.orangesoftware.financisto.backup.DatabaseExport`` / ``DatabaseImport``):

    PACKAGE:ru.orangesoftware.financisto
    VERSION_CODE:100
    VERSION_NAME:1.7.4
    DATABASE_VERSION:211
    #START
    $ENTITY:<table>
    <column>:<value>
    ...
    $$
    ... more entities ...
    #END

The whole stream is optionally gzip-compressed (the real ``.backup`` files
are). Import auto-detects gzip via the magic header; export gzips by default.
Newlines inside values are replaced with spaces (Financisto does the same).
"""
from __future__ import annotations

import gzip
from typing import Dict, List, Tuple

GZIP_MAGIC = b"\x1f\x8b"

# Identity Delfin advertises in the exported header. DATABASE_VERSION matches a
# recent Financisto schema; Financisto's importer ignores unknown columns and
# fills defaults, so the exact value only needs to be plausible.
DELFIN_PACKAGE = "ru.orangesoftware.financisto"
DELFIN_VERSION_CODE = "100"
DELFIN_VERSION_NAME = "Delfin"
DELFIN_DATABASE_VERSION = "211"

Entity = Tuple[str, Dict[str, str]]  # (table_name, {column: value})


def looks_like_backup(raw: bytes) -> bool:
    """Best-effort detection of a Financisto backup (gzipped or plain text)."""
    try:
        head = gzip.decompress(raw[: 1 << 16]) if raw[:2] == GZIP_MAGIC else raw
    except (OSError, EOFError):
        # truncated gzip block still tells us it's gzip; peek failed only because
        # we sliced it — treat as a backup so the parser can give a real error.
        return raw[:2] == GZIP_MAGIC
    head_text = head[:256].decode("utf-8", errors="replace")
    return head_text.startswith("PACKAGE:") or "$ENTITY:" in head_text


def decompress(raw: bytes) -> bytes:
    """Transparently gunzip if needed."""
    if raw[:2] == GZIP_MAGIC:
        return gzip.decompress(raw)
    return raw


def parse(raw: bytes) -> Tuple[Dict[str, str], List[Entity]]:
    """
    Parse a backup file into (header, entities).

    Mirrors Financisto's own parser: a line starting with ``$`` either closes
    the current entity (``$$``) or opens a new one (``$ENTITY:<table>`` — the
    table name is whatever follows the first colon). All other lines inside an
    entity are ``column:value`` pairs.
    """
    text = decompress(raw).decode("utf-8", errors="replace")

    header: Dict[str, str] = {}
    entities: List[Entity] = []
    in_body = False

    table: str | None = None
    values: Dict[str, str] = {}
    inside_entity = False

    for line in text.splitlines():
        if not in_body:
            if line == "#START":
                in_body = True
            elif ":" in line:
                k, v = line.split(":", 1)
                header[k] = v
            continue

        if line == "#END":
            break

        if line.startswith("$"):
            if line == "$$":
                if table is not None and values:
                    entities.append((table, values))
                table = None
                values = {}
                inside_entity = False
            else:
                idx = line.find(":")
                if idx > 0:
                    table = line[idx + 1:]
                    values = {}
                    inside_entity = True
        elif inside_entity:
            idx = line.find(":")
            if idx > 0:
                values[line[:idx]] = line[idx + 1:]

    return header, entities


def _clean(value: object) -> str:
    """Serialise a value, stripping newlines like Financisto does."""
    return str(value).replace("\n", " ").replace("\r", " ")


def serialize(entities: List[Entity], *, gzip_output: bool = True) -> bytes:
    """Serialise entities into a (gzipped) Financisto backup byte stream."""
    out: List[str] = [
        f"PACKAGE:{DELFIN_PACKAGE}",
        f"VERSION_CODE:{DELFIN_VERSION_CODE}",
        f"VERSION_NAME:{DELFIN_VERSION_NAME}",
        f"DATABASE_VERSION:{DELFIN_DATABASE_VERSION}",
        "#START",
    ]
    for table, row in entities:
        out.append(f"$ENTITY:{table}")
        for col, val in row.items():
            if val is None:
                continue
            out.append(f"{col}:{_clean(val)}")
        out.append("$$")
    out.append("#END")

    data = "\n".join(out).encode("utf-8")
    return gzip.compress(data) if gzip_output else data
