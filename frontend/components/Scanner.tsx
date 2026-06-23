"use client";

import { useState } from "react";

type ScanItem = {
  symbol: string;
  asset_class?: string;
  lean: string;
  conviction: number;
  last_close?: number;
  rsi_14?: number;
  macd_hist?: number;
  reasons?: string[];
  ok: boolean;
  error?: string;
};

const DEFAULT_WATCHLIST = [
  "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
];

function leanColor(lean: string) {
  if (lean === "bullish") return "text-bull";
  if (lean === "bearish") return "text-bear";
  return "text-neutral";
}
function dot(lean: string) {
  if (lean === "bullish") return "bg-bull";
  if (lean === "bearish") return "bg-bear";
  return "bg-neutral";
}

export default function Scanner({ onPick }: { onPick?: (symbol: string) => void }) {
  const [symbols, setSymbols] = useState(DEFAULT_WATCHLIST.join(", "));
  const [interval, setIntervalVal] = useState("1h");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<ScanItem[]>([]);

  async function runScan() {
    setLoading(true);
    setError(null);
    try {
      const list = symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: list, interval }),
      });
      if (!res.ok) throw new Error(`API error ${res.status}`);
      const data = await res.json();
      setResults(data.results || []);
    } catch (e: any) {
      setError(e.message || "Scan failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="bg-panel rounded-2xl border border-white/5 p-5 mb-6">
        <label className="text-xs text-neutral block mb-1">Watchlist (comma-separated)</label>
        <textarea
          value={symbols}
          onChange={(e) => setSymbols(e.target.value)}
          rows={2}
          className="w-full bg-panelhi rounded-lg px-3 py-2 text-sm outline-none focus:ring-1 ring-accent resize-none"
        />
        <div className="flex items-center gap-3 mt-3">
          <select
            value={interval}
            onChange={(e) => setIntervalVal(e.target.value)}
            className="bg-panelhi rounded-lg px-3 py-2 text-sm outline-none"
          >
            {["15m", "1h", "4h", "1d"].map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
          <button
            onClick={runScan}
            disabled={loading}
            className="bg-accent hover:bg-blue-600 disabled:opacity-50 text-white px-5 py-2 rounded-lg text-sm font-semibold transition"
          >
            {loading ? "Scanning…" : "Scan watchlist"}
          </button>
          <span className="text-xs text-neutral/70">Fast technical screen (no LLM). Click a card for full AI analysis.</span>
        </div>
      </div>

      {error && (
        <div className="bg-bear/15 border border-bear/40 rounded-xl p-4 text-bear text-sm mb-6">
          {error} — is the backend running on :8011?
        </div>
      )}

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {results.map((r) => (
          <button
            key={r.symbol}
            onClick={() => r.ok && onPick?.(r.symbol)}
            className="text-left bg-panel hover:bg-panelhi rounded-xl border border-white/5 p-4 transition"
          >
            {!r.ok ? (
              <div className="text-bear text-sm">{r.symbol}: {r.error}</div>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <span className="font-semibold">{r.symbol.replace("_", "/")}</span>
                  <span className={`flex items-center gap-1.5 text-sm font-semibold ${leanColor(r.lean)}`}>
                    <span className={`w-2 h-2 rounded-full ${dot(r.lean)}`} />
                    {r.lean.toUpperCase()}
                  </span>
                </div>
                <div className="mt-2 flex items-baseline justify-between">
                  <span className="text-2xl font-bold">{r.conviction}<span className="text-neutral text-sm">/100</span></span>
                  <span className="text-xs text-neutral font-mono">
                    {r.last_close} · RSI {r.rsi_14}
                  </span>
                </div>
                {r.reasons && r.reasons.length > 0 && (
                  <div className="mt-2 text-xs text-neutral/80 line-clamp-2">{r.reasons.join(" · ")}</div>
                )}
              </>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
