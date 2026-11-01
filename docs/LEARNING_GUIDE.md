# Learning guide: understanding this codebase

Work through the stops in order. Try each exercise before looking at the hint.

## 1. Who may see what
**Read:** `migrations/versions/0001_initial_schema.py` (the `readable_documents` view), `app/modules/documents/service.py`, `app/retrieval/search.py`.

One SQL view defines access: owners and share rows. The API (`access_level`) and retrieval (the `allowed` CTE) both read it, so they can never disagree. A document you can't read returns **404, not 403**: a 403 would confirm it exists.

**Exercises**
1. Trace every path by which Alice could see Bob's memo (list, page, download, search, Q&A, citation history, compare, duplicates, CSV export). Find the line that blocks each one. `tests/test_documents_acl.py` checks them all.
2. Why does `history()` in `conversations/router.py` re-check access for each citation, even though the citation was created legitimately?

## 2. Reading a file
**Read:** `app/pipeline/validate.py`, `app/pipeline/extract.py`.

**Exercises**
1. Why is the file type sniffed from bytes and not from `Content-Type`? What could an attacker do otherwise?
2. A DOCX is a ZIP. What does `check_limits` defend against, and why does a size check on the *upload* not help?
3. When does a PDF page go through OCR? What happens to a page that has a little text but is mostly an image?

## 3. Chunks and citations
**Read:** `app/pipeline/text.py` (`chunk_page`), `app/retrieval/qa.py` (`_locate`).

Chunks never cross pages and store character offsets, so a citation maps to an exact highlight.

**Exercises**
1. `test_chunks_cover_page_with_exact_offsets` asserts `page[c.start:c.end] == c.text`. What breaks in the UI if that ever fails?
2. The sentence splitter treats "1,234.56" as one token. Find the regex and explain the lookahead.

## 4. Hybrid search
**Read:** `app/retrieval/search.py`.

**Exercises**
1. Why are the question's words OR-ed rather than AND-ed in the full-text query?
2. Why is the vector branch ordered by `distance + 0`? (Hint: approximate indexes and filters; see the module docstring.)
3. Reciprocal Rank Fusion uses ranks, not scores. Why is that convenient when combining `ts_rank_cd` and cosine similarity?

## 5. Grounding
**Read:** `app/retrieval/qa.py`, `app/ai/extractive.py`, `app/ai/claude.py`.

**Exercises**
1. List the three places where an answer can become "not found". Which of them run *before* the language model is called, and why does that matter for cost and safety?
2. The first evaluation had 8 % correct refusals. Read the "Design changes" section of `docs/DESIGN.md` and explain what entity-constrained retrieval fixed.
3. Write a test where the provider returns two sentences, only one cited. What does the UI show for the uncited one?

## 6. Measuring honestly
**Read:** `app/evaluation/run.py`, `app/synthetic/corpus.py`.

**Exercises**
1. Why are retrieval and answers measured separately? Give an example where retrieval is perfect but the answer is wrong.
2. The "leakage" questions are about Bob's invoices, asked by Alice. What exact condition makes a result count as leaked?
3. Change `min_relevance` in the config to `0.0` and re-run `make evaluate`. Which metric changes, and why?

## Check yourself
You understand the system when you can, without notes:
1. Explain the path of a scanned PNG from upload to a cited answer, naming each stage and table.
2. Explain why this system can say "not found" when an ordinary chatbot would make something up.
3. Explain what changes, and what is sent where, when you add an `ANTHROPIC_API_KEY`.
