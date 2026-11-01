"""Evaluation harness. Every reported number comes from here.

Measured separately, on the labelled synthetic corpus:
* **Retrieval** — recall@5 and MRR of the gold page, for keyword-only, vector-only and hybrid.
* **Answers** — correctness (gold answer string appears in the answer), citation accuracy
  (a citation points at the gold document and page), refusal accuracy on unanswerable
  questions, and **leakage** (any citation to a document the asker doesn't own; must be 0).
* **Understanding** — classification accuracy, invoice/contract field accuracy, OCR
  character error rate on scanned copies vs. the text of their digital originals.

LLM-dependent rows depend on the active provider (``extractive`` offline or Claude) and the
report states which one produced them.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.providers import get_llm
from app.ai.textutil import identifiers
from app.core.config import get_settings
from app.db.models import Conversation, Document, DocumentVersion, Extraction, Page, User
from app.retrieval.qa import answer_question
from app.retrieval.search import documents_containing, search


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9.]", "", s.lower().replace(",", ""))


def _cer(ref: str, hyp: str) -> float:
    """Character error rate (Levenshtein distance / reference length), whitespace-normalised."""
    a, b = " ".join(ref.split()), " ".join(hyp.split())
    if not a:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1] / len(a)


def evaluate(db: Session, manifest: dict[str, Any], doc_ids: dict[str, uuid.UUID]) -> dict[str, Any]:
    """``doc_ids`` maps corpus filename → loaded document id."""
    t0 = time.perf_counter()
    users = {u.email.split("@")[0]: u for u in db.scalars(select(User))}
    by_file = {d["filename"]: d for d in manifest["documents"]}
    id_to_file = {v: k for k, v in doc_ids.items()}

    def same_facts(cited: str | None, gold: str | None) -> bool:
        """A scanned copy and its digital original carry the same facts; either is a correct source."""
        if cited is None or gold is None:
            return False
        strip = lambda f: f.replace("_scan.png", "").rsplit(".", 1)[0]  # noqa: E731
        return strip(cited) == strip(gold)

    report: dict[str, Any] = {
        "provider": get_llm().name,
        "embedding_model": get_settings().embedding_model,
        "corpus": {
            "documents": len(manifest["documents"]),
            "questions": len(manifest["questions"]),
            "seed": manifest["seed"],
            "scale": manifest["scale"],
        },
    }

    # ---- retrieval
    retrieval: dict[str, dict[str, float]] = {}
    answerable = [q for q in manifest["questions"] if q["answer"] is not None]
    for mode in ("keyword", "vector", "hybrid", "hybrid+entity"):
        hits_at5 = rr = 0.0
        for q in answerable:
            uid = users[q["asker"]].id
            if mode == "hybrid+entity":  # what question answering actually uses
                idents = identifiers(q["question"])
                scope = documents_containing(db, uid, idents) if idents else None
                hits = search(db, uid, q["question"], limit=10, document_ids=scope) if scope != [] else []
            else:
                hits = search(db, uid, q["question"], limit=10, mode=mode)
            rank = next(
                (
                    i + 1
                    for i, h in enumerate(hits)
                    if same_facts(id_to_file.get(h.document_id), q["gold_file"]) and h.page_no == q["gold_page"]
                ),
                None,
            )
            hits_at5 += 1 if rank and rank <= 5 else 0
            rr += 1 / rank if rank else 0
        retrieval[mode] = {"recall_at_5": round(hits_at5 / len(answerable), 3), "mrr": round(rr / len(answerable), 3)}
    report["retrieval"] = retrieval

    # ---- answers
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    eval_conversations: list[uuid.UUID] = []
    for q in manifest["questions"]:
        asker = users[q["asker"]]
        conv = Conversation(user_id=asker.id, title=f"eval {q['id']}")
        db.add(conv)
        db.flush()
        eval_conversations.append(conv.id)
        r = answer_question(db, asker.id, conv, q["question"])
        cited_files = [id_to_file.get(c.document_id) for c in r.citations]
        leaked = any(f and by_file[f]["owner"] != q["asker"] for f in cited_files)
        row = {"id": q["id"], "status": r.status, "leaked": leaked}
        if q["answer"] is None:
            row["correct_refusal"] = r.status == "not_found"
        else:
            row["correct"] = r.status == "answered" and _norm(q["answer"]) in _norm(r.answer)
            row["citation_hit"] = any(
                same_facts(f, q["gold_file"]) and c.page_no == q["gold_page"]
                for f, c in zip(cited_files, r.citations, strict=True)
            )
        by_kind[q["kind"]].append(row)
    # answer_question commits, so remove the evaluation's conversations explicitly
    # (messages and citations cascade) — running an evaluation must not pollute user data.
    db.execute(delete(Conversation).where(Conversation.id.in_(eval_conversations)))
    db.commit()

    def rate(rows: list[dict[str, Any]], key: str) -> float | None:
        vals = [r[key] for r in rows if key in r]
        return round(sum(vals) / len(vals), 3) if vals else None

    all_rows = [r for rows in by_kind.values() for r in rows]
    report["answers"] = {
        "overall": {
            "answer_correct": rate(all_rows, "correct"),
            "citation_on_gold_page": rate(all_rows, "citation_hit"),
            "refusal_correct": rate(all_rows, "correct_refusal"),
            "leakage_count": sum(r["leaked"] for r in all_rows),
            "questions": len(all_rows),
        },
        "by_kind": {
            k: {
                "n": len(v),
                "answer_correct": rate(v, "correct"),
                "citation_on_gold_page": rate(v, "citation_hit"),
                "refusal_correct": rate(v, "correct_refusal"),
                "leaked": sum(r["leaked"] for r in v),
            }
            for k, v in sorted(by_kind.items())
        },
    }

    # ---- understanding
    cls_ok = cls_n = 0
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    field_hits: dict[str, list[bool]] = defaultdict(list)
    cers = []
    for fname, did in doc_ids.items():
        spec = by_file[fname]
        doc = db.get(Document, did)
        if doc is None:
            continue
        if spec["doc_type"] != "other":
            cls_n += 1
            cls_ok += doc.doc_type == spec["doc_type"]
            confusion[spec["doc_type"]][doc.doc_type or "none"] += 1
        ex = db.scalar(
            select(Extraction).where(
                Extraction.version_id == doc.current_version_id, Extraction.kind.in_(["invoice", "contract"])
            )
        )
        f = spec["facts"]
        if spec["doc_type"] == "invoice" and ex is not None:
            for k in ("invoice_number", "invoice_date", "due_date", "total", "subtotal", "tax", "vendor", "customer"):
                field_hits[f"invoice.{k}"].append(ex.data.get(k) == f[k])
        if spec["doc_type"] == "contract" and ex is not None and not spec.get("near_duplicate_of"):
            field_hits["contract.parties"].append(ex.data.get("parties") == f["parties"])
            field_hits["contract.effective_date"].append(ex.data.get("effective_date") == f["effective_date"])
            field_hits["contract.termination_notice_days"].append(
                ex.data.get("termination_notice_days") == f["notice_days"]
            )
            field_hits["contract.governing_law"].append(ex.data.get("governing_law") == f["governing_law"])
            field_hits["contract.auto_renewal"].append(ex.data.get("auto_renewal") == f["auto_renewal"])
        if spec["scanned"]:
            original = fname.replace("_scan.png", ".pdf")
            if original in doc_ids:
                o = db.get(Document, doc_ids[original])
                if o is None:
                    continue
                ref = " ".join(p.text for p in db.scalars(select(Page).where(Page.version_id == o.current_version_id)))
                hyp = " ".join(
                    p.text for p in db.scalars(select(Page).where(Page.version_id == doc.current_version_id))
                )
                # Compare the non-table text only: digital tables are rendered as Markdown, scans are not.
                cers.append(_cer(ref.split("|")[0], hyp[: len(ref.split("|")[0]) + 50]))
    report["understanding"] = {
        "classification_accuracy": round(cls_ok / cls_n, 3) if cls_n else None,
        "classified": cls_n,
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "field_accuracy": {k: round(sum(v) / len(v), 3) for k, v in sorted(field_hits.items())},
        "ocr_cer_mean": round(sum(cers) / len(cers), 4) if cers else None,
        "ocr_samples": len(cers),
    }
    statuses = db.execute(select(DocumentVersion.status)).scalars().all()
    report["processing"] = {s: statuses.count(s) for s in set(statuses)}
    report["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    return report


def render_markdown(r: dict[str, Any]) -> str:
    a, ret, u = r["answers"], r["retrieval"], r["understanding"]
    lines = [
        "# Evaluation report",
        "",
        f"Generated by `python -m app.cli evaluate` on the synthetic corpus (seed {r['corpus']['seed']}, "
        f"{r['corpus']['documents']} documents, {r['corpus']['questions']} questions). "
        f"**Answer provider: `{r['provider']}`**; embeddings: `{r['embedding_model']}`. All documents are synthetic; "
        "these numbers describe this corpus, not real-world performance.",
        "",
        "## Retrieval (gold page in results)",
        "",
        "| Mode | Recall@5 | MRR |",
        "|---|---|---|",
        *[f"| {m} | {v['recall_at_5']} | {v['mrr']} |" for m, v in ret.items()],
        "",
        "## Answers",
        "",
        f"- Answer contains the gold value: **{a['overall']['answer_correct']}**",
        f"- At least one citation on the gold page: **{a['overall']['citation_on_gold_page']}**",
        f'- Correct "not found" on unanswerable/other-user questions: **{a["overall"]["refusal_correct"]}**',
        f"- Citations to another user's documents (leakage): **{a['overall']['leakage_count']}**",
        "",
        "| Question kind | n | Correct | Citation on gold page | Correct refusal | Leaked |",
        "|---|---|---|---|---|---|",
        *[
            f"| {k} | {v['n']} | {v['answer_correct']} | {v['citation_on_gold_page']} "
            f"| {v['refusal_correct']} | {v['leaked']} |"
            for k, v in a["by_kind"].items()
        ],
        "",
        "## Document understanding",
        "",
        f"- Classification accuracy: **{u['classification_accuracy']}** ({u['classified']} documents)",
        f"- OCR character error rate (scans vs. digital originals): **{u['ocr_cer_mean']}** ({u['ocr_samples']} scans)",
        "",
        "| Field | Accuracy |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in u["field_accuracy"].items()],
        "",
        f"_Processing status: {r['processing']} · elapsed {r['elapsed_seconds']} s_",
        "",
    ]
    return "\n".join(lines)


def write_report(r: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "evaluation.json").write_text(json.dumps(r, indent=2, default=str))
    (out_dir / "evaluation.md").write_text(render_markdown(r))
