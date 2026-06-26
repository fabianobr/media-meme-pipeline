# Security Policy

This repository is intended to be public. Do not commit secrets, generated media, private run logs, `.env` files, Telegram identifiers, API tokens, or ComfyUI/Ollama payload dumps with sensitive content.

## Local Services

ComfyUI, Ollama, n8n, and Docker socket access must remain private. Do not expose them directly to the internet. If remote access is needed, place it behind authenticated access with MFA and keep service ports bound to localhost or a trusted private network.

## Secrets

Use a private env file such as `~/.hermes/.env` for real credentials. The committed `.env.example` must contain names only, never real values.

Before publishing or pushing changes:

```bash
git status --short
git grep -n -Ei 'token|secret|password|bearer|chat_id|telegram|x_bearer|api[_-]?key' -- ':!.env.example'
```

Review any matches manually. Some documentation references are expected, but real values are not.

## Generated Media

Generated videos, images, audio, prompts, run summaries, caches, and local review data belong under `data/media-pipeline/` or another ignored output directory. They are private by default.

