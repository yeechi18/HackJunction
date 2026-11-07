"""Create local owner credentials without printing passwords or other configuration."""

import argparse
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

from pwdlib import PasswordHash

from company.config import REPO_ROOT


def replace_env_values(content: str, values: dict[str, str]) -> str:
    remaining = dict(values)
    lines = []
    for line in content.splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Z_]+)\s*=", line)
        key = match.group(1) if match else None
        if key in values:
            if key in remaining:
                lines.append(f"{key}={json.dumps(remaining.pop(key))}")
            continue  # Remove duplicate definitions of only the explicitly updated keys.
        lines.append(line)
    lines.extend(f"{key}={json.dumps(value)}" for key, value in remaining.items())
    return "\n".join(lines) + "\n"


def write_private_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initialize_admin(environment: Path, credentials: Path, *, rotate: bool = False) -> bool:
    content = environment.read_text(encoding="utf-8") if environment.exists() else ""
    existing = re.search(r"^\s*ADMIN_PASSWORD_HASH\s*=\s*[\"']?\$argon2", content, re.MULTILINE)
    if existing and not rotate:
        return False
    password = secrets.token_urlsafe(24)
    updates = {"ADMIN_PASSWORD_HASH": PasswordHash.recommended().hash(password)}
    secret = re.search(r"^\s*SESSION_SECRET\s*=\s*(.*)$", content, re.MULTILINE)
    if secret is None or len(secret.group(1).strip().strip("\"'")) < 32:
        updates["SESSION_SECRET"] = secrets.token_urlsafe(48)
    write_private_atomic(credentials, f"username=owner\npassword={password}\n")
    write_private_atomic(environment, replace_env_values(content, updates))
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize local owner credentials; restart the API."
    )
    parser.add_argument(
        "--rotate", action="store_true", help="Explicitly replace an existing password."
    )
    arguments = parser.parse_args()
    location = REPO_ROOT / "var" / "local-admin.txt"
    created = initialize_admin(REPO_ROOT / ".env", location, rotate=arguments.rotate)
    if created:
        print(f"Owner credentials saved to {location}. Restart the API to load configuration.")
    else:
        print("Owner hash already exists; no credentials were changed. Use --rotate to replace it.")


if __name__ == "__main__":
    main()
