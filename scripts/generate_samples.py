"""Regenerate synthetic fixtures. No customer data is included in this repository."""

from pathlib import Path

import pypdfium2 as pdfium
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

DESTINATION = Path(__file__).resolve().parents[1] / "src" / "docintel" / "samples"


def build(name: str, receipt: bool = False, inconsistent: bool = False):
    values = [
        ("Type", "receipt" if receipt else "invoice"),
        ("Vendor", "Kopi Corner" if receipt else "ABC Supplies Sdn Bhd"),
        ("Number", "RCP-0042" if receipt else "INV-10234"),
        ("Date", "2026-01-15"),
        ("Due", "N/A" if receipt else "2026-02-15"),
        ("Currency", "MYR"),
    ]
    items = (
        ["Coffee beans | 1 | 12.00 | 12.00", "Filter papers | 2 | 4.00 | 8.00"]
        if receipt
        else [
            "Consulting services | 2 | 1000.00 | 2000.00",
            "Integration support | 5 | 200.00 | 1000.00",
        ]
    )
    totals = [
        ("Subtotal", "20.00" if receipt else "3000.00"),
        ("Tax", "1.20" if receipt else "250.00"),
        ("Discount", "0.00"),
        ("Total", "21.20" if receipt else "3255.00" if inconsistent else "3250.00"),
    ]
    pdf = canvas.Canvas(str(DESTINATION / f"{name}.pdf"), pagesize=A4, invariant=True)
    pdf.setTitle(f"Synthetic {name} - Document Intelligence Agent")
    pdf.setFillColor(HexColor("#1c5544"))
    pdf.rect(0, 735, A4[0], 107, fill=1, stroke=0)
    pdf.setFillColor(HexColor("#ffffff"))
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(44, 786, "RECEIPT" if receipt else "INVOICE")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(44, 758, "SYNTHETIC DEMO DOCUMENT")
    pdf.setFillColor(HexColor("#20392f"))
    pdf.setFont("Helvetica", 12)
    y = 695
    lines = [f"{key}: {value}" for key, value in values] + [""]
    lines += [f"Item: {item}" for item in items] + [""]
    lines += [f"{key}: {value}" for key, value in totals]
    for line in lines:
        pdf.drawString(44, y, line)
        y -= 26
    pdf.setStrokeColor(HexColor("#dbe4d8"))
    pdf.line(44, 130, 550, 130)
    pdf.setFillColor(HexColor("#64726a"))
    pdf.setFont("Helvetica", 9)
    pdf.drawString(44, 109, "Fictional fixture for software demonstration. Not a payable document.")
    if inconsistent:
        pdf.drawString(
            44, 91, "Test case: the printed total intentionally differs from the itemized total."
        )
    pdf.save()


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    build("invoice")
    build("receipt", receipt=True)
    build("inconsistent", inconsistent=True)
    with pdfium.PdfDocument(str(DESTINATION / "invoice.pdf")) as pdf:
        page = pdf[0]
        bitmap = page.render(scale=2)
        image = bitmap.to_pil().convert("RGB")
        image.save(DESTINATION / "invoice.png")
        image.save(DESTINATION / "scanned-invoice.pdf", "PDF", resolution=144)
        image.close()
        bitmap.close()
        page.close()


if __name__ == "__main__":
    main()
