import asyncio
import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from docintel import __version__
from docintel.agent import DocumentAgent
from docintel.config import Settings
from docintel.errors import DocumentError
from docintel.models import DocumentReport

ROOT = Path(__file__).parent
logger = logging.getLogger(__name__)


class UploadLimitMiddleware:
    """Bound multipart bytes before parsing, including bodies without Content-Length."""

    def __init__(self, app, limit: int):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/analyze":
            return await self.app(scope, receive, send)
        chunks = []
        length = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            length += len(chunk)
            if length > self.limit:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "file_too_large",
                            "message": "The upload exceeds the size limit.",
                        }
                    },
                )
                return await response(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None, agent: DocumentAgent | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="Document Intelligence Agent",
        version=__version__,
        description="Upload an invoice or receipt, extract structured data, and validate it.",
    )
    app.add_middleware(UploadLimitMiddleware, limit=settings.max_upload_bytes + 65536)
    app.mount("/assets", StaticFiles(directory=ROOT / "static"), name="assets")
    semaphore = asyncio.Semaphore(settings.max_concurrent_documents)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
            )
            if not request.url.path.startswith("/docs")
            else "frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(DocumentError)
    async def document_error(request: Request, exc: DocumentError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request.state.request_id,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error("request_failed request_id=%s error_type=%s", request_id, type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Processing failed. See the server request ID.",
                    "request_id": request_id,
                }
            },
        )

    @app.get("/", include_in_schema=False)
    async def home():
        return FileResponse(ROOT / "static" / "index.html")

    @app.get("/health")
    async def health():
        ready = settings.provider == "demo" or bool(settings.openai_api_key.get_secret_value())
        return {
            "status": "ok",
            "provider": settings.provider,
            "configured": ready,
            "version": __version__,
            "max_upload_mb": settings.max_upload_mb,
            "max_pages": settings.max_pages,
        }

    @app.get("/api/examples/{name}")
    async def example(name: str):
        examples = {
            "invoice": "invoice.pdf",
            "receipt": "receipt.pdf",
            "inconsistent": "inconsistent.pdf",
        }
        if name not in examples:
            return Response(status_code=404)
        return FileResponse(
            ROOT / "samples" / examples[name], media_type="application/pdf", filename=examples[name]
        )

    @app.post("/api/analyze", response_model=DocumentReport)
    async def analyze(request: Request, file: Annotated[UploadFile, File()]):
        try:
            content = await file.read(settings.max_upload_bytes + 1)
            if len(content) > settings.max_upload_bytes:
                raise DocumentError("file_too_large", "The document exceeds the upload limit.", 413)
            # Fail fast when busy; never build an unbounded processing queue.
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=0.05)
            except asyncio.TimeoutError as exc:
                raise DocumentError(
                    "server_busy", "Processing capacity is busy. Try again shortly.", 429
                ) from exc
            try:
                processor = agent or DocumentAgent(settings)
                try:
                    return await run_in_threadpool(
                        processor.run,
                        content,
                        file.filename or "document",
                        request.state.request_id,
                    )
                finally:
                    if agent is None:
                        processor.close()
            finally:
                semaphore.release()
        finally:
            await file.close()

    return app


app = create_app()
