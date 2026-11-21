# Engineering notes

This implementation targets the Appfuxion AI Engineer Take Home Assessment v1.
Run with `DOCINTEL_PROVIDER=gemini` to demonstrate its LLM requirements. The demo
provider exists for reproducible, offline tests and is visibly labeled as non-AI.

## Architecture decisions

The repository contains a FastAPI service, browser interface, CLI, explicit agent,
local OCR, validation tools, persistence, and tests. No agent framework is needed
for a four-step workflow with one conditional retry.

1. `POST /documents` checks size, extension, and signature, reserves a bounded
   processing slot, and atomically stores the original bytes and metadata in SQLite.
   It assigns a random `doc_...` ID independent of the HTTP request ID and filename.
2. The API responds `202` with the ID and status URL. A background task executes
   blocking parsing/OCR and Gemini calls in a thread pool. `GET /documents/{id}`
   exposes `processing`, `completed`, or `failed`, and the completed report/error.
3. `TextExtractionTool` reads embedded PDF text and renders sparse pages for
   Tesseract OCR. PNG/JPEG uploads use the same local OCR. Page numbers and extraction
   methods are retained. PDFium rendering is locked because it is not thread-safe.
4. `GeminiExtractionTool` requests JSON matching `FinancialDocument`. Pydantic
   validates all properties, rejects extras/non-finite numbers, and retains nullable
   unknowns. Decimal strings preserve monetary precision across JSON boundaries.
5. `ValidationTool` checks required fields, currency/date formats, evidence quotes,
   line arithmetic, subtotal reconciliation, and the total. Its result is authoritative.
6. If extraction-specific problems merit a recheck, the agent asks Gemini to reread
   the original text once. It accepts the candidate only when its `(errors, issues)`
   score improves. Arithmetic discrepancies alone never trigger a rewrite.
7. `GeminiSummaryTool` receives the final structured values and validation findings.
   It generates a short report, checked against a separate schema and the validation
   status. The agent always validates before summarizing. A failed summary is a
   processing error; there is no silent substitution of a template for AI output.
8. The report is saved in the same database. The interface polls for completion and
   embeds the document ID in its URL so that a bookmark or refresh reloads the report.

`POST /api/analyze` remains a synchronous compatibility endpoint and uses the same
storage and processing path. The CLI directly invokes the agent and writes a report
only when `--output` is supplied; its IDs do not imply an API database record.

SQLite stores both upload bytes and report JSON in transactions. This avoids separate
file/database writes getting out of sync and needs no external database service.
Uploads are limited to 10 MB by default, which makes this reasonable for a local
assessment application. Each connection is short-lived; WAL permits status readers
alongside writes. First-use schema/WAL initialization is serialized across threads
to prevent competing connections from racing to change the journal mode. Docker
stores the database on a named volume owned by the non-root
application user, while the rest of the container filesystem is read-only.

## Prompt design

Extraction instructions are server-owned system instructions. Document text is sent
as untrusted user content with explicit page boundaries. The prompt requires:

- Nulls for missing or ambiguous values, including unspecified tax/discount.
- Printed values preserved even when they do not balance.
- ISO dates/currencies only when supported by the document.
- Verbatim evidence quotes and page numbers for non-null top-level fields.
- No execution of document instructions and no invented line items or amounts.

The recheck adds only affected field names and validation codes to the instructions.
It does not supply a corrected total for the model to copy.

The summary prompt is separate. Its input contains structured fields and the complete
validation report, excluding extraction notes and evidence quotations. All strings
remain untrusted data. It asks for 2–4 sentences, prohibits invented facts or new
calculations, and requires explicit review language when checks fail. The response
schema bounds summary length and requires a validation status matching the actual
validator result. The UI displays deterministic findings independently of the prose.

Both calls use temperature zero and schema-constrained JSON followed by local
validation. `llm.py` centralizes bounded timeouts, SDK retries, refusals, truncation,
JSON errors, and safe provider-error messages. The SDK may make three attempts per
call. A normal document uses two logical Gemini calls: extraction and summary. One
semantic recheck can increase this to three, excluding transport retries.

## Reliability and data handling

- Multipart bytes are bounded before FastAPI parses them, including chunked bodies.
  Full decoding, page/text/pixel limits, and OCR timeouts apply inside the worker.
- Processing capacity is bounded per process; excess uploads receive `429` before
  storage instead of entering an unbounded queue. No LLM-facing shell or file tools
  are exposed. The Python agent chooses which tools execute.
- Stable document IDs, HTTP request IDs, hashes, per-tool durations, and outcomes
  support diagnosis. Logs deliberately omit filenames, text, financial values,
  provider exception details, and credentials. Token usage metrics are not collected.
- Expected failures persist a safe error. Unexpected failures persist a generic
  error and log only the exception type. Busy/invalid/misconfigured uploads rejected
  before acceptance do not create a document record.
- This is a **single-process service**. Start one Uvicorn worker per database.
  Startup marks unfinished records from the previous process `processing_interrupted`.
  Original bytes remain available, but the user must upload again to retry. A crash
  is therefore visible rather than leaving a document perpetually processing.
- Source bytes and reports persist until the operator removes the storage. They are
  not encrypted by the application. The database, uploads, and `.env` are excluded
  from Git and the Docker build context. Use host/volume permissions and synthetic
  documents for public demonstrations. Keep the service bound to localhost.

## Limitations

OCR accuracy depends on scan quality and installed language packs. The PDF text-layer
heuristic can miss image regions in hybrid pages. Handwriting, rotated tables, merged
cells, tax-inclusive prices, multiple invoices in one file, credit notes, and complex
line discounts are not comprehensively supported. One upload produces one document.

Evidence checks establish that a quote occurs on a page; they do not prove that the
quote entails the extracted field. Prompt instructions and schema checks reduce
errors but cannot eliminate prompt injection or hallucination. The summary-status
check validates its status field, not every claim in its prose. Human comparison with
the original remains necessary; `passed` does not establish authenticity or payment.

Background tasks are local to the web process, not a durable distributed queue. SQLite
and the in-process semaphore are intended for one service instance. There is no
authentication, tenant isolation, automated retention/deletion, encryption, malware
scanning, or real-world accuracy benchmark. Storage can grow until the disk fills.

Layout detection, table-region extraction, a VLM, multi-agent collaboration, RAG,
confidence calibration, and token/cost observability are optional assessment bonuses
and are not claimed as implemented. OCR routing, evidence checks, the bounded recheck,
and timing traces provide a subset of the optional guardrail/observability features.

## Improvements for production

1. Add authentication and document ownership checks to every status/source route;
   enforce per-user upload and cost quotas at a gateway.
2. Replace local background tasks with a durable queue, job leases, retry policies,
   idempotency keys, and worker isolation. Use object storage for uploads and a
   transactional database for metadata; support multiple API/worker instances.
3. Sandbox PDF/OCR execution, scan uploads, encrypt stored data, manage secrets
   centrally, and add audited retention, deletion, backup, and recovery policies.
4. Evaluate a labeled invoice/receipt dataset by field and document type. Measure
   OCR failures, arithmetic discrepancies, evidence accuracy, and summary fidelity.
   Add layout/table handling or multimodal extraction only where evaluation supports it.
5. Add calibrated confidence, field-level semantic evidence checks, and a review
   workflow. Measure latency, provider tokens, cost, queue age, and error rates without
   logging financial documents. Test provider/schema changes against a versioned corpus.

## Assessment checklist

| Required part | Implementation / evidence |
| --- | --- |
| One repository and setup instructions | Root README, Python package, Docker Compose, CI |
| Engineering notes | This file: architecture, prompts, limitations, production improvements |
| Upload API for PDF/JPG/PNG | `api.py`, `reader.py`; bounded multipart intake |
| Store upload, assign ID, trigger processing | `storage.py`; `POST /documents` and background task |
| Extract document text | `reader.py`; embedded text and real Tesseract OCR |
| LLM structured extraction and schema validation | `extractors.py`, `llm.py`, `models.py` |
| Error handling | Safe API/provider errors, stored failure state, restart recovery |
| Agent workflow using tools | `agent.py`; timed tool calls and conditional recheck |
| At least one validation tool, before summary | `validation.py`; ordering and discrepancy tests |
| AI-generated report | `summaries.py`; Gemini summary after final validation |
| Final ID, structured values, summary | `DocumentReport.document_id`, `.document`, `.summary` |
| Git repository submission | Public repository linked in README |
