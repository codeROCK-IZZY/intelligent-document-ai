# AI card — how answers are produced

## Pipeline per question
1. **Follow-up resolution.** "and when does it expire?" borrows the key terms of the previous question. Offline this happens only when the question refers back ("it", "that", "and…"); Claude rewrites the question.
2. **Entity constraint.** Codes, numbers and capitalised names in the question must all appear in a document for it to be eligible. If none qualify, the answer is "not found" and the language model is **not called**.
3. **Hybrid retrieval** over documents the user may read (owner or shared): PostgreSQL full text plus pgvector cosine similarity, fused with Reciprocal Rank Fusion. Top 8 passages.
4. **Relevance gate.** Weak retrieval also means "not found" without a model call.
5. **Answer:**
   - *Offline (extractive):* the source sentences that best match what the question asks are quoted verbatim, each cited. No generated text.
   - *Claude (`claude-opus-5-5` by default):* passages are sent as separate document blocks with **native citations**. Each returned sentence carries the passage it came from. The system prompt marks passages as untrusted data and asks for "NOT_FOUND" when unsupported. Refusals and API errors are handled; server-side refusal fallbacks are enabled.
6. **Grounding enforcement in code.** An answer with no citations is withheld. Uncited sentences are shown highlighted as "not supported by a source".

## Embeddings
`BAAI/bge-small-en-v1.5` (384 dimensions, English-optimised) runs locally on CPU through ONNX, so nothing is sent anywhere for embeddings. For multilingual collections, consider `intfloat/multilingual-e5-small`; changing the model requires re-indexing.

## Evaluated how
See [`ml/reports/evaluation.md`](../ml/reports/evaluation.md): 75 labelled questions over 136 synthetic documents. They include unanswerable questions and "leakage probes" (questions about another user's documents). Retrieval is scored separately from answers. A smaller version runs in CI as a regression guard, and fails if leakage is ever above 0 or refusals drop.

## When not to trust it
- **Questions spanning many documents** ("total of all unpaid invoices"). Retrieval returns 8 passages; aggregation needs the structured extraction export instead.
- **Scanned documents with OCR confidence below ~80 %** (shown on the page view).
- **Documents in languages other than EN/FR/ES**, or English questions about non-English documents in offline mode.
- **Exact legal or financial decisions.** Use the citation to check the original page.

## Data handling
- **Offline mode:** nothing leaves the server.
- **Claude mode:** the question, recent conversation turns and the retrieved passages (not whole documents) are sent to Anthropic's API for each question. Whole document text, truncated, is sent once for summary, classification and field extraction during processing. The UI shows which mode is active.
