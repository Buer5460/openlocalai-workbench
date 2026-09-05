import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from openlocalai.server import AppServer
from openlocalai.store import Store


class FakeOllama:
    model = "qwen3.5:4b-q4_K_M"
    auto_pull = False

    def status(self, refresh=False):
        return {
            "available": False,
            "base_url": "http://127.0.0.1:11434",
            "version": None,
            "model": self.model,
            "installed": False,
            "installed_models": [],
            "error": "测试环境未启动 Ollama",
        }

    def answer(self, question, citations):
        raise AssertionError("model answer should not be called while unavailable")

    def list_models(self, timeout=2.0):
        return ["qwen3.5:4b-q4_K_M", "qwen3.5:9b-q4_K_M", "qwen3-embedding:0.6b"]

    def set_model(self, model):
        self.model = model

    def pull_events(self, model):
        yield {"status": "success", "total": 1, "completed": 1}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.db", model_client=FakeOllama())
        self.server = AppServer(("127.0.0.1", 0), self.store, Path(self.temp.name), self.store.model_client)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address
        self.cookie = ""
        self.csrf = ""

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, payload=None, *, csrf=True, origin=None, content_type="application/json"):
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = content_type
        if self.cookie:
            headers["Cookie"] = self.cookie
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if origin is not None:
            headers["Origin"] = origin
        connection = http.client.HTTPConnection(self.host, self.port, timeout=3)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        result = json.loads(raw) if raw else None
        set_cookie = response.getheader("Set-Cookie")
        if set_cookie:
            self.cookie = set_cookie.split(";", 1)[0]
        connection.close()
        return response.status, result, dict(response.getheaders())

    def bootstrap(self):
        status, body, _ = self.request(
            "POST", "/api/auth/bootstrap", {"username": "admin", "password": "correct horse battery staple"}
        )
        self.assertEqual(status, 201)
        self.csrf = body["csrf_token"]
        return body

    def test_bootstrap_auth_csrf_and_identity_bound_approval(self):
        status, body, _ = self.request("GET", "/api/documents")
        self.assertEqual(status, 428)
        self.assertEqual(body["code"], "admin_setup_required")

        session = self.bootstrap()
        self.assertEqual(session["user"]["username"], "admin")
        status, body, _ = self.request("POST", "/api/documents", {"name": "规则", "content": "报告发布前需要审批。"}, csrf=False)
        self.assertEqual(status, 403)
        self.assertEqual(body["code"], "csrf_failed")

        status, _, _ = self.request("POST", "/api/documents", {"name": "规则", "content": "报告发布前需要审批。"})
        self.assertEqual(status, 201)
        status, report, _ = self.request("POST", "/api/reports", {"title": "报告", "question": "报告何时审批？"})
        self.assertEqual(status, 201)
        status, approved, _ = self.request(
            "POST", f"/api/reports/{report['id']}/approve", {"reviewer": "伪造审批人"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(approved["reviewer"], "admin")

        status, body, _ = self.request(
            "POST", "/api/models/select", {"model": "qwen3-embedding:0.6b"}
        )
        self.assertEqual(status, 400)
        self.assertIn("不能用于生成", body["error"])
        status, body, _ = self.request(
            "POST", "/api/models/select", {"model": "qwen3.5:9b-q4_K_M"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["active_model"], "qwen3.5:9b-q4_K_M")
        self.assertEqual(self.store.get_setting("active_model"), "qwen3.5:9b-q4_K_M")

        status, body, _ = self.request("POST", "/api/auth/logout", None)
        self.assertEqual(status, 200)
        self.assertFalse(body["authenticated"])
        self.csrf = ""
        status, body, _ = self.request("GET", "/api/documents")
        self.assertEqual(status, 401)
        self.assertEqual(body["code"], "auth_required")

    def test_content_type_origin_and_login_rate_limit(self):
        status, body, _ = self.request(
            "POST",
            "/api/auth/bootstrap",
            {"username": "admin", "password": "correct horse battery staple"},
            content_type="text/plain",
        )
        self.assertEqual(status, 400)
        self.assertIn("Content-Type", body["error"])
        self.bootstrap()

        status, body, _ = self.request(
            "POST",
            "/api/auth/login",
            {"username": "admin", "password": "wrong-password-long"},
            origin="https://attacker.invalid",
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["code"], "origin_rejected")

        self.cookie = ""
        self.csrf = ""
        for _ in range(5):
            status, _, _ = self.request(
                "POST", "/api/auth/login", {"username": "admin", "password": "wrong-password-long"}
            )
            self.assertEqual(status, 401)
        status, body, headers = self.request(
            "POST", "/api/auth/login", {"username": "admin", "password": "correct horse battery staple"}
        )
        self.assertEqual(status, 429)
        self.assertEqual(body["code"], "login_rate_limited")
        self.assertIn("Retry-After", headers)


if __name__ == "__main__":
    unittest.main()
