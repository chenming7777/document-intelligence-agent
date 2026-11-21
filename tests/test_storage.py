from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from docintel.errors import DocumentError
from docintel.storage import DocumentStore


def test_original_is_atomic_and_survives_reopening(settings, invoice_bytes):
    store = DocumentStore(settings.data_dir)
    document_id = store.create(invoice_bytes, r"C:\private\invoice.pdf", "request-1")
    reopened = DocumentStore(settings.data_dir)
    assert reopened.original(document_id) == invoice_bytes
    record = reopened.get(document_id)
    assert record.filename == "invoice.pdf"
    assert record.status == "processing"
    assert record.request_id == "request-1"


@pytest.mark.parametrize("shared_store", [True, False])
def test_concurrent_uploads_have_distinct_ids(settings, invoice_bytes, shared_store):
    store = DocumentStore(settings.data_dir)
    barrier = Barrier(4)

    def upload(i):
        writer = store if shared_store else DocumentStore(settings.data_dir)
        barrier.wait(timeout=10)
        return writer.create(invoice_bytes, "invoice.pdf", str(i))

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(upload, range(8)))
    assert len(set(ids)) == 8
    assert all(store.original(document_id) == invoice_bytes for document_id in ids)


@pytest.mark.parametrize("document_id", ["../secret", "' OR 1=1--", "doc_" + "0" * 32])
def test_unknown_or_unsafe_ids_are_not_found(settings, document_id):
    with pytest.raises(DocumentError) as exc:
        DocumentStore(settings.data_dir).get(document_id)
    assert exc.value.status_code == 404


def test_storage_errors_are_safe(settings):
    settings.data_dir.write_text("not a directory")
    with pytest.raises(DocumentError) as exc:
        DocumentStore(settings.data_dir).create(b"data", "invoice.pdf", "request")
    assert exc.value.code == "storage_unavailable"
    assert str(settings.data_dir) not in exc.value.message
