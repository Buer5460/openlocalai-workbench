import math
import re
from collections import Counter


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    latin = re.findall(r"[a-z0-9_]{2,}", normalized)
    cjk_runs = re.findall(r"[\u3400-\u9fff]+", normalized)
    cjk: list[str] = []
    for run in cjk_runs:
        cjk.extend(run[i : i + 2] for i in range(max(1, len(run) - 1)))
    return latin + cjk


def chunk_text(text: str, size: int = 420, overlap: int = 60) -> list[str]:
    cleaned = re.sub(r"\r\n?", "\n", text).strip()
    if not cleaned:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 1 <= size:
            current = f"{current}\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= size:
            current = paragraph
            continue
        step = max(1, size - overlap)
        chunks.extend(paragraph[i : i + size] for i in range(0, len(paragraph), step))
        current = ""
    if current:
        chunks.append(current)
    return chunks


def rank(query: str, rows: list[dict], limit: int = 5) -> list[dict]:
    query_terms = Counter(tokenize(query))
    if not query_terms:
        return []
    scored: list[dict] = []
    for row in rows:
        terms = Counter(tokenize(row["content"]))
        overlap = sum(min(count, terms[term]) for term, count in query_terms.items())
        if not overlap:
            continue
        score = overlap / math.sqrt(max(1, sum(terms.values())))
        item = dict(row)
        item["score"] = round(score, 4)
        scored.append(item)
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]

