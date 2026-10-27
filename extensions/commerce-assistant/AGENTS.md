# Commerce assistant extension

This directory is an independent Dify companion tool service maintained in a downstream fork. It does not modify Dify core or implement a Dify plugin. Read the repository root AGENTS.md first; for this standalone extension, run commands from this directory rather than through Dify's core `api/` project.

```sh
uv sync --extra test
uv run uvicorn commerce_app.api:app --host 127.0.0.1 --port 8011
uv run --extra test pytest -q
```

- Keep source, tests, configuration, and documentation within this extension unless the requested scope explicitly includes other paths.
- Keep public policy tool authentication separate from user authentication. Do not expose order data or human confirmation actions in the Dify tool schema.
- Keep the default offline rules mode truthful. Do not label lexical policy filtering as vector RAG or claim a real Dify deployment was tested when only HTTP substitutes were used.
- Never commit generated databases, runtime logs, credentials, `.env`, caches, or virtual environments.
- Preserve upstream authorship and license notices. Keep downstream ownership and AI assistance attribution in AUTHORS.md accurate.
