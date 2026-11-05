"""Prepare the dedicated Linux account and Python runtime for the Windows app."""

import argparse
import fcntl
import hashlib
import os
import pwd
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ACCOUNT = "finsight-desktop"
ACCOUNT_HOME = Path("/home") / ACCOUNT
ACCOUNT_COMMENT = "FinSight AI desktop service"
ACCOUNT_SHELL = "/usr/sbin/nologin"
STATE = ACCOUNT_HOME / ".local/share/finsight-ai/desktop"


def check_prerequisites():
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("FinSight Desktop requires Python 3.12 in Ubuntu 24.04.")
    executables = ("/usr/lib/postgresql/16/bin/initdb", "/usr/lib/postgresql/16/bin/postgres")
    if not all(os.access(path, os.X_OK) for path in executables) or not shutil.which("redis-server"):
        raise RuntimeError(
            "Install the Linux prerequisites in Ubuntu first: "
            "sudo apt install python3-venv postgresql-16 redis-server"
        )


def service_account():
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        subprocess.run(["useradd", "--system", "--create-home", "--home-dir", str(ACCOUNT_HOME),
                        "--shell", ACCOUNT_SHELL, "--comment", ACCOUNT_COMMENT, ACCOUNT], check=True)
        account = pwd.getpwnam(ACCOUNT)
    if (account.pw_uid == 0 or Path(account.pw_dir) != ACCOUNT_HOME
            or account.pw_shell != ACCOUNT_SHELL or account.pw_gecos != ACCOUNT_COMMENT):
        raise RuntimeError("The reserved Linux account finsight-desktop already belongs to another setup.")
    return account


def install_runtime(requirements):
    account = service_account()
    if os.geteuid() != account.pw_uid:
        raise RuntimeError("Runtime dependencies must be installed as the dedicated Linux account.")
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / "desktop.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Close FinSight AI before initializing or updating its runtime.") from None
        digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
        stamp = STATE / "requirements.sha256"
        python = STATE / "venv/bin/python"
        pip = [str(python), "-m", "pip", "--disable-pip-version-check", "--no-input"]
        recreate = not python.is_file() or subprocess.run(
            [*pip, "--version"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20
        ).returncode != 0
        if recreate:
            try:
                venv.EnvBuilder(with_pip=True).create(STATE / "venv")
            except subprocess.CalledProcessError:
                raise RuntimeError("Install python3-venv in Ubuntu and run setup again.") from None
        if recreate or not stamp.is_file() or stamp.read_text(encoding="ascii").strip() != digest:
            print("Installing locked Python dependencies for FinSight AI...", flush=True)
            subprocess.run([*pip, "install", "--timeout", "20", "--retries", "1", "-r", str(requirements)],
                           check=True, timeout=3600)
        subprocess.run([*pip, "check"], check=True, timeout=30)
        temporary = stamp.with_suffix(".tmp")
        temporary.write_text(digest + "\n", encoding="ascii")
        temporary.replace(stamp)
    print("FinSight AI runtime is ready. Open FinSight.Desktop.exe.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--install-user", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    bundle = args.bundle.resolve(strict=True)
    requirements = bundle / "backend/requirements.lock"
    if not requirements.is_file() or not (bundle / "backend/app/main.py").is_file():
        raise RuntimeError("FinSight AI runtime files are missing. Extract the entire package before setup.")
    check_prerequisites()
    if args.install_user:
        install_runtime(requirements)
        return
    if os.geteuid() != 0:
        raise RuntimeError("Start setup with the Windows Setup-FinSight.cmd launcher.")
    service_account()
    os.execvp("runuser", ["runuser", "-u", ACCOUNT, "--", sys.executable, "-I", __file__, str(bundle), "--install-user"])


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else "FinSight AI setup failed. Review the preceding setup output.",
              file=sys.stderr)
        raise SystemExit(1) from None
