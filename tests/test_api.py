from fastapi.testclient import TestClient

from docintel.api import create_app


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
