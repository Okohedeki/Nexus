"""Graph intelligence — score entities for "interestingness" and pick topics for
auto-research and auto-podcast generation.

Interestingness combines three signals:
- recent activity (sources mentioning the entity in the last N days)
- connectivity (distinct neighboring entities via co-occurrence)
- mass (total source count)

The picker enforces cooldowns via the `topic_attention` table so the same
topic isn't repeatedly chosen.
"""

import logging

logger = logging.getLogger(__name__)


async def score_entities(db, recent_days: int = 7, limit: int = 50) -> list[dict]:
    """Return up to `limit` entities ranked by interestingness score, descending."""
    cursor = await db.execute(
        f"""
        WITH entity_recent AS (
            SELECT es.entity_id, COUNT(DISTINCT s.id) AS recent_count
            FROM entity_sources es
            JOIN sources s ON s.id = es.source_id
            WHERE COALESCE(s.ingested_at, s.updated_at) >= datetime('now', '-{recent_days} days')
            GROUP BY es.entity_id
        ),
        entity_total AS (
            SELECT entity_id, COUNT(DISTINCT source_id) AS total_count
            FROM entity_sources
            GROUP BY entity_id
        ),
        entity_neighbors AS (
            SELECT es1.entity_id AS eid,
                   COUNT(DISTINCT es2.entity_id) AS neighbor_count
            FROM entity_sources es1
            JOIN entity_sources es2 ON es2.source_id = es1.source_id
                                   AND es2.entity_id != es1.entity_id
            GROUP BY es1.entity_id
        )
        SELECT e.id, e.name, e.entity_type,
               COALESCE(er.recent_count, 0)   AS recent_count,
               COALESCE(et.total_count,  0)   AS total_count,
               COALESCE(en.neighbor_count, 0) AS neighbor_count,
               (3.0 * COALESCE(er.recent_count, 0)
                + 1.0 * COALESCE(en.neighbor_count, 0)
                + 0.5 * COALESCE(et.total_count, 0)) AS score
        FROM entities e
        LEFT JOIN entity_recent  er ON er.entity_id = e.id
        LEFT JOIN entity_total   et ON et.entity_id = e.id
        LEFT JOIN entity_neighbors en ON en.eid = e.id
        WHERE COALESCE(et.total_count, 0) >= 2     -- skip one-shot mentions
        ORDER BY score DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def _had_recent_attention(db, entity_id: int, kind: str, cooldown_hours: int) -> bool:
    cursor = await db.execute(
        """SELECT 1 FROM topic_attention
           WHERE entity_id = ? AND attention_kind = ?
             AND occurred_at >= datetime('now', '-' || ? || ' hours')
           LIMIT 1""",
        (entity_id, kind, cooldown_hours),
    )
    return await cursor.fetchone() is not None


async def pick_topic_for_research(db, cooldown_hours: int = 72) -> dict | None:
    """Pick the highest-scoring entity not researched in the last `cooldown_hours`."""
    candidates = await score_entities(db, recent_days=7, limit=30)
    for c in candidates:
        if c["score"] <= 0:
            continue
        if await _had_recent_attention(db, c["id"], "research", cooldown_hours):
            continue
        return c
    return None


async def pick_topic_for_podcast(db, cooldown_hours: int = 168, min_recent: int = 3) -> dict | None:
    """Pick the highest-scoring entity with fresh material and not podcasted recently.

    `min_recent` enforces "sparse, interesting": skip entities without at least N
    new sources in the last week.
    """
    candidates = await score_entities(db, recent_days=7, limit=30)
    for c in candidates:
        if c["recent_count"] < min_recent:
            continue
        if await _had_recent_attention(db, c["id"], "podcast", cooldown_hours):
            continue
        return c
    return None


async def record_attention(db, entity_id: int, kind: str, detail: str = "") -> int:
    cursor = await db.execute(
        """INSERT INTO topic_attention (entity_id, attention_kind, detail)
           VALUES (?, ?, ?)""",
        (entity_id, kind, detail),
    )
    await db.commit()
    return cursor.lastrowid


async def gather_entity_neighbors(db, entity_id: int, limit: int = 10) -> list[str]:
    """Names of entities co-occurring with this entity in shared sources."""
    cursor = await db.execute(
        """SELECT e.name, COUNT(DISTINCT es2.source_id) AS shared
           FROM entity_sources es1
           JOIN entity_sources es2 ON es2.source_id = es1.source_id
                                  AND es2.entity_id != es1.entity_id
           JOIN entities e ON e.id = es2.entity_id
           WHERE es1.entity_id = ?
           GROUP BY e.id
           ORDER BY shared DESC
           LIMIT ?""",
        (entity_id, limit),
    )
    return [row[0] for row in await cursor.fetchall()]


async def gather_entity_recent_sources(db, entity_id: int, limit: int = 10) -> list[dict]:
    """Recent sources mentioning this entity, newest first."""
    cursor = await db.execute(
        """SELECT s.id, s.url, s.title, s.source_type, s.summary, s.content_text, s.ingested_at
           FROM sources s
           JOIN entity_sources es ON es.source_id = s.id
           WHERE es.entity_id = ?
           ORDER BY s.ingested_at DESC
           LIMIT ?""",
        (entity_id, limit),
    )
    return [dict(r) for r in await cursor.fetchall()]
