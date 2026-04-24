"""Load the demo JSON seed into a fresh knowledge graph DB.

Safe by default: refuses to run if the DB already has sources. Pass --force
to blow away existing rows in the tables we touch and reseed.

Run from the project root:
    python scripts/seed_demo.py            # fresh install
    python scripts/seed_demo.py --force    # overwrite
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import aiosqlite  # noqa: E402

from services.knowledge_graph import init_db  # noqa: E402


SEED_PATH = ROOT / "data" / "demo" / "seed.json"


async def _table_empty(conn, table) -> bool:
    cursor = await conn.execute(f"SELECT COUNT(*) FROM {table}")
    (n,) = await cursor.fetchone()
    return n == 0


async def _wipe(conn):
    tables = [
        "research_discoveries", "research_threads",
        "generated_content_sources", "generated_content",
        "relationships", "entity_sources", "entities",
        "source_categories", "sources",
        # leave categories — init_db seeds defaults additively
    ]
    for t in tables:
        await conn.execute(f"DELETE FROM {t}")
    await conn.commit()


async def seed(force: bool = False):
    if not SEED_PATH.exists():
        raise SystemExit(f"Seed file not found: {SEED_PATH}")

    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    from config import load_config
    cfg = load_config()
    conn = await init_db(cfg.kg_db_path)

    if not await _table_empty(conn, "sources"):
        if not force:
            print("Sources table is not empty. Pass --force to wipe & reseed.")
            await conn.close()
            return
        print("--force: wiping existing data.")
        await _wipe(conn)

    # Insert categories (name is UNIQUE; tolerate duplicates from default seed)
    for c in data.get("categories", []):
        await conn.execute(
            """INSERT OR IGNORE INTO categories (id, name, parent_id, color, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (c["id"], c["name"], c.get("parent_id"), c.get("color"), c.get("sort_order", 0)),
        )

    for s in data.get("sources", []):
        await conn.execute(
            """INSERT OR REPLACE INTO sources
               (id, url, title, source_type, summary, content_text,
                ingested_at, is_note, chat_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (s["id"], s.get("url"), s.get("title"), s.get("source_type"),
             s.get("summary"), s.get("content_text"), s.get("ingested_at"),
             s.get("is_note", 0), s.get("chat_id", 0)),
        )

    for sc in data.get("source_categories", []):
        await conn.execute(
            """INSERT OR IGNORE INTO source_categories (source_id, category_id)
               VALUES (?, ?)""",
            (sc["source_id"], sc["category_id"]),
        )

    for e in data.get("entities", []):
        await conn.execute(
            """INSERT OR REPLACE INTO entities (id, name, entity_type)
               VALUES (?, ?, ?)""",
            (e["id"], e.get("name"), e.get("entity_type")),
        )

    for es in data.get("entity_sources", []):
        await conn.execute(
            """INSERT OR IGNORE INTO entity_sources (entity_id, source_id)
               VALUES (?, ?)""",
            (es["entity_id"], es["source_id"]),
        )

    for r in data.get("relationships", []):
        await conn.execute(
            """INSERT OR IGNORE INTO relationships
               (source_entity_id, target_entity_id, relationship_type, weight)
               VALUES (?, ?, ?, ?)""",
            (r["source_entity_id"], r["target_entity_id"],
             r.get("relationship_type") or "related", r.get("weight", 1.0)),
        )

    for g in data.get("generated_content", []):
        await conn.execute(
            """INSERT OR REPLACE INTO generated_content
               (id, content_type, title, content, parameters, model_used, cost_usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (g["id"], g.get("content_type"), g.get("title"), g.get("content"),
             g.get("parameters"), g.get("model_used"),
             g.get("cost_usd", 0.0), g.get("created_at")),
        )

    for gcs in data.get("generated_content_sources", []):
        await conn.execute(
            """INSERT OR IGNORE INTO generated_content_sources (generated_id, source_id)
               VALUES (?, ?)""",
            (gcs.get("generated_id") or gcs.get("id"), gcs["source_id"]),
        )

    for t in data.get("research_threads", []):
        await conn.execute(
            """INSERT OR REPLACE INTO research_threads
               (id, generated_id, status, cadence_hours, max_per_poll,
                focus_keywords, last_polled_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (t["id"], t["generated_id"], t.get("status", "active"),
             t.get("cadence_hours", 24), t.get("max_per_poll", 5),
             t.get("focus_keywords"), t.get("last_polled_at"), t.get("created_at")),
        )

    for d in data.get("research_discoveries", []):
        await conn.execute(
            """INSERT OR IGNORE INTO research_discoveries
               (thread_id, source_id, query, discovered_at)
               VALUES (?, ?, ?, ?)""",
            (d["thread_id"], d["source_id"], d.get("query"), d.get("discovered_at")),
        )

    await conn.commit()

    stats = data.get("stats", {})
    print(
        "Demo seed loaded:\n"
        f"  {stats.get('sources', 0)} sources\n"
        f"  {stats.get('entities', 0)} entities\n"
        f"  {stats.get('relationships', 0)} relationships\n"
        f"  {stats.get('categories', 0)} categories\n"
        f"  {stats.get('generated_content', 0)} generated article(s)\n"
        f"  {stats.get('research_threads', 0)} research thread(s) with "
        f"{stats.get('research_discoveries', 0)} discoveries"
    )
    await conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true",
                    help="Wipe existing data before seeding.")
    args = p.parse_args()
    asyncio.run(seed(force=args.force))
