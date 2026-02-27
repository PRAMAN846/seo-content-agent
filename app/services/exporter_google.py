from __future__ import annotations

from pathlib import Path
from uuid import uuid4


def export_to_local_doc(query: str, article_markdown: str) -> str:
    slug = "-".join(query.lower().split())[:60]
    out_dir = Path("exports")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{slug}-{uuid4().hex[:6]}.md"
    path.write_text(article_markdown, encoding="utf-8")
    return str(path.resolve())
