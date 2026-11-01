import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { Collection, DocumentSummary, Page } from "../api/types";
import { Badge, Button, Card, Empty, ErrorBox, Input, Loading, PageHeader, Select, SourceTag, Table } from "../components/ui";
import { useAuth } from "../lib/auth";
import { bytes, dateTime, humanize, STATUS_TONE } from "../lib/format";

const TYPES = ["invoice", "contract", "receipt", "resume", "report", "letter", "policy", "other"];
const PAGE = 25;

export function LibraryPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [collection, setCollection] = useState("");
  const [owned, setOwned] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (q) params.set("q", q);
  if (type) params.set("type", type);
  if (collection) params.set("collection_id", collection);
  if (owned) params.set("owned", owned);
  const docs = useQuery({
    queryKey: ["documents", params.toString()],
    queryFn: () => api<Page<DocumentSummary>>(`/documents?${params}`),
    // Keep polling while anything is still being processed.
    refetchInterval: (query) =>
      query.state.data?.items.some((d) => ["queued", "processing"].includes(d.current_version?.status ?? "")) ? 2000 : false,
  });
  const collections = useQuery({ queryKey: ["collections"], queryFn: () => api<Collection[]>("/collections") });

  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id].slice(-5)));

  return (
    <>
      <PageHeader title="Library" subtitle={docs.data ? `${docs.data.total} documents you can access` : undefined}
        actions={<>
          <Button variant="secondary" disabled={selected.length < 1}
            onClick={() => navigate(`/ask?${selected.map((s) => `doc=${s}`).join("&")}`)}>Ask about selected ({selected.length})</Button>
          <Button variant="secondary" disabled={selected.length < 2}
            onClick={() => navigate(`/analysis?${selected.map((s) => `doc=${s}`).join("&")}`)}>Compare</Button>
        </>} />
      {can("member") && <UploadCard collections={collections.data ?? []} />}
      <Card className="mt-6">
        <div className="mb-4 grid gap-3 sm:grid-cols-4">
          <Input aria-label="Search titles" placeholder="Search titles…" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
          <Select aria-label="Document type" value={type} onChange={(e) => { setType(e.target.value); setOffset(0); }}>
            <option value="">All types</option>
            {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
          <Select aria-label="Collection" value={collection} onChange={(e) => { setCollection(e.target.value); setOffset(0); }}>
            <option value="">All collections</option>
            {collections.data?.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.document_count})</option>)}
          </Select>
          <Select aria-label="Ownership" value={owned} onChange={(e) => { setOwned(e.target.value); setOffset(0); }}>
            <option value="">Mine and shared with me</option>
            <option value="true">Only mine</option>
            <option value="false">Shared with me</option>
          </Select>
        </div>
        {docs.isLoading ? <Loading /> : docs.error ? <ErrorBox error={docs.error} /> : !docs.data!.items.length ? (
          <Empty>No documents yet. {can("member") ? "Upload one above." : ""}</Empty>
        ) : (
          <>
            <Table>
              <thead><tr><th><span className="sr-only">Select</span></th><th>Title</th><th>Type</th><th>Status</th><th>Pages</th><th>Size</th><th>Access</th><th>Added</th></tr></thead>
              <tbody>
                {docs.data!.items.map((d) => {
                  const v = d.current_version;
                  return (
                    <tr key={d.id} className="hover:bg-slate-50">
                      <td><input type="checkbox" aria-label={`Select ${d.title}`} checked={selected.includes(d.id)} onChange={() => toggle(d.id)} /></td>
                      <td className="max-w-xs"><Link to={`/documents/${d.id}`} className="font-medium text-sky-700 hover:underline">{d.title}</Link>
                        <div className="truncate text-xs text-slate-500">{v?.filename}</div></td>
                      <td>{d.doc_type ? <span className="flex items-center gap-1 capitalize">{d.doc_type}<SourceTag source={d.doc_type_source} /></span> : "—"}</td>
                      <td>{v && <Badge tone={STATUS_TONE[v.status]}>{v.status === "processing" ? `processing: ${humanize(v.stage)}` : v.status}</Badge>}</td>
                      <td className="tabular-nums">{v?.page_count ?? "—"}</td>
                      <td className="tabular-nums">{v ? bytes(v.size_bytes) : "—"}</td>
                      <td>{d.access === "owner" ? "Owner" : <span className="text-xs">Shared by {d.owner_email} ({d.access})</span>}</td>
                      <td className="whitespace-nowrap text-slate-600">{dateTime(d.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
            <div className="mt-4 flex items-center justify-between text-sm text-slate-600">
              <span>{offset + 1}–{Math.min(offset + PAGE, docs.data!.total)} of {docs.data!.total}</span>
              <div className="flex gap-2">
                <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>Previous</Button>
                <Button variant="secondary" disabled={offset + PAGE >= docs.data!.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
              </div>
            </div>
          </>
        )}
      </Card>
    </>
  );
}

function UploadCard({ collections }: { collections: Collection[] }) {
  const qc = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const [collection, setCollection] = useState("");
  const [newCollection, setNewCollection] = useState("");
  const [results, setResults] = useState<{ name: string; ok: boolean; message: string }[]>([]);
  const upload = useMutation({
    mutationFn: async () => {
      const out: { name: string; ok: boolean; message: string }[] = [];
      for (const f of files) {
        const form = new FormData();
        form.append("file", f);
        if (collection) form.append("collection_id", collection);
        try {
          const r = await api<{ duplicate_of: string | null }>("/documents", { form });
          out.push({ name: f.name, ok: true, message: r.duplicate_of ? "uploaded — identical to a document you already have" : "uploaded, processing…" });
        } catch (e) {
          out.push({ name: f.name, ok: false, message: e instanceof ApiError ? e.message : "upload failed" });
        }
      }
      return out;
    },
    onSuccess: (out) => { setResults(out); setFiles([]); void qc.invalidateQueries({ queryKey: ["documents"] }); },
  });
  const createCollection = useMutation({
    mutationFn: () => api<Collection>("/collections", { body: { name: newCollection } }),
    onSuccess: (c) => { setCollection(c.id); setNewCollection(""); void qc.invalidateQueries({ queryKey: ["collections"] }); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (files.length) upload.mutate(); };
  return (
    <Card title="Upload documents">
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-[1fr_220px_auto]">
        <input type="file" multiple aria-label="Files" accept=".pdf,.docx,.txt,.png,.jpg,.jpeg,.tif,.tiff"
          onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          className="block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm" />
        <Select aria-label="Upload into collection" value={collection} onChange={(e) => setCollection(e.target.value)}>
          <option value="">No collection</option>
          {collections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>
        <Button type="submit" disabled={!files.length} busy={upload.isPending}>Upload {files.length > 1 ? `${files.length} files` : ""}</Button>
      </form>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <Input aria-label="New collection name" placeholder="New collection…" value={newCollection} className="max-w-xs"
          onChange={(e) => setNewCollection(e.target.value)} />
        <Button variant="secondary" disabled={!newCollection.trim()} busy={createCollection.isPending} onClick={() => createCollection.mutate()}>Create collection</Button>
        <span className="text-xs text-slate-500">PDF, Word, text or images (scans are OCR'd). Max 25 MB, 300 pages.</span>
      </div>
      <ErrorBox error={createCollection.error} />
      {results.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm" aria-live="polite">
          {results.map((r, i) => <li key={i} className={r.ok ? "text-emerald-700" : "text-red-700"}>{r.name}: {r.message}</li>)}
        </ul>
      )}
    </Card>
  );
}
