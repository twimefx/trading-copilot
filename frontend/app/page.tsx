"use client";

import { useState } from "react";

type Range = { low: number; high: number; source: string };
type Analysis = {
  lean?: string;
  conviction?: number;
  summary?: string;
  drivers?: string[];
  risks?: string[];
  range_24h?: Range;
  suggested_invalidation?: string;
  disclaimer?: string;
  cost_usd?: number;
  raw?: string;
};

const PRESETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"];

function leanColor(lean?: string) {
  if (lean === "bullish") return "text-bull";
  if (lean === "bearish") return "text-bear";
  return "text-neutral";
}
function leanBg(lean?: string) {
  if (lean === "bullish") return "bg-bull/15 border-bull/40";
  if (lean === "bearish") return "bg-bear/15 border-bear/40";
  return "bg-neutral/10 border-neutral/30";
}

export default function Home() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [interval, setIntervalVal] = useState("1h");
  const [useKronos, setUseKronos] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Analysis | null>(null);

  async function analyze() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/copilot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol, interval, include_kronos: useKronos }),
      });
      if (!res.ok) throw new Error(`API error ${res.status}`);
      setResult(await res.json());
    } catch (e: any) {
      setError(e.message || "Request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen max-w-3xl mx-auto px-5 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">
          AI Trading <span className="text-accent">Copilot</span>
        </h1>
        <p className="text-neutral mt-1 text-sm">
          Market intelligence, explained. Not financial advice.
        </p>
      </header>

      {/* Controls */}
      <div className="bg-panel rounded-2xl border border-white/5 p-5 mb-6">
        <div className="flex flex-wrap gap-2 mb-3">
          {PRESETS.map((p) => (
            <button
              key={p}
              onClick={() => setSymbol(p)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
                symbol === p ? "bg-accent text-white" : "bg-panelhi text-neutral hover:text-white"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="bg-panelhi rounded-lg px-3 py-2 text-sm flex-1 min-w-[140px] outline-none focus:ring-1 ring-accent"
            placeholder="Symbol e.g. BTCUSDT"
          />
          <select
            value={interval}
            onChange={(e) => setIntervalVal(e.target.value)}
            className="bg-panelhi rounded-lg px-3 py-2 text-sm outline-none"
          >
            {["15m", "1h", "4h", "1d"].map((i) => (
              <option key={i} value={i}>{i}</option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-sm text-neutral cursor-pointer">
            <input type="checkbox" checked={useKronos} onChange={(e) => setUseKronos(e.target.checked)} />
            Kronos range
          </label>
          <button
            onClick={analyze}
            disabled={loading}
            className="bg-accent hover:bg-blue-600 disabled:opacity-50 text-white px-5 py-2 rounded-lg text-sm font-semibold transition"
          >
            {loading ? "Analyzing…" : "Analyze"}
          </button>
        </div>
        {useKronos && (
          <p className="text-xs text-neutral/70 mt-2">
            Kronos range adds a CPU forecast (~30–90s). Off = faster.
          </p>
        )}
      </div>

      {error && (
        <div className="bg-bear/15 border border-bear/40 rounded-xl p-4 text-bear text-sm mb-6">
          {error} — is the backend running on :8011?
        </div>
      )}

      {result && (
        <div className="space-y-4">
          {/* Verdict */}
          <div className={`rounded-2xl border p-6 ${leanBg(result.lean)}`}>
            <div className="flex items-baseline justify-between">
              <div>
                <span className="text-neutral text-sm">{symbol}</span>
                <div className={`text-3xl font-bold uppercase ${leanColor(result.lean)}`}>
                  {result.lean}
                </div>
              </div>
              <div className="text-right">
                <div className="text-neutral text-xs">CONVICTION</div>
                <div className="text-3xl font-bold">{result.conviction}<span className="text-neutral text-lg">/100</span></div>
              </div>
            </div>
            <div className="mt-3 h-2 bg-black/30 rounded-full overflow-hidden">
              <div
                className={`h-full ${result.lean === "bullish" ? "bg-bull" : result.lean === "bearish" ? "bg-bear" : "bg-neutral"}`}
                style={{ width: `${result.conviction ?? 0}%` }}
              />
            </div>
            <p className="mt-4 text-sm leading-relaxed text-gray-200">{result.summary}</p>
          </div>

          {/* Drivers + Risks */}
          <div className="grid md:grid-cols-2 gap-4">
            <div className="bg-panel rounded-2xl border border-white/5 p-5">
              <h3 className="text-bull font-semibold text-sm mb-3">DRIVERS</h3>
              <ul className="space-y-2 text-sm text-gray-300">
                {result.drivers?.map((d, i) => (
                  <li key={i} className="flex gap-2"><span className="text-bull">+</span>{d}</li>
                ))}
              </ul>
            </div>
            <div className="bg-panel rounded-2xl border border-white/5 p-5">
              <h3 className="text-bear font-semibold text-sm mb-3">RISKS</h3>
              <ul className="space-y-2 text-sm text-gray-300">
                {result.risks?.map((r, i) => (
                  <li key={i} className="flex gap-2"><span className="text-bear">!</span>{r}</li>
                ))}
              </ul>
            </div>
          </div>

          {/* Range + invalidation */}
          <div className="grid md:grid-cols-2 gap-4">
            {result.range_24h && (
              <div className="bg-panel rounded-2xl border border-white/5 p-5">
                <h3 className="text-accent font-semibold text-sm mb-2">24H RANGE (Kronos)</h3>
                <div className="text-lg font-mono">
                  ${result.range_24h.low?.toLocaleString()} – ${result.range_24h.high?.toLocaleString()}
                </div>
                <p className="text-xs text-neutral mt-1">Volatility estimate, not a direction call.</p>
              </div>
            )}
            {result.suggested_invalidation && (
              <div className="bg-panel rounded-2xl border border-white/5 p-5">
                <h3 className="text-yellow-400 font-semibold text-sm mb-2">INVALIDATION</h3>
                <p className="text-sm text-gray-300">{result.suggested_invalidation}</p>
              </div>
            )}
          </div>

          <div className="flex justify-between items-center text-xs text-neutral pt-2">
            <span>cost: ${result.cost_usd}</span>
          </div>
          <p className="text-xs text-neutral/60 border-t border-white/5 pt-3">{result.disclaimer}</p>
        </div>
      )}
    </main>
  );
}
