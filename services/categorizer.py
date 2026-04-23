"""Auto-categorize a source into one of the existing categories using the LLM."""

import json
import logging
import re

from services.knowledge_graph import get_categories, set_source_categories
from services.providers.detection import get_provider

logger = logging.getLogger(__name__)


_PROMPT = """You categorize knowledge-graph items into ONE of the available categories.

CATEGORIES:
{cat_lines}

ITEM TITLE: {title}

ITEM CONTENT (excerpt):
{excerpt}

Pick the SINGLE category that best fits. Return ONLY a JSON object:
{{"category": "<exact name from list>"}}

If none clearly fits, pick "Society & Culture" as a default if it exists, else the
first category in the list. Do not invent new categories.
"""


async def auto_categorize_source(
    db,
    source_id: int,
    title: str,
    content_excerpt: str,
    model: str = "sonnet",
) -> int | None:
    """Pick a category for the source via LLM, store it, return category_id (or None)."""
    cats = await get_categories(db)
    if not cats:
        return None

    cat_lines = "\n".join(f"- {c['name']}" for c in cats)
    cat_by_name = {c["name"].lower(): c for c in cats}

    prompt = _PROMPT.format(
        cat_lines=cat_lines,
        title=title or "(untitled)",
        excerpt=(content_excerpt or "")[:1500],
    )

    provider = get_provider()
    try:
        raw, _cost = await provider.run_simple(prompt, model=model, timeout=45)
    except Exception as e:
        logger.warning("auto_categorize LLM call failed: %s", e)
        return None

    match = re.search(r"\{[\s\S]*?\}", raw or "")
    chosen = None
    if match:
        try:
            data = json.loads(match.group())
            chosen = (data.get("category") or "").strip().lower()
        except json.JSONDecodeError:
            pass

    if not chosen or chosen not in cat_by_name:
        # Fallback: case-insensitive substring match
        for name, c in cat_by_name.items():
            if name in (raw or "").lower():
                chosen = name
                break

    if not chosen or chosen not in cat_by_name:
        return None

    cat_id = cat_by_name[chosen]["id"]
    # Only assign if the source has no category yet (don't override manual picks)
    cursor = await db.execute(
        "SELECT COUNT(*) FROM source_categories WHERE source_id = ?", (source_id,)
    )
    (has_any,) = await cursor.fetchone()
    if has_any > 0:
        return None
    await set_source_categories(db, source_id, [cat_id])
    logger.info("Auto-categorized source %s → %s", source_id, cat_by_name[chosen]["name"])
    return cat_id
