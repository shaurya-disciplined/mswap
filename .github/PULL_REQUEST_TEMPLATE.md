## Step:
<!-- e.g. W0.S5 or issue reference -->

## What changed
<!-- Summary of changes made -->

## How verified
<!-- Commands executed and test results -->

## Skill findings
<!-- Findings from skills applied during this step, or None -->

## Checklist
- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run mypy src`
- [ ] `uv run pytest -q`
- [ ] `uv run python scripts/check_fixtures.py`
- [ ] `uv run python scripts/check_privacy.py`
- [ ] `CHANGELOG.md` updated
- [ ] `QUEUE.md` updated
- [ ] `.agent/PROGRESS.md` updated
- [ ] No secrets, real tokens, or personal paths in diff
