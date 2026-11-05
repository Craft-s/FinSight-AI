"""Own the Windows desktop backend inside WSL and relay HTTP over stdio."""

import base64
import fcntl
import hashlib
import http.client
import json
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

MAX_REQUEST = 1024 * 1024
MAX_RESPONSE = 24 * 1024 * 1024
MAX_LINE = 2 * MAX_REQUEST
MAX_WORKERS = 8
ARCHIVE_COMPANIES = ("0000320193", "0000789019", "0001045810")
ARCHIVE_FILINGS = (
    ("0000320193", "0000320193-24-000123"),
    ("0000320193", "0000320193-24-000081"),
    ("0001045810", "0001045810-23-000017"),
)


class RuntimeFailure(Exception):
    pass


class StopRequested(Exception):
    pass


class UnixHttpConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("finsight.local", timeout=timeout)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise RuntimeFailure("桌面数据目录权限不正确。")
    path.chmod(0o700)


def atomic_json(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as pending:
        tmp = Path(pending.name)
        try:
            pending.write(json.dumps(data).encode("utf-8"))
            pending.flush()
            os.fsync(pending.fileno())
            os.replace(tmp, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            tmp.unlink(missing_ok=True)


def private_config(path):
    if not path.exists():
        if (path.parent / "postgres").exists():
            raise RuntimeFailure("现有桌面数据库缺少凭据配置。请从备份恢复 config.json。")
        atomic_json(path, {
            "admin_token": secrets.token_hex(32),
            "master_key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
        })
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        if not re.fullmatch(r"[a-f0-9]{64}", config["admin_token"]):
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}=", config["master_key"]):
            raise ValueError
        if not isinstance(config.get("ollama_base_url", ""), str):
            raise TypeError
    except (OSError, KeyError, TypeError, ValueError):
        raise RuntimeFailure("桌面凭据配置无法读取。请检查 Linux 用户数据目录中的 config.json。") from None
    path.chmod(0o600)
    return config


def postgres_bin(data):
    if data.exists():
        try:
            version = (data / "PG_VERSION").read_text(encoding="ascii").strip()
        except OSError:
            raise RuntimeFailure("现有 PostgreSQL 数据目录无有效版本信息。") from None
        versions = [version] if re.fullmatch(r"[0-9]+", version) else []
    else:
        versions = ["16"]
    required = ("postgres", "initdb", "psql", "createdb", "pg_isready")
    for version in versions:
        candidate = Path("/usr/lib/postgresql") / version / "bin"
        if all(os.access(candidate / name, os.X_OK) for name in required):
            return candidate
    raise RuntimeFailure("找不到与桌面数据兼容的 PostgreSQL。请检查 WSL 安装。")


def stop_process(process, sig, timeout):
    """Give the leader time to shut down, then stop its remaining session members."""
    if process.poll() is None:
        try:
            process.send_signal(sig)
            process.wait(timeout=timeout)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
    # RQ places its work horse in another process group in the same session.
    # A pidfd prevents targeting a reused PID between the session check and signal.
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            if os.getsid(pid) != process.pid:
                continue
            fd = os.pidfd_open(pid)
            try:
                if os.getsid(pid) == process.pid:
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
            finally:
                os.close(fd)
        except (ProcessLookupError, PermissionError):
            continue
    process.wait()


def local_request(request):
    method = request.get("method")
    path = request.get("path")
    if method not in ("GET", "POST") or not isinstance(path, str) or not 1 <= len(path) <= 4096:
        raise ValueError
    if not all(33 <= ord(char) <= 126 for char in path) or "#" in path or "\\" in path:
        raise ValueError
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or not (parts.path == "/health" or parts.path.startswith("/api/v1/")):
        raise ValueError
    decoded = unquote(parts.path, errors="strict")
    if "\\" in decoded or any(ord(char) < 32 for char in decoded):
        raise ValueError
    if {".", ".."} & set(decoded.split("/")):
        raise ValueError
    body = request.get("body", "")
    if not isinstance(body, str) or len(body) > 4 * ((MAX_REQUEST + 2) // 3):
        raise ValueError
    data = base64.b64decode(body, validate=True)
    if len(data) > MAX_REQUEST or (method == "GET" and data):
        raise ValueError
    content_type = request.get("contentType") or "application/json"
    if not isinstance(content_type, str) or not re.fullmatch(
        r"application/json(?:;\s*charset=utf-8)?", content_type, flags=re.IGNORECASE,
    ):
        raise ValueError
    return method, path, data, content_type


class DesktopRuntime:
    def __init__(self, bundle, state, socket_directory, log):
        self.bundle = bundle
        self.state = state
        self.log = log
        self.socket_directory = socket_directory
        self.api_socket = socket_directory / "api.sock"
        self.redis_socket = socket_directory / "redis.sock"
        self.python = state / "venv/bin/python"
        self.stop = threading.Event()
        self.write_lock = threading.Lock()
        self.requests = queue.Queue(maxsize=32)
        self.children = []
        self.config = private_config(state / "config.json")
        self.env = {
            "PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "LANG": "C.UTF-8",
            "PYTHONPATH": str(bundle / "backend"), "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "DATABASE_URL": "postgresql+psycopg://finsight@/finsight?host=" + quote(str(socket_directory), safe=""),
            "REDIS_URL": "unix://" + str(self.redis_socket),
            "LOCAL_ADMIN_TOKEN": self.config["admin_token"],
            "MASTER_ENCRYPTION_KEY": self.config["master_key"],
            "OLLAMA_BASE_URL": self.config.get("ollama_base_url", ""),
            "DATA_MODE": "sec_snapshot", "CN_DATA_MODE": "cn_live", "TASK_MODE": "rq",
            "DEVELOPMENT": "false", "SNAPSHOT_DIRECTORY": str(bundle / "data/snapshots"),
            "CORS_ORIGINS": "https://finsight.local",
        }
        self.worker_name = "finsight-desktop-" + secrets.token_hex(10)

    def emit(self, payload):
        try:
            with self.write_lock:
                sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
                sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError):
            self.stop.set()

    def note(self, message, kind="status"):
        self.log.write(f"{datetime.now(UTC).isoformat()} {kind}: {message}\n")
        self.log.flush()
        self.emit({"type": kind, "message": message})

    def reply(self, identifier, status, content_type, body, disposition=None):
        self.emit({"id": identifier, "status": status, "contentType": content_type,
                   "disposition": disposition, "body": base64.b64encode(body).decode("ascii")})

    def reply_error(self, identifier, status, code, message):
        body = json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False).encode("utf-8")
        self.reply(identifier, status, "application/json; charset=utf-8", body)

    def read_requests(self):
        # Raw reads keep the interpreter's stdin buffer unlocked during teardown.
        pending = bytearray()
        try:
            while not self.stop.is_set():
                block = os.read(sys.stdin.fileno(), 65536)
                if not block:
                    break
                pending.extend(block)
                while (newline := pending.find(b"\n")) >= 0:
                    if newline > MAX_LINE:
                        return
                    request = json.loads(bytes(pending[:newline]))
                    del pending[:newline + 1]
                    if not isinstance(request, dict) or request.get("type") == "shutdown":
                        return
                    identifier = request.get("id")
                    if type(identifier) is not int or not 0 <= identifier < 2**53:
                        return
                    try:
                        self.requests.put_nowait(request)
                    except queue.Full:
                        self.reply_error(identifier, 503, "DESKTOP_BUSY", "桌面服务正在处理其它请求。")
                if len(pending) > MAX_LINE:
                    return
        except (ValueError, OSError, RecursionError):
            pass
        finally:
            self.stop.set()

    def check_stop(self):
        if self.stop.is_set():
            raise StopRequested

    def check_children(self):
        self.check_stop()
        for label, process, _sig, _timeout in self.children:
            if (code := process.poll()) is not None:
                raise RuntimeFailure(f"{label}已退出（状态 {code}）。")

    def spawn(self, args, *, capture=False):
        self.check_stop()
        return subprocess.Popen(
            args, cwd=self.state, env=self.env, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def run(self, args, label, timeout=30, *, capture=False, check=True):
        with self.spawn(args, capture=capture) as process:
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    self.check_children()
                    if time.monotonic() >= deadline:
                        raise RuntimeFailure(f"{label}超时。")
                    self.stop.wait(0.05)
            except BaseException:
                stop_process(process, signal.SIGTERM, 1)
                raise
            if check and process.returncode:
                raise RuntimeFailure(f"{label}失败（状态 {process.returncode}）。")
            result = process.stdout.read().strip() if capture else process.returncode
        return result

    def start_service(self, args, label, sig=signal.SIGTERM, timeout=4):
        self.children.append((label, self.spawn(args), sig, timeout))

    def wait_until(self, ready, label, timeout=35):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check_children()
            if ready():
                return
            self.stop.wait(0.2)
        raise RuntimeFailure(f"等待{label}就绪超时。")

    def start_database(self):
        self.note("正在启动本地数据库…")
        data = self.state / "postgres"
        binaries = postgres_bin(data)
        if not data.exists():
            with tempfile.TemporaryDirectory(dir=self.state, prefix="pg-init-") as stage:
                cluster = Path(stage) / "cluster"
                self.run([binaries / "initdb", "-D", cluster, "--no-instructions", "--locale=C.UTF-8",
                          "--encoding=UTF8", "--auth-local=trust", "--auth-host=reject", "-U", "finsight"],
                         "初始化 PostgreSQL", timeout=90)
                cluster.rename(data)
        self.start_service([binaries / "postgres", "-D", data, "-k", self.socket_directory,
                            "-c", "listen_addresses=", "-c", "unix_socket_permissions=0700",
                            # Use the cluster's own pg_dynshmem instead of WSL session IPC.
                            "-c", "dynamic_shared_memory_type=mmap"],
                           "PostgreSQL", sig=signal.SIGINT, timeout=6)
        connect = ["-h", self.socket_directory, "-p", "5432", "-U", "finsight"]
        self.wait_until(lambda: self.run([binaries / "pg_isready", *connect, "-t", "1", "-d", "postgres"],
                                        "检查 PostgreSQL", timeout=2, check=False) == 0, "数据库")
        has_database = self.run([binaries / "psql", *connect, "-XAt", "-d", "postgres", "-c",
                                 "SELECT 1 FROM pg_database WHERE datname = 'finsight'"],
                                "检查桌面数据库", capture=True)
        if has_database != b"1":
            self.run([binaries / "createdb", *connect, "finsight"], "创建桌面数据库")

    def start_redis(self):
        redis = shutil.which("redis-server", path=self.env["PATH"])
        redis_cli = shutil.which("redis-cli", path=self.env["PATH"])
        if not redis or not redis_cli:
            raise RuntimeFailure("WSL 未安装 Redis。请重新运行桌面环境设置。")
        data = self.state / "redis"
        private_directory(data)
        self.start_service([redis, "--port", "0", "--unixsocket", self.redis_socket,
                            "--unixsocketperm", "700", "--dir", data, "--appendonly", "yes",
                            "--save", "", "--daemonize", "no"], "Redis")
        self.redis_cli = [redis_cli, "--raw", "-s", self.redis_socket]

        def ready():
            return self.redis_socket.exists() and self.run([*self.redis_cli, "PING"], "检查 Redis", timeout=2,
                                                            capture=True, check=False) == b"PONG"

        self.wait_until(ready, "队列存储")

    def migrate_and_seed(self):
        self.note("正在检查数据库结构…")
        self.run([self.python, "-m", "alembic", "-c", self.bundle / "backend/alembic.ini", "upgrade", "head"],
                 "数据库迁移", timeout=90)
        marker = self.state / "archive-seed-v1.json"
        if marker.exists():
            return
        self.note("正在导入随程序附带的真实历史财务资料…")
        for cik in ARCHIVE_COMPANIES:
            self.run([self.python, "-m", "app.cli", "import-snapshot", "--directory",
                      self.bundle / "data/snapshots" / cik], "导入历史财务快照", timeout=90)
        for cik, accession in ARCHIVE_FILINGS:
            self.run([self.python, "-m", "app.cli", "import-document", "--manifest",
                      self.bundle / "data/documents" / cik / accession / "manifest.json"],
                     "导入历史财报正文", timeout=90)
        atomic_json(marker, {"completed": True})

    def start_app(self):
        self.note("正在启动数据服务与后台任务…")
        self.start_service([self.python, "-m", "uvicorn", "app.main:app", "--uds", self.api_socket,
                            "--no-access-log", "--timeout-graceful-shutdown", "4"], "API", timeout=6)
        self.start_service([self.python, "-m", "app.workers.run", "--name", self.worker_name], "RQ worker")

        def api_ready():
            connection = UnixHttpConnection(self.api_socket, 1)
            try:
                connection.request("GET", "/health/ready")
                with connection.getresponse() as response:
                    payload = json.loads(response.read(8192))
                    return response.status == 200 and payload["database"] == payload["redis"] == "ok" \
                        and payload["task_mode"] == "rq"
            except (OSError, http.client.HTTPException, KeyError, ValueError):
                return False
            finally:
                connection.close()

        def worker_ready():
            key = "rq:worker:" + self.worker_name
            return self.run([*self.redis_cli, "HGET", key, "pid"], "检查 RQ worker", timeout=2,
                            capture=True) == str(self.children[-1][1].pid).encode("ascii")

        self.wait_until(api_ready, "API")
        self.wait_until(worker_ready, "后台任务")

    def forward(self, request):
        identifier = request["id"]
        try:
            method, path, data, content_type = local_request(request)
        except ValueError:
            self.reply_error(identifier, 400, "DESKTOP_REQUEST_INVALID", "桌面请求无效。")
            return
        headers = {"Content-Type": content_type}
        if request.get("authorized") is True:
            headers["X-Admin-Token"] = self.config["admin_token"]
        connection = UnixHttpConnection(self.api_socket, 155)
        try:
            connection.request(method, path, body=data or None, headers=headers)
            with connection.getresponse() as response:
                body = response.read(MAX_RESPONSE + 1)
                if len(body) > MAX_RESPONSE:
                    self.reply_error(identifier, 502, "DESKTOP_RESPONSE_LIMIT", "桌面响应超过大小限制。")
                    return
                self.reply(identifier, response.status, response.getheader("Content-Type", "application/octet-stream"),
                           body, response.getheader("Content-Disposition"))
        except (OSError, http.client.HTTPException, ValueError):
            if not self.stop.is_set():
                self.reply_error(identifier, 502, "DESKTOP_API_UNAVAILABLE", "暂时无法连接本地数据服务。")
        finally:
            connection.close()

    def serve(self):
        threading.Thread(target=self.read_requests, daemon=True).start()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            try:
                if not self.python.is_file():
                    raise RuntimeFailure("桌面 Python 环境不存在。请先运行桌面环境设置。")
                expected = hashlib.sha256((self.bundle / "backend/requirements.lock").read_bytes()).hexdigest()
                stamp = self.state / "requirements.sha256"
                if not stamp.is_file() or stamp.read_text(encoding="ascii").strip() != expected:
                    raise RuntimeFailure("桌面依赖与程序版本不一致。请重新运行桌面环境设置。")
                self.start_database()
                self.start_redis()
                self.migrate_and_seed()
                self.start_app()
                self.note("本地桌面服务已就绪。", kind="ready")
                active = set()
                while True:
                    self.check_children()
                    active = {future for future in active if not future.done()}
                    if len(active) >= MAX_WORKERS:
                        self.stop.wait(0.05)
                        continue
                    try:
                        request = self.requests.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    active.add(pool.submit(self.forward, request))
            except StopRequested:
                pass
            finally:
                self.stop.set()
                for label, process, sig, timeout in reversed(self.children):
                    stop_process(process, sig, timeout)
                    self.log.write(f"{datetime.now(UTC).isoformat()} stopped: {label}\n")
                self.log.flush()


def main():
    if os.getuid() == 0 or len(sys.argv) != 2:
        raise RuntimeFailure("桌面运行时需要专用的非 root Linux 用户与有效程序目录。")
    os.umask(0o077)
    bundle = Path(sys.argv[1]).resolve(strict=True)
    if not (bundle / "backend/alembic.ini").is_file():
        raise RuntimeFailure("桌面程序包缺少后端文件。")
    state = Path.home() / ".local/share/finsight-ai/desktop"
    private_directory(state)
    with (state / "desktop.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeFailure("桌面程序正在运行，或首次设置尚未完成。") from None
        with (
            (state / "runtime.log").open("w", encoding="utf-8") as log,
            tempfile.TemporaryDirectory(prefix="finsight-desktop-", dir="/tmp") as socket_dir,
        ):
            runtime = DesktopRuntime(bundle, state, Path(socket_dir), log)
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(sig, lambda *_args: runtime.stop.set())
            try:
                runtime.serve()
            except RuntimeFailure as exc:
                runtime.note(str(exc), kind="error")
                return 1
            except Exception as exc:  # noqa: BLE001 - keep credentials out of protocol errors
                runtime.note(f"本地运行时无法继续（{type(exc).__name__}）。", kind="error")
                return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeFailure) as exc:
        message = str(exc) if isinstance(exc, RuntimeFailure) else "无法读取或初始化桌面运行目录。"
        print(json.dumps({"type": "error", "message": message}), flush=True)
        raise SystemExit(1) from None
