# Document Intelligence Agent

A single-repository implementation of the Appfusion AI/ML take-home assessment. Upload a PDF or image, extract an invoice or receipt into a typed schema, validate the values, and generate a short report.

Includes **DocLens**, a responsive upload and review interface, a FastAPI API, and a CLI. The offline demo runs without an API key. Live mode uses Google Gemini for structured extraction and an AI-generated report after validation. Original uploads and completed reports persist in SQLite with stable document IDs.

[Public repository](https://github.com/chenming7777/document-intelligence-agent) · [Engineering notes and assessment checklist](ENGINEERING_NOTES.md)

![DocLens document studio](docs/preview.png)

## Quick start

Python 3.10+ is required. From this repository:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m uvicorn docintel.api:app --host 127.0.0.1 --port 8000 --no-access-log
```

On macOS/Linux, activate with `source .venv/bin/activate` and copy configuration with `cp .env.example .env`.

Open **http://127.0.0.1:8000**. Click **Invoice**, **Receipt**, or **Mismatch**. These samples go through the real PDF reader, a clearly labeled deterministic demo parser, the validator, and the summarizer. The mismatch sample preserves the incorrect printed total and returns `needs_review`.

The `demo` provider only understands the bundled labeled sample layout. It is **not an LLM** and does not claim to extract arbitrary documents. Switch to `gemini` for the assessment's LLM workflow.

## Live Gemini extraction and reports

Set these values in your local `.env` (which Git ignores):

```dotenv
DOCINTEL_PROVIDER=gemini
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-3.8-flash
```

Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/api-keys), add it to `.env`, and restart the server. `GEMINI_MODEL` can be another Gemini model supporting structured output. The Google Gen AI SDK calls `models.generate_content` with `response_mime_type="application/json"` and the document's JSON schema, then validates the result using Pydantic. It handles blocked prompts, incomplete output, malformed JSON, timeouts, and provider errors. See the [official SDK structured-output documentation](https://googleapis.github.io/python-genai/#json-response-schema).

A second Gemini call writes the short report from the final extracted values and validation findings. Summary failures are reported as processing failures rather than silently returning a template. `summary_method` identifies Gemini or offline demo output.

Text extraction and validation run locally. In live mode, **extracted page text and structured fields/validation findings are sent to Google Gemini** and is subject to Google's applicable data-use terms. The key is read server-side and is never sent to the browser. Use synthetic documents for assessment demonstrations.

The SDK is constrained to the 2.33 release series to retain Python 3.10 compatibility. Live extraction requires a Gemini key; keys from other providers are not interchangeable. The offline demo remains available with `DOCINTEL_PROVIDER=demo`.

## Scans and images

Text PDFs work immediately. PNG/JPEG images and PDFs with sparse or missing text layers use local Tesseract OCR. Install Tesseract and the appropriate language data, then put it on PATH or set:

```dotenv
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
OCR_LANGUAGE=eng
```

For Linux: `sudo apt-get install tesseract-ocr tesseract-ocr-eng`. The Docker image includes Tesseract. Missing OCR returns an actionable `503 ocr_unavailable`, never fabricated text.

```sh
docker compose up --build
```

The container binds to localhost port 8000, runs as a non-root user, and reads configuration from `.env`. Copy `.env.example` first. Additional languages require their Tesseract language packs. The named `document_data` volume stores uploads/reports in `/data`; they survive a rebuild or restart. The rest of the container filesystem is read-only.

To process your own PDF: open **http://127.0.0.1:8000**, choose or drag in the file, then click **Analyze document**. You do not need to copy it into the repository. Text PDFs use their text layer; scanned PDFs and images automatically use OCR. With `DOCINTEL_PROVIDER=gemini` and your key configured, the report includes extracted values, validation findings, and Gemini's summary. Bookmark its URL to reopen it later.

## API and CLI

- `GET /` — review interface
- `GET /health` — liveness, provider, configuration presence, and limits; does not test provider reachability
- `GET /docs` — interactive OpenAPI documentation
- `POST /documents` — multipart field `file`; stores the upload, starts processing, returns `202`
- `GET /documents/{document_id}` — processing status, final report, or safe error
- `GET /documents/{document_id}/source` — downloads the stored original
- `POST /api/analyze` — synchronous compatibility endpoint; stores the upload and returns a full `DocumentReport`
- `GET /api/examples/invoice` (also `receipt`, `inconsistent`) — synthetic PDFs

```sh
curl -F "file=@src/docintel/samples/invoice.pdf" http://127.0.0.1:8000/documents
# Use the document_id returned by the upload:
curl http://127.0.0.1:8000/documents/doc_REPLACE_WITH_RETURNED_ID
docintel src/docintel/samples/invoice.pdf --provider demo --output output/report.json
```

On PowerShell, use `curl.exe`. CLI exit codes: `0` = checks passed, `2` = processed but needs review, `1` = processing failed.

An accepted upload returns immediately (also setting the `Location` response header):

```json
{"document_id":"doc_0123456789abcdef0123456789abcdef","status":"processing","status_url":"/documents/doc_0123456789abcdef0123456789abcdef"}
```

Poll the status URL until `status` is `completed` or `failed`. Completion contains
`report.document_id`, `report.document` (the structured financial data), `report.validation`,
`report.summary`, `report.summary_method`, page text, and the tool trace. Failure contains
`error.code` and `error.message`. Processing status `completed` is distinct from financial
validation status `needs_review`: a discrepancy is a completed analysis needing human review.

The CLI invokes the same agent directly; it does not store uploads in the API database.
Use `--output` to save its JSON report. CLI-generated document IDs are not API retrieval IDs.

Example `report.document` section (abbreviated):

```json
{
  "document_type": "invoice",
  "vendor": "ABC Supplies Sdn Bhd",
  "invoice_number": "INV-10234",
  "invoice_date": "2026-01-15",
  "due_date": "2026-02-15",
  "total_amount": "3250.00",
  "currency": "MYR",
  "line_items": [
    {"description": "Consulting services", "quantity": "2", "unit_price": "1000.00", "amount": "2000.00"},
    {"description": "Integration support", "quantity": "5", "unit_price": "200.00", "amount": "1000.00"}
  ]
}
```

Monetary values and quantities serialize as **decimal strings**, preserving precision instead of converting money to binary floating point. Missing fields remain `null`. Reports also contain validation findings, page text, source quotes, a SHA-256 fingerprint, stable document ID, request ID, provider/model information, and a timed tool trace.

## Workflow and tool usage

```mermaid
flowchart TD
    A[PDF or image upload] --> S[Store original and assign document ID]
    S --> B[TextExtractionTool]
    B -->|Text PDF| C[Extract embedded text]
    B -->|Image or sparse page| D[Tesseract OCR]
    C --> E[StructuredExtractionTool]
    D --> E
    E --> F[ValidationTool]
    F -->|Missing or unsupported extraction; at most once| G[Recheck extraction against original source]
    G --> H[Validate and select lower issue count]
    F -->|No extraction recheck needed| I[Gemini summary of final values and validation]
    H --> I
    I --> J[Persist JSON and human-readable report]
```

`DocumentAgent` is a bounded, tool-using orchestrator with explicit branching. The Python orchestrator selects tools; the LLM does not receive shell, file, or network tools. This makes the workflow reproducible and inspectable without an agent framework. A recheck only happens for extraction problems (missing fields, invalid dates/currency, or unverified evidence), never solely to make incorrect arithmetic balance. If the recheck fails or has no better validation score, the first extraction is retained.

Gemini handles semantic extraction and report writing; Python handles arithmetic and business checks. Summary generation always follows final validation, and its returned validation status must agree with the validator. These guardrails do not prove every statement in generated prose; the interface also shows the deterministic findings and original document. The offline demo uses an explicitly labeled template summary.

## Validation rules

- Required vendor, date, currency, total; invoice number is required for invoices, optional for receipts.
- Valid ISO dates and currency codes; due date cannot precede invoice date.
- Decimal arithmetic for quantity × unit price, sum of line amounts, and subtotal + tax − discount.
- Tolerance of one currency minor unit; 0-, 2-, 3-, and selected 4-decimal currencies are supported.
- Missing tax/discount is **not** silently treated as zero. Incomplete reconciliation is a warning.
- Unsupported magnitudes, negative values, missing items, and incomplete lines are flagged.
- Evidence quotes must occur on the cited source page. This verifies quote membership, not full semantic entailment.
- Any error or warning produces `needs_review`. `passed` means the configured checks passed, not that the document is authentic.

## Engineering decisions and limits

- Limits: 10 MB, 10 pages, 60,000 extracted characters, bounded rendered image pixels; configurable in `.env`.
- Multipart bodies are bounded before parsing, even without `Content-Length`. Extensions and decoded content are checked.
- Two concurrent document processors per process, with fast rejection when busy. Blocking OCR/SDK work runs outside the event loop; PDFium rendering is serialized for thread safety.
- Provider timeout and at most two SDK retries per call; at most one semantic recheck. A live document uses two logical model calls (extraction + summary), or three with a recheck. OCR has a per-page timeout.
- Document IDs, request IDs, and tool durations in logs; no document text, filenames, API keys, or financial values are deliberately logged.
- Originals, metadata, statuses, and reports persist in `data/documents.sqlite3` locally (`DOCINTEL_DATA_DIR` overrides the directory), or the Docker named volume. Storage is excluded from Git and image builds. Financial data is not encrypted by the application and is retained until the operator removes it. Back up the database before changing storage.
- Run **one Uvicorn worker per database**. Startup marks interrupted tasks as failed with a clear retry message; completed reports remain available. Background tasks are in-process, not a durable queue.
- Document text is untrusted input. The prompt separates instructions from evidence; UI rendering uses `textContent` and restrictive resource policies.
- This is a production-oriented **assessment service**, not a hardened public financial platform. It has no user accounts, distributed rate limiting, or distributed job queue. Keep it on localhost; an external deployment needs authentication, gateway limits, a worker queue, and process isolation for untrusted PDF parsing.
- PDF embedded-text detection is heuristic; hybrid pages with misleading text layers, handwriting, complex tax-inclusive layouts, credit notes, line-level discounts, and multiple invoices in one file may need review. One document is extracted per upload. There is no accuracy claim against a real-world benchmark.

The required architecture, prompt design, limitations, production improvements, and requirement-by-requirement mapping are in [ENGINEERING_NOTES.md](ENGINEERING_NOTES.md).

## Test and maintain

```sh
python -m pytest --cov=docintel --cov-report=term-missing
python -m ruff check .
python -m ruff format --check .
python scripts/generate_samples.py
```

Tests use synthetic PDFs and mocked provider responses; no paid API calls or API keys are required. OCR integration tests run when a local Tesseract binary is available and otherwise skip with a reason. GitHub Actions installs Tesseract and runs tests and lint checks. `requirements.lock` pins the tested development environment; install it before `pip install --no-deps -e .` for a pinned setup.

Verification covers persistence across app restarts, stored errors, original-file retrieval,
interrupted-job recovery, bounded processing capacity, Gemini request/schema contracts,
summary ordering/status consistency, arithmetic discrepancies, API/CLI behavior, and OCR.
Browser checks cover uploads, saved-report reload, JSON/source views, downloads, and mobile layout.
Live smoke checks use only bundled synthetic documents; they are separate from the offline CI suite.

Verified on 2026-11-21:

| Check | Result |
| --- | --- |
| Docker / Python 3.12 / Tesseract | 93 tests passed, 97% backend coverage; no network or API keys used |
| Lint and formatting | Ruff checks passed |
| Browser | Upload, status polling, saved-report reload, original/JSON downloads, stored errors, and 390px mobile layout passed |
| Live Gemini | Scanned invoice: real OCR, structured extraction, passed validation, and AI summary. Mismatch invoice: printed MYR 3255 retained, `needs_review`, and AI summary explaining the discrepancy |

## Repository map

```text
src/docintel/
  api.py             FastAPI routes, limits, request IDs
  agent.py           Bounded workflow and timed tool trace
  reader.py          PDF text and OCR tools
  extractors.py      Gemini and explicit demo implementations
  summaries.py       Gemini report generation and labeled demo summary
  llm.py             Shared Gemini JSON requests and safe errors
  storage.py         SQLite originals, lifecycle state, and reports
  validation.py      Deterministic financial checks
  models.py          Shared Pydantic schemas
  config.py          Environment settings
  cli.py             Command-line entry point
  static/            DocLens upload/review interface
  samples/           Synthetic PDFs and a scan/image
ENGINEERING_NOTES.md  Design decisions, prompts, limits, production plan, checklist
tests/               Reader, validator, agent, SDK, summary, persistence, API, CLI tests
scripts/             Reproducible sample generation
.github/workflows/   CI
```

Assessment mapping: structured extraction → `extractors.py` + `models.py`; tool usage → reader/extractor/validator; agent workflow → `agent.py`; engineering → API, config, error handling, tests, container, CI, and this documentation. All components live in this repository.
