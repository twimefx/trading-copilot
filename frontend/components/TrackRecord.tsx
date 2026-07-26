"use client";

import { useEffect, useState } from "react";
import type { useApi } from "@/lib/api";

type Api = ReturnType<typeof useApi>;

type Stats = {
  total_signals: number;
  scored: number;
  pending: number;
  flat_or_neutral: number;
  correct: number;
  incorrect: number;
  accuracy_pct: number | null;
  per_symbol: Record<string, { correct: number; incorrect: number; accuracy_pct: number | null }>;
  note: string;
};

type Signal = {
  id: string;
  symbol: string;
  interval: string;
  lean: string;
  conviction?: number | null;
  entry_price?: number | null;
  outcome_price?: number | null;
  outcome?: string | null;
  created_at: number;
  resolved_at?: number | null;
};

function ts(epoch?: number | null) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString();
}

function leanColor(lean: string) {
  if (lean === "bullish") return "text-bull";
  if (lean === "bearish") return "text-bear";
  return "text-neutral";
}

function outcomeBadge(outcome?: string | null) {
  if (outcome === "correct") return "bg-bull/15 text-bull";
  if (outcome === "incorrect") return "bg-bear/15 text-bear";
  if (outcome === "flat") return "bg-white/5 text-neutral";
  return "bg-accent/15 text-accent";
}

export default function TrackRecord({ api }: { api: Api }) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setStats((await api.signalStats()) as Stats);
        setSignals(await api.signalHistory());
      } catch (e: any) {
        setError(e.message || "Failed to load track record");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) return <p className="text-bear text-sm">{error}</p>;
  if (!stats) return <p className="text-sm text-neutral">Loading track record…</p>;

  return (
    <div className="space-y-6">
      <div className="bg-panel rounded-2xl border border-white/5 p-5">
        <h3 className="font-semibold mb-1">Signal track record</h3>
        <p className="text-xs text-neutral mb-4">{stats.note}</p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="bg-panelhi rounded-xl p-4">
            <div className="text-2xl font-bold">{stats.total_signals}</div>
            <div className="text-[11px] text-neutral">total calls</div>
          </div>
          <div className="bg-panelhi rounded-xl p-4">
            <div className="text-2xl font-bold">
              {stats.accuracy_pct == null ? "—" : `${stats.accuracy_pct}%`}
            </div>
            <div className="text-[11px] text-neutral">directional accuracy</div>
          </div>
          <div className="bg-panelhi rounded-xl p-4">
            <div className="text-2xl font-bold">
              <span className="text-bull">{stats.correct}</span>
              <span className="text-neutral/50"> / </span>
              <span className="text-bear">{stats.incorrect}</span>
            </div>
            <div className="text-[11px] text-neutral">correct / incorrect</div>
          </div>
          <div className="bg-panelhi rounded-xl p-4">
            <div className="text-2xl font-bold">{stats.pending}</div>
            <div className="text-[11px] text-neutral">awaiting horizon</div>
          </div>
        </div>
        {stats.accuracy_pct == null && stats.total_signals > 0 && (
          <p className="text-[11px] text-neutral mt-3">
            Accuracy appears once the first signals pass their 24-period scoring horizon — we never
            show a number before the data exists.
          </p>
        )}
      </div>

      {Object.keys(stats.per_symbol).length > 0 && (
        <div className="bg-panel rounded-2xl border border-white/5 p-5">
          <h3 className="font-semibold mb-3">Per symbol</h3>
          <div className="space-y-2">
            {Object.entries(stats.per_symbol).map(([sym, s]) => (
              <div key={sym} className="flex items-center justify-between bg-panelhi rounded-xl px-4 py-2.5">
                <span className="text-sm font-medium">{sym}</span>
                <span className="text-xs text-neutral">
                  {s.correct}✓ {s.incorrect}✗ · {s.accuracy_pct == null ? "—" : `${s.accuracy_pct}%`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="bg-panel rounded-2xl border border-white/5 p-5">
        <h3 className="font-semibold mb-3">Recent calls ({signals.length})</h3>
        {signals.length === 0 && (
          <p className="text-sm text-neutral">
            No logged calls yet — every non-cached Copilot analysis is recorded here automatically.
          </p>
        )}
        <div className="space-y-2">
          {signals.slice(0, 50).map((s) => (
            <div key={s.id} className="flex flex-wrap items-center justify-between gap-2 bg-panelhi rounded-xl px-4 py-3">
              <div>
                <span className="text-sm font-medium">{s.symbol}</span>
                <span className="text-xs text-neutral ml-2">{s.interval}</span>
                <span className={`text-sm ml-3 font-medium ${leanColor(s.lean)}`}>
                  {s.lean}
                  {s.conviction != null ? ` (${s.conviction})` : ""}
                </span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-[11px] text-neutral">{ts(s.created_at)}</span>
                <span className={`text-[11px] px-2 py-1 rounded-md ${outcomeBadge(s.outcome)}`}>
                  {s.outcome ?? "pending"}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
