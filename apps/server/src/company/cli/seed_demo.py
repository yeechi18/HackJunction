"""M1 validates demo fixtures; no order schema or seeding exists yet."""

import json
from pathlib import Path

from company.config import REPO_ROOT


def validate_fixtures(directory: Path) -> int:
    paths = [directory / "product.json", directory / "evidence.json"]
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("is_demo") is not True:
            raise ValueError(f"{path.name} must be an object explicitly marked is_demo=true.")
    return len(paths)


def main() -> None:
    try:
        count = validate_fixtures(REPO_ROOT / "fixtures" / "demo")
    except (OSError, ValueError):
        print("Demo fixture validation failed; check product.json/evidence.json and demo labels.")
        raise SystemExit(1) from None
    print(f"Validated {count} demo fixture files. M1 does not insert orders or alter the database.")


if __name__ == "__main__":
    main()

