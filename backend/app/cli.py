"""Command-line tools: ``python -m app.cli <command>``.

migrate                     apply database migrations
create-user --email E --role R   create a user (password from DOCAI_USER_PASSWORD or prompt)
generate-corpus --out DIR   write the synthetic corpus + manifest
load-demo                   create demo users alice/bob/admin and upload the corpus through the real pipeline
evaluate --out DIR          run the evaluation on the loaded demo corpus and write the report
worker                      run the background worker
drain                       process all queued jobs, then exit
demo                        migrate + load-demo + drain (one-command setup)
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging

DEMO_USERS = {"alice": "member", "bob": "member", "admin": "admin"}
CORPUS_DIR = Path(os.environ.get("DOCAI_CORPUS_DIR", "./var/corpus"))


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
    print("migrations applied")


def _create_user(email: str, role: str, password: str | None = None) -> None:
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    password = password or os.environ.get("DOCAI_USER_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    if len(password) < 12:
        sys.exit("password must be at least 12 characters")
    with session_scope() as db:
        if db.scalar(select(User).where(User.email == email.lower())):
            print(f"user {email} exists — skipped")
            return
        db.add(
            User(
                email=email.lower(),
                full_name=email.split("@")[0].title(),
                password_hash=hash_password(password),
                role=role,
            )
        )
    print(f"user {email} ({role}) created")


def load_demo(corpus_dir: Path = CORPUS_DIR, scale: float = 1.0) -> dict[str, str]:
    """Generate the corpus (if needed) and upload every file as its owner. Returns filename → document id."""
    from sqlalchemy import select

    from app.db.models import User
    from app.db.session import session_scope
    from app.modules.documents.service import create_document
    from app.synthetic.corpus import build_corpus

    password = os.environ.get("DOCAI_DEMO_PASSWORD")
    if not password:
        sys.exit("set DOCAI_DEMO_PASSWORD (min 12 chars) for the demo users")
    for name, role in DEMO_USERS.items():
        _create_user(f"{name}@example.com", role, password)
    manifest_path = corpus_dir / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text()) if manifest_path.exists() else build_corpus(corpus_dir, scale=scale)
    )
    ids: dict[str, str] = {}
    with session_scope() as db:
        users = {u.email.split("@")[0]: u for u in db.scalars(select(User))}
        for d in manifest["documents"]:
            data = (corpus_dir / d["filename"]).read_bytes()
            doc, _, _ = create_document(db, users[d["owner"]].id, data, d["filename"], d["title"], None)
            ids[d["filename"]] = str(doc.id)
    (corpus_dir / "loaded_ids.json").write_text(json.dumps(ids))
    print(f"uploaded {len(ids)} documents; processing is queued (run `drain` or the worker)")
    return ids


def _evaluate(out: Path) -> None:
    import uuid

    from app.db.session import new_session
    from app.evaluation.run import evaluate, write_report

    manifest = json.loads((CORPUS_DIR / "manifest.json").read_text())
    ids = {k: uuid.UUID(v) for k, v in json.loads((CORPUS_DIR / "loaded_ids.json").read_text()).items()}
    db = new_session()
    try:
        report = evaluate(db, manifest, ids)
    finally:
        db.close()
    write_report(report, out)
    print(json.dumps({"retrieval": report["retrieval"], "answers": report["answers"]["overall"]}, indent=1))


def main(argv: list[str] | None = None) -> None:
    configure_logging(get_settings().log_level, as_json=False)
    p = argparse.ArgumentParser(
        prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    cu = sub.add_parser("create-user")
    cu.add_argument("--email", required=True)
    cu.add_argument("--role", choices=["admin", "member", "viewer"], default="member")
    gc = sub.add_parser("generate-corpus")
    gc.add_argument("--out", type=Path, default=CORPUS_DIR)
    gc.add_argument("--scale", type=float, default=1.0)
    ld = sub.add_parser("load-demo")
    ld.add_argument("--scale", type=float, default=1.0)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--out", type=Path, default=Path("../ml/reports"))
    sub.add_parser("worker")
    sub.add_parser("drain")
    demo = sub.add_parser("demo")
    demo.add_argument("--scale", type=float, default=1.0)
    args = p.parse_args(argv)

    if args.cmd == "migrate":
        _migrate()
    elif args.cmd == "create-user":
        _create_user(args.email, args.role)
    elif args.cmd == "generate-corpus":
        from app.synthetic.corpus import build_corpus

        m = build_corpus(args.out, scale=args.scale)
        print(f"{len(m['documents'])} documents, {len(m['questions'])} questions → {args.out}")
    elif args.cmd == "load-demo":
        load_demo(scale=args.scale)
    elif args.cmd == "evaluate":
        _evaluate(args.out)
    elif args.cmd == "worker":
        from app.jobs.worker import run_forever

        run_forever()
    elif args.cmd in ("drain", "demo"):
        if args.cmd == "demo":
            _migrate()
            load_demo(scale=args.scale)
        from app.jobs.worker import drain

        print(f"processed {drain()} jobs")


if __name__ == "__main__":
    main()
