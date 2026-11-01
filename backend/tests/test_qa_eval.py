"""Grounded Q&A behaviour and an evaluation regression guard."""

import uuid
from pathlib import Path

import pytest

from app.ai.base import AnswerResult, CitedSentence
from app.db.models import Conversation
from app.db.session import new_session
from app.retrieval import qa
from tests.conftest import INVOICE_TXT, create_user, drain_jobs, reset_database, upload


class SpyProvider:
    """Records calls; returns a scripted result."""

    name = "spy"

    def __init__(self, result: AnswerResult):
        self.result, self.calls = result, 0

    def answer(self, question, passages, history):
        self.calls += 1
        return self.result

    def rewrite_question(self, question, history):
        return question

    def summarize(self, text):
        return None

    def classify(self, text, labels):
        return None

    def extract(self, kind, text, schema):
        return None


@pytest.fixture
def invoice_doc(client, auth):
    h = auth("alice")
    upload(client, h, "inv.txt", INVOICE_TXT.encode())
    drain_jobs()
    return h


def _conv(client, h):
    return client.post("/api/v1/conversations", headers=h, json={}).json()["id"]


def ask(client, h, cid, q):
    return client.post(f"/api/v1/conversations/{cid}/messages", headers=h, json={"question": q}).json()


def test_answer_with_citation_offsets(client, invoice_doc):
    h = invoice_doc
    r = ask(client, h, _conv(client, h), "What is the total due on invoice INV-7781?")
    assert r["status"] == "answered" and "108.00" in r["answer"] and r["provider"] == "extractive"
    c = r["citations"][0]
    page = client.get(f"/api/v1/documents/{c['document_id']}/pages/{c['page_no']}", headers=h).json()["text"]
    assert page[c["char_start"] : c["char_end"]] == c["cited_text"]  # exact highlight location
    assert all(s["supported"] for s in r["sentences"])


def test_unknown_entity_never_calls_the_model(client, invoice_doc, monkeypatch):
    spy = SpyProvider(AnswerResult("answered", [CitedSentence("x", [(0, "x")])], "spy"))
    monkeypatch.setattr(qa, "get_llm", lambda: spy)
    r = ask(client, invoice_doc, _conv(client, invoice_doc), "What is the total due on invoice INV-9999?")
    assert r["status"] == "not_found" and spy.calls == 0
    assert any("INV-9999" in n for n in r["notes"])


def test_uncited_answer_is_withheld(client, invoice_doc, monkeypatch):
    spy = SpyProvider(AnswerResult("answered", [CitedSentence("The total is $5 million.", [])], "spy"))
    monkeypatch.setattr(qa, "get_llm", lambda: spy)
    r = ask(client, invoice_doc, _conv(client, invoice_doc), "What is the total due on invoice INV-7781?")
    assert spy.calls == 1 and r["status"] == "not_found" and "5 million" not in r["answer"]
    assert any("no supporting citation" in n for n in r["notes"])


def test_provider_error_degrades_to_extractive(client, invoice_doc, monkeypatch):
    monkeypatch.setattr(qa, "get_llm", lambda: SpyProvider(AnswerResult("error", [], "spy")))
    r = ask(client, invoice_doc, _conv(client, invoice_doc), "What is the total due on invoice INV-7781?")
    assert r["status"] == "answered" and r["provider"] == "extractive" and "108.00" in r["answer"]
    assert any("unavailable" in n for n in r["notes"])


def test_conversation_history_and_scoping(client, invoice_doc, auth):
    h = invoice_doc
    cid = _conv(client, h)
    ask(client, h, cid, "What is the total due on invoice INV-7781?")
    hist = client.get(f"/api/v1/conversations/{cid}/messages", headers=h).json()
    assert [m["role"] for m in hist] == ["user", "assistant"] and hist[1]["citations"]
    assert client.get(f"/api/v1/conversations/{cid}/messages", headers=auth("bob")).status_code == 404
    convs = client.get("/api/v1/conversations", headers=h).json()
    assert convs[0]["title"].startswith("What is the total due")


ROLE_ENDPOINTS = [
    ("POST", "/api/v1/documents", "member"),
    ("POST", "/api/v1/collections", "member"),
    ("GET", "/api/v1/admin/stats", "admin"),
    ("GET", "/api/v1/admin/jobs", "admin"),
    ("GET", "/api/v1/users", "admin"),
]


@pytest.mark.parametrize(("method", "path", "minimum"), ROLE_ENDPOINTS)
def test_role_matrix(client, auth, method, path, minimum):
    assert client.request(method, path).status_code == 401
    lower = {"member": ["viewer"], "admin": ["viewer", "member"]}[minimum]
    for role in lower:
        assert client.request(method, path, headers=auth(f"u_{role}", role), json={}).status_code == 403


# ---------------------------------------------------------------- evaluation regression guard
@pytest.fixture(scope="module")
def small_corpus(tmp_path_factory):
    from app.modules.documents.service import create_document
    from app.synthetic.corpus import build_corpus

    reset_database()
    out: Path = tmp_path_factory.mktemp("corpus")
    manifest = build_corpus(out, seed=11, scale=0.25)
    users = {n: create_user(f"{n}@example.com") for n in ("alice", "bob")}
    ids = {}
    with new_session() as db:
        for d in manifest["documents"]:
            doc, _, _ = create_document(
                db, users[d["owner"]].id, (out / d["filename"]).read_bytes(), d["filename"], d["title"], None
            )
            ids[d["filename"]] = doc.id
        db.commit()
    drain_jobs()
    return manifest, ids


@pytest.mark.slow
def test_evaluation_regression_guard(small_corpus):
    """Fails CI if a change causes any leakage, loses refusals, or breaks extraction."""
    from app.evaluation.run import evaluate, render_markdown

    manifest, ids = small_corpus
    with new_session() as db:
        r = evaluate(db, manifest, ids)
    a, u = r["answers"]["overall"], r["understanding"]
    assert a["leakage_count"] == 0
    assert a["refusal_correct"] == 1.0
    assert a["answer_correct"] >= 0.75 and a["citation_on_gold_page"] >= 0.9
    assert r["retrieval"]["hybrid+entity"]["recall_at_5"] >= 0.95
    assert u["classification_accuracy"] >= 0.95 and min(u["field_accuracy"].values()) >= 0.9
    assert r["processing"] == {"ready": len(ids)}
    assert "# Evaluation report" in render_markdown(r)


def test_conversation_rows_cleaned_by_eval(small_corpus):
    with new_session() as db:
        assert db.query(Conversation).count() == 0  # evaluation does not persist its conversations
        assert uuid.UUID(str(next(iter(small_corpus[1].values()))))
