from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import select
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


# Frozen apps unpack code into a temporary directory; save user data beside
# the executable so profiles and credentials survive the next launch.
APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
DEFAULT_PROXY_FILE = APP_DIR / "proxies.txt"
DEFAULT_DATA_DIR = APP_DIR / "proxydeck_data"
DEFAULT_START_URL = "https://www.google.com/"
IP_DETAILS_URL = "https://ipwho.is/"
STATE_VERSION = 1


class ProxyError(RuntimeError):
    """User-facing proxy manager error."""


@dataclass(frozen=True)
class ProxySpec:
    raw: str
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""
    region_hint: str = ""

    @property
    def key(self) -> str:
        canonical = (
            f"{self.scheme.lower()}://{self.host.lower()}:{self.port}"
            f"|{self.username}|{self.password}"
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def masked(self) -> str:
        if self.username:
            return f"{self.address}  /  {self.username}:••••••"
        return self.address

    @property
    def server_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def authenticated_url(self) -> str:
        if not self.username:
            return self.server_url
        username = quote(self.username, safe="")
        password = quote(self.password, safe="")
        return f"{self.scheme}://{username}:{password}@{self.host}:{self.port}"


def parse_proxy(raw: str) -> ProxySpec:
    original = raw.strip()
    if not original:
        raise ValueError("Proxy cannot be empty.")

    proxy_text, separator, region_hint = original.partition("|")
    proxy_text = proxy_text.strip()
    scheme = "http"
    lowered = proxy_text.lower()
    for prefix in ("http://", "https://", "socks5://", "socks4://"):
        if lowered.startswith(prefix):
            scheme = prefix[:-3]
            proxy_text = proxy_text[len(prefix):]
            break

    parts = proxy_text.split(":", 3)
    if len(parts) not in (2, 4):
        raise ValueError(
            "Use HOST:PORT or HOST:PORT:USERNAME:PASSWORD."
        )
    host = parts[0].strip()
    if not host:
        raise ValueError("Proxy host cannot be empty.")
    try:
        port = int(parts[1].strip())
    except ValueError as error:
        raise ValueError("Proxy port must be a number.") from error
    if not 1 <= port <= 65535:
        raise ValueError("Proxy port must be between 1 and 65535.")

    username = parts[2].strip() if len(parts) == 4 else ""
    password = parts[3] if len(parts) == 4 else ""
    if len(parts) == 4 and (not username or not password):
        raise ValueError(
            "Authenticated proxies require both a username and password."
        )
    return ProxySpec(
        raw=original,
        scheme=scheme,
        host=host,
        port=port,
        username=username,
        password=password,
        region_hint=region_hint.strip() if separator else "",
    )


def read_proxy_file(path: Path) -> tuple[list[ProxySpec], list[tuple[int, str]]]:
    proxies: list[ProxySpec] = []
    errors: list[tuple[int, str]] = []
    if not path.exists():
        return proxies, errors
    for number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            proxies.append(parse_proxy(stripped))
        except ValueError as error:
            errors.append((number, str(error)))
    return proxies, errors


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_record(spec: ProxySpec) -> dict:
    return {
        "proxy_id": spec.key,
        "address": spec.address,
        "alias": "",
        "favorite": False,
        "notes": "",
        "created_at": _now(),
        "updated_at": _now(),
        "last_opened_at": "",
        "last_closed_at": "",
        "last_checked_at": "",
        "launch_count": 0,
        "active_seconds": 0.0,
        "bytes_up": 0,
        "bytes_down": 0,
        "health": "Unchecked",
        "latency_ms": None,
        "exit_ip": "",
        "country": "",
        "country_code": "",
        "region": spec.region_hint,
        "city": "",
        "isp": "",
        "timezone": "",
        "last_error": "",
    }


class ProxyStore:
    """Owns proxies.txt and non-sensitive persistent usage metadata."""

    def __init__(
        self,
        proxy_file: Path = DEFAULT_PROXY_FILE,
        data_dir: Path = DEFAULT_DATA_DIR,
    ) -> None:
        self.proxy_file = Path(proxy_file)
        self.data_dir = Path(data_dir)
        self.state_file = self.data_dir / "state.json"
        self.settings_file = self.data_dir / "settings.json"
        self._lock = threading.RLock()

    def proxies(self) -> tuple[list[ProxySpec], list[tuple[int, str]]]:
        specs, errors = read_proxy_file(self.proxy_file)
        with self._lock:
            state = self._load_state_unlocked()
            changed = False
            for spec in specs:
                if spec.key not in state["proxies"]:
                    state["proxies"][spec.key] = _empty_record(spec)
                    changed = True
                else:
                    record = state["proxies"][spec.key]
                    if record.get("address") != spec.address:
                        record["address"] = spec.address
                        record["updated_at"] = _now()
                        changed = True
                    if spec.region_hint and not record.get("region"):
                        record["region"] = spec.region_hint
                        changed = True
            if changed:
                self._write_state_unlocked(state)
        return specs, errors

    def record(self, spec_or_key: ProxySpec | str) -> dict:
        key = spec_or_key.key if isinstance(spec_or_key, ProxySpec) else spec_or_key
        with self._lock:
            state = self._load_state_unlocked()
            record = state["proxies"].get(key)
            if record is None:
                if not isinstance(spec_or_key, ProxySpec):
                    return {}
                record = _empty_record(spec_or_key)
                state["proxies"][key] = record
                self._write_state_unlocked(state)
            return copy.deepcopy(record)

    def all_records(self) -> dict[str, dict]:
        with self._lock:
            return copy.deepcopy(self._load_state_unlocked()["proxies"])

    def update(self, spec_or_key: ProxySpec | str, **values) -> dict:
        key = spec_or_key.key if isinstance(spec_or_key, ProxySpec) else spec_or_key
        with self._lock:
            state = self._load_state_unlocked()
            if key not in state["proxies"]:
                if not isinstance(spec_or_key, ProxySpec):
                    raise ProxyError("Proxy metadata no longer exists.")
                state["proxies"][key] = _empty_record(spec_or_key)
            values["updated_at"] = _now()
            state["proxies"][key].update(values)
            self._write_state_unlocked(state)
            return copy.deepcopy(state["proxies"][key])

    def mark_opened(self, spec: ProxySpec) -> None:
        record = self.record(spec)
        self.update(
            spec,
            last_opened_at=_now(),
            launch_count=int(record.get("launch_count", 0)) + 1,
            last_error="",
        )

    def mark_closed(
        self,
        spec: ProxySpec,
        active_seconds: float,
        bytes_up: int,
        bytes_down: int,
    ) -> None:
        record = self.record(spec)
        self.update(
            spec,
            last_closed_at=_now(),
            active_seconds=float(record.get("active_seconds", 0.0))
            + max(0.0, active_seconds),
            bytes_up=int(record.get("bytes_up", 0)) + max(0, bytes_up),
            bytes_down=int(record.get("bytes_down", 0)) + max(0, bytes_down),
        )

    def save_check(
        self,
        spec: ProxySpec,
        result: dict,
        *,
        bytes_up: int = 0,
        bytes_down: int = 0,
    ) -> None:
        record = self.record(spec)
        values = {
            "last_checked_at": _now(),
            "health": result.get("health", "Error"),
            "latency_ms": result.get("latency_ms"),
            "exit_ip": result.get("exit_ip", ""),
            "country": result.get("country", ""),
            "country_code": result.get("country_code", ""),
            "region": result.get("region", "") or spec.region_hint,
            "city": result.get("city", ""),
            "isp": result.get("isp", ""),
            "timezone": result.get("timezone", ""),
            "last_error": result.get("error", ""),
            "bytes_up": int(record.get("bytes_up", 0)) + max(0, bytes_up),
            "bytes_down": int(record.get("bytes_down", 0))
            + max(0, bytes_down),
        }
        self.update(spec, **values)

    def add(self, raw: str) -> ProxySpec:
        spec = parse_proxy(raw)
        specs, _errors = read_proxy_file(self.proxy_file)
        if any(existing.key == spec.key for existing in specs):
            raise ValueError("That proxy is already in proxies.txt.")
        with self._lock:
            lines = (
                self.proxy_file.read_text(encoding="utf-8-sig").splitlines()
                if self.proxy_file.exists()
                else []
            )
            lines.append(spec.raw)
            self._write_proxy_lines(lines)
        self.record(spec)
        return spec

    def edit(self, old_spec: ProxySpec, raw: str) -> ProxySpec:
        new_spec = parse_proxy(raw)
        specs, _errors = read_proxy_file(self.proxy_file)
        if any(
            existing.key == new_spec.key and existing.raw != old_spec.raw
            for existing in specs
        ):
            raise ValueError("That proxy is already in proxies.txt.")
        with self._lock:
            lines = self.proxy_file.read_text(
                encoding="utf-8-sig"
            ).splitlines()
            try:
                index = next(
                    i for i, line in enumerate(lines)
                    if line.strip() == old_spec.raw
                )
            except StopIteration as error:
                raise ValueError("The selected proxy is no longer in the file.") from error
            lines[index] = new_spec.raw
            self._write_proxy_lines(lines)

            if old_spec.key != new_spec.key:
                state = self._load_state_unlocked()
                old_record = state["proxies"].pop(old_spec.key, None)
                if old_record is not None:
                    old_record["proxy_id"] = new_spec.key
                    old_record["address"] = new_spec.address
                    old_record["updated_at"] = _now()
                    state["proxies"][new_spec.key] = old_record
                    self._write_state_unlocked(state)
        self.record(new_spec)
        return new_spec

    def remove(self, spec: ProxySpec) -> None:
        with self._lock:
            lines = self.proxy_file.read_text(
                encoding="utf-8-sig"
            ).splitlines()
            try:
                index = next(
                    i for i, line in enumerate(lines) if line.strip() == spec.raw
                )
            except StopIteration as error:
                raise ValueError("The selected proxy is no longer in the file.") from error
            del lines[index]
            self._write_proxy_lines(lines)

    def import_file(self, source: Path) -> tuple[int, list[str]]:
        candidates, errors = read_proxy_file(Path(source))
        current, _current_errors = read_proxy_file(self.proxy_file)
        known = {spec.key for spec in current}
        additions = [spec for spec in candidates if spec.key not in known]
        with self._lock:
            lines = (
                self.proxy_file.read_text(encoding="utf-8-sig").splitlines()
                if self.proxy_file.exists()
                else []
            )
            lines.extend(spec.raw for spec in additions)
            self._write_proxy_lines(lines)
        for spec in additions:
            self.record(spec)
        error_text = [f"Line {line}: {message}" for line, message in errors]
        return len(additions), error_text

    def settings(self) -> dict:
        defaults = {
            "start_url": DEFAULT_START_URL,
            "health_timeout": 15,
            "close_on_exit": True,
        }
        with self._lock:
            if not self.settings_file.exists():
                return defaults
            try:
                loaded = json.loads(
                    self.settings_file.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                return defaults
        if isinstance(loaded, dict):
            defaults.update(loaded)
        return defaults

    def save_settings(self, settings: dict) -> None:
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_file.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(settings, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(self.settings_file)

    def profile_path(self, spec: ProxySpec) -> Path:
        safe_address = re.sub(r"[^A-Za-z0-9._-]+", "_", spec.address)[:48]
        return self.data_dir / "chrome_profiles" / f"{safe_address}-{spec.key[:10]}"

    def delete_profile(self, spec: ProxySpec) -> None:
        target = self.profile_path(spec)
        root = (self.data_dir / "chrome_profiles").resolve()
        resolved = target.resolve()
        if resolved == root or root not in resolved.parents:
            raise ProxyError("Refusing to delete a path outside chrome_profiles.")
        if target.exists():
            shutil.rmtree(target)

    def _load_state_unlocked(self) -> dict:
        if not self.state_file.exists():
            return {"version": STATE_VERSION, "proxies": {}}
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProxyError(f"Could not read state.json: {error}") from error
        if not isinstance(state, dict) or not isinstance(
            state.get("proxies"), dict
        ):
            raise ProxyError("state.json has an invalid format.")
        state.setdefault("version", STATE_VERSION)
        return state

    def _write_state_unlocked(self, state: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self.state_file)

    def _write_proxy_lines(self, lines: Iterable[str]) -> None:
        self.proxy_file.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(lines).rstrip("\n")
        if text:
            text += "\n"
        temporary = self.proxy_file.with_suffix(self.proxy_file.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self.proxy_file)


class _BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _BridgeHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        bridge: AuthenticatedProxyBridge = self.server.bridge
        upstream = None
        try:
            request_head, request_body = self._read_request(self.request)
            if not request_head:
                return
            upstream = socket.create_connection(
                (bridge.spec.host, bridge.spec.port), timeout=20
            )
            if bridge.spec.scheme == "https":
                upstream = ssl.create_default_context().wrap_socket(
                    upstream, server_hostname=bridge.spec.host
                )
            outgoing = bridge.with_authorization(request_head) + request_body
            upstream.sendall(outgoing)
            bridge.add_up(len(outgoing))
            self._relay(self.request, upstream, bridge)
        except (OSError, ValueError) as error:
            bridge.last_error = str(error)
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass

    @staticmethod
    def _read_request(client: socket.socket) -> tuple[bytes, bytes]:
        data = bytearray()
        client.settimeout(20)
        while b"\r\n\r\n" not in data:
            chunk = client.recv(65536)
            if not chunk:
                return b"", b""
            data.extend(chunk)
            if len(data) > 1024 * 1024:
                raise ValueError("Proxy request headers are too large.")
        head, body = bytes(data).split(b"\r\n\r\n", 1)
        return head + b"\r\n\r\n", body

    @staticmethod
    def _relay(
        client: socket.socket,
        upstream: socket.socket,
        bridge: "AuthenticatedProxyBridge",
    ) -> None:
        sockets = (client, upstream)
        client.settimeout(None)
        upstream.settimeout(None)
        while not bridge.stop_event.is_set():
            readable, _writable, exceptional = select.select(
                sockets, [], sockets, 1.0
            )
            if exceptional:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                if source is client:
                    bridge.add_up(len(data))
                    upstream.sendall(data)
                else:
                    bridge.add_down(len(data))
                    client.sendall(data)


class AuthenticatedProxyBridge:
    """Local no-auth HTTP proxy that forwards to one upstream proxy."""

    def __init__(self, spec: ProxySpec) -> None:
        if spec.scheme not in {"http", "https"}:
            raise ProxyError(
                "Chrome launching and bandwidth tracking currently support "
                "HTTP/HTTPS proxies."
            )
        self.spec = spec
        credentials = base64.b64encode(
            f"{spec.username}:{spec.password}".encode("utf-8")
        ).decode("ascii")
        self.authorization = (
            f"Proxy-Authorization: Basic {credentials}".encode("ascii")
            if spec.username
            else b""
        )
        self.stop_event = threading.Event()
        self.last_error = ""
        self._server: _BridgeServer | None = None
        self._thread: threading.Thread | None = None
        self._count_lock = threading.Lock()
        self._bytes_up = 0
        self._bytes_down = 0

    @property
    def url(self) -> str:
        if self._server is None:
            raise ProxyError("The local proxy bridge is not running.")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> "AuthenticatedProxyBridge":
        self._server = _BridgeServer(("127.0.0.1", 0), _BridgeHandler)
        self._server.bridge = self
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            args=(0.05,),
            name=f"proxy-bridge-{self.spec.key[:8]}",
            daemon=True,
        )
        self._thread.start()
        return self

    def close(self) -> None:
        server = self._server
        if server is None:
            return
        self._server = None
        self.stop_event.set()
        server.shutdown()
        server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def counters(self) -> tuple[int, int]:
        with self._count_lock:
            return self._bytes_up, self._bytes_down

    def add_up(self, amount: int) -> None:
        with self._count_lock:
            self._bytes_up += amount

    def add_down(self, amount: int) -> None:
        with self._count_lock:
            self._bytes_down += amount

    def with_authorization(self, request_head: bytes) -> bytes:
        lines = request_head[:-4].split(b"\r\n")
        filtered = [
            line
            for line in lines
            if not line.lower().startswith(b"proxy-authorization:")
        ]
        if self.authorization:
            filtered.insert(1, self.authorization)
        return b"\r\n".join(filtered) + b"\r\n\r\n"


def find_chrome() -> Path | None:
    candidates: list[Path] = []
    if sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if base := os.environ.get(variable):
                candidates.append(
                    Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
                )
        try:
            import winreg

            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(
                        hive,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                    ) as key:
                        value, _kind = winreg.QueryValueEx(key, None)
                        if value:
                            candidates.append(Path(value))
                except OSError:
                    pass
        except ImportError:
            pass
    elif sys.platform == "darwin":
        candidates.append(
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        )
    else:
        for command in ("google-chrome", "google-chrome-stable", "chromium"):
            if found := shutil.which(command):
                candidates.append(Path(found))
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


@dataclass
class BrowserSession:
    spec: ProxySpec
    process: subprocess.Popen
    bridge: AuthenticatedProxyBridge
    started_at: float
    profile_path: Path
    last_sample_at: float = field(default_factory=time.monotonic)
    last_sample_bytes: int = 0
    current_rate: float = 0.0


class BrowserManager:
    def __init__(
        self,
        store: ProxyStore,
        event_callback: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.store = store
        self.chrome = find_chrome()
        self.event_callback = event_callback or (lambda *_args: None)
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = threading.RLock()

    def launch(self, spec: ProxySpec, start_url: str) -> bool:
        if not self.chrome:
            raise ProxyError("Google Chrome was not found on this computer.")
        with self._lock:
            current = self._sessions.get(spec.key)
            if current and current.process.poll() is None:
                self.open_tab(spec, start_url)
                return False

        profile_path = self.store.profile_path(spec)
        profile_path.mkdir(parents=True, exist_ok=True)
        bridge = AuthenticatedProxyBridge(spec).start()
        command = [
            str(self.chrome),
            f"--user-data-dir={profile_path}",
            f"--proxy-server={bridge.url}",
            "--proxy-bypass-list=<-loopback>",
            "--disable-quic",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            start_url,
        ]
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except Exception:
            bridge.close()
            raise
        session = BrowserSession(
            spec=spec,
            process=process,
            bridge=bridge,
            started_at=time.monotonic(),
            profile_path=profile_path,
        )
        with self._lock:
            self._sessions[spec.key] = session
        self.store.mark_opened(spec)
        self.event_callback("opened", spec.key, "Chrome opened")
        threading.Thread(
            target=self._watch_session,
            args=(session,),
            name=f"chrome-watch-{spec.key[:8]}",
            daemon=True,
        ).start()
        return True

    def open_tab(self, spec: ProxySpec, url: str) -> None:
        with self._lock:
            session = self._sessions.get(spec.key)
            if not session or session.process.poll() is not None:
                raise ProxyError("Open this proxy's browser first.")
        subprocess.Popen(
            [str(self.chrome), f"--user-data-dir={session.profile_path}", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.event_callback("tab", spec.key, "New tab opened")

    def close(self, key: str) -> None:
        with self._lock:
            session = self._sessions.get(key)
        if session and session.process.poll() is None:
            session.process.terminate()

    def close_all(self) -> None:
        with self._lock:
            keys = list(self._sessions)
        for key in keys:
            self.close(key)

    def shutdown(self, timeout: float = 5.0) -> None:
        """Close managed Chrome processes and allow watcher threads to persist stats."""
        self.close_all()
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            with self._lock:
                sessions = list(self._sessions.values())
            if not sessions:
                return
            for session in sessions:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                try:
                    session.process.wait(timeout=min(0.15, remaining))
                except subprocess.TimeoutExpired:
                    pass
            time.sleep(0.03)

        # A stuck Chrome must not retain a dead localhost proxy configuration.
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if session.process.poll() is None:
                session.process.kill()
        final_deadline = time.monotonic() + 1.0
        while time.monotonic() < final_deadline:
            with self._lock:
                if not self._sessions:
                    return
            time.sleep(0.03)

    def active_keys(self) -> set[str]:
        with self._lock:
            return {
                key
                for key, session in self._sessions.items()
                if session.process.poll() is None
            }

    def activity(self, key: str) -> dict | None:
        with self._lock:
            session = self._sessions.get(key)
            if not session or session.process.poll() is not None:
                return None
            up, down = session.bridge.counters()
            now = time.monotonic()
            elapsed = max(0.001, now - session.last_sample_at)
            total = up + down
            session.current_rate = max(0.0, total - session.last_sample_bytes) / elapsed
            session.last_sample_at = now
            session.last_sample_bytes = total
            return {
                "bytes_up": up,
                "bytes_down": down,
                "rate": session.current_rate,
                "seconds": max(0.0, now - session.started_at),
            }

    def activities(self) -> dict[str, dict]:
        return {
            key: activity
            for key in self.active_keys()
            if (activity := self.activity(key)) is not None
        }

    def _watch_session(self, session: BrowserSession) -> None:
        try:
            session.process.wait()
        finally:
            up, down = session.bridge.counters()
            session.bridge.close()
            elapsed = max(0.0, time.monotonic() - session.started_at)
            self.store.mark_closed(session.spec, elapsed, up, down)
            with self._lock:
                if self._sessions.get(session.spec.key) is session:
                    del self._sessions[session.spec.key]
            self.event_callback("closed", session.spec.key, "Chrome closed")


def check_proxy(spec: ProxySpec, timeout: float = 15.0) -> tuple[dict, int, int]:
    started = time.monotonic()
    bridge: AuthenticatedProxyBridge | None = None
    try:
        bridge = AuthenticatedProxyBridge(spec).start()
        opener = build_opener(
            ProxyHandler({"http": bridge.url, "https": bridge.url})
        )
        request = Request(
            IP_DETAILS_URL,
            headers={
                "User-Agent": "ProxyDeck/1.0 (+desktop proxy manager)",
                "Accept": "application/json",
            },
        )
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        latency = round((time.monotonic() - started) * 1000)
        if not payload.get("success", True):
            raise ProxyError(payload.get("message") or "IP lookup was unsuccessful.")
        connection = payload.get("connection") or {}
        timezone_data = payload.get("timezone") or {}
        result = {
            "health": "Healthy",
            "latency_ms": latency,
            "exit_ip": str(payload.get("ip") or ""),
            "country": str(payload.get("country") or ""),
            "country_code": str(payload.get("country_code") or ""),
            "region": str(payload.get("region") or ""),
            "city": str(payload.get("city") or ""),
            "isp": str(connection.get("isp") or connection.get("org") or ""),
            "timezone": str(timezone_data.get("id") or ""),
            "error": "",
        }
    except Exception as error:
        result = {
            "health": "Error",
            "latency_ms": None,
            "error": f"{type(error).__name__}: {error}",
        }
    finally:
        if bridge is None:
            return result, 0, 0
        up, down = bridge.counters()
        bridge.close()
    return result, up, down


def format_bytes(value: int | float) -> str:
    amount = float(max(0, value))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{amount:.0f} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return "0 B"


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"
