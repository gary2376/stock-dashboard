# Security notes / 安全說明

This is a **single-user, local-first** tool. Read this before exposing it anywhere.

## Threat model

- The HTTP API has **no authentication**. It serves your holdings, analysis, notes and chat history. It binds to
  `127.0.0.1` by default (`scripts/run_local.sh`). Never use `--host 0.0.0.0` or publish the port; for remote
  access use a private network such as Tailscale (`scripts/run_tailscale.sh`).
- Write requests that carry an `Origin` header from a different host are rejected (basic CSRF guard). This does
  not replace authentication.
- Request bodies are capped (5 MB), imports are validated and transactional with an automatic pre-import backup,
  chat and pin-compile calls are size- and rate-limited, and only one AI job of each kind runs at a time.

## AI CLI usage

- AI features call **your own logged-in** `claude` or `codex` CLI as a subprocess. Your holdings, notes and
  analysis context are sent to that provider when you use those features (a consent dialog is shown first).
- `claude` is invoked with no tools, no MCP servers, no plugins and no slash commands, from an empty temporary
  directory. `codex` runs in a read-only sandbox, which can still read the filesystem: **treat it as capability
  reduction, not isolation.**
- Notes, headlines and pin text are **untrusted data**: prompts put them in delimited blocks and say to ignore
  instructions inside, but prompt injection cannot be fully eliminated. Code, not the model, enforces the score caps,
  the verified quotes, the fact references and the now/at-purchase scope rules.

## Files

- Personal data lives in `data/` (git-ignored). Pins, imports and caches are written atomically; `data/pins.json`
  is mode 0600.
- Demo mode (`STOCK_DASHBOARD_DEMO=1`) disables every route that would call an AI CLI or the network.

## Reporting

Open a GitHub issue for non-sensitive problems. For anything sensitive, contact the repository owner privately
through GitHub.
