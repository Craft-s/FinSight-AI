"""Initialize the local encryption key without printing it or rotating an existing key."""
import argparse
import re
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values


def initialize(path: Path) -> str:
    if not path.is_file():
        raise ValueError("Copy .env.example to .env before initializing the encryption key.")
    existing = dotenv_values(path).get("MASTER_ENCRYPTION_KEY")
    if existing:
        try:
            Fernet(existing.encode("ascii"))
        except (ValueError, UnicodeError):
            raise ValueError("An invalid encryption key already exists. Restore it from backup or follow the documented credential reset procedure.") from None
        return "Existing encryption key retained."
    content = path.read_text(encoding="utf-8")
    value = Fernet.generate_key().decode("ascii")
    line = "MASTER_ENCRYPTION_KEY=" + value
    if re.search(r"^MASTER_ENCRYPTION_KEY=.*$", content, flags=re.MULTILINE):
        content = re.sub(r"^MASTER_ENCRYPTION_KEY=.*$", line, content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + "\n" + line + "\n"
    path.write_text(content, encoding="utf-8")
    return "Encryption key initialized in the local environment file; back it up securely."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    args = parser.parse_args()
    try:
        print(initialize(args.env_file))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
