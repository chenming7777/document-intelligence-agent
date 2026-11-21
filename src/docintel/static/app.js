"use strict";
const $ = (id) => document.getElementById(id);
let selectedFile = null,
  currentReport = null,
  busy = false,
  maxUploadBytes = 10 * 1024 * 1024;

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = false;
}

function selectFile(file) {
  if (busy) return;
  $("error").hidden = true;
  selectedFile = file || null;
  $("file-label").textContent = file ? file.name : "Drop your document here";
  $("analyze").disabled = !file;
}

function setBusy(value) {
  busy = value;
  $("analyze").disabled = value || !selectedFile;
  $("file").disabled = value;
  document.querySelectorAll("[data-sample]").forEach((b) => {
    b.disabled = value;
  });
  $("upload-form").setAttribute("aria-busy", String(value));
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function activateTab(name, focus = false) {
  document.querySelectorAll("[data-tab]").forEach((b) => {
    const active = b.dataset.tab === name;
    b.setAttribute("aria-selected", String(active));
    b.tabIndex = active ? 0 : -1;
    $("panel-" + b.dataset.tab).hidden = !active;
    if (active && focus) b.focus();
  });
}

function renderReport(report) {
  currentReport = report;
  const doc = report.document,
    passed = report.validation.status === "passed";
  $("results").hidden = false;
  $("empty-state").hidden = true;
  $("validation-status").textContent = passed ? "Checks passed" : "Needs review";
  $("validation-status").className = "badge" + (passed ? "" : " warning");
  $("report-provider").textContent = report.provider === "demo" ? "Offline demo · no LLM called" :
    "Gemini extraction + AI summary · " + report.model;
  $("summary").textContent = report.summary;
  $("fields").replaceChildren();
  const fields = {
    vendor: "Vendor",
    invoice_number: "Document number",
    invoice_date: "Document date",
    due_date: "Due date",
    currency: "Currency",
    subtotal: "Subtotal",
    tax_amount: "Tax",
    discount_amount: "Discount",
    total_amount: "Total amount",
    document_type: "Document type"
  };
  Object.entries(fields).forEach(([key, label]) => {
    const group = element("div");
    group.append(element("dt", label), element("dd", doc[key] ?? "Not provided"));
    $("fields").append(group);
  });
  $("items").replaceChildren();
  doc.line_items.forEach((item) => {
    const row = element("tr");
    ["description", "quantity", "unit_price", "amount"].forEach((key) => row.append(element(
      "td", item[key] ?? "—")));
    $("items").append(row);
  });
  if (!doc.line_items.length) {
    const row = element("tr"),
      cell = element("td", "No line items extracted.");
    cell.colSpan = 4;
    row.append(cell);
    $("items").append(row);
  }
  $("issues").replaceChildren();
  if (passed) $("issues").append(element("li",
    "All configured checks passed. Compare the result with the source before use.", "pass"));
  report.validation.issues.forEach((issue) => {
    const row = element("li");
    row.append(element("strong", issue.severity.toUpperCase() + " · " + issue.field), element(
      "span", issue.message));
    $("issues").append(row);
  });
  $("json").textContent = JSON.stringify(report, null, 2);
  $("trace").replaceChildren();
  report.trace.forEach((e) => $("trace").append(element("li",
    `${e.tool} · attempt ${e.attempt} · ${e.duration_ms} ms · ${e.outcome}`)));
  $("source-pages").replaceChildren();
  report.pages.forEach((page) => $("source-pages").append(element("h3",
    `Page ${page.page} · ${page.method === "ocr" ? "OCR" : "PDF text"}`), element("pre", page
    .text)));
  $("notes").replaceChildren();
  [...doc.notes, ...report.warnings].forEach((note) => $("notes").append(element("li", note)));
  $("request-id").textContent = `Document ${report.document_id} · Request ${report.request_id} · SHA-256 ${report.sha256}`;
  $("source-download").href = `/documents/${report.document_id}/source`;
  document.querySelectorAll("[data-step]").forEach((node) => {
    node.textContent = report.trace.some((e) => e.tool === node.dataset.step) ? "Done" : "—";
    node.className = "step-status complete";
  });
  activateTab("details");
  $("progress").textContent = passed ? "Analysis complete. Your report is ready." :
    "Analysis complete. Some details need your review.";
  $("results-title").focus({
    preventScroll: true
  });
  $("results").scrollIntoView({
    behavior: "smooth",
    block: "start"
  });
}
async function loadDocument(documentId) {
  const deadline = Date.now() + 20 * 60 * 1000;
  while (Date.now() < deadline) {
    const response = await fetch(`/documents/${documentId}`);
    const record = await response.json();
    if (!response.ok) throw new Error(record.error?.message || "Could not load this document.");
    if (record.status === "failed") throw new Error(record.error?.message || "Processing failed.");
    if (record.status === "completed") {
      renderReport(record.report);
      return;
    }
    $("progress").textContent = `Document saved. OCR and analysis are in progress. You can reopen this page to check the result.`;
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  throw new Error("Processing is taking longer than expected. Reopen this page to check its status.");
}
async function analyze(file) {
  if (busy || !file) return;
  $("error").hidden = true;
  if (file.size > maxUploadBytes) {
    showError("This file exceeds the configured upload limit.");
    return;
  }
  if (!/\.(pdf|png|jpe?g)$/i.test(file.name)) {
    showError("Please select a PDF, PNG, or JPEG document.");
    return;
  }
  setBusy(true);
  currentReport = null;
  $("results").hidden = true;
  $("empty-state").hidden = false;
  $("progress").textContent =
    "Processing your document. OCR and live extraction may take a moment…";
  document.querySelectorAll("[data-step]").forEach((n) => {
    n.textContent = "Pending";
    n.className = "step-status";
  });
  try {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch("/documents", {
      method: "POST",
      body: form
    });
    const receipt = await response.json();
    if (!response.ok) throw new Error(receipt.error?.message ||
      "The document could not be processed.");
    history.replaceState(null, "", `/?document=${receipt.document_id}`);
    await loadDocument(receipt.document_id);
  } catch (error) {
    showError(error.message || "Connection failed. Check that the server is running.");
    $("progress").textContent =
    "Processing did not complete. See the message beside your upload.";
    document.querySelectorAll("[data-step]").forEach((n) => {
      n.textContent = "—";
    });
  } finally {
    setBusy(false);
  }
}
$("file").addEventListener("change", (event) => selectFile(event.target.files[0]));
$("upload-form").addEventListener("submit", (event) => {
  event.preventDefault();
  analyze(selectedFile);
});
$("drop-zone").addEventListener("dragover", (event) => {
  event.preventDefault();
  if (!busy) $("drop-zone").classList.add("dragging");
});
$("drop-zone").addEventListener("dragleave", () => $("drop-zone").classList.remove("dragging"));
$("drop-zone").addEventListener("drop", (event) => {
  event.preventDefault();
  $("drop-zone").classList.remove("dragging");
  selectFile(event.dataTransfer.files[0]);
});
document.querySelectorAll("[data-sample]").forEach((b) => b.addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  try {
    const name = b.dataset.sample;
    const response = await fetch("/api/examples/" + name);
    if (!response.ok) throw new Error("Could not load the sample document.");
    const file = new File([await response.blob()], name + ".pdf", {
      type: "application/pdf"
    });
    setBusy(false);
    selectFile(file);
    await analyze(file);
  } catch (error) {
    showError(error.message);
    setBusy(false);
  }
}));
document.querySelectorAll("[data-tab]").forEach((b, index, buttons) => {
  b.addEventListener("click", () => activateTab(b.dataset.tab));
  b.addEventListener("keydown", (event) => {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % buttons.length;
    if (event.key === "ArrowLeft") next = (index - 1 + buttons.length) % buttons.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = buttons.length - 1;
    if (next !== undefined) {
      event.preventDefault();
      activateTab(buttons[next].dataset.tab, true);
    }
  });
});
$("download").addEventListener("click", () => {
  if (!currentReport) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(currentReport, null, 2)], {
    type: "application/json"
  }));
  const a = element("a");
  a.href = url;
  a.download = `report-${currentReport.document_id}.json`;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
fetch("/health").then((response) => response.json()).then((health) => {
  $("mode").textContent = health.provider === "demo" ? "Offline demo" : health.configured ?
    "Live extraction" : "API key needed";
  $("mode").classList.toggle("warning", !health.configured);
  maxUploadBytes = health.max_upload_mb * 1024 * 1024;
  $("file-hint").textContent =
    `PDF, PNG or JPG · up to ${health.max_upload_mb} MB · ${health.max_pages} pages`;
  if (health.provider === "gemini") $("privacy").textContent =
    "Extracted text and structured data are sent to Google Gemini. Documents and reports are saved on this server; bookmark the report page to reopen it.";
}).catch(() => {
  $("mode").textContent = "Server unavailable";
});

const savedDocument = new URLSearchParams(location.search).get("document");
if (savedDocument && /^doc_[0-9a-f]{32}$/.test(savedDocument)) {
  setBusy(true);
  $("progress").textContent = "Loading your saved document…";
  loadDocument(savedDocument).catch((error) => {
    showError(error.message || "Could not reconnect to the server. Reload to try again.");
    $("progress").textContent = "The saved document could not be loaded.";
  }).finally(() => setBusy(false));
}
