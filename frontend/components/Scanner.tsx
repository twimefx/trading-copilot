"use client";

import { useState } from "react";
import type { useApi } from "@/lib/api";

type Api = ReturnType<typeof useApi>;

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

const CRYPTO_WATCHLIST = [
  "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
];
const FOREX_WATCHLIST = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "XAU_USD"];

type Filter = "all" | "bullish" | "bearish";

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
function barColor(lean: string) {
  if (lean === "bullish") return "bg-bull";
  if (lean === "bearish") return "bg-bear";
  return "bg-neutral";
}
function fmtPrice(n?: number) {
  if (n == null) return "—";
  // FX & metals trade with more decimals than crypto majors
  const decimals = n < 10 ? 4 : n < 1000 ? 2 : 2;
  return n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
function rsiTone(rsi?: number) {
  if (rsi == null) return "text-neutral";
  if (rsi >= 70) return "text-bear";
  if (rsi <= 30) return "text-bull";
  return "text-neutral";
}

export default function Scanner({ api, onPick }: { api: Api; onPick?: (symbol: string) => void }) {
  const [symbols, setSymbols] = useState(CRYPTO_WATCHLIST.join(", "));
  const [interval, setIntervalVal] = useState("1h");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<ScanItem[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [scanned, setScanned] = useState(false);

  function addPreset(list: string[]) {
    const current = symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
    const merged = Array.from(new Set([...current, ...list]));
    setSymbols(merged.join(", "));
  }

  async function runScan() {
    setLoading(true);
    setError(null);
    try {
      const list = symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
      const data: any = await api.scan({ symbols: list, interval });
      setResults(data.results || []);
      setScanned(true);
    } catch (e: any) {
      setError(e.message || "Scan failed");
    } finally {
      setLoading(false);
    }
  }

  const ok = results.filter((r) => r.ok);
  const bullCount = ok.filter((r) => r.lean === "bullish").length;
  const bearCount = ok.filter((r) => r.lean === "bearish").length;
  const neutralCount = ok.filter((r) => r.lean === "neutral").length;
  const failed = results.filter((r) => !r.ok);

  const shown = results.filter((r) => {
    if (!r.ok) return filter === "all";
    if (filter === "all") return true;
    return r.lean === filter;
  });

  return (
    <div>
      {/* Controls */}
      <div className="bg-panel rounded-2xl border border-white/5 p-5 mb-6">
        <div className="flex flex-wrap gap-2 mb-3">
          <button
            onClick={() => addPreset(CRYPTO_WATCHLIST)}
            className="px-3 py-1.5 rounded-lg text-sm font-medium bg-panelhi text-neutral hover:text-white transition"
          >
            + Crypto majors
          </button>
          <button
            onClick={() => addPreset(FOREX_WATCHLIST)}
            className="px-3 py-1.5 rounded-lg text-sm font-medium bg-panelhi text-neutral hover:text-white transition"
          >
            + Forex / metals
          </button>
          <button
            onClick={() => setSymbols("")}
            className="px-3 py-1.5 rounded-lg text-sm font-medium bg-panelhi text-neutral/70 hover:text-white transition ml-auto"
          >
            Clear
          </button>
        </div>
        <label className="text-xs text-neutral block mb-1">Watchlist (comma-separated)</label>
        <textarea
          value={symbols}
          onChange={(e) => setSymbols(e.target.value)}
          rows={2}
          className="w-full bg-panelhi rounded-lg px-3 py-2 text-sm outline-none focus:ring-1 ring-accent resize-none font-mono"
          placeholder="BTCUSDT, ETHUSDT, EUR_USD…"
        />
        <div className="flex flex-wrap items-center gap-3 mt-3">
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
          {error}. The scan service may be busy — try again in a moment.
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3 animate-pulse">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-panel rounded-xl border border-white/5 h-32" />
          ))}
        </div>
      )}

      {/* Summary + filter bar */}
      {!loading && scanned && ok.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <div className="flex gap-2 text-xs">
            <Stat label="Bullish" value={bullCount} cls="text-bull bg-bull/10" />
            <Stat label="Bearish" value={bearCount} cls="text-bear bg-bear/10" />
            <Stat label="Neutral" value={neutralCount} cls="text-neutral bg-white/5" />
            {failed.length > 0 && <Stat label="Errors" value={failed.length} cls="text-yellow-400 bg-yellow-400/10" />}
          </div>
          <div className="flex gap-1 ml-auto bg-panel rounded-lg p-1">
            {(["all", "bullish", "bearish"] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1 rounded-md text-xs font-medium capitalize transition ${
                  filter === f ? "bg-accent text-white" : "text-neutral hover:text-white"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Empty states */}
      {!loading && !scanned && (
        <div className="text-center text-neutral/60 text-sm py-16 border border-dashed border-white/10 rounded-2xl">
          Build a watchlist above and hit <span className="text-accent">Scan watchlist</span> to screen the market.
        </div>
      )}
      {!loading && scanned && shown.length === 0 && (
        <div className="text-center text-neutral/60 text-sm py-10">
          No {filter !== "all" ? filter : ""} results.
        </div>
      )}

      {/* Results grid */}
      {!loading && (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {shown.map((r) => (
            <button
              key={r.symbol}
              onClick={() => r.ok && onPick?.(r.symbol)}
              disabled={!r.ok}
              className={`text-left rounded-xl border border-white/5 p-4 transition ${
                r.ok ? "bg-panel hover:bg-panelhi hover:border-white/15 cursor-pointer" : "bg-panel/50 cursor-default"
              }`}
            >
              {!r.ok ? (
                <div className="text-yellow-400/80 text-sm">
                  <div className="font-semibold">{r.symbol.replace("_", "/")}</div>
                  <div className="text-xs text-neutral/60 mt-1">{r.error}</div>
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{r.symbol.replace("_", "/")}</span>
                      {r.asset_class && (
                        <span className="text-[10px] uppercase tracking-wide text-neutral/50 bg-white/5 px-1.5 py-0.5 rounded">
                          {r.asset_class === "forex" ? "FX" : r.asset_class}
                        </span>
                      )}
                    </div>
                    <span className={`flex items-center gap-1.5 text-xs font-semibold ${leanColor(r.lean)}`}>
                      <span className={`w-2 h-2 rounded-full ${dot(r.lean)}`} />
                      {r.lean.toUpperCase()}
                    </span>
                  </div>

                  <div className="mt-3 flex items-baseline justify-between">
                    <span className="text-2xl font-bold">
                      {r.conviction}<span className="text-neutral text-sm">/100</span>
                    </span>
                    <span className="text-xs text-neutral font-mono">
                      ${fmtPrice(r.last_close)} · <span className={rsiTone(r.rsi_14)}>RSI {r.rsi_14?.toFixed(0)}</span>
                    </span>
                  </div>

                  {/* Conviction bar — matches Copilot */}
                  <div className="mt-2 h-1.5 bg-black/30 rounded-full overflow-hidden">
                    <div className={`h-full ${barColor(r.lean)}`} style={{ width: `${r.conviction}%` }} />
                  </div>

                  {r.reasons && r.reasons.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1">
                      {r.reasons.slice(0, 3).map((reason, i) => (
                        <span key={i} className="text-[11px] text-neutral/80 bg-white/5 px-1.5 py-0.5 rounded">
                          {reason}
                        </span>
                      ))}
                    </div>
                  )}

                  <div className="mt-3 text-[11px] text-accent/80">Analyze with AI →</div>
                </>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, cls }: { label: string; value: number; cls: string }) {
  return (
    <span className={`px-2.5 py-1 rounded-md font-medium ${cls}`}>
      {value} {label}
    </span>
  );
}
