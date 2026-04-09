# Contributing to Kirok

Thank you for your interest in contributing to Kirok! This document provides guidelines for contributing to the project.

## Getting Started

1. **Fork** the repository
2. **Clone** your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/kirok-mcp.git
   cd kirok-mcp
   ```
3. **Install** dependencies with [uv](https://docs.astral.sh/uv/):
   ```bash
   uv sync
   ```
4. **Set up** your environment:
   ```bash
   cp .env.example .env
   # Edit .env and add your GEMINI_API_KEY
   ```

## Development Workflow

### Making Changes

1. Create a new branch for your feature or fix:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Make your changes in the `src/kirok_mcp/` directory
3. Test your changes locally by running the MCP server
4. Commit your changes with clear, descriptive messages

### Code Style

- **Python 3.12+** — use modern Python features (type hints, `|` union, etc.)
- **Docstrings** — all public functions must have docstrings
- **Logging** — use `logging.getLogger("kirok.module_name")` for all log output
- **Error handling** — fail gracefully, never crash the MCP server

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new tool for memory tagging
fix: handle empty query in recall
docs: update architecture diagram
refactor: simplify deduplication logic
```

## Pull Requests

1. Ensure your code follows the style guidelines above
2. Update documentation if you've changed any public interfaces
3. Add an entry to `CHANGELOG.md` under `[Unreleased]`
4. Submit your PR with a clear description of the changes

## Reporting Issues

- Use GitHub Issues for bug reports and feature requests
- Include your Python version, OS, and steps to reproduce

## License

By contributing to Kirok, you agree that your contributions will be licensed under the MIT License.
