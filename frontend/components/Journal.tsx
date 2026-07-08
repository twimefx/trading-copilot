"use client";

import { useEffect, useState } from "react";
import { JournalEntry, JournalStats } from "@/lib/journal";
import type { useApi } from "@/lib/api";

type Api = ReturnType<typeof useApi>;

const STATUS_TABS = ["all", "idea", "open", "closed"] as const;
type StatusFilter = (typeof STATUS_TABS)[number];

function leanColor(lean?: string) {
  if (lean === "bullish") return "text-bull";
  if (lean === "bearish") return "text-bear";
  return "text-neutral";
}
function fmtDate(ts: number) {
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}
function fmtNum(n?: number | null) {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

export default function Journal({ api, refreshKey }: { api: Api; refreshKey?: number }) {
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [stats, setStats] = useState<JournalStats | null>(null);
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [es, st] = await Promise.all([
        api.journalList(filter === "all" ? undefined : filter),
        api.journalStats() as Promise<JournalStats>,
      ]);
      setEntries(es as JournalEntry[]);
      setStats(st);
    } catch (e: any) {
      setError(e.message || "Failed to load journal");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, refreshKey]);

  async function patch(id: string, fields: Record<string, any>) {
    try {
      await api.journalUpdate(id, fields);
      await load();
    } catch (e: any) {
      setError(e.message || "Update failed");
    }
  }

  async function remove(id: string) {
    try {
      await api.journalDelete(id);
      await load();
    } catch (e: any) {
      setError(e.message || "Delete failed");
    }
  }

  return (
    <div>
      {/* Performance summary */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          <Stat label="Closed trades" value={String(stats.closed_trades)} />
          <Stat
            label="Win rate"
            value={stats.win_rate != null ? `${Math.round(stats.win_rate * 100)}%` : "—"}
            hint={`${stats.wins}W / ${stats.losses}L`}
          />
          <Stat
            label="Total P&L"
            value={fmtNum(stats.total_pnl)}
            valueClass={stats.total_pnl > 0 ? "text-bull" : stats.total_pnl < 0 ? "text-bear" : ""}
          />
          <Stat label="Open + ideas" value={String(entries.filter((e) => e.status === "open" || e.status === "idea").length)} />
        </div>
      )}

      {/* Status filter */}
      <div className="flex gap-2 mb-4">
        {STATUS_TABS.map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize transition ${
              filter === s ? "bg-accent text-white" : "bg-panelhi text-neutral hover:text-white"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {error && (
        <div className="bg-bear/15 border border-bear/40 rounded-xl p-3 text-bear text-sm mb-4">
          {error}
        </div>
      )}

      {loading && (
        <div className="space-y-3 animate-pulse">
          {[0, 1, 2].map((i) => (
            <div key={i} className="rounded-xl border border-white/5 bg-panel h-24" />
          ))}
        </div>
      )}

      {!loading && entries.length === 0 && (
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center">
          <p className="text-neutral text-sm">No journal entries yet.</p>
          <p className="text-neutral/60 text-xs mt-1">
            Run a Copilot analysis and hit “Save to Journal” to track your calls and outcomes.
          </p>
        </div>
      )}

      {!loading && entries.length > 0 && (
        <div className="space-y-3">
          {entries.map((e) => (
            <EntryCard key={e.id} entry={e} onPatch={patch} onDelete={remove} />
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, hint, valueClass }: { label: string; value: string; hint?: string; valueClass?: string }) {
  return (
    <div className="bg-panel rounded-xl border border-white/5 p-4">
      <div className="text-neutral text-[11px] uppercase tracking-wide">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${valueClass || ""}`}>{value}</div>
      {hint && <div className="text-neutral/60 text-xs mt-0.5">{hint}</div>}
    </div>
  );
}

function EntryCard({
  entry,
  onPatch,
  onDelete,
}: {
  entry: JournalEntry;
  onPatch: (id: string, fields: Record<string, any>) => void;
  onDelete: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState(entry.notes ?? "");
  const [entryPrice, setEntryPrice] = useState(entry.entry_price ?? "");
  const [exitPrice, setExitPrice] = useState(entry.exit_price ?? "");
  const [pnl, setPnl] = useState(entry.pnl ?? "");

  function saveDetails() {
    onPatch(entry.id, {
      notes,
      entry_price: entryPrice === "" ? null : Number(entryPrice),
      exit_price: exitPrice === "" ? null : Number(exitPrice),
      pnl: pnl === "" ? null : Number(pnl),
    });
    setOpen(false);
  }

  const statusColor =
    entry.status === "closed" ? "bg-white/10 text-neutral"
    : entry.status === "open" ? "bg-accent/20 text-accent"
    : entry.status === "cancelled" ? "bg-bear/15 text-bear"
    : "bg-yellow-400/10 text-yellow-400";

  return (
    <div className="bg-panel rounded-xl border border-white/5 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold">{entry.symbol.replace("_", "/")}</span>
            <span className="text-neutral/60 text-xs">{entry.interval}</span>
            <span className={`uppercase text-xs font-bold ${leanColor(entry.lean)}`}>{entry.lean}</span>
            {entry.conviction != null && (
              <span className="text-neutral/60 text-xs">· {entry.conviction}/100</span>
            )}
            <span className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${statusColor}`}>
              {entry.status}
            </span>
            {entry.outcome && (
              <span className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
                entry.outcome === "win" ? "bg-bull/15 text-bull" : entry.outcome === "loss" ? "bg-bear/15 text-bear" : "bg-white/10 text-neutral"
              }`}>
                {entry.outcome}
              </span>
            )}
          </div>
          {entry.summary && (
            <p className="text-sm text-gray-300 mt-2 line-clamp-2">{entry.summary}</p>
          )}
          <div className="text-[11px] text-neutral/50 mt-2">{fmtDate(entry.created_at)}</div>
        </div>
        <div className="text-right shrink-0">
          {entry.pnl != null && (
            <div className={`font-mono font-bold ${entry.pnl > 0 ? "text-bull" : entry.pnl < 0 ? "text-bear" : "text-neutral"}`}>
              {entry.pnl > 0 ? "+" : ""}{fmtNum(entry.pnl)}
            </div>
          )}
        </div>
      </div>

      {/* Quick actions */}
      <div className="flex flex-wrap items-center gap-2 mt-3 pt-3 border-t border-white/5">
        <span className="text-[11px] text-neutral/60 mr-1">Status:</span>
        {(["idea", "open", "closed", "cancelled"] as const).map((s) => (
          <button
            key={s}
            onClick={() => onPatch(entry.id, { status: s })}
            className={`px-2 py-1 rounded text-[11px] capitalize transition ${
              entry.status === s ? "bg-accent text-white" : "bg-panelhi text-neutral hover:text-white"
            }`}
          >
            {s}
          </button>
        ))}
        {entry.status === "closed" && (
          <>
            <span className="w-px h-4 bg-white/10 mx-1" />
            <span className="text-[11px] text-neutral/60">Outcome:</span>
            {(["win", "loss", "breakeven"] as const).map((o) => (
              <button
                key={o}
                onClick={() => onPatch(entry.id, { outcome: o })}
                className={`px-2 py-1 rounded text-[11px] capitalize transition ${
                  entry.outcome === o
                    ? o === "win" ? "bg-bull text-white" : o === "loss" ? "bg-bear text-white" : "bg-neutral text-white"
                    : "bg-panelhi text-neutral hover:text-white"
                }`}
              >
                {o}
              </button>
            ))}
          </>
        )}
        <div className="ml-auto flex gap-2">
          <button onClick={() => setOpen((v) => !v)} className="text-[11px] text-accent hover:underline">
            {open ? "Close" : "Details"}
          </button>
          <button onClick={() => onDelete(entry.id)} className="text-[11px] text-bear/80 hover:text-bear hover:underline">
            Delete
          </button>
        </div>
      </div>

      {/* Editable details */}
      {open && (
        <div className="mt-3 grid sm:grid-cols-3 gap-3">
          <Field label="Entry price">
            <input type="number" value={entryPrice} onChange={(e) => setEntryPrice(e.target.value)}
              className="w-full bg-panelhi rounded px-2 py-1.5 text-sm outline-none focus:ring-1 ring-accent" />
          </Field>
          <Field label="Exit price">
            <input type="number" value={exitPrice} onChange={(e) => setExitPrice(e.target.value)}
              className="w-full bg-panelhi rounded px-2 py-1.5 text-sm outline-none focus:ring-1 ring-accent" />
          </Field>
          <Field label="P&L">
            <input type="number" value={pnl} onChange={(e) => setPnl(e.target.value)}
              className="w-full bg-panelhi rounded px-2 py-1.5 text-sm outline-none focus:ring-1 ring-accent" />
          </Field>
          <div className="sm:col-span-3">
            <Field label="Notes">
              <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2}
                className="w-full bg-panelhi rounded px-2 py-1.5 text-sm outline-none focus:ring-1 ring-accent resize-none" />
            </Field>
          </div>
          <div className="sm:col-span-3 flex justify-end">
            <button onClick={saveDetails}
              className="bg-accent hover:bg-blue-600 text-white px-4 py-1.5 rounded text-sm font-semibold transition">
              Save details
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-[11px] text-neutral/60 block mb-1">{label}</span>
      {children}
    </label>
  );
}
