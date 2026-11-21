"""Atomic local persistence for original uploads, lifecycle state, and reports."""

import hashlib
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from docintel.errors import DocumentError
from docintel.models import DocumentRecord, DocumentReport, ProcessingError

# SQLite's initial switch to WAL can return SQLITE_BUSY immediately when two
# connections attempt it together, even with a connection timeout configured.
_INITIALIZE_LOCK = threading.Lock()


def safe_filename(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).name[:200] or "document"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocumentStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "documents.sqlite3"
        self._initialized = False

    @contextmanager
    def connect(self):
        connection = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=10)
            connection.row_factory = sqlite3.Row
            with _INITIALIZE_LOCK:
                if not self._initialized:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute("""
                        CREATE TABLE IF NOT EXISTS documents (
                            document_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                            filename TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL,
                            status TEXT NOT NULL, created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            report TEXT, error TEXT
                        )
                    """)
                    connection.commit()
                    self._initialized = True
            with connection:
                yield connection
        except (OSError, sqlite3.Error) as exc:
            raise DocumentError(
                "storage_unavailable",
                "Document storage is unavailable. Check disk space and permissions.",
                503,
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def create(self, content: bytes, filename: str, request_id: str) -> str:
        document_id = "doc_" + uuid.uuid4().hex
        timestamp = now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, 'processing', ?, ?, NULL, NULL)",
                (
                    document_id,
                    request_id,
                    safe_filename(filename),
                    hashlib.sha256(content).hexdigest(),
                    content,
                    timestamp,
                    timestamp,
                ),
            )
        return document_id

    def complete(self, report: DocumentReport) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE documents SET status='completed', report=?, error=NULL, updated_at=? "
                "WHERE document_id=?",
                (report.model_dump_json(), now(), report.document_id),
            )

    def fail(self, document_id: str, error: DocumentError) -> None:
        payload = ProcessingError(code=error.code, message=error.message).model_dump_json()
        with self.connect() as db:
            db.execute(
                "UPDATE documents SET status='failed', error=?, updated_at=? WHERE document_id=?",
                (payload, now(), document_id),
            )

    def recover_interrupted(self) -> None:
        # Single-process service: no request from the prior process is still executing.
        error = ProcessingError(
            code="processing_interrupted",
            message="The server stopped before processing finished. Upload the document again.",
        ).model_dump_json()
        with self.connect() as db:
            db.execute(
                "UPDATE documents SET status='failed', error=?, updated_at=? "
                "WHERE status='processing'",
                (error, now()),
            )

    def _row(self, document_id: str, columns: str):
        if not re.fullmatch(r"doc_[0-9a-f]{32}", document_id):
            raise DocumentError("document_not_found", "Document not found.", 404)
        with self.connect() as db:
            # columns is an internal constant; document IDs always use parameter binding.
            row = db.execute(
                f"SELECT {columns} FROM documents WHERE document_id=?", (document_id,)
            ).fetchone()
        if row is None:
            raise DocumentError("document_not_found", "Document not found.", 404)
        return row

    def get(self, document_id: str) -> DocumentRecord:
        row = dict(
            self._row(
                document_id,
                "document_id, request_id, filename, sha256, status, "
                "created_at, updated_at, report, error",
            )
        )
        row["report"] = DocumentReport.model_validate_json(row["report"]) if row["report"] else None
        row["error"] = ProcessingError.model_validate_json(row["error"]) if row["error"] else None
        return DocumentRecord(**row)

    def original(self, document_id: str) -> bytes:
        return self._row(document_id, "content")["content"]
