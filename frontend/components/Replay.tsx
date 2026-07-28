"use client";

import { useState } from "react";
import type { useApi } from "@/lib/api";

type Api = ReturnType<typeof useApi>;

type Outcome = {
  available: boolean;
  note?: string;
  entry_price?: number;
  final_close?: number;
  move_pct?: number;
  max_excursion_up_pct?: number;
  max_excursion_down_pct?: number;
  verdict?: string;
  periods?: number;
};

type ReplayResult = {
  symbol: string;
  interval: string;
  mode: string;
  as_of: number;
  analysis: any;
  outcome: Outcome;
  note?: string;
  cached?: boolean;
};

function ts(epoch?: number | null) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString();
}

function leanColor(lean?: string) {
  if (lean === "bullish") return "text-bull";
  if (lean === "bearish") return "text-bear";
  return "text-neutral";
}

function verdictBadge(v?: string) {
  if (v === "correct") return "bg-bull/15 text-bull";
  if (v === "incorrect") return "bg-bear/15 text-bear";
  if (v === "flat") return "bg-white/5 text-neutral";
  return "bg-accent/15 text-accent";
}

export default function Replay({ api, symbol, interval }: { api: Api; symbol: string; interval: string }) {
  const [asOf, setAsOf] = useState<string>("");
  const [mode, setMode] = useState<"copilot" | "debate">("copilot");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [upgrade, setUpgrade] = useState(false);
  const [result, setResult] = useState<ReplayResult | null>(null);

  async function run() {
    setError(null);
    setUpgrade(false);
    setResult(null);
    if (!asOf) {
      setError("Pick a date/time to replay from.");
      return;
    }
    const epoch = Math.floor(new Date(asOf).getTime() / 1000);
    if (!Number.isFinite(epoch) || epoch <= 0) {
      setError("Invalid date/time.");
      return;
    }
    setLoading(true);
    try {
      const r = (await api.replay({
        symbol, interval, as_of: epoch, mode, include_kronos: false,
      })) as ReplayResult;
      setResult(r);
    } catch (e: any) {
      if (e?.status === 402) setUpgrade(true);
      setError(e?.message || "Replay failed");
    } finally {
      setLoading(false);
    }
  }

  const analysis = result?.analysis;
  const lean: string | undefined =
    result?.mode === "debate" ? analysis?.consensus?.lean : analysis?.lean;
  const conviction: number | undefined =
    result?.mode === "debate" ? analysis?.consensus?.confidence : analysis?.conviction;
  const o = result?.outcome;

  return (
    <div className="space-y-6">
      <div className="bg-panel rounded-2xl border border-white/5 p-5">
        <h3 className="font-semibold mb-1">Market Replay</h3>
        <p className="text-xs text-neutral mb-4">
          Run the AI as it would have answered at a past moment — the model sees no future
          candles — then see what actually happened over the next 24 periods. Premium feature.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-[11px] text-neutral mb-1">Replay from (your local time)</label>
            <input
              type="datetime-local"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              className="bg-panelhi border border-white/10 rounded-lg px-3 py-2 text-sm text-ink"
            />
          </div>
          <div>
            <label className="block text-[11px] text-neutral mb-1">Mode</label>
            <div className="flex gap-1 bg-panelhi rounded-lg p-1">
              {(["copilot", "debate"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${
                    mode === m ? "bg-blue text-white" : "text-neutral hover:text-white"
                  }`}
                >
                  {m === "copilot" ? "Copilot" : "Debate panel"}
                </button>
              ))}
            </div>
          </div>
          <button
            onClick={run}
            disabled={loading}
            className="bg-blue hover:bg-bluehi disabled:opacity-50 text-white px-5 py-2 rounded-full text-sm font-semibold"
          >
            {loading ? "Replaying…" : `Replay ${symbol}`}
          </button>
        </div>
        {error && (
          <div className="mt-4">
            <p className="text-bear text-sm">{error}</p>
            {upgrade && (
              <p className="text-xs text-neutral mt-1">
                Market Replay is a Premium feature — upgrade to unlock it.
              </p>
            )}
          </div>
        )}
      </div>

      {result && (
        <>
          <div className="bg-panel rounded-2xl border border-white/5 p-5">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <h3 className="font-semibold">
                The call as of {ts(result.as_of)}
                {result.cached && <span className="text-[11px] text-neutral ml-2">(cached)</span>}
              </h3>
              <span className={`text-sm font-semibold ${leanColor(lean)}`}>
                {lean ?? "—"}
                {conviction != null ? ` (${conviction})` : ""}
              </span>
            </div>
            {result.mode === "debate" ? (
              <div className="space-y-2 text-sm">
                <p>{analysis?.synthesis}</p>
                {analysis?.dissent && (
                  <p className="text-xs text-neutral">Dissent: {analysis.dissent}</p>
                )}
                {analysis?.consensus && (
                  <p className="text-[11px] text-neutral">
                    agreement {analysis.consensus.agreement}% · votes{" "}
                    {Object.entries(analysis.consensus.vote_counts ?? {})
                      .map(([k, v]) => `${v} ${k}`)
                      .join(", ")}
                  </p>
                )}
              </div>
            ) : (
              <div className="space-y-2 text-sm">
                <p>{analysis?.summary}</p>
                {Array.isArray(analysis?.drivers) && analysis.drivers.length > 0 && (
                  <ul className="text-xs text-neutral list-disc list-inside">
                    {analysis.drivers.slice(0, 4).map((d: string, i: number) => (
                      <li key={i}>{d}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {analysis?.disclaimer && (
              <p className="text-[11px] text-neutral/60 mt-3">{analysis.disclaimer}</p>
            )}
          </div>

          <div className="bg-panel rounded-2xl border border-white/5 p-5">
            <h3 className="font-semibold mb-3">What actually happened (next 24 periods)</h3>
            {!o?.available ? (
              <p className="text-sm text-neutral">{o?.note ?? "Outcome unavailable."}</p>
            ) : (
              <>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <div className="bg-panelhi rounded-xl p-4">
                    <div className={`text-2xl font-bold ${(o.move_pct ?? 0) >= 0 ? "text-bull" : "text-bear"}`}>
                      {(o.move_pct ?? 0) >= 0 ? "+" : ""}
                      {o.move_pct}%
                    </div>
                    <div className="text-[11px] text-neutral">move over {o.periods} periods</div>
                  </div>
                  <div className="bg-panelhi rounded-xl p-4">
                    <div className="text-2xl font-bold">
                      <span className={`text-[11px] px-2 py-1 rounded-md ${verdictBadge(o.verdict)}`}>
                        {o.verdict}
                      </span>
                    </div>
                    <div className="text-[11px] text-neutral">verdict vs call</div>
                  </div>
                  <div className="bg-panelhi rounded-xl p-4">
                    <div className="text-lg font-bold text-bull">+{o.max_excursion_up_pct}%</div>
                    <div className="text-[11px] text-neutral">max upside excursion</div>
                  </div>
                  <div className="bg-panelhi rounded-xl p-4">
                    <div className="text-lg font-bold text-bear">{o.max_excursion_down_pct}%</div>
                    <div className="text-[11px] text-neutral">max downside excursion</div>
                  </div>
                </div>
                <p className="text-[11px] text-neutral mt-3">
                  entry {o.entry_price} → final close {o.final_close}
                </p>
              </>
            )}
            {result.note && <p className="text-[11px] text-neutral/60 mt-3">{result.note}</p>}
          </div>
        </>
      )}
    </div>
  );
}
