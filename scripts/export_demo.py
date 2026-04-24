"""Export a curated subset of the knowledge graph as a JSON demo seed.

Picks up to N most-recent sources per category, plus their entities,
relationships, category links, and up to 1 generated article with its
research thread + discoveries. Writes to data/demo/seed.json.

Run from the project root:
    python scripts/export_demo.py
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

# Make the project importable so config.load_config() works
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── Config ────────────────────────────────────────────────────────
PER_CATEGORY = 3         # up to N most-recent sources per category
MAX_SOURCES  = 20        # hard cap on total sources
MAX_CONTENT_CHARS = 1500 # truncate content_text to keep seed small


def _resolve_db_path() -> str:
    """Mirror config.py's KG_DB_PATH fallback."""
    env = os.environ.get("KG_DB_PATH")
    if env:
        return env
    return str(ROOT / "data" / "knowledge.db")


def _rows(cur, q, params=()):
    cur.execute(q, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def pick_source_ids(cur) -> list[int]:
    """Top N per category, capped at MAX_SOURCES overall, preferring diverse types."""
    ids, seen = [], set()
    cats = _rows(cur,
        "SELECT id, name FROM categories ORDER BY sort_order, id")
    for cat in cats:
        rows = _rows(cur, """
            SELECT s.id
            FROM sources s
            JOIN source_categories sc ON sc.source_id = s.id
            WHERE sc.category_id = ?
            ORDER BY s.ingested_at DESC
            LIMIT ?""", (cat["id"], PER_CATEGORY))
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            ids.append(r["id"])
            if len(ids) >= MAX_SOURCES:
                return ids
    # Fallback: if categories are sparse, top-up from newest uncategorized
    if len(ids) < MAX_SOURCES:
        for r in _rows(cur, """
            SELECT id FROM sources
            WHERE id NOT IN (SELECT source_id FROM source_categories)
            ORDER BY ingested_at DESC
            LIMIT ?""", (MAX_SOURCES - len(ids),)):
            if r["id"] not in seen:
                seen.add(r["id"]); ids.append(r["id"])
    return ids


def export(db_path: str, out_path: Path):
    if not Path(db_path).exists():
        raise SystemExit(f"DB not found: {db_path}")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    source_ids = pick_source_ids(cur)
    if not source_ids:
        raise SystemExit("No sources found to export.")

    placeholders = ",".join("?" * len(source_ids))

    sources = _rows(cur, f"""
        SELECT id, url, title, source_type, summary, content_text,
               ingested_at, COALESCE(is_note, 0) AS is_note, chat_id
        FROM sources WHERE id IN ({placeholders})""", source_ids)

    # Truncate content_text to keep seed compact
    for s in sources:
        if s.get("content_text") and len(s["content_text"]) > MAX_CONTENT_CHARS:
            s["content_text"] = s["content_text"][:MAX_CONTENT_CHARS] + "…"

    # Categories (export all — seeds will merge additively)
    categories = _rows(cur, """
        SELECT id, name, parent_id, color, sort_order FROM categories
        ORDER BY sort_order, id""")

    source_categories = _rows(cur, f"""
        SELECT source_id, category_id FROM source_categories
        WHERE source_id IN ({placeholders})""", source_ids)

    # Entities mentioned in any of the chosen sources
    entities = _rows(cur, f"""
        SELECT DISTINCT e.id, e.name, e.entity_type
        FROM entities e
        JOIN entity_sources es ON es.entity_id = e.id
        WHERE es.source_id IN ({placeholders})""", source_ids)
    entity_ids = [e["id"] for e in entities]
    entity_placeholders = ",".join("?" * len(entity_ids)) if entity_ids else "NULL"

    entity_sources = _rows(cur, f"""
        SELECT entity_id, source_id FROM entity_sources
        WHERE source_id IN ({placeholders})""", source_ids)

    relationships = []
    if entity_ids:
        relationships = _rows(cur, f"""
            SELECT source_entity_id, target_entity_id, relationship_type, weight
            FROM relationships
            WHERE source_entity_id IN ({entity_placeholders})
              AND target_entity_id IN ({entity_placeholders})""",
            entity_ids + entity_ids)

    # Optional: pick the most-recent generated article to show thread UI
    generated_content = []
    generated_content_sources = []
    research_threads = []
    research_discoveries = []

    article = _rows(cur, """
        SELECT * FROM generated_content
        WHERE content_type = 'article'
        ORDER BY created_at DESC LIMIT 1""")
    if article:
        gid = article[0]["id"]
        # Only include if all its linked sources are in our selection set
        src_rows = _rows(cur, """
            SELECT source_id FROM generated_content_sources
            WHERE generated_id = ?""", (gid,))
        linked_ids = {r["source_id"] for r in src_rows}
        if linked_ids and linked_ids.issubset(set(source_ids)):
            generated_content = article
            generated_content_sources = src_rows
            thread = _rows(cur,
                "SELECT * FROM research_threads WHERE generated_id = ?", (gid,))
            if thread:
                research_threads = thread
                tid = thread[0]["id"]
                # Only include discoveries whose source is in our selection
                research_discoveries = _rows(cur, f"""
                    SELECT * FROM research_discoveries
                    WHERE thread_id = ?
                      AND source_id IN ({placeholders})""",
                    (tid, *source_ids))

    out = {
        "$schema_version": 1,
        "exported_at": "auto",
        "stats": {
            "sources": len(sources),
            "entities": len(entities),
            "relationships": len(relationships),
            "categories": len(categories),
            "generated_content": len(generated_content),
            "research_threads": len(research_threads),
            "research_discoveries": len(research_discoveries),
        },
        "categories": categories,
        "sources": sources,
        "source_categories": source_categories,
        "entities": entities,
        "entity_sources": entity_sources,
        "relationships": relationships,
        "generated_content": generated_content,
        "generated_content_sources": generated_content_sources,
        "research_threads": research_threads,
        "research_discoveries": research_discoveries,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    size_kb = out_path.stat().st_size / 1024
    print(f"Exported {len(sources)} sources, {len(entities)} entities, "
          f"{len(relationships)} relationships -> {out_path} ({size_kb:.1f} KB)")

    conn.close()


if __name__ == "__main__":
    export(_resolve_db_path(), ROOT / "data" / "demo" / "seed.json")
