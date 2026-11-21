from fastapi.testclient import TestClient

from docintel.api import create_app
from docintel.storage import DocumentStore


def test_health_and_frontend(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["provider"] == "demo"
        page = client.get("/")
        assert page.status_code == 200
        assert "DocLens" in page.text
        assert page.headers["Cache-Control"] == "no-store"
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_api_full_workflow(settings, invoice_bytes):
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/analyze", files={"file": ("invoice.pdf", invoice_bytes)})
        assert response.status_code == 200
        report = response.json()
        assert report["document"]["total_amount"] == "3250.00"
        assert report["validation"]["status"] == "passed"
        assert report["request_id"] == response.headers["X-Request-ID"]
        assert report["provider"] == "demo"
        assert client.get("/documents/" + report["document_id"]).json()["report"] == report


def test_document_lifecycle_and_original_survive_restart(settings, invoice_bytes):
    with TestClient(create_app(settings)) as client:
        response = client.post("/documents", files={"file": ("invoice.pdf", invoice_bytes)})
        assert response.status_code == 202
        receipt = response.json()
        assert receipt["status"] == "processing"
        url = receipt["status_url"]
        assert response.headers["Location"] == url
        record = client.get(url).json()
        assert record["status"] == "completed"
        assert record["document_id"] == record["report"]["document_id"]
        assert record["report"]["summary_method"] == "demo"
        assert client.get(url + "/source").content == invoice_bytes
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(url).json() == record


def test_failed_processing_is_retrievable_and_redacted(settings, invoice_bytes):
    from unittest.mock import Mock

    agent = Mock()
    agent.run.side_effect = RuntimeError("private-provider-debug API_KEY=secret")
    with TestClient(create_app(settings, agent)) as client:
        response = client.post("/documents", files={"file": ("invoice.pdf", invoice_bytes)})
        assert response.status_code == 202
        result = client.get(response.json()["status_url"]).json()
        assert result["status"] == "failed"
        assert result["error"]["code"] == "internal_error"
        assert "private-provider-debug" not in str(result)
        assert result["report"] is None


def test_interrupted_work_is_not_left_processing_forever(settings, invoice_bytes):
    store = DocumentStore(settings.data_dir)
    document_id = store.create(invoice_bytes, "invoice.pdf", "old-request")
    with TestClient(create_app(settings)) as client:
        record = client.get("/documents/" + document_id).json()
        assert record["status"] == "failed"
        assert record["error"]["code"] == "processing_interrupted"
        assert store.original(document_id) == invoice_bytes


def test_document_routes_validate_ids_and_bound_bodies(settings):
    settings.max_upload_mb = 1
    with TestClient(create_app(settings)) as client:
        assert client.get("/documents/not-an-id").status_code == 404
        assert client.get("/documents/doc_" + "0" * 32 + "/source").status_code == 404
        assert client.post("/documents", files={"file": ("a.exe", b"bad")}).status_code == 415
        assert client.post("/documents", files={"file": ("a.pdf", b"")}).status_code == 422
        assert client.post("/documents", files={"file": ("a.pdf", b"bad")}).status_code == 422
        response = client.post("/documents/", content=iter([b"x" * 600000, b"x" * 600000]))
        assert response.status_code == 413


def test_background_capacity_is_bounded_and_released(settings, invoice_bytes):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from unittest.mock import Mock

    from docintel.agent import DocumentAgent

    started, release = Event(), Event()
    settings.max_concurrent_documents = 1
    real_agent = DocumentAgent(settings)

    def process(*args):
        started.set()
        assert release.wait(5)
        return real_agent.run(*args)

    agent = Mock()
    agent.run.side_effect = process
    with TestClient(create_app(settings, agent)) as client, ThreadPoolExecutor() as pool:
        first = pool.submit(
            client.post, "/documents", files={"file": ("invoice.pdf", invoice_bytes)}
        )
        try:
            assert started.wait(5)
            document_id = agent.run.call_args.args[3]
            assert client.get("/documents/" + document_id).json()["status"] == "processing"
            busy = client.post("/documents", files={"file": ("invoice.pdf", invoice_bytes)})
            assert busy.status_code == 429
        finally:
            release.set()
        assert first.result().status_code == 202
        next_upload = client.post("/documents", files={"file": ("invoice.pdf", invoice_bytes)})
        assert next_upload.status_code == 202


def test_storage_failure_releases_reserved_capacity(settings, invoice_bytes, monkeypatch):
    from unittest.mock import Mock

    from docintel.errors import DocumentError

    settings.max_concurrent_documents = 1
    with TestClient(create_app(settings)) as client:
        with monkeypatch.context() as patch:
            patch.setattr(
                DocumentStore,
                "create",
                Mock(side_effect=DocumentError("storage_unavailable", "Storage unavailable.", 503)),
            )
            failure = client.post("/documents", files={"file": ("invoice.pdf", invoice_bytes)})
            assert failure.status_code == 503
        retry = client.post("/documents", files={"file": ("invoice.pdf", invoice_bytes)})
        assert retry.status_code == 202


def test_bad_upload_response(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/analyze", files={"file": ("a.exe", b"bad")})
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_file"
        assert response.json()["error"]["request_id"]


def test_bounded_body(settings):
    settings.max_upload_mb = 1
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/analyze", files={"file": ("a.pdf", b"x" * 1200000)})
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "file_too_large"


def test_chunked_body_without_length_is_bounded(settings):
    settings.max_upload_mb = 1
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/analyze",
            content=iter([b"x" * 600000, b"y" * 600000]),
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 413


def test_missing_upload_and_sample_allowlist(settings):
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/analyze").status_code == 422
        assert client.get("/api/examples/invoice").content.startswith(b"%PDF-")
        assert client.get("/api/examples/not-a-sample").status_code == 404


def test_live_mode_missing_key(settings, invoice_bytes):
    settings.provider = "gemini"
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["configured"] is False
        response = client.post("/api/analyze", files={"file": ("a.pdf", invoice_bytes)})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "missing_api_key"
