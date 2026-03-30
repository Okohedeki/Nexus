"""FastAPI backend for the Nexus Knowledge Graph viewer."""

import os
import sys

import aiosqlite
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = FastAPI(title="Nexus Knowledge Graph")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
DB_PATH = os.environ.get(
    "KG_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "knowledge.db"),
)


async def get_db() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    return conn


# ── Request models ───────────────────────────────────────────────


class NoteCreate(BaseModel):
    title: str
    content: str
    category_ids: list[int] = []


class NoteUpdate(BaseModel):
    title: str
    content: str
    category_ids: list[int] = []


class IngestRequest(BaseModel):
    url: str
    category_ids: list[int] = []


class CategoryCreate(BaseModel):
    name: str
    parent_id: int | None = None
    color: str | None = None


class CategoryUpdate(BaseModel):
    name: str | None = None
    parent_id: int | None = None
    color: str | None = None


class SetCategories(BaseModel):
    category_ids: list[int]


# ── Pages ─────────────────────────────────────────────────────────


@app.get("/")
async def index():
    return FileResponse(
        os.path.join(STATIC_DIR, "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ── API: Graph data ──────────────────────────────────────────────


@app.get("/api/graph")
async def graph_data():
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT e.id, e.name, e.entity_type, e.description,
                      (SELECT COUNT(*) FROM entity_sources es WHERE es.entity_id = e.id) AS source_count,
                      (SELECT COUNT(*) FROM relationships r
                       WHERE r.source_entity_id = e.id OR r.target_entity_id = e.id) AS rel_count
               FROM entities e"""
        )
        entities = [dict(r) for r in await cursor.fetchall()]

        cursor = await db.execute(
            """SELECT r.id, r.source_entity_id AS source, r.target_entity_id AS target,
                      r.relationship_type, r.weight,
                      e1.name AS source_name, e2.name AS target_name
               FROM relationships r
               JOIN entities e1 ON e1.id = r.source_entity_id
               JOIN entities e2 ON e2.id = r.target_entity_id"""
        )
        relationships = [dict(r) for r in await cursor.fetchall()]

        return {"nodes": entities, "links": relationships}
    finally:
        await db.close()


# ── API: Entities ────────────────────────────────────────────────


@app.get("/api/entities")
async def list_entities(
    q: str = Query("", description="Search query"),
    limit: int = Query(100, description="Max results"),
):
    db = await get_db()
    try:
        if q:
            cursor = await db.execute(
                """SELECT e.id, e.name, e.entity_type, e.description,
                          COUNT(es.source_id) AS source_count
                   FROM entities e
                   LEFT JOIN entity_sources es ON es.entity_id = e.id
                   WHERE e.name LIKE ?
                   GROUP BY e.id
                   ORDER BY source_count DESC
                   LIMIT ?""",
                (f"%{q}%", limit),
            )
        else:
            cursor = await db.execute(
                """SELECT e.id, e.name, e.entity_type, e.description,
                          COUNT(es.source_id) AS source_count
                   FROM entities e
                   LEFT JOIN entity_sources es ON es.entity_id = e.id
                   GROUP BY e.id
                   ORDER BY source_count DESC
                   LIMIT ?""",
                (limit,),
            )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


@app.get("/api/entities/{entity_id}")
async def get_entity(entity_id: int):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, name, entity_type, description FROM entities WHERE id = ?",
            (entity_id,),
        )
        entity = await cursor.fetchone()
        if not entity:
            return JSONResponse({"error": "not found"}, status_code=404)

        entity = dict(entity)

        cursor = await db.execute(
            """SELECT r.relationship_type, r.weight,
                      e1.id AS source_id, e1.name AS source_name, e1.entity_type AS source_type,
                      e2.id AS target_id, e2.name AS target_name, e2.entity_type AS target_type
               FROM relationships r
               JOIN entities e1 ON e1.id = r.source_entity_id
               JOIN entities e2 ON e2.id = r.target_entity_id
               WHERE r.source_entity_id = ? OR r.target_entity_id = ?""",
            (entity_id, entity_id),
        )
        entity["relationships"] = [dict(r) for r in await cursor.fetchall()]

        cursor = await db.execute(
            """SELECT s.id, s.url, s.title, s.source_type, s.summary, s.ingested_at,
                      COALESCE(s.is_note, 0) AS is_note
               FROM sources s
               JOIN entity_sources es ON es.source_id = s.id
               WHERE es.entity_id = ?
               ORDER BY s.ingested_at DESC""",
            (entity_id,),
        )
        entity["sources"] = [dict(r) for r in await cursor.fetchall()]

        return entity
    finally:
        await db.close()


# ── API: Sources ─────────────────────────────────────────────────


@app.get("/api/sources")
async def list_sources(
    limit: int = Query(50),
    category_id: int = Query(None),
):
    db = await get_db()
    try:
        from services.knowledge_graph import get_recent_sources
        items = await get_recent_sources(db, limit=limit, category_id=category_id)

        # Attach categories to each source
        for item in items:
            cursor = await db.execute(
                """SELECT c.id, c.name, c.color FROM categories c
                   JOIN source_categories sc ON sc.category_id = c.id
                   WHERE sc.source_id = ?""",
                (item["id"],),
            )
            item["categories"] = [dict(r) for r in await cursor.fetchall()]

        return items
    finally:
        await db.close()


@app.get("/api/sources/{source_id}")
async def get_source(source_id: int):
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT id, url, title, source_type, content_text, summary, ingested_at, chat_id,
                      COALESCE(is_note, 0) AS is_note, updated_at
               FROM sources WHERE id = ?""",
            (source_id,),
        )
        source = await cursor.fetchone()
        if not source:
            return JSONResponse({"error": "not found"}, status_code=404)

        source = dict(source)

        cursor = await db.execute(
            """SELECT e.id, e.name, e.entity_type, e.description
               FROM entities e
               JOIN entity_sources es ON es.entity_id = e.id
               WHERE es.source_id = ?""",
            (source_id,),
        )
        source["entities"] = [dict(r) for r in await cursor.fetchall()]

        cursor = await db.execute(
            """SELECT c.id, c.name, c.color FROM categories c
               JOIN source_categories sc ON sc.category_id = c.id
               WHERE sc.source_id = ?""",
            (source_id,),
        )
        source["categories"] = [dict(r) for r in await cursor.fetchall()]

        return source
    finally:
        await db.close()


@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: int):
    db = await get_db()
    try:
        from services.knowledge_graph import delete_source_by_id
        ok = await delete_source_by_id(db, source_id)
        if not ok:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"ok": True}
    finally:
        await db.close()


@app.get("/api/sources/{source_id}/backlinks")
async def source_backlinks(source_id: int):
    db = await get_db()
    try:
        from services.knowledge_graph import get_source_backlinks
        return await get_source_backlinks(db, source_id)
    finally:
        await db.close()


@app.put("/api/sources/{source_id}/categories")
async def set_categories(source_id: int, body: SetCategories):
    db = await get_db()
    try:
        from services.knowledge_graph import set_source_categories
        await set_source_categories(db, source_id, body.category_ids)
        return {"ok": True}
    finally:
        await db.close()


# ── API: Notes ───────────────────────────────────────────────────


@app.post("/api/notes")
async def create_note(body: NoteCreate):
    db = await get_db()
    try:
        from services.knowledge_graph import create_note as kg_create_note, set_source_categories
        from services.ingestion_service import ingest_note_content

        note_id = await kg_create_note(db, body.title, body.content)

        if body.category_ids:
            await set_source_categories(db, note_id, body.category_ids)

        # Run entity extraction in background-ish (but still await)
        model = os.environ.get("DEFAULT_MODEL", "sonnet")
        result = await ingest_note_content(db, note_id, body.title, body.content, model=model)

        return {
            "id": note_id,
            "title": body.title,
            "entity_count": result.get("entity_count", 0),
            "rel_count": result.get("rel_count", 0),
            "cost_usd": result.get("cost_usd", 0.0),
            "summary": result.get("summary", ""),
        }
    finally:
        await db.close()


@app.put("/api/notes/{note_id}")
async def update_note(note_id: int, body: NoteUpdate):
    db = await get_db()
    try:
        from services.knowledge_graph import update_note as kg_update_note, set_source_categories
        from services.ingestion_service import ingest_note_content

        ok = await kg_update_note(db, note_id, body.title, body.content)
        if not ok:
            return JSONResponse({"error": "not found or not a note"}, status_code=404)

        if body.category_ids is not None:
            await set_source_categories(db, note_id, body.category_ids)

        model = os.environ.get("DEFAULT_MODEL", "sonnet")
        result = await ingest_note_content(db, note_id, body.title, body.content, model=model)

        return {
            "id": note_id,
            "title": body.title,
            "entity_count": result.get("entity_count", 0),
            "rel_count": result.get("rel_count", 0),
            "cost_usd": result.get("cost_usd", 0.0),
            "summary": result.get("summary", ""),
        }
    finally:
        await db.close()


# ── API: Ingestion ───────────────────────────────────────────────


@app.post("/api/ingest")
async def ingest_url_endpoint(body: IngestRequest):
    db = await get_db()
    try:
        from services.ingestion_service import ingest_url
        from services.knowledge_graph import set_source_categories

        model = os.environ.get("DEFAULT_MODEL", "sonnet")
        whisper_model = os.environ.get("WHISPER_MODEL", "base")
        tmp_dir = os.path.join(os.path.dirname(DB_PATH), "tmp")

        result = await ingest_url(
            db, body.url, model=model, whisper_model=whisper_model, tmp_dir=tmp_dir,
        )

        if result["success"] and body.category_ids:
            await set_source_categories(db, result["source_id"], body.category_ids)

        return result
    finally:
        await db.close()


# ── API: Categories ──────────────────────────────────────────────


@app.get("/api/categories")
async def list_categories():
    db = await get_db()
    try:
        from services.knowledge_graph import get_categories
        return await get_categories(db)
    finally:
        await db.close()


@app.post("/api/categories")
async def create_category_endpoint(body: CategoryCreate):
    db = await get_db()
    try:
        from services.knowledge_graph import create_category
        cat_id = await create_category(db, body.name, body.parent_id, body.color)
        return {"id": cat_id, "name": body.name}
    finally:
        await db.close()


@app.put("/api/categories/{category_id}")
async def update_category_endpoint(category_id: int, body: CategoryUpdate):
    db = await get_db()
    try:
        from services.knowledge_graph import update_category
        ok = await update_category(db, category_id, body.name, body.parent_id, body.color)
        if not ok:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"ok": True}
    finally:
        await db.close()


@app.delete("/api/categories/{category_id}")
async def delete_category_endpoint(category_id: int):
    db = await get_db()
    try:
        from services.knowledge_graph import delete_category
        ok = await delete_category(db, category_id)
        if not ok:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"ok": True}
    finally:
        await db.close()


# ── API: Stats ───────────────────────────────────────────────────


@app.get("/api/stats")
async def stats():
    db = await get_db()
    try:
        from services.knowledge_graph import get_stats
        return await get_stats(db)
    finally:
        await db.close()


# ── Static files (must be last) ──────────────────────────────────

# ── API: Setup Wizard ───────────────────────────────────────────


ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


class SetupSave(BaseModel):
    provider: str = ""
    ollama_model: str = "llama3.2"
    telegram_token: str = ""
    telegram_ids: str = ""
    discord_token: str = ""
    discord_ids: str = ""
    default_cwd: str = ""
    default_model: str = "sonnet"
    claude_timeout: int = 300
    shell_timeout: int = 60
    max_budget_usd: float = 1.0


@app.get("/api/setup/status")
async def setup_status():
    """Check whether platforms are configured."""
    from dotenv import dotenv_values
    env = dotenv_values(ENV_PATH) if os.path.exists(ENV_PATH) else {}

    tg_token = env.get("TELEGRAM_BOT_TOKEN", "")
    dc_token = env.get("DISCORD_BOT_TOKEN", "")

    has_telegram = bool(tg_token and not tg_token.startswith("your-"))
    has_discord = bool(dc_token and not dc_token.startswith("your-"))

    # Detect LLM providers
    from services.providers.detection import detect_providers
    providers = detect_providers()

    return {
        "configured": has_telegram or has_discord,
        "telegram": has_telegram,
        "discord": has_discord,
        "env_exists": os.path.exists(ENV_PATH),
        "providers": providers,
        "selected_provider": env.get("PROVIDER", ""),
    }


@app.post("/api/setup/save")
async def setup_save(body: SetupSave):
    """Write platform tokens and settings to .env file."""
    lines = []

    # Provider
    if body.provider:
        lines.append(f"PROVIDER={body.provider}")
    if body.provider == "ollama" and body.ollama_model:
        lines.append(f"OLLAMA_MODEL={body.ollama_model}")

    # Platforms
    if body.telegram_token:
        lines.append(f"TELEGRAM_BOT_TOKEN={body.telegram_token}")
        if body.telegram_ids:
            lines.append(f"TELEGRAM_ALLOWED_IDS={body.telegram_ids}")

    if body.discord_token:
        lines.append(f"DISCORD_BOT_TOKEN={body.discord_token}")
        if body.discord_ids:
            lines.append(f"DISCORD_ALLOWED_IDS={body.discord_ids}")

    lines.append(f"DEFAULT_CWD={body.default_cwd or os.getcwd()}")
    lines.append(f"DEFAULT_MODEL={body.default_model}")
    lines.append(f"CLAUDE_TIMEOUT={body.claude_timeout}")
    lines.append(f"SHELL_TIMEOUT={body.shell_timeout}")
    lines.append(f"MAX_BUDGET_USD={body.max_budget_usd}")
    lines.append("")

    with open(ENV_PATH, "w") as f:
        f.write("\n".join(lines))

    return {"ok": True, "path": ENV_PATH}


@app.post("/api/setup/validate")
async def setup_validate(body: dict):
    """Validate a bot token by making a test API call."""
    import httpx

    platform = body.get("platform", "")
    token = body.get("token", "")

    if not token:
        return {"valid": False, "error": "No token provided"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if platform == "telegram":
                resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                data = resp.json()
                if data.get("ok"):
                    bot_name = data["result"].get("username", "unknown")
                    return {"valid": True, "name": f"@{bot_name}"}
                return {"valid": False, "error": data.get("description", "Invalid token")}

            elif platform == "discord":
                resp = await client.get(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bot {token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {"valid": True, "name": f"{data.get('username', 'unknown')}"}
                return {"valid": False, "error": f"HTTP {resp.status_code}"}

            else:
                return {"valid": False, "error": f"Unknown platform: {platform}"}

    except Exception as e:
        return {"valid": False, "error": str(e)}


# ── Static files (must be last) ──────────────────────────────────

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def start():
    import uvicorn
    port = int(os.environ.get("WEB_PORT", "8420"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    start()
