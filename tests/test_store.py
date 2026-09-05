import os
import tempfile
import unittest
import sqlite3
from pathlib import Path

from openlocalai.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.db")

    def tearDown(self):
        self.store.connection.close()
        self.temp.cleanup()

    def test_full_workflow(self):
        document = self.store.add_document("制度.md", "最终决定发布前需要负责人审批。所有动作进入审计记录。")
        self.assertGreater(document["chunks"], 0)
        answer = self.store.answer("最终决定由谁审批？")
        self.assertTrue(answer["citations"])
        report = self.store.create_report("审批简报", "最终决定由谁审批？")
        self.assertEqual(report["status"], "pending")
        approved = self.store.approve_report(report["id"], "复核员")
        self.assertEqual(approved["status"], "approved")
        self.assertGreaterEqual(len(self.store.list_audits()), 4)

    def test_no_evidence_is_explicit(self):
        self.store.add_document("材料", "只包含苹果和梨。")
        result = self.store.answer("量子计算要求是什么？")
        self.assertIn("没有找到足够证据", result["answer"])

    def test_report_lists_every_citation_the_model_may_reference(self):
        citations = [
            {"document_name": f"材料{index}", "position": index - 1}
            for index in range(1, 6)
        ]
        self.store.answer = lambda question: {
            "answer": "结论来自第五条证据。[5]",
            "citations": citations,
        }
        report = self.store.create_report("引用完整性", "依据是什么？")
        self.assertIn("[5] 材料5，第 5 段", report["content"])

    @unittest.skipUnless(os.name == "posix", "POSIX file modes are required")
    def test_new_data_directory_and_database_are_private(self):
        data_dir = Path(self.temp.name) / "private-data"
        private_store = Store(data_dir / "openlocalai.db")
        try:
            self.assertEqual(data_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(private_store.path.stat().st_mode & 0o777, 0o600)
        finally:
            private_store.close()

    def test_migration_adds_auth_without_losing_old_data(self):
        self.store.connection.close()
        legacy_path = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE documents (id TEXT PRIMARY KEY, name TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE chunks (id TEXT PRIMARY KEY, document_id TEXT NOT NULL, position INTEGER NOT NULL, content TEXT NOT NULL);
            CREATE TABLE reports (id TEXT PRIMARY KEY, title TEXT NOT NULL, question TEXT NOT NULL, content TEXT NOT NULL,
                                  status TEXT NOT NULL, reviewer TEXT, created_at TEXT NOT NULL, approved_at TEXT);
            CREATE TABLE audits (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
            INSERT INTO documents VALUES ('legacy', '旧材料', '保留内容', '2026-01-01T00:00:00+08:00');
            """
        )
        connection.commit()
        connection.close()
        migrated = Store(legacy_path)
        try:
            self.assertEqual(migrated.list_documents()[0]["id"], "legacy")
            self.assertFalse(migrated.admin_configured())
            migrated.bootstrap_admin("admin", "correct horse battery staple")
            self.assertTrue(migrated.admin_configured())
            migrated.set_setting("active_model", "qwen3.5:4b-q4_K_M")
            self.assertEqual(migrated.get_setting("active_model"), "qwen3.5:4b-q4_K_M")
        finally:
            migrated.close()
        self.store = Store(Path(self.temp.name) / "test.db")


if __name__ == "__main__":
    unittest.main()
