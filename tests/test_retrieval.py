import unittest

from openlocalai.retrieval import chunk_text, rank, tokenize


class RetrievalTests(unittest.TestCase):
    def test_chinese_tokens_and_rank(self):
        self.assertIn("审批", tokenize("负责人审批"))
        rows = [{"content": "最终决定发布前需要负责人审批", "id": "1"}, {"content": "今日天气晴朗", "id": "2"}]
        self.assertEqual(rank("谁负责审批", rows)[0]["id"], "1")

    def test_chunking_keeps_text(self):
        chunks = chunk_text("第一段。\n\n第二段。", size=10)
        self.assertEqual("\n".join(chunks), "第一段。\n第二段。")


if __name__ == "__main__":
    unittest.main()
