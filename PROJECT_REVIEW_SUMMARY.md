# Project Review Summary (2026-03-04)

## Scope
- Repository-level static review across architecture, runtime entrypoints, security posture, and test/dependency setup.
- Attempted test execution with `pytest -q`.

## Top Findings (Ordered by Severity)

### 1) Critical: Sensitive credentials and access token are committed
- `config/credentials.json` contains API key, API secret, user identity, and an access token.
- References: `config/credentials.json:2`, `config/credentials.json:3`, `config/credentials.json:5`, `config/credentials.json:34`.
- Impact: Immediate secret exposure risk and potential account misuse.
- Note: `.gitignore` tries to ignore this file (`.gitignore:30`), but it is already tracked in git, so ignore rules do not protect it retroactively.

### 2) High: Onboarding/docs and packaging are inconsistent, causing setup failure
- README instructs `pip install -r requirements.txt` (`README.md:19`), but no `requirements.txt` exists in the repo.
- `pytest` currently fails in this environment because `duckdb` is missing, despite being required by tests.
- References: `README.md:19`, `tests/conftest.py:3`, `pyproject.toml:8`.
- Impact: New developers and CI cannot reliably bootstrap or validate the project.

### 3) Medium: Main trading entry script is a stub
- `scripts/run_trading.py` only prints a message and does not initialize or run the orchestrator.
- References: `scripts/run_trading.py:14`, `scripts/run_trading.py:16`.
- Impact: Confusing operational surface area and potential misuse of a non-functional entrypoint.

### 4) Medium: Security guarantee in code comments is violated by repository state
- `CredentialManager` docstring claims: "No credentials stored in version control".
- Reference: `core/auth/credentials.py:17`.
- Contradicted by tracked `config/credentials.json`.
- Impact: Trust gap between design intent and actual controls.

## What Is Working Well
- Clear domain separation (`core/`, `scripts/`, `flask_app/`, `tests/`) and substantial test suite footprint.
- Good architectural direction around deterministic runner and provider abstractions.
- Presence of multiple operational and architecture docs under `docs/`.

## Improvement Priorities

### Immediate (today)
1. Rotate all exposed broker/API credentials immediately.
2. Remove `config/credentials.json` from git history and tracking; keep only a sanitized template.
3. Add pre-commit or CI secret scanning (e.g., `gitleaks`) to block future secret commits.

### Short term (this week)
1. Fix setup path:
   - Either add a real `requirements.txt` generated from `pyproject.toml`, or update README to use `pip install .` / `pip install -e .`.
2. Add a CI workflow to run tests and basic linting on every push/PR.
3. Replace or remove stub entrypoints (`scripts/run_trading.py`) to avoid dead operational commands.

### Medium term
1. Add environment bootstrap docs for local dev and CI (Python version, install command, DB init, smoke test command).
2. Add a minimal smoke test job that verifies imports and runner wiring.
3. Add policy checks for tracked artifacts (large binaries, generated backfills, temporary files).

## Verification Notes
- `pytest -q` could not complete in this environment due to missing dependency: `ModuleNotFoundError: No module named 'duckdb'`.
- Because tests did not execute successfully here, runtime correctness beyond static review remains a residual risk.
