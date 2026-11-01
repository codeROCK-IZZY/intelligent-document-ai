import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SearchHit } from "../api/types";
import { Button, Card, Empty, ErrorBox, Input, PageHeader, Select } from "../components/ui";

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"hybrid" | "keyword" | "vector">("hybrid");
  const search = useMutation({ mutationFn: () => api<SearchHit[]>("/search", { body: { query, mode, limit: 20 } }) });
  const submit = (e: FormEvent) => { e.preventDefault(); if (query.trim()) search.mutate(); };
  return (
    <>
      <PageHeader title="Search" subtitle="Keyword and meaning-based search across every document you can access." />
      <Card>
        <form onSubmit={submit} className="flex flex-wrap gap-2">
          <Input aria-label="Search query" placeholder="e.g. termination notice, invoice INV-20260003, hotel allowance" value={query}
            onChange={(e) => setQuery(e.target.value)} className="min-w-[16rem] flex-1" />
          <Select aria-label="Search mode" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)} className="w-44">
            <option value="hybrid">Hybrid (recommended)</option><option value="keyword">Keyword only</option><option value="vector">Meaning only</option>
          </Select>
          <Button type="submit" busy={search.isPending}>Search</Button>
        </form>
        <ErrorBox error={search.error} />
      </Card>
      {search.data && (
        <div className="mt-6 space-y-3">
          {search.data.length === 0 ? <Card><Empty>No matching passages.</Empty></Card> : search.data.map((h) => (
            <Card key={h.chunk_id}>
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-sm">
                <Link className="font-medium text-sky-700 hover:underline" to={`/documents/${h.document_id}?page=${h.page_no}&start=${h.char_start}&end=${h.char_end}`}>
                  {h.document_title} — page {h.page_no}
                </Link>
                <span className="text-xs text-slate-500">
                  score {h.score.toFixed(4)}{h.vector_similarity !== null && ` · similarity ${h.vector_similarity.toFixed(2)}`}{h.keyword_rank ? " · keyword match" : ""}
                </span>
              </div>
              <p className="line-clamp-4 whitespace-pre-wrap text-sm text-slate-700">{h.text}</p>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
