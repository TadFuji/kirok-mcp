<div align="center">

# 📝 Kirok

**Persistent Memory for AI Agents**

*Retain knowledge. Recall with precision. Reflect for deeper insights.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)

🇬🇧 English | **[🇯🇵 日本語はこちら](docs/README_ja.md)**

</div>

---

Kirok (記録, "record" in Japanese) is a **Model Context Protocol (MCP) server** that gives AI agents persistent, searchable memory. Without Kirok, your AI assistant forgets everything when you start a new conversation. With Kirok, it remembers your preferences, past decisions, lessons learned, and can even generate insights from accumulated knowledge.

## ✨ What Can Kirok Do?

| Feature | What It Means |
|---------|---------------|
| **🧠 Retain** | Your AI stores information and automatically extracts key details |
| **🔍 Recall** | Your AI searches past memories using both meaning and keywords |
| **💡 Reflect** | Your AI analyzes accumulated memories to generate insights |
| **🔄 Smart Dedup** | Automatically avoids storing duplicate information |
| **📊 Observations** | Detects patterns across your memories over time |
| **🎯 Bank Missions** | Customize what each memory bank focuses on |

---

## 🎁 Bonus: Core "Kirok" Agent Skill Included

To help your AI understand and use its new memory capabilities automatically, we've bundled the core **"kirok" Agent Skill** inside the `skills/` directory.

- `kirok`: Teaches the AI how to use Kirok's memory mechanics effectively. Instead of having to tell the AI "remember this", the AI will automatically know when and how to store context.

**How to use (Quick Start)**:
1. Copy the `skills` folder into your working directory.
2. In your very first chat, just tell the AI: **"Please read `skills/kirok/SKILL.md` and follow its instructions."**
*(Pro Tip: You can add this sentence to your Custom Instructions / System Prompt so the AI reads it automatically every time you start a new conversation!)*

---

## 🚀 Getting Started (Step by Step)

Follow these steps in order. Estimated time: **10–15 minutes**.

### Step 1: Install Python 3.12+

Kirok requires Python 3.12 or newer.

<details>
<summary><b>🍎 Mac</b></summary>

The easiest way is using [Homebrew](https://brew.sh/):

```bash
# Install Homebrew (if you don't have it)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python
brew install python@3.12
```

Verify the installation:
```bash
python3 --version
# Should show: Python 3.12.x or newer
```

</details>

<details>
<summary><b>🪟 Windows</b></summary>

1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Download the latest Python 3.12+ installer
3. **Important**: Check the box ✅ **"Add Python to PATH"** during installation
4. Click "Install Now"

Verify the installation by opening **PowerShell** and running:
```powershell
python --version
# Should show: Python 3.12.x or newer
```

</details>

### Step 2: Install uv (Python Package Manager)

[uv](https://docs.astral.sh/uv/) is a fast Python package manager that Kirok uses.

<details>
<summary><b>🍎 Mac</b></summary>

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then restart your terminal, and verify:
```bash
uv --version
```

</details>

<details>
<summary><b>🪟 Windows</b></summary>

Open **PowerShell** and run:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then **close and reopen PowerShell**, and verify:
```powershell
uv --version
```

</details>

### Step 3: Get a Gemini API Key (Free)

Kirok uses Google's Gemini AI for understanding and searching your memories. The free tier is more than enough for personal use.

1. Go to **[Google AI Studio](https://aistudio.google.com/apikey)**
2. Sign in with your Google account
3. Click **"Create API Key"**
4. Copy the key (it starts with `AIza...`) — you'll need it in Step 5

> **💡 Tip**: The free tier allows 1,500 requests per day — plenty for normal use.

### Step 4: Download and Install Kirok

<details>
<summary><b>🍎 Mac</b></summary>

```bash
# Choose where to install (e.g., your home directory)
cd ~

# Download Kirok
git clone https://github.com/TadFuji/kirok-mcp.git
cd kirok-mcp

# Install dependencies
uv sync
```

</details>

<details>
<summary><b>🪟 Windows</b></summary>

```powershell
# Choose where to install (e.g., your Desktop)
cd $env:USERPROFILE\Desktop

# Download Kirok
git clone https://github.com/TadFuji/kirok-mcp.git
cd kirok-mcp

# Install dependencies
uv sync
```

> **Don't have Git?** Download it from [git-scm.com](https://git-scm.com/download/win) first.  
> Alternatively, download Kirok as a ZIP from the [GitHub page](https://github.com/TadFuji/kirok-mcp) → green "Code" button → "Download ZIP", then unzip it.

</details>

### Step 5: Configure Your API Key

<details>
<summary><b>🍎 Mac</b></summary>

```bash
cp .env.example .env
```

Open the `.env` file in any text editor and replace `your-api-key-here` with the API key you copied in Step 3:

```
GEMINI_API_KEY=AIzaSy...your-key-here...
```

</details>

<details>
<summary><b>🪟 Windows</b></summary>

```powershell
Copy-Item .env.example .env
```

Open the `.env` file in Notepad (or any text editor) and replace `your-api-key-here` with the API key you copied in Step 3:

```
GEMINI_API_KEY=AIzaSy...your-key-here...
```

</details>

### Step 6: Connect to Claude Desktop

Now connect Kirok to your AI client. The most common setup is **Claude Desktop**.

#### Find the config file

| OS | Config file location |
|----|---------------------|
| 🍎 Mac | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| 🪟 Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

> **💡 How to open the config file**:
> In Claude Desktop, go to **Settings** (gear icon) → **Developer** → **Edit Config**.
> If the option doesn't appear, create the file manually at the path above.

#### Add Kirok to the config

Open the config file and add the Kirok server. **Replace `/path/to/kirok-mcp` with the actual folder path** where you installed Kirok.

<details>
<summary><b>🍎 Mac example</b></summary>

```json
{
  "mcpServers": {
    "kirok": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/Users/yourname/kirok-mcp",
        "kirok-mcp"
      ]
    }
  }
}
```

> Replace `/Users/yourname/kirok-mcp` with your actual path.

</details>

<details>
<summary><b>🪟 Windows example</b></summary>

```json
{
  "mcpServers": {
    "kirok": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "C:\\Users\\YourName\\Desktop\\kirok-mcp",
        "kirok-mcp"
      ]
    }
  }
}
```

> Replace `C:\\Users\\YourName\\Desktop\\kirok-mcp` with your actual path.  
> **Important**: Use double backslashes `\\` in JSON on Windows.

</details>

<details>
<summary><b>📌 Already have other MCP servers?</b></summary>

If your config file already has other servers, just add the `kirok` entry inside the existing `mcpServers` object:

```json
{
  "mcpServers": {
    "existing-server": {
      "...": "..."
    },
    "kirok": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/path/to/kirok-mcp",
        "kirok-mcp"
      ]
    }
  }
}
```

</details>

#### Restart Claude Desktop

After saving the config file, **completely quit and restart Claude Desktop**. Kirok should now appear in the MCP tools list.

### Step 7: Verify It Works

In a new Claude Desktop conversation, try asking:

> "Use Kirok to remember that my favorite programming language is Python."

Claude should use the `KIROK_retain` tool to store this memory. Then in a **new conversation**, ask:

> "What's my favorite programming language?"

If Claude recalls "Python" using `KIROK_recall`, everything is working! 🎉

---

## 🔧 Other MCP Clients

<details>
<summary><b>Gemini CLI / Antigravity</b></summary>

Add to your `mcp_config.json`:

```json
{
  "mcpServers": {
    "kirok": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/path/to/kirok-mcp",
        "kirok-mcp"
      ]
    }
  }
}
```

</details>

<details>
<summary><b>VS Code / Cursor</b></summary>

Add to your workspace or user MCP settings (`.vscode/mcp.json` or VS Code settings):

```json
{
  "mcpServers": {
    "kirok": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/path/to/kirok-mcp",
        "kirok-mcp"
      ]
    }
  }
}
```

</details>

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│                  MCP Client                      │
│       (Claude Desktop, Cursor, etc.)             │
└──────────────────────┬──────────────────────────┘
                       │ MCP Protocol (stdio)
┌──────────────────────▼──────────────────────────┐
│              Kirok MCP Server                    │
│  ┌───────────┐  ┌──────────┐  ┌──────────────┐  │
│  │  19 Tools │  │ LLM      │  │ Embedding    │  │
│  │  (CRUD)   │  │ Client   │  │ Client       │  │
│  └─────┬─────┘  └────┬─────┘  └──────┬───────┘  │
│        │             │               │           │
│  ┌─────▼─────────────▼───────────────▼───────┐   │
│  │            SQLite + FTS5                   │   │
│  │  memories │ models │ observations │ config │   │
│  └────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │    Google Gemini API    │
          │  gemini-embedding-001   │
          │  gemini-2.5-flash-lite  │
          └─────────────────────────┘
```

## 📖 Tools Reference

Kirok provides **19 MCP tools** organized into five categories:

### Core Operations

| Tool | Description |
|------|-------------|
| `KIROK_retain` | Store a memory with automatic entity extraction, embedding, and smart deduplication |
| `KIROK_recall` | Search memories using hybrid semantic + keyword search with RRF |
| `KIROK_reflect` | Generate insights from accumulated memories, saved as optionally auto-refreshing mental models |
| `KIROK_smart_retain` | Score content importance before running the full retain pipeline — ideal for bulk ingestion |
| `KIROK_consolidate` | Manually trigger observation consolidation |

### Memory Management

| Tool | Description |
|------|-------------|
| `KIROK_get_memory` | Get full details of a specific memory |
| `KIROK_update_memory` | Update content/context of an existing memory |
| `KIROK_forget` | Delete a specific memory (irreversible) |
| `KIROK_list_memories` | Browse memories with pagination |

### Mental Models

| Tool | Description |
|------|-------------|
| `KIROK_list_mental_models` | List insights generated by Reflect |
| `KIROK_get_mental_model` | Get full details of a mental model |
| `KIROK_delete_mental_model` | Delete a mental model (irreversible) |
| `KIROK_refresh_mental_model` | Re-analyze with latest memories |

### Bank Management

| Tool | Description |
|------|-------------|
| `KIROK_list_banks` | List all memory banks with counts |
| `KIROK_stats` | Get detailed statistics for a bank |
| `KIROK_clear_bank` | Delete all memories and observations in a bank |
| `KIROK_delete_bank` | Permanently delete a bank and all its data |

### Configuration

| Tool | Description |
|------|-------------|
| `KIROK_set_bank_config` | Set retain/observations missions for a bank |
| `KIROK_get_bank_config` | View current bank configuration |

## ⚙️ Configuration

All configuration is via environment variables in the `.env` file:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GEMINI_API_KEY` | ✅ | — | Google Gemini API key ([get one free](https://aistudio.google.com/apikey)) |
| `KIROK_DB_PATH` | ❌ | `~/.kirok/memory.db` | Custom database path |
| `KIROK_DEDUP_THRESHOLD` | ❌ | `0.85` | Similarity threshold for deduplication (0.0–1.0) |
| `KIROK_REFLECT_TIMEOUT` | ❌ | `300` | Timeout in seconds for reflect operations |
| `KIROK_CONSOLIDATION_TIMEOUT` | ❌ | `120` | Timeout in seconds for consolidation |

## 🩺 Diagnostics

Run the offline setup checker:

```bash
uv run kirok-doctor
```

It checks Python version, `.env` loading, `GEMINI_API_KEY` presence (without
printing the key), required Python modules, SQLite FTS5 support, and database
directory writability. It does **not** call Gemini or any network API.

JSON output is available for automation:

```bash
uv run kirok-doctor --json
```

If your local environment cannot run the script entry point, use the module form:

```bash
uv run python -m kirok_mcp.diagnostics
```

## 🧪 How It Works

### The Retain → Recall → Reflect Loop

1. **Retain**: When you store a memory, Kirok:
   - Generates a semantic embedding via `gemini-embedding-001`
   - Extracts entities and keywords via `gemini-2.5-flash-lite`
   - Checks for duplicates using cosine similarity (> 0.85 threshold)
   - If similar memories exist: decides to ADD, UPDATE existing, or SKIP
   - Indexes in both SQLite and FTS5 for hybrid search
   - Auto-consolidates observations in the background

   **Smart Retain** first asks the LLM to score content importance (1-10).
   If the score meets the threshold, it runs this same Retain pipeline — including
   deduplication, UPDATE/NOOP decisions, indexing, and auto-consolidation.

2. **Recall**: When you search, Kirok:
   - Runs semantic search (cosine similarity on embeddings)
   - Runs keyword search (FTS5 with BM25 ranking)
   - Merges results using [Reciprocal Rank Fusion](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) (RRF, k=60)
   - Shows consolidated observations first, then supporting memories

3. **Reflect**: When you reflect, Kirok:
   - Retrieves relevant memories via semantic search
   - Sends them to the LLM with existing mental models as context
   - Saves the resulting insight as a new mental model
   - Can mark that model for auto-refresh after future consolidation

### Memory Banks

Memories are organized into **banks** — think of them as folders for your AI's memory:

- `"work"` — Work-related decisions and learnings
- `"personal"` — Personal preferences and habits
- `"projects"` — Project-specific knowledge

Create as many banks as you need. Your AI agent will suggest appropriate bank names as you use Kirok.

---

## 🧑‍💻 Development

Run the unit test suite:

```bash
uv run python -m unittest discover -s tests
```

The tests cover the SQLite database layer, FTS query handling, bank clearing and
deletion consistency, embedding utilities, Smart Retain's routing through the
shared Retain pipeline, Reflect auto-refresh options, and offline diagnostics.
They do not call Gemini or any external API.

---

## ❓ Troubleshooting

<details>
<summary><b>"uv: command not found" or "'uv' is not recognized"</b></summary>

**uv is not installed or not in your PATH.**

- Run the uv installation command again from [Step 2](#step-2-install-uv-python-package-manager)
- Close and reopen your terminal / PowerShell after installation
- On Mac, you may need to restart your shell: `source ~/.zshrc`

</details>

<details>
<summary><b>Not sure what's wrong with your setup?</b></summary>

Run:

```bash
uv run kirok-doctor
```

If that command itself fails because your environment is mid-upgrade or a local
script is locked, try:

```bash
uv run python -m kirok_mcp.diagnostics
```

The diagnostic output is offline and safe to share after checking paths; it never
prints your Gemini API key.

</details>

<details>
<summary><b>"Python 3.12+ is required" or version mismatch</b></summary>

Check your Python version:
```bash
python3 --version   # Mac
python --version    # Windows
```

If it shows an older version, install Python 3.12+ from [Step 1](#step-1-install-python-312).

On Mac with multiple Python versions, uv will automatically find the right one. On Windows, uninstall older versions or adjust your PATH.

</details>

<details>
<summary><b>Kirok doesn't appear in Claude Desktop</b></summary>

1. Make sure you **completely quit** Claude Desktop (not just close the window) and restart it
2. Check that the path in `claude_desktop_config.json` is correct and uses the right format:
   - Mac: `/Users/yourname/kirok-mcp` (forward slashes)
   - Windows: `C:\\Users\\YourName\\Desktop\\kirok-mcp` (double backslashes)
3. Check for JSON syntax errors in your config file (missing commas, brackets, etc.)
4. Look at Claude Desktop logs for error messages

</details>

<details>
<summary><b>"GEMINI_API_KEY not set" or API errors</b></summary>

1. Make sure you copied `.env.example` to `.env` (not `.env.example`)
2. Open `.env` and verify your API key is there: `GEMINI_API_KEY=AIzaSy...`
3. Make sure there are no spaces around the `=` sign
4. Make sure the key is valid — test it at [Google AI Studio](https://aistudio.google.com/)

</details>

<details>
<summary><b>"git: command not found" or "'git' is not recognized"</b></summary>

Git is not installed on your system:
- **Mac**: Run `xcode-select --install` in Terminal
- **Windows**: Download from [git-scm.com](https://git-scm.com/download/win)

Alternatively, download Kirok as a ZIP from [GitHub](https://github.com/TadFuji/kirok-mcp) (green "Code" button → "Download ZIP").

</details>

---

## 📂 Project Structure

```
kirok-mcp/
├── src/kirok_mcp/
│   ├── __init__.py       # Package metadata
│   ├── server.py         # MCP server + 19 tool definitions
│   ├── db.py             # SQLite database layer + FTS5
│   ├── llm.py            # Gemini LLM for extraction & reflection
│   └── embeddings.py     # Gemini Embeddings + similarity utils
├── docs/
│   ├── architecture.md   # Detailed system design
│   └── tools-reference.md # Complete tool documentation
├── .env.example          # Environment template
├── pyproject.toml        # Project metadata & dependencies
├── LICENSE               # MIT License
├── CHANGELOG.md          # Version history
└── CONTRIBUTING.md       # Contribution guidelines
```

## 📜 License

MIT License — see [LICENSE](LICENSE) for details.

## 🙏 Acknowledgements

- [Model Context Protocol (MCP)](https://modelcontextprotocol.io) by Anthropic
- [Google Gemini API](https://ai.google.dev/) for embeddings and LLM
- [Mem0](https://github.com/mem0ai/mem0) for inspiration on smart deduplication
- [Reciprocal Rank Fusion](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) (Cormack et al., 2009)
