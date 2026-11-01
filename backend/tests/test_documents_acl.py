"""Documents API and access control. The central guarantee: one user's documents never
reach another user — through listing, detail, pages, download, search, Q&A, citations,
comparison, duplicates or exports — unless explicitly shared."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from app.db.models import DocumentVersion, metadata
from app.db.session import get_engine, new_session
from tests.conftest import INVOICE_TXT, drain_jobs, upload

POLICY = (
    b"Northwind Remote Work Policy\n\nPurpose\nThis policy applies to all employees.\n\nPolicy\n"
    b"Employees may work remotely up to 3 days per week with manager approval.\n"
)
SECRET = (
    b"Project Falcon Budget Memo\n\nThe confidential budget for Project Falcon is 4,750,000 USD, approved by "
    b"the board on 2026-02-14. Do not distribute.\n"
)


def test_schema_matches_models(clean_db):
    with get_engine().connect() as conn:
        mc = MigrationContext.configure(
            conn, opts={"include_object": lambda o, n, t, r, c: n != "alembic_version", "compare_type": True}
        )
        assert compare_metadata(mc, metadata) == []


def test_upload_processes_and_details(client, auth):
    h = auth("alice")
    doc = upload(client, h, "invoice.txt", INVOICE_TXT.encode())["document"]
    assert doc["current_version"]["status"] == "queued" and doc["access"] == "owner"
    assert drain_jobs() == 1
    d = client.get(f"/api/v1/documents/{doc['id']}", headers=h).json()
    v = d["current_version"]
    assert v["status"] == "ready" and v["page_count"] == 1 and v["language"] == "en"
    assert set(v["stages"]) == {"extract", "classify", "index", "understand", "deduplicate"}
    assert all(s["status"] == "done" for s in v["stages"].values())
    assert d["doc_type"] == "invoice" and d["doc_type_source"] == "rule:keywords"
    assert (
        d["fields"]["kind"] == "invoice"
        and d["fields"]["data"]["total"] == 108.0
        and d["fields"]["validation"]["valid"]
    )
    assert d["summary"]["source"] == "rule:extractive-summary"
    page = client.get(f"/api/v1/documents/{doc['id']}/pages/1", headers=h).json()
    assert "INV-7781" in page["text"] and page["page_count"] == 1


def test_role_and_validation_rules(client, auth):
    assert (
        client.post(
            "/api/v1/documents", headers=auth("val", "viewer"), files={"file": ("a.txt", b"hello world")}
        ).status_code
        == 403
    )
    h = auth("alice")
    r = client.post("/api/v1/documents", headers=h, files={"file": ("x.pdf", b"MZ\x90\x00 not a pdf \x00\x01")})
    assert r.status_code == 422 and "Unsupported" in r.json()["detail"]
    assert client.get("/api/v1/documents").status_code == 401


def test_other_users_documents_are_invisible(client, auth):
    alice, bob = auth("alice"), auth("bob")
    secret = upload(client, bob, "secret.txt", SECRET)["document"]
    upload(client, alice, "policy.txt", POLICY)
    drain_jobs()
    sid = secret["id"]
    # Listing, detail, pages, download, chunks: all 404 (not 403 — existence is not revealed).
    assert client.get("/api/v1/documents", headers=alice).json()["total"] == 1
    for path in (
        f"/documents/{sid}",
        f"/documents/{sid}/pages/1",
        f"/documents/{sid}/file",
        f"/documents/{sid}/chunks",
    ):
        assert client.get(f"/api/v1{path}", headers=alice).status_code == 404, path
    assert client.patch(f"/api/v1/documents/{sid}", headers=alice, json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/v1/documents/{sid}", headers=alice).status_code == 404
    # Search and Q&A: nothing from bob's memo, even when asked directly or by id.
    hits = client.post("/api/v1/search", headers=alice, json={"query": "Project Falcon confidential budget"}).json()
    assert all(x["document_id"] != sid for x in hits)
    assert client.post("/api/v1/search", headers=alice, json={"query": "budget", "document_ids": [sid]}).json() == []
    conv = client.post("/api/v1/conversations", headers=alice, json={}).json()
    ans = client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        headers=alice,
        json={"question": "What is the budget for Project Falcon?"},
    ).json()
    assert ans["status"] == "not_found" and ans["citations"] == []
    assert client.post("/api/v1/conversations", headers=alice, json={"document_ids": [sid]}).status_code == 404
    assert client.post("/api/v1/compare", headers=alice, json={"document_ids": [sid, sid]}).status_code == 404
    # Bob can find it.
    ans = client.post(
        f"/api/v1/conversations/{client.post('/api/v1/conversations', headers=bob, json={}).json()['id']}/messages",
        headers=bob,
        json={"question": "What is the budget for Project Falcon?"},
    ).json()
    assert ans["status"] == "answered" and "4,750,000" in ans["answer"] and ans["citations"][0]["document_id"] == sid


def test_sharing_grants_and_revokes_access(client, auth):
    alice, bob = auth("alice"), auth("bob")
    sid = upload(client, bob, "secret.txt", SECRET)["document"]["id"]
    drain_jobs()
    assert client.put(f"/api/v1/documents/{sid}/shares", headers=alice, json=[]).status_code == 404
    r = client.put(f"/api/v1/documents/{sid}/shares", headers=bob, json=[{"email": "alice@example.com"}])
    assert r.status_code == 200
    d = client.get(f"/api/v1/documents/{sid}", headers=alice).json()
    assert d["access"] == "read" and d["shares"] is None  # only the owner sees the share list
    conv = client.post("/api/v1/conversations", headers=alice, json={}).json()
    ans = client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        headers=alice,
        json={"question": "What is the budget for Project Falcon?"},
    ).json()
    assert ans["status"] == "answered" and ans["citations"]
    # Read access cannot edit, version or delete.
    assert client.patch(f"/api/v1/documents/{sid}", headers=alice, json={"title": "x"}).status_code == 403
    assert client.delete(f"/api/v1/documents/{sid}", headers=alice).status_code == 403
    # Revoke: the document disappears, and old citations are hidden from history.
    client.put(f"/api/v1/documents/{sid}/shares", headers=bob, json=[])
    assert client.get(f"/api/v1/documents/{sid}", headers=alice).status_code == 404
    hist = client.get(f"/api/v1/conversations/{conv['id']}/messages", headers=alice).json()
    assert all(m["citations"] == [] for m in hist)


def test_edit_share_allows_new_version_and_diff(client, auth):
    alice, bob = auth("alice"), auth("bob")
    did = upload(client, alice, "policy.txt", POLICY)["document"]["id"]
    client.put(
        f"/api/v1/documents/{did}/shares", headers=alice, json=[{"email": "bob@example.com", "permission": "edit"}]
    )
    r = client.post(
        f"/api/v1/documents/{did}/versions",
        headers=bob,
        files={"file": ("policy-v2.txt", POLICY.replace(b"3 days", b"4 days"))},
    )
    assert r.status_code == 202
    drain_jobs()
    d = client.get(f"/api/v1/documents/{did}", headers=alice).json()
    assert [v["version_no"] for v in d["versions"]] == [2, 1]
    diff = client.get(f"/api/v1/documents/{did}/diff?base=1&other=2", headers=alice).json()
    assert diff["added"] == 1 and diff["removed"] == 1 and any("4 days" in line for line in diff["diff"])
    # Search uses only the current version.
    hits = client.post("/api/v1/search", headers=alice, json={"query": "remote work days per week"}).json()
    assert hits and all("3 days" not in h["text"] for h in hits)


def test_download_headers_and_owner_delete(client, auth):
    h = auth("alice")
    did = upload(client, h, "inv<script>.txt", INVOICE_TXT.encode())["document"]["id"]
    r = client.get(f"/api/v1/documents/{did}/file", headers=h)
    assert r.content == INVOICE_TXT.encode()
    assert r.headers["content-disposition"] == 'attachment; filename="invscript.txt"'
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.delete(f"/api/v1/documents/{did}", headers=h).status_code == 204
    with new_session() as s:
        assert s.scalar(select(DocumentVersion)) is None


def test_collections_and_duplicate_detection(client, auth):
    h = auth("alice")
    coll = client.post("/api/v1/collections", headers=h, json={"name": "Finance"}).json()
    assert client.post("/api/v1/collections", headers=h, json={"name": "Finance"}).status_code == 409
    first = upload(client, h, "a.txt", INVOICE_TXT.encode(), collection_id=coll["id"])
    second = upload(client, h, "b.txt", INVOICE_TXT.encode())
    assert first["duplicate_of"] is None and second["duplicate_of"] == first["document"]["id"]
    near = INVOICE_TXT.replace("Payment terms: Net 30.", "Payment terms: Net 30. Thank you!")
    upload(client, h, "c.txt", near.encode())
    drain_jobs()
    assert client.get(f"/api/v1/documents?collection_id={coll['id']}", headers=h).json()["total"] == 1
    dup = client.get("/api/v1/duplicates", headers=h).json()
    assert len(dup["exact"]) == 1 and len(dup["exact"][0]) == 2
    assert dup["near"] and dup["near"][0]["similarity"] >= 0.8
    # Another user's collection can't be used.
    other = client.post("/api/v1/collections", headers=auth("bob"), json={"name": "Bob"}).json()
    r = client.post(
        "/api/v1/documents", headers=h, files={"file": ("d.txt", b"hello there")}, data={"collection_id": other["id"]}
    )
    assert r.status_code == 404


def test_compare_and_csv_export(client, auth):
    h = auth("alice")
    a = upload(client, h, "a.txt", INVOICE_TXT.encode(), title="=HYPERLINK(evil)")["document"]["id"]
    b = upload(client, h, "b.txt", INVOICE_TXT.replace("108.00", "109.00").replace("8.00", "9.00").encode())[
        "document"
    ]["id"]
    drain_jobs()
    cmp = client.post("/api/v1/compare", headers=h, json={"document_ids": [a, b]}).json()
    pair = cmp["pairs"][0]
    assert {d["field"] for d in pair["field_differences"]} == {"tax", "total"}
    assert 0.8 < pair["text_similarity"] < 1.0
    csv_text = client.get("/api/v1/reports/extractions.csv?kind=invoice", headers=h).text
    assert "'=HYPERLINK(evil)" in csv_text and "\n=HYPERLINK" not in csv_text
    assert csv_text.count("INV-7781") == 2


def test_failed_processing_is_reported(client, auth):
    h = auth("alice")
    did = upload(client, h, "broken.pdf", b"%PDF-1.7\nthis is not really a pdf")["document"]["id"]
    from app.db.models import Job

    with new_session() as s:  # make the retry immediate so the test doesn't wait for backoff
        for _ in range(3):
            drain_jobs()
            for j in s.scalars(select(Job)):
                j.run_after = j.created_at
            s.commit()
    v = client.get(f"/api/v1/documents/{did}", headers=h).json()["current_version"]
    assert v["status"] == "failed" and v["error"].startswith("extract:")
    assert v["stages"]["extract"]["status"] == "failed"
