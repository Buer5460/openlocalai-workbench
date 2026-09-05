from __future__ import annotations

import hmac
import json
import mimetypes
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .diagnostics import collect_diagnostics
from .ollama import (
    ALLOWED_MODELS,
    GENERATION_MODELS,
    MODEL_PROFILES,
    ModelPullManager,
    OllamaClient,
    PullInProgressError,
)
from .store import SESSION_SECONDS, Store


# Accommodates two million UTF-8 CJK characters plus JSON escaping overhead.
MAX_BODY = 8_500_000
SESSION_COOKIE = "openlocalai_session"


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        store: Store,
        web_root: Path,
        model_client: OllamaClient | None = None,
    ):
        super().__init__(address, Handler)
        self.store = store
        self.web_root = web_root
        self.model_client = model_client or store.model_client or OllamaClient()
        saved_model = store.get_setting("active_model")
        if saved_model in GENERATION_MODELS:
            self.model_client.set_model(saved_model)
        if self.store.model_client is None:
            self.store.model_client = self.model_client
        self.model_pulls = ModelPullManager(
            self.model_client, self.store.audit_event, self.activate_pulled_model
        )
        self.login_attempts: dict[tuple[str, str], list[float]] = {}
        self.login_attempts_lock = threading.Lock()

    def activate_model(self, model: str, actor: str) -> None:
        if model not in GENERATION_MODELS:
            raise ValueError("向量模型不能用于生成回答")
        installed = self.model_client.list_models(timeout=3.0)
        if model not in installed:
            raise ValueError("模型尚未安装完成，不能设为当前生成模型")
        self.model_client.set_model(model)
        self.store.set_setting("active_model", model)
        self.store.audit_event("model.select", f"操作者：{actor[:64]}；模型：{model}")

    def activate_pulled_model(self, model: str) -> None:
        if model not in GENERATION_MODELS:
            return
        self.model_client.set_model(model)
        self.store.set_setting("active_model", model)
        self.store.audit_event("model.select", f"操作者：model-pull；模型：{model}")

    def login_retry_after(self, remote: str, username: str) -> int:
        key = (remote[:120], username.strip().lower()[:64])
        current = time.monotonic()
        with self.login_attempts_lock:
            attempts = [stamp for stamp in self.login_attempts.get(key, []) if current - stamp < 300]
            if attempts:
                self.login_attempts[key] = attempts
            else:
                self.login_attempts.pop(key, None)
            return max(0, int(300 - (current - attempts[0]))) if len(attempts) >= 5 else 0

    def record_login_failure(self, remote: str, username: str) -> None:
        key = (remote[:120], username.strip().lower()[:64])
        current = time.monotonic()
        with self.login_attempts_lock:
            attempts = [stamp for stamp in self.login_attempts.get(key, []) if current - stamp < 300]
            attempts.append(current)
            self.login_attempts[key] = attempts[-5:]

    def clear_login_failures(self, remote: str, username: str) -> None:
        key = (remote[:120], username.strip().lower()[:64])
        with self.login_attempts_lock:
            self.login_attempts.pop(key, None)

    def status(self) -> dict:
        full_model = self.model_client.status()
        model = {
            "available": full_model["available"],
            "model": full_model["model"],
            "installed": full_model["installed"],
            "error": full_model["error"],
        }
        configured = self.store.admin_configured()
        if not configured:
            state = "setup_required"
        elif not model["available"] or not model["installed"]:
            state = "degraded"
        else:
            state = "ready"
        return {
            "status": state,
            "version": __version__,
            "offline": True,
            "telemetry": False,
            "auth": {"configured": configured, "setup_required": not configured},
            "model": model,
            "fallback": {"available": True, "mode": "extractive"},
            "model_pull": {"active": self.model_pulls.active(), "auto_pull": self.model_client.auto_pull},
        }


class Handler(BaseHTTPRequestHandler):
    server: AppServer

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def json_response(
        self, status: int, payload: object, extra_headers: list[tuple[str, str]] | None = None
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("Content-Type 必须为 application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Content-Length 无效") from error
        if length <= 0 or length > MAX_BODY:
            raise ValueError("请求正文为空或超过 8.5 MB")
        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("请求必须是有效 UTF-8 JSON") from error
        if not isinstance(data, dict):
            raise ValueError("请求正文必须是 JSON 对象")
        return data

    def remote_label(self) -> str:
        return self.client_address[0] if self.client_address else "unknown"

    def origin_is_valid(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        host = self.headers.get("Host", "").strip().lower()
        return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host and not parsed.username

    def cookie_token(self) -> str:
        header = self.headers.get("Cookie", "")
        if not header:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(header)
        except CookieError:
            return ""
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def session_payload(self, session: dict) -> dict:
        return {
            "authenticated": True,
            "user": {"id": session["user_id"], "username": session["username"], "role": session["role"]},
            "csrf_token": session["csrf_token"],
            "expires_at": session["expires_at"],
        }

    def require_auth(self, mutation: bool = False) -> dict | None:
        if not self.server.store.admin_configured():
            self.json_response(
                428,
                {"error": "请先创建本机管理员账号", "code": "admin_setup_required"},
            )
            return None
        session = self.server.store.get_session(self.cookie_token())
        if session is None:
            self.json_response(401, {"error": "请先登录", "code": "auth_required"})
            return None
        if mutation:
            supplied = self.headers.get("X-CSRF-Token", "")
            if not supplied or not hmac.compare_digest(supplied, session["csrf_token"]):
                self.json_response(403, {"error": "请求校验失败，请刷新页面后重试", "code": "csrf_failed"})
                return None
        return session

    def session_cookie_header(self, token: str) -> tuple[str, str]:
        secure = "; Secure" if self.headers.get("Origin", "").lower().startswith("https://") else ""
        return (
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict{secure}",
        )

    def clear_cookie_header(self) -> tuple[str, str]:
        secure = "; Secure" if self.headers.get("Origin", "").lower().startswith("https://") else ""
        return (
            "Set-Cookie",
            f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict{secure}",
        )

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.json_response(200, {"status": "ok", "version": __version__, "offline": True})
        if path == "/api/status":
            return self.json_response(200, self.server.status())
        if path == "/api/auth/status":
            configured = self.server.store.admin_configured()
            return self.json_response(200, {"configured": configured, "setup_required": not configured})
        if path.startswith("/api/"):
            session = self.require_auth()
            if session is None:
                return None
            if path == "/api/auth/session":
                return self.json_response(200, self.session_payload(session))
            if path == "/api/documents":
                return self.json_response(200, {"items": self.server.store.list_documents()})
            if path == "/api/audits":
                return self.json_response(200, {"items": self.server.store.list_audits()})
            if path == "/api/doctor":
                result = collect_diagnostics(self.server.store.path, self.server.model_client)
                self.server.store.audit_event("system.doctor", f"操作者：{session['username']}")
                return self.json_response(200, result)
            if path == "/api/models":
                state = self.server.model_client.status(refresh=True)
                profiles = [dict({"id": name}, **MODEL_PROFILES[name]) for name in sorted(ALLOWED_MODELS)]
                return self.json_response(
                    200,
                    {
                        "configured": self.server.model_client.model,
                        "active_model": self.server.model_client.model,
                        "profiles": profiles,
                        "ollama": state,
                        "active_pull": self.server.model_pulls.active(),
                    },
                )
            if path.startswith("/api/models/pull/"):
                try:
                    return self.json_response(200, self.server.model_pulls.get(path.rsplit("/", 1)[-1]))
                except KeyError as error:
                    return self.json_response(404, {"error": str(error), "code": "pull_job_not_found"})
            if path.startswith("/api/reports/"):
                try:
                    return self.json_response(200, self.server.store.get_report(path.rsplit("/", 1)[-1]))
                except KeyError as error:
                    return self.json_response(404, {"error": str(error)})
            return self.json_response(404, {"error": "接口不存在"})
        return self.serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self.origin_is_valid():
            return self.json_response(403, {"error": "拒绝跨站请求", "code": "origin_rejected"})
        try:
            if path == "/api/auth/bootstrap":
                data = self.read_json()
                user = self.server.store.bootstrap_admin(
                    str(data.get("username", "")), str(data.get("password", "")), self.remote_label()
                )
                token, session = self.server.store.create_session(user["id"])
                return self.json_response(
                    201, self.session_payload(session), [self.session_cookie_header(token)]
                )
            if path == "/api/auth/login":
                if not self.server.store.admin_configured():
                    return self.json_response(
                        428, {"error": "请先创建本机管理员账号", "code": "admin_setup_required"}
                    )
                data = self.read_json()
                username = str(data.get("username", ""))
                retry_after = self.server.login_retry_after(self.remote_label(), username)
                if retry_after:
                    return self.json_response(
                        429,
                        {"error": "登录失败次数过多，请稍后重试", "code": "login_rate_limited"},
                        [("Retry-After", str(retry_after))],
                    )
                user = self.server.store.authenticate(
                    username, str(data.get("password", "")), self.remote_label()
                )
                if user is None:
                    self.server.record_login_failure(self.remote_label(), username)
                    return self.json_response(401, {"error": "账号或密码错误", "code": "invalid_credentials"})
                self.server.clear_login_failures(self.remote_label(), username)
                token, session = self.server.store.create_session(user["id"])
                return self.json_response(200, self.session_payload(session), [self.session_cookie_header(token)])

            if not path.startswith("/api/"):
                return self.json_response(404, {"error": "接口不存在"})
            session = self.require_auth(mutation=True)
            if session is None:
                return None
            if path == "/api/auth/logout":
                self.server.store.delete_session(self.cookie_token(), session["username"])
                return self.json_response(
                    200, {"authenticated": False}, [self.clear_cookie_header()]
                )

            data = self.read_json()
            if path == "/api/documents":
                result = self.server.store.add_document(str(data.get("name", "")), str(data.get("content", "")))
                return self.json_response(201, result)
            if path == "/api/answer":
                return self.json_response(200, self.server.store.answer(str(data.get("question", ""))))
            if path == "/api/reports":
                result = self.server.store.create_report(
                    str(data.get("title", "工作简报")), str(data.get("question", ""))
                )
                return self.json_response(201, result)
            if path.startswith("/api/reports/") and path.endswith("/approve"):
                report_id = path.split("/")[-2]
                return self.json_response(
                    200, self.server.store.approve_report(report_id, session["username"])
                )
            if path == "/api/models/pull":
                job = self.server.model_pulls.start(str(data.get("model", "")), session["username"])
                return self.json_response(202, job)
            if path == "/api/models/select":
                model = str(data.get("model", "")).strip()
                self.server.activate_model(model, session["username"])
                return self.json_response(200, {"active_model": model})
            return self.json_response(404, {"error": "接口不存在"})
        except PullInProgressError as error:
            return self.json_response(409, {"error": str(error), "code": "pull_in_progress"})
        except ValueError as error:
            return self.json_response(400, {"error": str(error), "code": "invalid_request"})
        except KeyError as error:
            return self.json_response(404, {"error": str(error)})
        except Exception as error:
            self.log_error("Unhandled API error: %s", type(error).__name__)
            return self.json_response(500, {"error": f"内部错误：{type(error).__name__}"})

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        candidate = (self.server.web_root / relative).resolve()
        root = self.server.web_root.resolve()
        if root not in candidate.parents and candidate != root:
            return self.json_response(403, {"error": "禁止访问"})
        if not candidate.is_file():
            candidate = self.server.web_root / "index.html"
        if not candidate.is_file():
            return self.json_response(404, {"error": "页面资源不存在"})
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(body)


def run(host: str, port: int, data_path: Path, open_browser: bool = False) -> None:
    web_root = Path(__file__).parent / "web"
    model_client = OllamaClient()
    store = Store(data_path, model_client=model_client)
    sample = Path(__file__).parent / "sample.md"
    if not store.list_documents() and sample.exists():
        store.add_document(sample.name, sample.read_text(encoding="utf-8"))
    server = AppServer((host, port), store, web_root, model_client)
    actual_host, actual_port = server.server_address[:2]
    browser_host = "127.0.0.1" if actual_host in {"0.0.0.0", "::"} else actual_host
    url = f"http://{browser_host}:{actual_port}"
    print(f"OpenLocalAI Workbench v{__version__}: {url}")
    print("首次使用请创建本机管理员。未连接模型时自动使用可追溯的证据摘录。按 Ctrl+C 停止。")
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    if model_client.auto_pull:
        try:
            server.model_pulls.maybe_auto_pull()
        except ValueError as error:
            store.audit_event("model.pull.auto_skip", str(error))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
