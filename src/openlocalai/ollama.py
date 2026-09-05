"""Small, defensive Ollama adapter implemented with the Python standard library."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.client import HTTPResponse
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:4b-q4_K_M"
OFFLINE_MODEL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "ollama"})
MAX_RESPONSE_BYTES = 2_000_000
MAX_PULL_METADATA_BYTES = 16_000_000
MAX_PULL_MODEL_BYTES = 40 * 1024**3
MODEL_PULL_TIMEOUT_SECONDS = 2 * 60 * 60
MODEL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,79}:[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

# Deliberately narrow: the browser cannot ask Ollama to fetch an arbitrary model.
MODEL_PROFILES: dict[str, dict] = {
    "qwen3.5:4b-q4_K_M": {
        "kind": "generation",
        "memory_gb": 8,
        "size_gb": 3.4,
        "label": "通用轻量版（8GB 内存）",
        "description": "适合普通办公电脑的问答与简报生成，磁盘大小为近似值。",
        "license": "Apache-2.0",
    },
    "qwen3.5:9b-q4_K_M": {
        "kind": "generation",
        "memory_gb": 16,
        "size_gb": 6.6,
        "label": "通用标准版（16GB 内存）",
        "description": "适合内存较充足的办公电脑，兼顾中文质量和本地推理资源，磁盘大小为近似值。",
        "license": "Apache-2.0",
    },
    "qwen3.5:27b-q4_K_M": {
        "kind": "generation",
        "memory_gb": 32,
        "size_gb": 17.0,
        "label": "通用增强版（32GB+ 内存）",
        "description": "面向高内存工作站；部署前应执行硬件检查，磁盘大小为近似值。",
        "license": "Apache-2.0",
    },
    "qwen3-embedding:0.6b": {
        "kind": "embedding",
        "memory_gb": 4,
        "size_gb": 0.7,
        "label": "中文向量模型",
        "description": "用于后续语义检索扩展，不用于生成回答，磁盘大小为近似值。",
        "license": "Apache-2.0",
    },
    # Compatibility with the initial container profile and existing installations.
    "qwen3:4b": {
        "kind": "generation",
        "memory_gb": 8,
        "size_gb": 2.6,
        "label": "兼容模型（Qwen3 4B）",
        "description": "兼容早期容器配置，磁盘大小为近似值。",
        "license": "Apache-2.0",
    },
}
ALLOWED_MODELS = frozenset(MODEL_PROFILES)
GENERATION_MODELS = frozenset(name for name, profile in MODEL_PROFILES.items() if profile["kind"] == "generation")


class OllamaError(RuntimeError):
    """A user-safe local model error."""


class PullInProgressError(ValueError):
    """Only one model download may mutate the Ollama store at a time."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


class OllamaClient:
    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OLLAMA_BASE_URL 必须是有效的 http(s) 地址")
        try:
            parsed.port
        except ValueError as error:
            raise ValueError("OLLAMA_BASE_URL 端口无效") from error
        if parsed.hostname.lower() not in OFFLINE_MODEL_HOSTS:
            raise ValueError("OLLAMA_BASE_URL 只能指向本机或随平台部署的 ollama 服务")
        self.model = (model or os.environ.get("OPENLOCALAI_MODEL") or DEFAULT_MODEL).strip()
        if not MODEL_ID_PATTERN.fullmatch(self.model):
            raise ValueError("OPENLOCALAI_MODEL 格式无效，必须包含固定版本标签")
        self.auto_pull = _bool_env("OPENLOCALAI_MODEL_AUTO_PULL", False)
        self.answer_timeout = _bounded_int_env("OPENLOCALAI_MODEL_TIMEOUT", 90, 10, 600)
        # Never send internal documents through HTTP_PROXY/HTTPS_PROXY, and never
        # follow a model server redirect to another host.
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())
        self._status_cache: tuple[float, dict] | None = None
        self._status_lock = threading.Lock()

    def set_model(self, model: str) -> None:
        if model not in GENERATION_MODELS:
            raise ValueError("只能选择平台清单中的生成模型")
        with self._status_lock:
            self.model = model
            self._status_cache = None

    def _open(self, path: str, payload: dict | None, timeout: float) -> HTTPResponse:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method="GET" if payload is None else "POST",
            headers={"Accept": "application/json", "User-Agent": "OpenLocalAI/0.2"},
        )
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            return self._opener.open(request, timeout=timeout)
        except HTTPError as error:
            detail = ""
            try:
                parsed = json.loads(error.read(4096).decode("utf-8", errors="replace"))
                detail = str(parsed.get("error", "")) if isinstance(parsed, dict) else ""
            except (json.JSONDecodeError, OSError):
                pass
            suffix = f"：{detail[:300]}" if detail else ""
            raise OllamaError(f"Ollama 返回 HTTP {error.code}{suffix}") from error
        except (TimeoutError, URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, TimeoutError):
                raise OllamaError("连接本地模型超时") from error
            raise OllamaError("无法连接本地 Ollama 服务，请确认服务已启动") from error

    def _json(self, path: str, payload: dict | None = None, timeout: float = 3.0) -> dict:
        with self._open(path, payload, timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise OllamaError("本地模型响应超过安全上限")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise OllamaError("本地模型返回了无效 JSON") from error
        if not isinstance(result, dict):
            raise OllamaError("本地模型返回格式不正确")
        return result

    def list_models(self, timeout: float = 2.0) -> list[str]:
        payload = self._json("/api/tags", timeout=timeout)
        result: list[str] = []
        for item in payload.get("models", []):
            if isinstance(item, dict):
                name = item.get("name") or item.get("model")
                if isinstance(name, str):
                    result.append(name)
        return sorted(set(result))

    def status(self, refresh: bool = False) -> dict:
        with self._status_lock:
            if not refresh and self._status_cache and time.monotonic() - self._status_cache[0] < 5:
                return dict(self._status_cache[1])
            try:
                version = self._json("/api/version", timeout=1.5).get("version", "unknown")
                models = self.list_models(timeout=2.0)
                result = {
                    "available": True,
                    "base_url": self.base_url,
                    "version": str(version),
                    "model": self.model,
                    "installed": self.model in models,
                    "installed_models": models,
                    "error": None,
                }
            except OllamaError as error:
                result = {
                    "available": False,
                    "base_url": self.base_url,
                    "version": None,
                    "model": self.model,
                    "installed": False,
                    "installed_models": [],
                    "error": str(error),
                }
            self._status_cache = (time.monotonic(), result)
            return dict(result)

    def answer(self, question: str, citations: list[dict]) -> str:
        source_blocks = []
        for index, item in enumerate(citations[:5], 1):
            content = str(item.get("content", ""))[:4000]
            source_blocks.append(
                f"[{index}] 文件：{item.get('document_name', '未知')}；段落：{int(item.get('position', 0)) + 1}\n{content}"
            )
        evidence = "\n\n".join(source_blocks)[:16_000]
        system = (
            "你是政府和国企内网中的证据问答助手。只能依据用户提供的证据回答，不得使用外部知识或猜测。"
            "每个事实结论后必须标注对应的 [数字] 引用。证据不足时明确说‘现有材料证据不足’，并说明缺少什么。"
            "不要伪造文件、条款、数字、日期或引用。回答使用简洁中文。"
        )
        user = f"问题：{question[:4000]}\n\n可用证据：\n{evidence}"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 768},
        }
        result = self._json("/api/chat", payload, timeout=float(self.answer_timeout))
        message = result.get("message")
        content = message.get("content") if isinstance(message, dict) else result.get("response")
        if not isinstance(content, str) or not content.strip():
            raise OllamaError("本地模型没有返回正文")
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE).strip()
        referenced = {int(value) for value in re.findall(r"\[(\d+)]", cleaned)}
        if not referenced or any(value < 1 or value > len(citations[:5]) for value in referenced):
            raise OllamaError("本地模型未生成可验证引用，已拒绝采用该回答")
        return cleaned[:20_000]

    def pull_events(self, model: str) -> Iterator[dict]:
        if model not in ALLOWED_MODELS:
            raise ValueError("不允许下载该模型，请从平台提供的固定模型清单中选择")
        deadline = time.monotonic() + MODEL_PULL_TIMEOUT_SECONDS
        total_read = 0
        try:
            with self._open("/api/pull", {"model": model, "stream": True}, timeout=120.0) as response:
                while True:
                    if time.monotonic() > deadline:
                        raise OllamaError("模型下载超过两小时安全时限，任务已停止")
                    line = response.readline(65_537)
                    if not line:
                        break
                    total_read += len(line)
                    if len(line) > 65_536 or total_read > MAX_PULL_METADATA_BYTES:
                        raise OllamaError("Ollama 下载状态响应超过安全上限")
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as error:
                        raise OllamaError("Ollama 返回了无效下载状态") from error
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        raise OllamaError(f"模型下载失败：{str(event['error'])[:300]}")
                    total = event.get("total")
                    if isinstance(total, int) and total > MAX_PULL_MODEL_BYTES:
                        raise OllamaError("模型体积超过 40 GiB 安全上限")
                    yield event
        except (TimeoutError, OSError) as error:
            raise OllamaError("模型下载连接中断或长时间没有进度") from error


@dataclass
class PullJob:
    id: str
    model: str
    status: str
    progress: int
    detail: str
    error: str | None
    created_at: str
    updated_at: str


class ModelPullManager:
    def __init__(
        self,
        client: OllamaClient,
        audit: Callable[[str, str], None],
        on_success: Callable[[str], None] | None = None,
    ):
        self.client = client
        self.audit = audit
        self.on_success = on_success
        self.lock = threading.Lock()
        self.jobs: dict[str, PullJob] = {}
        self.active_job_id: str | None = None

    def start(self, model: str, actor: str = "system") -> dict:
        model = model.strip()
        if model not in ALLOWED_MODELS:
            raise ValueError("不允许下载该模型，请从平台提供的固定模型清单中选择")
        with self.lock:
            if self.active_job_id:
                active = self.jobs.get(self.active_job_id)
                if active and active.status in {"queued", "downloading"}:
                    raise PullInProgressError(f"已有模型下载任务正在运行：{active.model}")
            stamp = _now()
            job = PullJob(uuid4().hex, model, "queued", 0, "等待 Ollama 响应", None, stamp, stamp)
            self.jobs[job.id] = job
            self.active_job_id = job.id
        self.audit("model.pull.start", f"操作者：{actor[:64]}；模型：{model}")
        threading.Thread(target=self._run, args=(job.id, actor), daemon=True).start()
        return self.get(job.id)

    def _run(self, job_id: str, actor: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.status = "downloading"
            job.detail = "正在下载和校验模型"
            job.updated_at = _now()
        try:
            saw_success = False
            for event in self.client.pull_events(job.model):
                total, completed = event.get("total"), event.get("completed")
                progress = int(completed * 100 / total) if isinstance(total, int) and total > 0 and isinstance(completed, int) else None
                status = str(event.get("status", "正在下载"))[:200]
                if status.lower() == "success":
                    saw_success = True
                with self.lock:
                    current = self.jobs[job_id]
                    if progress is not None:
                        current.progress = max(current.progress, min(99, progress))
                    current.detail = status
                    current.updated_at = _now()
            if not saw_success:
                raise OllamaError("Ollama 连接已结束，但没有确认模型安装成功")
            if self.on_success is not None:
                self.on_success(job.model)
            with self.lock:
                current = self.jobs[job_id]
                current.status = "succeeded"
                current.progress = 100
                current.detail = "模型安装完成"
                current.updated_at = _now()
            self.client.status(refresh=True)
            self.audit("model.pull.complete", f"操作者：{actor[:64]}；模型：{job.model}")
        except (OllamaError, ValueError, OSError) as error:
            with self.lock:
                current = self.jobs[job_id]
                current.status = "failed"
                current.error = str(error)[:500]
                current.detail = "模型安装失败"
                current.updated_at = _now()
            self.audit("model.pull.fail", f"操作者：{actor[:64]}；模型：{job.model}；原因：{str(error)[:240]}")
        finally:
            with self.lock:
                if self.active_job_id == job_id:
                    self.active_job_id = None

    def get(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError("模型下载任务不存在或服务已经重启")
            return asdict(job)

    def active(self) -> dict | None:
        with self.lock:
            job = self.jobs.get(self.active_job_id or "")
            return asdict(job) if job else None

    def maybe_auto_pull(self) -> dict | None:
        if not self.client.auto_pull or self.client.model not in ALLOWED_MODELS:
            return None
        state = self.client.status(refresh=True)
        if state["available"] and state["installed"]:
            return None
        return self.start(self.client.model, actor="startup")
