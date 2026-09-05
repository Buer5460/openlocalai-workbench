import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .auth import (
    hash_password,
    new_csrf_token,
    new_session_token,
    token_digest,
    validate_username,
    verify_password,
)
from .ollama import OllamaClient, OllamaError
from .retrieval import chunk_text, rank


SESSION_SECONDS = 12 * 60 * 60


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path, model_client: OllamaClient | None = None):
        self.path = path
        self.model_client = model_client
        parent_created = not path.parent.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix" and parent_created:
            try:
                path.parent.chmod(0o700)
            except OSError:
                pass
        self.connection = sqlite3.connect(path, check_same_thread=False)
        if os.name == "posix":
            try:
                path.chmod(0o600)
            except OSError:
                pass
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.lock = threading.RLock()
        self._dummy_salt, self._dummy_hash = hash_password("OpenLocalAI-dummy-password")
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, content TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
              id TEXT PRIMARY KEY, document_id TEXT NOT NULL, position INTEGER NOT NULL,
              content TEXT NOT NULL, FOREIGN KEY(document_id) REFERENCES documents(id)
            );
            CREATE TABLE IF NOT EXISTS reports (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, question TEXT NOT NULL,
              content TEXT NOT NULL, status TEXT NOT NULL, reviewer TEXT,
              created_at TEXT NOT NULL, approved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audits (
              id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
              detail TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
              password_salt BLOB NOT NULL, password_hash BLOB NOT NULL,
              role TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
              csrf_token TEXT NOT NULL, created_at TEXT NOT NULL,
              expires_at INTEGER NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def audit(self, action: str, detail: str) -> None:
        self.connection.execute(
            "INSERT INTO audits(action, detail, created_at) VALUES (?, ?, ?)",
            (action, detail, now()),
        )

    def audit_event(self, action: str, detail: str) -> None:
        """Write an audit event safely from HTTP and background threads."""
        with self.lock:
            self.audit(action[:100], detail[:1000])
            self.connection.commit()

    def admin_configured(self) -> bool:
        with self.lock:
            row = self.connection.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return row is not None

    def bootstrap_admin(self, username: str, password: str, remote: str = "local") -> dict:
        normalized = validate_username(username)
        with self.lock:
            if self.connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise ValueError("管理员已经初始化，不能重复创建")
            salt, digest = hash_password(password)
            user_id = uuid4().hex
            created = now()
            self.connection.execute(
                "INSERT INTO users(id, username, password_salt, password_hash, role, created_at) VALUES (?, ?, ?, ?, 'admin', ?)",
                (user_id, normalized, salt, digest, created),
            )
            self.audit("auth.bootstrap", f"管理员：{normalized}；来源：{remote[:120]}")
            self.connection.commit()
        return {"id": user_id, "username": normalized, "role": "admin", "created_at": created}

    def authenticate(self, username: str, password: str, remote: str = "local") -> dict | None:
        normalized = username.strip()
        with self.lock:
            row = self.connection.execute(
                "SELECT id, username, password_salt, password_hash, role FROM users WHERE username = ? COLLATE NOCASE",
                (normalized,),
            ).fetchone()
        salt = row["password_salt"] if row is not None else self._dummy_salt
        expected = row["password_hash"] if row is not None else self._dummy_hash
        password_matches = verify_password(password, salt, expected)
        valid = row is not None and password_matches
        if not valid:
            self.audit_event("auth.login.failed", f"账号：{normalized[:64]}；来源：{remote[:120]}")
            return None
        user = {"id": row["id"], "username": row["username"], "role": row["role"]}
        self.audit_event("auth.login", f"管理员：{row['username']}；来源：{remote[:120]}")
        return user

    def create_session(self, user_id: str) -> tuple[str, dict]:
        token = new_session_token()
        csrf = new_csrf_token()
        created = now()
        expires = int(time.time()) + SESSION_SECONDS
        with self.lock:
            self.connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
            self.connection.execute(
                "INSERT INTO sessions(token_hash, user_id, csrf_token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (token_digest(token), user_id, csrf, created, expires),
            )
            self.connection.commit()
        session = self.get_session(token)
        if session is None:  # defensive; the freshly inserted session cannot normally disappear
            raise RuntimeError("无法创建管理员会话")
        return token, session

    def get_session(self, token: str) -> dict | None:
        if not token or len(token) > 256:
            return None
        with self.lock:
            row = self.connection.execute(
                """SELECT users.id AS user_id, users.username, users.role,
                          sessions.csrf_token, sessions.expires_at
                   FROM sessions JOIN users ON users.id = sessions.user_id
                   WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
                (token_digest(token), int(time.time())),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token: str, actor: str = "unknown") -> None:
        if not token:
            return
        with self.lock:
            self.connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),))
            self.audit("auth.logout", f"管理员：{actor[:64]}")
            self.connection.commit()

    def get_setting(self, key: str) -> str | None:
        with self.lock:
            row = self.connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.lock:
            self.connection.execute(
                """INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key[:100], value[:500], now()),
            )
            self.connection.commit()

    def add_document(self, name: str, content: str) -> dict:
        name, content = name.strip(), content.strip()
        if not name or not content:
            raise ValueError("材料名称和正文不能为空")
        if len(content) > 2_000_000:
            raise ValueError("Alpha 单份材料上限为 2,000,000 字符")
        doc_id = uuid4().hex
        pieces = chunk_text(content)
        with self.lock:
            self.connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?)", (doc_id, name[:200], content, now())
            )
            for position, piece in enumerate(pieces):
                self.connection.execute(
                    "INSERT INTO chunks VALUES (?, ?, ?, ?)",
                    (uuid4().hex, doc_id, position, piece),
                )
            self.audit("document.import", f"导入材料：{name[:200]}；分段：{len(pieces)}")
            self.connection.commit()
        return {"id": doc_id, "name": name[:200], "chunks": len(pieces)}

    def list_documents(self) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT id, name, created_at, length(content) AS characters FROM documents ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def search(self, question: str, limit: int = 5) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                """SELECT chunks.id, chunks.content, chunks.position,
                          documents.id AS document_id, documents.name AS document_name
                   FROM chunks JOIN documents ON documents.id = chunks.document_id"""
            ).fetchall()
        return rank(question, [dict(row) for row in rows], limit)

    def answer(self, question: str) -> dict:
        question = question.strip()
        if not question:
            raise ValueError("问题不能为空")
        if len(question) > 4_000:
            raise ValueError("问题不能超过 4,000 个字符")
        citations = self.search(question)
        mode = "extractive"
        model_name = None
        warning = None
        if not citations:
            answer = "现有材料中没有找到足够证据。请补充材料或换一种问法；系统不会在无证据时推断结论。"
        else:
            lines = ["根据当前材料，可核实的信息如下："]
            for index, item in enumerate(citations[:3], 1):
                excerpt = item["content"].replace("\n", " ").strip()
                lines.append(f"{index}. {excerpt} [{index}]")
            answer = "\n\n".join(lines)
            if self.model_client is not None:
                model_name = self.model_client.model
                state = self.model_client.status()
                if state["available"] and state["installed"]:
                    try:
                        answer = self.model_client.answer(question, citations)
                        mode = "ollama"
                    except OllamaError as error:
                        warning = f"本地模型回答未通过校验，已使用证据摘录：{error}"
                elif not state["available"]:
                    warning = "未连接到 Ollama，已使用证据摘录回答"
                else:
                    warning = f"本地模型 {model_name} 尚未安装，已使用证据摘录回答"
        with self.lock:
            self.audit("question.answer", f"问题：{question[:240]}；证据：{len(citations)} 条；模式：{mode}")
            self.connection.commit()
        return {
            "question": question,
            "answer": answer,
            "citations": citations,
            "mode": mode,
            "model": model_name,
            "warning": warning,
        }

    def create_report(self, title: str, question: str) -> dict:
        result = self.answer(question)
        report_id = uuid4().hex
        sources = "\n".join(
            f"[{index}] {item['document_name']}，第 {item['position'] + 1} 段"
            for index, item in enumerate(result["citations"][:5], 1)
        ) or "无可用来源"
        content = (
            f"# {title.strip()}\n\n"
            f"> 状态：待人工审核；由本地材料自动形成，不代表最终决定。\n\n"
            f"## 任务\n\n{question.strip()}\n\n"
            f"## 初步结论\n\n{result['answer']}\n\n"
            f"## 来源\n\n{sources}\n\n"
            "## 人工复核要点\n\n- 核对原文和适用范围\n- 确认是否存在更新材料\n- 由有权限人员作出最终决定\n"
        )
        with self.lock:
            self.connection.execute(
                "INSERT INTO reports VALUES (?, ?, ?, ?, 'pending', NULL, ?, NULL)",
                (report_id, title.strip()[:200], question.strip(), content, now()),
            )
            self.audit("report.create", f"生成待审报告：{title.strip()[:200]}")
            self.connection.commit()
        return self.get_report(report_id)

    def get_report(self, report_id: str) -> dict:
        with self.lock:
            row = self.connection.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            raise KeyError("报告不存在")
        return dict(row)

    def approve_report(self, report_id: str, reviewer: str) -> dict:
        reviewer = reviewer.strip()
        if not reviewer:
            raise ValueError("审批人不能为空")
        approved_at = now()
        with self.lock:
            cursor = self.connection.execute(
                "UPDATE reports SET status='approved', reviewer=?, approved_at=? WHERE id=? AND status='pending'",
                (reviewer[:100], approved_at, report_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("报告不存在或已经审批")
            self.audit("report.approve", f"报告：{report_id}；审批人：{reviewer[:100]}")
            self.connection.commit()
        return self.get_report(report_id)

    def list_audits(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM audits ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self.lock:
            self.connection.close()
