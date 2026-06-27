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

Run dependency and secret checks in CI or locally before release. Review pinned Python packages and container/image updates for published vulnerabilities; do not silently advance the compatible `homelab-ai` tag or model revisions. Treat model files and custom-node repositories as executable supply-chain inputs: use only the locked origins, commits, sizes, and SHA-256 values.

`HF_TOKEN` must be provided through the process environment only. Do not place credentials in command-line arguments because process listings and shell history may expose them. The installer never prints the token.

Public bug reports must redact usernames, absolute home paths, IP addresses, Reddit caches, prompts, media, Telegram identifiers, container environment dumps, and logs that may contain request headers.

## Generated Media

Generated videos, images, audio, prompts, run summaries, caches, and local review data belong under `data/media-pipeline/` or another ignored output directory. They are private by default.
