# Development guide

## Prerequisites
Python 3.12+, Node 22, PostgreSQL 16 with the **pgvector** extension, and **Tesseract OCR** (with `fra` and `spa` language data). On Debian/Ubuntu:

```bash
sudo apt-get install -y postgresql-16-pgvector tesseract-ocr tesseract-ocr-fra tesseract-ocr-spa
```

## Database
pgvector must be enabled by a superuser once per database. The migration also runs `CREATE EXTENSION IF NOT EXISTS`, which works for the RDS master user.

```bash
sudo -u postgres psql -c "CREATE ROLE docai LOGIN PASSWORD 'docai' CREATEDB;" -c "CREATE DATABASE docai OWNER docai;" -c "CREATE DATABASE docai_test OWNER docai;"
```

```bash
for d in docai docai_test; do sudo -u postgres psql -d $d -c "CREATE EXTENSION IF NOT EXISTS vector;"; done
```

## Backend

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
```

```bash
export DOCAI_DATABASE_URL=postgresql+psycopg://docai:docai@localhost:5432/docai DOCAI_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))") DOCAI_COOKIE_SECURE=false DOCAI_DEMO_PASSWORD='choose-a-long-password'
```

```bash
python -m app.cli demo
```

```bash
uvicorn app.main:app --reload --port 8000
```

Then, in another terminal, start the worker:

```bash
python -m app.cli worker
```

The first run downloads the embedding model (about 70 MB) to the fastembed cache.

## Frontend

```bash
cd frontend && npm install && npm run dev
```

The app is at <http://localhost:5173>, and `/api` is proxied to port 8000.

## Tests

| Command | What it runs |
|---|---|
| `cd backend && pytest` | 60 tests on `docai_test` (schema rebuilt from the migration). AI runs in offline mode, so no API key is needed and no cost is incurred |
| `pytest -m "not slow"` | Skips the end-to-end evaluation regression guard |
| `cd frontend && npm test` | Vitest |
| `make smoke E2E_PASSWORD=...` | Browser smoke test against the running stack |
| `make evaluate` | Full evaluation on the loaded demo library → `ml/reports/` |

## CLI (`python -m app.cli`)
`migrate` · `create-user --email E --role admin|member|viewer` · `generate-corpus` · `load-demo` · `drain` · `worker` · `evaluate --out DIR` · `demo`

## Code map
| Path | Responsibility |
|---|---|
| `app/pipeline/validate.py` | Real file type from bytes; size, page and zip-bomb limits |
| `app/pipeline/extract.py` | Per-page text and tables; OCR for scanned pages |
| `app/pipeline/text.py` | Normalisation, language detection, page-bounded chunking |
| `app/pipeline/understand.py` | Classification, entities, invoice/contract fields and validation, MinHash |
| `app/pipeline/runner.py` | Orchestrates stages, records status and timings, idempotent retries |
| `app/retrieval/search.py` | Hybrid search with access control in SQL; entity filter |
| `app/retrieval/qa.py` | Grounded Q&A: gate → provider → enforcement → persistence |
| `app/ai/` | Provider interface, Claude (native citations), extractive (offline), embeddings |
| `app/evaluation/run.py` | All reported metrics |
| `migrations/versions/0001_initial_schema.py` | Schema, including the `readable_documents` access view |
