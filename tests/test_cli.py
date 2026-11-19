import json

from docintel.cli import main
from tests.conftest import SAMPLES


def test_cli_json_output(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["docintel", str(SAMPLES / "invoice.pdf"), "--provider", "demo"]
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["document"]["invoice_number"] == "INV-10234"


def test_cli_review_exit_code(monkeypatch, tmp_path):
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "docintel",
            str(SAMPLES / "inconsistent.pdf"),
            "--provider",
            "demo",
            "--output",
            str(output),
        ],
    )
    assert main() == 2
    assert json.loads(output.read_text())["validation"]["status"] == "needs_review"


def test_cli_missing_file(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        "sys.argv", ["docintel", str(tmp_path / "missing.pdf"), "--provider", "demo"]
    )
    assert main() == 1
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "file_error"
