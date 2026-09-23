default:
    @just --list

# Install dependencies and pre-commit hooks
install:
    uv sync --dev
    GIT_CONFIG_GLOBAL=/dev/null uv run pre-commit install

# Start FastAPI development server
dev:
    uv run uvicorn prbot.main:api --reload

# Run pytest tests
test *args:
    uv run pytest {{ args }}

# Lint code with ruff
lint:
    uv run ruff check .

# Lint and auto-fix
lint-fix:
    uv run ruff check --fix .

# Format code with ruff
format:
    uv run ruff format .

# Check code formatting
format-check:
    uv run ruff format --check .

# Type check with ty
typecheck:
    uv run ty check

# Check Alembic migrations are up to date with models
check-migrations:
    uv run python scripts/check_migrations.py

# Enforce layered architecture with import-linter
lint-imports:
    uv run lint-imports

# Verify uv.lock is in sync with pyproject.toml
lock-check:
    uv lock --check

# Upgrade every locked dependency to its latest compatible release
upgrade:
    uv lock --upgrade
    uv sync --dev
    uv run pre-commit autoupdate

# Run all checks (same set as CI)
check: lock-check lint format-check typecheck check-migrations lint-imports

# Seed channel cursors from existing tracked PRs (one-time, after first deploy)
seed-cursors *args:
    uv run python scripts/seed_cursors.py {{ args }}

# Seed cursors on the Fly.io machine
seed-cursors-prod *args:
    fly ssh console -a prbot -C "uv run --no-sync python scripts/seed_cursors.py {{ args }}"

# Serve docs locally with hot-reload
docs:
    uv run --group docs mkdocs serve

# Build docs site
docs-build:
    uv run --group docs mkdocs build --strict

# Remove Python cache files
clean:
    find . -type f -name "*.pyc" -delete
    find . -type d -name "__pycache__" -delete
