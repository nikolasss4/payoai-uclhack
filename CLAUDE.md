# PayoAI

## Tests

- Don't run the database-backed pytest suite (`uv run pytest`, `tests/`) after every change. It takes too long.
- Run it only when the user asks for it. When a change needs a new or updated test, write it, and say it hasn't been run.
- Quick checks are fine on every change, such as `npx tsc --noEmit` in `frontend/`.
