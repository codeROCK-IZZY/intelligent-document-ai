import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { AskResult, Citation, Conversation, HistoryMessage, SystemInfo } from "../api/types";
import { Badge, Button, Card, Empty, ErrorBox, Loading } from "../components/ui";
import { ANSWER_TONE, humanize } from "../lib/format";

interface Turn { question: string; result: AskResult | null; history?: HistoryMessage }

export function AskPage() {
  const { conversationId } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const scopeDocs = params.getAll("doc");
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: () => api<Conversation[]>("/conversations") });
  const info = useQuery({ queryKey: ["system-info"], queryFn: () => api<SystemInfo>("/system/info"), staleTime: Infinity });
  const create = useMutation({
    mutationFn: () => api<Conversation>("/conversations", { body: { document_ids: scopeDocs.length ? scopeDocs : null } }),
    onSuccess: (c) => { void qc.invalidateQueries({ queryKey: ["conversations"] }); navigate(`/ask/${c.id}`, { replace: true }); },
  });
  // Start a conversation automatically when arriving from "Ask about this document".
  const started = useRef(false);
  useEffect(() => {
    if (!conversationId && scopeDocs.length && !started.current) { started.current = true; create.mutate(); }
  }, [conversationId, scopeDocs.length, create]);

  return (
    <div className="grid gap-6 lg:grid-cols-[240px_1fr]">
      <aside>
        <Button className="mb-3 w-full" onClick={() => create.mutate()} busy={create.isPending}>New conversation</Button>
        {conversations.isLoading ? <Loading /> : (
          <ul className="space-y-1 text-sm">
            {conversations.data?.map((c) => (
              <li key={c.id}>
                <Link to={`/ask/${c.id}`} className={clsx("block truncate rounded px-2 py-1.5", c.id === conversationId ? "bg-slate-200 font-medium" : "hover:bg-slate-100")}>
                  {c.title}{c.scope.document_ids?.length ? <span className="text-xs text-slate-500"> · {c.scope.document_ids.length} doc(s)</span> : null}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <section>
        {conversationId ? <Chat key={conversationId} conversationId={conversationId} info={info.data} /> : (
          <Card><Empty>Start a new conversation to ask questions about your documents. Answers only use documents you can access, and every statement links to its source.</Empty></Card>
        )}
      </section>
    </div>
  );
}

function Chat({ conversationId, info }: { conversationId: string; info: SystemInfo | undefined }) {
  const qc = useQueryClient();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [active, setActive] = useState<Citation | null>(null);
  const history = useQuery({
    queryKey: ["history", conversationId],
    queryFn: () => api<HistoryMessage[]>(`/conversations/${conversationId}/messages`),
  });
  const ask = useMutation({
    mutationFn: (q: string) => api<AskResult>(`/conversations/${conversationId}/messages`, { body: { question: q } }),
    onMutate: (q) => setTurns((t) => [...t, { question: q, result: null }]),
    onSuccess: (r) => {
      setTurns((t) => [...t.slice(0, -1), { ...t[t.length - 1]!, result: r }]);
      void qc.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: () => setTurns((t) => t.slice(0, -1)),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (q) { ask.mutate(q); setQuestion(""); }
  };

  // Past turns come from history (citations re-checked against current access).
  const past: Turn[] = [];
  const msgs = history.data ?? [];
  for (let i = 0; i < msgs.length; i += 2) {
    if (msgs[i]?.role === "user" && msgs[i + 1]) past.push({ question: msgs[i]!.content, result: null, history: msgs[i + 1] });
  }
  const all = turns.length ? [...past.slice(0, past.length), ...turns] : past;

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
      <div className="space-y-4">
        {history.isLoading && <Loading />}
        {all.length === 0 && !history.isLoading && (
          <Card><Empty>Ask anything about your documents — e.g. "What is the termination notice period in the contract with Acme?"</Empty></Card>
        )}
        {all.map((t, i) => <TurnView key={i} turn={t} onCite={setActive} />)}
        <form onSubmit={submit} className="sticky bottom-0 flex gap-2 rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <label htmlFor="question" className="sr-only">Your question</label>
          <textarea id="question" rows={2} value={question} maxLength={2000} placeholder="Ask a question about your documents…"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(e); } }}
            className="flex-1 resize-none rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-sky-600 focus:outline-none" />
          <Button type="submit" busy={ask.isPending} disabled={!question.trim()}>Ask</Button>
        </form>
        <ErrorBox error={ask.error} />
        <p className="text-xs text-slate-500">
          {info?.llm_mode === "claude"
            ? `Answers are written by ${info.llm_model} from your documents only; your passages are sent to Anthropic's API.`
            : "Offline mode: answers are the most relevant sentences quoted from your documents (no AI-generated text, nothing leaves the server)."}
        </p>
      </div>
      <aside>
        <Card title="Source">
          {!active ? <p className="text-sm text-slate-500">Click a citation number to see the exact passage.</p> : (
            <div className="space-y-2 text-sm">
              <p className="font-medium">{active.document_title}</p>
              <p className="text-xs text-slate-500">Page {active.page_no}</p>
              <blockquote className="rounded border-l-4 border-yellow-400 bg-yellow-50 p-2 text-slate-800">{active.cited_text}</blockquote>
              <Link className="text-sky-700 hover:underline"
                to={`/documents/${active.document_id}?page=${active.page_no}${active.char_start !== undefined ? `&start=${active.char_start}&end=${active.char_end}` : ""}`}>
                Open page with passage highlighted →
              </Link>
            </div>
          )}
        </Card>
      </aside>
    </div>
  );
}

function TurnView({ turn, onCite }: { turn: Turn; onCite: (c: Citation) => void }) {
  const r = turn.result;
  const h = turn.history;
  const status = r?.status ?? h?.status ?? null;
  const citations = r?.citations ?? h?.citations ?? [];
  return (
    <div className="space-y-2">
      <div data-testid="question" className="ml-auto max-w-[85%] rounded-xl bg-sky-700 px-4 py-2 text-sm text-white">{turn.question}</div>
      <div data-testid="answer" aria-busy={!r && !h} className="max-w-[95%] rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm shadow-sm">
        {!r && !h ? <span className="text-slate-500">Searching your documents…</span> : (
          <>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              {status && <Badge tone={ANSWER_TONE[status]}>{status === "not_found" ? "not found in your documents" : humanize(status)}</Badge>}
              <span className="text-xs text-slate-500">{r?.provider ?? h?.provider}</span>
              {r && <span className="text-xs text-slate-400">{r.latency_ms} ms · {r.retrieved} passages retrieved</span>}
            </div>
            {r ? (
              status === "answered" ? (
                <p className="leading-relaxed">
                  {r.sentences.map((s, i) => (
                    <span key={i} className={clsx(!s.supported && "bg-red-50 text-red-800")} title={s.supported ? undefined : "Not supported by a source"}>
                      {s.text}
                      {s.citations.map((n) => {
                        const c = r.citations.find((x) => x.ordinal === n);
                        return c ? <CiteChip key={n} n={n} onClick={() => onCite(c)} /> : null;
                      })}{" "}
                    </span>
                  ))}
                </p>
              ) : <p>{r.answer}</p>
            ) : (
              <p className="leading-relaxed">{h!.content} {citations.map((c) => <CiteChip key={c.ordinal} n={c.ordinal} onClick={() => onCite(c)} />)}</p>
            )}
            {r?.notes.map((n) => <p key={n} className="mt-2 text-xs text-amber-700">{n}</p>)}
            {citations.length > 0 && (
              <ol className="mt-3 space-y-1 border-t border-slate-100 pt-2 text-xs text-slate-600">
                {citations.map((c) => (
                  <li key={c.ordinal}>
                    <button className="text-left hover:underline" onClick={() => onCite(c)}>[{c.ordinal}] {c.document_title}, page {c.page_no}</button>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function CiteChip({ n, onClick }: { n: number; onClick: () => void }) {
  return (
    <button onClick={onClick} aria-label={`Show source ${n}`}
      className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-sky-100 px-1 align-super text-[10px] font-semibold text-sky-800 hover:bg-sky-200">
      {n}
    </button>
  );
}
