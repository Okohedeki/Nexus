# Nexus

A knowledge graph desktop app. Send URLs, audio, video, or PDFs from Telegram or Discord and Nexus auto-extracts entities, relationships, and detailed summaries into a searchable, visual knowledge graph.

## Features

- **Multi-provider** - Entity extraction via Claude Code, OpenCode, or Ollama (local LLMs). Auto-detects what's installed.
- **Multi-platform ingestion** - Send links and media from Telegram, Discord, or both — they feed the same knowledge graph
- **Knowledge Graph** - Auto-extracts entities, relationships, and summaries from everything you share
- **Desktop UI** - Native desktop window with interactive D3.js graph visualization, notes editor, and category management
- **Setup Wizard** - First-run guided setup in the desktop UI, no manual config needed
- **Standalone Build** - Package as a Windows desktop app with PyInstaller

## Supported LLM Providers

| Provider | Agentic | Sessions | Cost | Install |
|----------|---------|----------|------|---------|
| **Claude Code** | Full (file edit, shell, MCP) | Multi-turn | Pay per use | `npm i -g @anthropic-ai/claude-code` |
| **OpenCode** | Full (file edit, shell, MCP) | Multi-turn | Depends on backend | [github.com/opencode-ai/opencode](https://github.com/opencode-ai/opencode) |
| **Ollama** | Chat only | No | Free (local) | [ollama.com/download](https://ollama.com/download) |

## Quick Start

### 1. Install

```bash
git clone <your-repo-url>
cd Nexus
pip install -r requirements.txt
```

### 2. Launch

```bash
python launcher.py
```

On first launch, the desktop app opens a setup wizard where you can configure your Telegram and/or Discord bot tokens. Follow the step-by-step instructions to create your bots.

### 3. Use

Send links or media to your bot on Telegram or Discord:

```
https://example.com/article           → extracts content, entities, summary
https://youtube.com/watch?v=xyz       → transcribes audio, extracts entities
(voice message or video)              → transcribes and ingests
```

Everything appears in the desktop Knowledge Graph UI — browse entities, view summaries, explore connections.

## Architecture

```
Nexus
├── launcher.py              # Entry point: starts all processes
├── config.py                # Multi-platform config loader
├── core/                    # Platform-agnostic logic
│   ├── auth.py              # Authorization decorator
│   └── commands.py          # URL + media ingestion handlers
├── platforms/               # Messaging adapters
│   ├── base.py              # PlatformContext protocol
│   ├── telegram/            # Telegram bot + adapter
│   └── discord/             # Discord bot + adapter
├── services/                # Shared services (no platform deps)
│   ├── providers/           # LLM provider adapters
│   │   ├── claude_code.py   # Claude Code CLI
│   │   ├── opencode.py      # OpenCode CLI
│   │   ├── ollama.py        # Ollama local LLM
│   │   └── detection.py     # Auto-detection + factory
│   ├── content_extractor.py # URL/media content extraction
│   ├── entity_extractor.py  # Claude-powered entity extraction
│   ├── ingestion_service.py # Ingestion pipeline orchestrator
│   ├── knowledge_graph.py   # SQLite knowledge graph
│   ├── transcriber.py       # Whisper audio transcription
│   └── output_formatter.py  # Message chunking
└── web/                     # Desktop UI
    ├── server.py            # FastAPI backend + setup API
    └��─ static/index.html    # Knowledge graph viewer
```

## Configuration

All settings are stored in `.env` (created by the setup wizard or manually from `.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `PROVIDER` | LLM provider (`claude_code`, `opencode`, `ollama`) | auto-detect |
| `OLLAMA_MODEL` | Model for Ollama provider | `llama3.2` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | *(optional)* |
| `TELEGRAM_ALLOWED_IDS` | Allowed Telegram chat IDs | |
| `DISCORD_BOT_TOKEN` | Discord bot token | *(optional)* |
| `DISCORD_ALLOWED_IDS` | Allowed Discord user/channel IDs | |
| `DEFAULT_CWD` | Working directory | `.` |
| `DEFAULT_MODEL` | Model for the provider | `sonnet` |
| `CLAUDE_TIMEOUT` | Timeout (seconds) | `300` |
| `SHELL_TIMEOUT` | Shell timeout (seconds) | `60` |
| `MAX_BUDGET_USD` | Max budget per request | `1.0` |

At least one platform token and one LLM provider must be configured.

## Building

Build a standalone Windows desktop app:

```bash
python build.py
```

Output: `dist/Nexus/Nexus.exe`

To auto-start on login:

```bash
python install_startup.py        # Python mode
python install_startup.py --exe  # Built exe mode
python install_startup.py --remove
```

## Prerequisites

- Python 3.11+
- At least one LLM provider:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (recommended, full agentic features)
  - [OpenCode](https://github.com/opencode-ai/opencode) (open-source alternative)
  - [Ollama](https://ollama.com) (free local LLM, chat only)
- A Telegram and/or Discord bot token

## License

MIT
