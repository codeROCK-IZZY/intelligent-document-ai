# Known limitations

## AI and retrieval
- **Not evaluated with Claude.** All reported numbers use the offline extractive provider. The Claude provider is unit-tested against a fake client (request shape, citation parsing, refusals, errors) but has **not** been run against the real API. Run `python -m app.cli evaluate` with a key to measure it, and expect some API cost.
- **Offline mode quotes; it doesn't understand.** It can't translate (English questions about French/Spanish documents: 0 / 4) or bridge wording gaps ("Who issued…?" when the document says "From:": 0 / 4).
- **Entity-constrained retrieval can be too strict.** If a question spells a name differently from the document ("Acme Robotic" vs "Acme Robotics"), no document qualifies and the answer is "not found". The note in the answer names the missing term. Capitalised common words in mid-sentence (e.g. "CEO") are also treated as required.
- **Synthetic corpus.** Clean layouts and consistent templates. Real invoices vary far more; the rule-based field extractors will need adapting to real formats, or the Claude extractor used instead.
- **Conflicting documents are not reconciled.** If two of your documents disagree (e.g. two versions of a policy saved as separate documents), both are cited. The system doesn't decide which is current; versions of *one* document are handled correctly.
- **Classification is keyword-based offline**, perfect on this corpus but brittle on unfamiliar formats.
- **Entities:** person names are not extracted (no NER model offline); organisations are detected only with a legal suffix (Inc, LLC, Ltd…).
- **Prompt injection:** the model has no tools and passages are marked as untrusted data, so injected text can at most mislead an answer, never trigger an action. The single injection test in the corpus passes, but this is not a guarantee against every injection.

## Scale and performance
- **Vector search is an exact scan over the user's own chunks** (always correct with access control; fast up to roughly 100k chunks per user). Beyond that, move to pgvector ≥ 0.8 iterative index scans (`hnsw.iterative_scan`), or partition the vectors by owner.
- **Near-duplicate detection compares all pairs** of a user's documents (fine for a few thousand). Use MinHash LSH banding at larger scale.
- **ONNX/Tesseract thread contention:** with the API and worker on one host, default thread counts made processing about 10× slower (11 min vs 19 s for the demo). The defaults are now `DOCAI_EMBEDDING_THREADS=2` and `OMP_THREAD_LIMIT=1`; tune these for your hardware.
- The API embeds each question locally (about 100 ms on CPU). There is no GPU support.
- **No concurrent load test** has been run.

## Platform
- **Answers are not streamed** (no Server-Sent Events, which the design proposed). The full answer returns in one response. Offline mode takes about 50 ms; Claude takes seconds.
- **Login rate limiter is per process** (as in the AI Predictive Maintenance platform).
- **Uploads are held in memory** up to the 25 MB limit before validation.
- **Deleted documents** are removed from the database immediately. On AWS the original file stays recoverable in the versioned S3 bucket for 7 days.
- **Docker image size** is about 1.06 GB (ONNX Runtime, Tesseract language data, the embedding model).

## Testing and infrastructure
- **S3 file storage** is not covered by automated tests and has not been run against real AWS.
- **Terraform has not been applied**, and was not validated locally (CI runs `fmt -check` and `validate`).
- **No license file yet.** A license will be added.
