# Contributing to django-boundary

Practical guide for contributors.

---

## Prerequisites

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Django 5.2 or later (installed as part of the dev setup)
- PostgreSQL 14+ (required by the test suite)

---

## Local Development Setup

```bash
git clone https://github.com/nigelcopley/django-boundary.git
cd django-boundary

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

Or without the optional group:

```bash
pip install -e .
pip install "Django~=5.2" pytest pytest-django pytest-cov factory-boy psycopg[binary]
```

---

## Running Tests

Tests require a running PostgreSQL instance. Set the following environment
variables before running pytest:

```bash
export POSTGRES_USER=icv_test
export POSTGRES_PASSWORD=icv_test_password
export POSTGRES_DB=boundary_test
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
```

Then run:

```bash
DJANGO_SETTINGS_MODULE=settings PYTHONPATH=src:tests pytest tests/ -v --tb=short
```

If you use Docker, a one-liner to start a compatible container:

```bash
docker run --rm -d \
  -e POSTGRES_USER=icv_test \
  -e POSTGRES_PASSWORD=icv_test_password \
  -e POSTGRES_DB=boundary_test \
  -p 5432:5432 \
  postgres:16
```

---

## Code Standards

All Python code is linted and formatted with
[ruff](https://docs.astral.sh/ruff/), configured in `pyproject.toml`.

| Setting | Value |
|---------|-------|
| Line length | 120 |
| Quote style | Double |
| Target Python | 3.12 |

```bash
ruff check .           # lint
ruff format --check .  # format check (no writes)
ruff format .          # format in place
```

CI will fail if either check reports errors. Run both before pushing.

---

## Project Structure

```
django-boundary/
    src/boundary/       # importable package
    tests/
        settings.py     # Django settings for the test suite
    pyproject.toml      # package metadata, dependencies, ruff config
    CHANGELOG.md
    README.md
    RELEASING.md
```

---

## Git Workflow

### Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <description>
```

| Type | When to use |
|------|-------------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `chore` | Maintenance, version bumps, dependency updates |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `style` | Formatting, whitespace, no logic change |
| `refactor` | Code change that is neither a fix nor a feature |

### Branches and PRs

Push feature branches and open a pull request against `main`. CI must pass
before merging. Prefer small, focused commits over large ones.

---

## Releasing

See [RELEASING.md](RELEASING.md) for the full release process.

The short version: bump version in `pyproject.toml` and
`src/boundary/__init__.py`, update `CHANGELOG.md`, merge to `main`, then push
a `v<version>` tag. CI handles the rest.
