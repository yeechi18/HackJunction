from pathlib import Path

from dotenv import dotenv_values
from pwdlib import PasswordHash

from company.cli.init_admin import initialize_admin


def test_admin_init_preserves_mimo_key_and_does_not_rotate_implicitly(tmp_path: Path):
    environment = tmp_path / ".env"
    credentials = tmp_path / "var" / "local-admin.txt"
    environment.write_text("MIMO_API_KEY=keep-exactly-this\nSESSION_SECRET=\n", encoding="utf-8")
    assert initialize_admin(environment, credentials)
    configuration = dotenv_values(environment, interpolate=False)
    assert configuration["MIMO_API_KEY"] == "keep-exactly-this"
    assert len(configuration["SESSION_SECRET"]) >= 32
    password = credentials.read_text(encoding="utf-8").split("password=", 1)[1].strip()
    assert PasswordHash.recommended().verify(password, configuration["ADMIN_PASSWORD_HASH"])
    unchanged = environment.read_bytes(), credentials.read_bytes()
    assert not initialize_admin(environment, credentials)
    assert (environment.read_bytes(), credentials.read_bytes()) == unchanged
