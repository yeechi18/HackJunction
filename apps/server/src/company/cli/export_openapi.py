"""Export a local contract without starting a server or connecting to PostgreSQL."""

import argparse
import json
from pathlib import Path

from company.config import REPO_ROOT
from company.main import create_app


def export_contract(output: Path) -> Path:
    output = output if output.is_absolute() else REPO_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the M1 FastAPI OpenAPI contract.")
    parser.add_argument("--output", type=Path, default=Path("contracts/openapi.json"))
    arguments = parser.parse_args()
    output = export_contract(arguments.output)
    print(f"OpenAPI contract written to {output}")


if __name__ == "__main__":
    main()

