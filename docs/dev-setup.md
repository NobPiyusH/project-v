# Developer setup notes

## Secret scanning and lint on commit

The repo has a pre-commit config (`.pre-commit-config.yaml`) that runs gitleaks on staged changes and `ruff check`.
Install the git hook once per clone:

```powershell
uv run --with pre-commit pre-commit install
```

Run it on everything by hand: `uv run --with pre-commit pre-commit run --all-files`.

Rules:
- Secrets live only in `.env` (git-ignored). `.env.example` holds placeholders and is the only allowlisted file (`.gitleaks.toml`).
- If gitleaks blocks a commit, remove the value and rotate the key if it was real. Do not allowlist it.
- Output is redacted (`--redact`); never paste a finding's value into chat or a PR.
- The ruff hook pins its own ruff version; it is pinned to match CI (`uvx ruff@0.17.5 check .`).
