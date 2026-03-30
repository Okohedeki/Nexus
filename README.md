# Nexus

An agentic desktop app that gives you remote access to AI coding agents through Telegram and Discord, with a built-in knowledge graph that automatically extracts and connects entities from everything you share.

## Features

- **Multi-provider** - Works with Claude Code, OpenCode, or Ollama (local LLMs). Auto-detects what's installed.
- **Multi-platform** - Control your AI agent from Telegram, Discord, or both simultaneously
- **Knowledge Graph** - Share URLs, audio, video, or PDFs and Nexus auto-extracts entities, relationships, and summaries into a searchable graph
- **Desktop UI** - Native desktop window with interactive D3.js graph visualization, notes editor, and category management
- **Shell Access** - Execute shell commands remotely from your phone
- **Session Management** - Per-user sessions with cost tracking, model switching, and working directory control
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

Send messages to your bot on Telegram or Discord:

```
Hello, can you help me write a Python script?   (plain text → Claude)
/sh ls -la                                       (shell command)
/status                                          (session info)
https://example.com/article                      (auto-ingest URL)
```

## Commands

| Command | Description |
|---------|-------------|
| *(plain text)* | Chat with Claude Code |
| `/claude <prompt>` | Send prompt to Claude (alias: `/cl`) |
| `/sh <command>` | Execute shell command |
| `/cancel` | Kill running Claude process |
| `/status` | Show session info |
| `/cwd [path]` | Get/set working directory |
| `/model [name]` | Get/set Claude model |
| `/newsession` | Start fresh Claude session |
| `/mcp <subcommand>` | Manage MCP servers |
| `/kg <question>` | Query knowledge graph |
| `/kgsearch <term>` | Search entities |
| `/kgstats` | Graph statistics |
| `/kgrecent` | Recent ingestions |

## Architecture

```
Nexus
├── launcher.py              # Entry point: starts all processes
├── config.py                # Multi-platform config loader
├── core/                    # Platform-agnostic business logic
│   ├── auth.py              # Authorization decorator
│   └── commands.py          # All command handlers
├── platforms/               # Platform adapters
│   ├── base.py              # PlatformContext protocol
│   ├── telegram/            # Telegram bot + adapter
│   └── discord/             # Discord bot + adapter
├── services/                # Shared services (no platform deps)
│   ├── providers/           # LLM provider adapters
│   │   ├── claude_code.py   # Claude Code CLI
│   │   ├── opencode.py      # OpenCode CLI
│   │   ├── ollama.py        # Ollama local LLM
│   │   └── detection.py     # Auto-detection + factory
│   ├── claude_runner.py     # Session management (uses providers)
│   ├── shell_runner.py      # Shell command execution
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
