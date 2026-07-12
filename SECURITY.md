# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `main` (latest) | Yes |
| Older tags / forks | Best effort |

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

Use one of:

1. **[Private vulnerability reporting](https://github.com/vbusnita/focus-audio/security/advisories/new)** on this repository (preferred)
2. GitHub Security Advisories for `vbusnita/focus-audio`

Include a clear description, impact, and steps to reproduce if possible. We’ll acknowledge and work on a fix as soon as practical.

## What this project handles (and does not)

- Focus Audio is a **local Grok Build plugin**. It talks to the **xAI API using the user’s own credentials**.
- API keys are resolved from the user’s **macOS Keychain** or environment; they must **never** be committed to git or written into `~/.grok/focus-audio/config.toml`.
- Runtime data (config, cache, sockets, logs) lives under **`~/.grok/focus-audio/`** on the user’s machine — not in this repository.

## Scope notes

In scope (examples):

- Secret leakage via logs, cache, or plugin code
- Unsafe handling of user API keys
- Supply-chain issues in published plugin files

Out of scope (examples):

- Compromise of a user’s own xAI account or Keychain
- Grok Build / xAI platform bugs unrelated to this plugin
- Social engineering against individual users

## Preferred hardening in this repo

- Secret scanning + push protection enabled on GitHub
- Branch protection on `main` (no force-push / no branch delete)
- CI tests on pushes and pull requests
- Actions limited to workflows defined in this repository
