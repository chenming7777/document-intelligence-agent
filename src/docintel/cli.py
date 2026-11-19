import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

from docintel.agent import DocumentAgent
from docintel.config import Settings
from docintel.errors import DocumentError


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract and validate an invoice or receipt.")
    parser.add_argument("document", type=Path)
    parser.add_argument("--provider", choices=["demo", "gemini"])
    parser.add_argument("--output", type=Path, help="Write report JSON to this path.")
    args = parser.parse_args()
    settings = Settings(**({"provider": args.provider} if args.provider else {}))
    try:
        with args.document.open("rb") as stream:
            content = stream.read(settings.max_upload_bytes + 1)
        with closing(DocumentAgent(settings)) as agent:
            report = agent.run(content, args.document.name)
        output = report.model_dump_json(indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
        else:
            print(output)
        return 0 if report.validation.status == "passed" else 2
    except (DocumentError, OSError) as exc:
        error = {"code": getattr(exc, "code", "file_error"), "message": str(exc)}
        print(json.dumps({"error": error}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
