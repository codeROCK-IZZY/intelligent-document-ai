# Operations runbook

## Health
`GET /api/health/live` (process) · `GET /api/health/ready` (database + active AI mode) · `GET /api/v1/system/info` (provider and model shown in the UI).

## Processing problems
- **A document stays `queued`.** The worker isn't running. Run `docker compose ps worker` and check its logs.
- **A document is `failed`.** The detail page shows the failing stage and error, and **Administration → Recent jobs** shows attempts. Common causes: corrupt or password-protected PDFs (stage `extract`), or more than `DOCAI_MAX_PAGES` pages. Re-uploading creates a new version that is processed fresh.
- **Re-process everything** (e.g. after changing the chunking or embedding model). Enqueue `process_version` for all versions; processing is idempotent, so old chunks are replaced, not duplicated:
  ```bash
  docker compose run --rm api python -c "from sqlalchemy import select; from app.db.session import session_scope; from app.db.models import DocumentVersion; from app.jobs import queue; s=session_scope().__enter__(); [queue.enqueue(s,'process_version',{'version_id':str(v.id)}) for v in s.scalars(select(DocumentVersion))]; s.commit()"
  ```
- **Slow processing.** Check thread limits (`DOCAI_EMBEDDING_THREADS`, `OMP_THREAD_LIMIT`). Oversubscription made processing about 10× slower in testing.

## Answers look wrong
1. Open the citation. Is the cited passage right? If not, it's a retrieval issue: try **Search** with the question to see what's retrieved, and check the "keyword match / similarity" scores.
2. "Not found" with a note like *No accessible document mentions: X*: the entity filter found no document containing X. Check the spelling, and whether the document is shared with the user.
3. Re-run `make evaluate` after changes to make sure leakage stays 0 and refusals stay at 1.0.

## Access questions
"Why can user X see document Y?" Look up the access view:
```sql
SELECT * FROM readable_documents WHERE document_id = '<id>';
```
Owner rows say `owner`; share rows say `read` or `edit`. Sharing changes are in `audit_log` (`document.share`).

## Backups
- **Local:**
  ```bash
  docker compose exec db pg_dump -U docai -Fc docai > docai.dump
  ```
  Also back up the `files` volume (the original uploads).
- **AWS:** RDS automated backups (7 days) and a final snapshot, plus the S3 bucket's versioning (7-day window for deleted files).

## Changing the AI provider
- **Turn Claude on:** set `ANTHROPIC_API_KEY`, then restart the API and worker. New uploads get Claude summaries, classification and extraction; existing documents keep their rule-based output until re-processed.
- **Turn Claude off:** remove the key, or set `DOCAI_LLM_PROVIDER=extractive`.
- **Cost control:** use `DOCAI_CLAUDE_MODEL` and `DOCAI_CLAUDE_EFFORT` (default `medium`). Each answer stores token usage in `messages.usage`.
