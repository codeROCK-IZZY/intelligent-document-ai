"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-11-02
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email              varchar(320) NOT NULL,
    full_name          varchar(200) NOT NULL DEFAULT '',
    password_hash      varchar(255) NOT NULL,
    role               varchar(20)  NOT NULL CHECK (role IN ('admin', 'member', 'viewer')),
    is_active          boolean      NOT NULL DEFAULT true,
    failed_login_count integer      NOT NULL DEFAULT 0,
    locked_until       timestamptz,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_email_lower CHECK (email = lower(email))
);
ALTER TABLE users RENAME CONSTRAINT users_pkey TO pk_users;

CREATE TABLE refresh_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL,
    token_hash  varchar(64) NOT NULL,
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_refresh_tokens_token_hash UNIQUE (token_hash),
    CONSTRAINT fk_refresh_tokens_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
ALTER TABLE refresh_tokens RENAME CONSTRAINT refresh_tokens_pkey TO pk_refresh_tokens;
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);

CREATE TABLE collections (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   uuid NOT NULL,
    name       varchar(200) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_collections_owner_name UNIQUE (owner_id, name),
    CONSTRAINT fk_collections_owner_id_users FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE
);
ALTER TABLE collections RENAME CONSTRAINT collections_pkey TO pk_collections;

CREATE TABLE documents (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id           uuid NOT NULL,
    collection_id      uuid,
    title              varchar(300) NOT NULL,
    doc_type           varchar(20) CHECK (doc_type IN
                       ('invoice', 'contract', 'receipt', 'resume', 'report', 'letter', 'policy', 'other')),
    doc_type_source    varchar(100),
    current_version_id uuid,  -- no FK: documents <-> versions would be circular; maintained by the service
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_documents_owner_id_users FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_documents_collection_id_collections
        FOREIGN KEY (collection_id) REFERENCES collections (id) ON DELETE SET NULL
);
ALTER TABLE documents RENAME CONSTRAINT documents_pkey TO pk_documents;
CREATE INDEX ix_documents_owner_id ON documents (owner_id);

CREATE TABLE document_shares (
    document_id uuid NOT NULL,
    user_id     uuid NOT NULL,
    permission  varchar(10) NOT NULL DEFAULT 'read' CHECK (permission IN ('read', 'edit')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_document_shares PRIMARY KEY (document_id, user_id),
    CONSTRAINT fk_document_shares_document_id_documents
        FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE,
    CONSTRAINT fk_document_shares_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- The ONE place that defines who may read a document. Every search, retrieval,
-- citation and export query joins through this view.
CREATE VIEW readable_documents AS
    SELECT d.id AS document_id, d.owner_id AS user_id, 'owner'::varchar AS access FROM documents d
    UNION ALL
    SELECT s.document_id, s.user_id, s.permission FROM document_shares s;

CREATE TABLE document_versions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id  uuid NOT NULL,
    version_no   integer NOT NULL,
    filename     varchar(255) NOT NULL,
    file_key     varchar(500) NOT NULL,
    sha256       varchar(64) NOT NULL,
    mime         varchar(100) NOT NULL,
    size_bytes   bigint NOT NULL,
    page_count   integer,
    language     varchar(10),
    status       varchar(20) NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
    stage        varchar(30),
    stages       jsonb NOT NULL DEFAULT '{}'::jsonb,
    error        text,
    uploaded_by  uuid,
    created_at   timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    CONSTRAINT uq_document_versions_doc_version UNIQUE (document_id, version_no),
    CONSTRAINT fk_document_versions_document_id_documents
        FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE,
    CONSTRAINT fk_document_versions_uploaded_by_users
        FOREIGN KEY (uploaded_by) REFERENCES users (id) ON DELETE SET NULL
);
ALTER TABLE document_versions RENAME CONSTRAINT document_versions_pkey TO pk_document_versions;
CREATE INDEX ix_document_versions_sha256 ON document_versions (sha256);

CREATE TABLE pages (
    version_id     uuid NOT NULL,
    page_no        integer NOT NULL,
    text           text NOT NULL,
    ocr_used       boolean NOT NULL DEFAULT false,
    ocr_confidence double precision,
    tables         jsonb NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT pk_pages PRIMARY KEY (version_id, page_no),
    CONSTRAINT fk_pages_version_id_document_versions
        FOREIGN KEY (version_id) REFERENCES document_versions (id) ON DELETE CASCADE
);

CREATE TABLE chunks (
    id          bigserial PRIMARY KEY,
    version_id  uuid NOT NULL,
    page_no     integer NOT NULL,
    ordinal     integer NOT NULL,
    char_start  integer NOT NULL,
    char_end    integer NOT NULL,
    text        text NOT NULL,
    tsv         tsvector NOT NULL,
    embedding   vector(384) NOT NULL,
    CONSTRAINT fk_chunks_version_id_document_versions
        FOREIGN KEY (version_id) REFERENCES document_versions (id) ON DELETE CASCADE
);
ALTER TABLE chunks RENAME CONSTRAINT chunks_pkey TO pk_chunks;
CREATE INDEX ix_chunks_version_id ON chunks (version_id);
CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv);
CREATE INDEX ix_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE extractions (
    id         bigserial PRIMARY KEY,
    version_id uuid NOT NULL,
    kind       varchar(20) NOT NULL CHECK (kind IN ('invoice', 'contract', 'summary')),
    data       jsonb NOT NULL,
    source     varchar(100) NOT NULL,
    validation jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_extractions_version_kind UNIQUE (version_id, kind),
    CONSTRAINT fk_extractions_version_id_document_versions
        FOREIGN KEY (version_id) REFERENCES document_versions (id) ON DELETE CASCADE
);
ALTER TABLE extractions RENAME CONSTRAINT extractions_pkey TO pk_extractions;

CREATE TABLE entities (
    id          bigserial PRIMARY KEY,
    version_id  uuid NOT NULL,
    entity_type varchar(20) NOT NULL,
    value       varchar(500) NOT NULL,
    normalized  varchar(500),
    page_no     integer NOT NULL,
    CONSTRAINT fk_entities_version_id_document_versions
        FOREIGN KEY (version_id) REFERENCES document_versions (id) ON DELETE CASCADE
);
ALTER TABLE entities RENAME CONSTRAINT entities_pkey TO pk_entities;
CREATE INDEX ix_entities_version_id ON entities (version_id);

CREATE TABLE minhash_signatures (
    version_id uuid NOT NULL,
    signature  bytea NOT NULL,
    CONSTRAINT pk_minhash_signatures PRIMARY KEY (version_id),
    CONSTRAINT fk_minhash_signatures_version_id_document_versions
        FOREIGN KEY (version_id) REFERENCES document_versions (id) ON DELETE CASCADE
);

CREATE TABLE conversations (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL,
    title      varchar(300) NOT NULL DEFAULT 'New conversation',
    scope      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_conversations_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
ALTER TABLE conversations RENAME CONSTRAINT conversations_pkey TO pk_conversations;
CREATE INDEX ix_conversations_user_id ON conversations (user_id);

CREATE TABLE messages (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL,
    role            varchar(10) NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text NOT NULL,
    answer_status   varchar(20) CHECK (answer_status IN ('answered', 'not_found', 'refused', 'error')),
    provider        varchar(100),
    usage           jsonb NOT NULL DEFAULT '{}'::jsonb,
    latency_ms      integer,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_messages_conversation_id_conversations
        FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);
ALTER TABLE messages RENAME CONSTRAINT messages_pkey TO pk_messages;
CREATE INDEX ix_messages_conversation_id ON messages (conversation_id);

CREATE TABLE citations (
    id         bigserial PRIMARY KEY,
    message_id uuid NOT NULL,
    chunk_id   bigint NOT NULL,
    ordinal    integer NOT NULL,
    cited_text text NOT NULL,
    sentence   text NOT NULL DEFAULT '',
    CONSTRAINT fk_citations_message_id_messages
        FOREIGN KEY (message_id) REFERENCES messages (id) ON DELETE CASCADE,
    CONSTRAINT fk_citations_chunk_id_chunks FOREIGN KEY (chunk_id) REFERENCES chunks (id) ON DELETE CASCADE
);
ALTER TABLE citations RENAME CONSTRAINT citations_pkey TO pk_citations;
CREATE INDEX ix_citations_message_id ON citations (message_id);

CREATE TABLE jobs (
    id           bigserial PRIMARY KEY,
    job_type     varchar(50) NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    status       varchar(20) NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts     integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3,
    run_after    timestamptz NOT NULL DEFAULT now(),
    locked_at    timestamptz,
    finished_at  timestamptz,
    last_error   text,
    result       jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE jobs RENAME CONSTRAINT jobs_pkey TO pk_jobs;
CREATE INDEX ix_jobs_status_run_after ON jobs (status, run_after);

CREATE TABLE audit_log (
    id        bigserial PRIMARY KEY,
    user_id   uuid,
    action    varchar(100) NOT NULL,
    entity    varchar(50) NOT NULL,
    entity_id varchar(100),
    details   jsonb NOT NULL DEFAULT '{}'::jsonb,
    ts        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_audit_log_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
);
ALTER TABLE audit_log RENAME CONSTRAINT audit_log_pkey TO pk_audit_log;
"""

DOWNGRADE_SQL = """
DROP VIEW IF EXISTS readable_documents;
DROP TABLE IF EXISTS audit_log, jobs, citations, messages, conversations, minhash_signatures, entities,
    extractions, chunks, pages, document_versions, document_shares, documents, collections,
    refresh_tokens, users CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
