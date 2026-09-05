import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openlocalai.ollama import ModelPullManager, OllamaClient, OllamaError


class PullClient:
    model = "qwen3.5:4b-q4_K_M"
    auto_pull = False

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def pull_events(self, model):
        self.started.set()
        yield {"status": "pulling manifest"}
        self.release.wait(timeout=2)
        yield {"status": "success", "total": 100, "completed": 100}

    def status(self, refresh=False):
        return {"available": True, "installed": True}


class FakeOllamaHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send(self, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            self._send({"version": "0.33.3"})
            return
        if self.path == "/api/tags":
            self._send({"models": [{"name": "qwen3.5:4b-q4_K_M"}]})
            return
        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.server.received.append((self.path, payload))
        if self.path == "/api/chat":
            self._send({"message": {"content": "负责人负责审批。[1]"}})
            return
        if self.path == "/api/pull":
            body = (
                b'{"status":"pulling manifest"}\n'
                b'{"status":"success","total":100,"completed":100}\n'
            )
            self._send(body, "application/x-ndjson")
            return
        self.send_error(404)


class OllamaTests(unittest.TestCase):
    def test_remote_model_endpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            OllamaClient(base_url="https://models.example.com", model="qwen3.5:4b-q4_K_M")

    def test_real_http_protocol_contract(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllamaHandler)
        server.received = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OllamaClient(
                base_url=f"http://127.0.0.1:{server.server_port}",
                model="qwen3.5:4b-q4_K_M",
            )
            status = client.status(refresh=True)
            self.assertTrue(status["available"])
            self.assertTrue(status["installed"])
            self.assertEqual(status["version"], "0.33.3")

            citations = [{"document_name": "制度", "position": 0, "content": "负责人负责审批"}]
            self.assertEqual(client.answer("谁审批？", citations), "负责人负责审批。[1]")
            self.assertEqual(list(client.pull_events("qwen3.5:4b-q4_K_M"))[-1]["status"], "success")

            requests = dict(server.received)
            self.assertEqual(requests["/api/chat"]["model"], "qwen3.5:4b-q4_K_M")
            self.assertFalse(requests["/api/chat"]["stream"])
            self.assertTrue(requests["/api/pull"]["stream"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_grounded_answer_rejects_missing_or_invalid_citations(self):
        client = OllamaClient(model="qwen3.5:4b-q4_K_M")
        citation = [{"document_name": "制度", "position": 0, "content": "负责人审批"}]
        client._json = lambda *args, **kwargs: {"message": {"content": "需要负责人审批。"}}
        with self.assertRaises(OllamaError):
            client.answer("谁审批？", citation)
        client._json = lambda *args, **kwargs: {"message": {"content": "需要负责人审批。[2]"}}
        with self.assertRaises(OllamaError):
            client.answer("谁审批？", citation)
        client._json = lambda *args, **kwargs: {"message": {"content": "需要负责人审批。[1]"}}
        self.assertEqual(client.answer("谁审批？", citation), "需要负责人审批。[1]")

    def test_pull_allowlist_and_single_active_job(self):
        client = PullClient()
        audits = []
        manager = ModelPullManager(client, lambda action, detail: audits.append((action, detail)))
        with self.assertRaises(ValueError):
            manager.start("arbitrary/model:latest", "admin")
        job = manager.start("qwen3.5:4b-q4_K_M", "admin")
        self.assertTrue(client.started.wait(timeout=1))
        with self.assertRaises(ValueError):
            manager.start("qwen3.5:9b-q4_K_M", "admin")
        client.release.set()
        deadline = time.monotonic() + 2
        while manager.get(job["id"])["status"] not in {"succeeded", "failed"} and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(manager.get(job["id"])["status"], "succeeded")
        self.assertEqual(manager.get(job["id"])["progress"], 100)
        self.assertEqual([action for action, _ in audits], ["model.pull.start", "model.pull.complete"])


if __name__ == "__main__":
    unittest.main()
