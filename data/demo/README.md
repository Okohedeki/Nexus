# Demo data

`seed.json` is a curated subset of a real NANTA knowledge graph, checked in
so fresh installs have something to explore before they've ingested their
own content.

**Contents (approximate):** 20 sources across 5+ categories, ~180 entities,
~220 relationships, plus one pre-generated article with its research
thread and 3 linked discoveries. All content text is truncated to 1500
chars to keep the file small. Nothing in this seed is private; it's the
same kind of public-web material NANTA ingests by design.

## Load it into a fresh install

From the project root:

```bash
python scripts/seed_demo.py
```

Refuses to run if the `sources` table isn't empty. Pass `--force` to wipe
and reseed:

```bash
python scripts/seed_demo.py --force
```

## Regenerate the seed from your current DB

When you want to publish an updated demo, run the exporter against your
live `data/knowledge.db`:

```bash
python scripts/export_demo.py
```

It picks up to 3 most-recent sources per category, capped at 20 overall,
plus the latest generated article (only if all its source links are
within the selected set). Adjust the constants at the top of
`scripts/export_demo.py` to tune what gets exported.

## What does NOT get exported

- `data/knowledge.db` itself (stays local, private).
- `data/audio/*.wav` — generated podcast audio (too large for git).
- `data/models/*.onnx` — Kokoro TTS model (~336 MB, auto-downloaded on
  first use).
- `data/tmp/` — scratch.
- `generation_jobs`, `topic_attention`, `research_events` — runtime
  tables; a fresh install starts them empty.
