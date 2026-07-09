"use client";

import { useState } from "react";
import type { useApi } from "@/lib/api";

type Api = ReturnType<typeof useApi>;

type Stats = {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  total_return_pct: number;
  buy_hold_return_pct: number;
  vs_buy_hold_pct: number;
  avg_win_pct: number | null;
  avg_loss_pct: number | null;
  profit_factor: number | null;
  avg_trade_pct: number | null;
  max_drawdown_pct: number;
  exposure_pct: number;
};

type Trade = {
  side: string; entry_price: number; exit_price: number;
  return_pct: number; reason: string;
};

type StrategyResult = {
  spec: { name?: string; direction?: string };
  rules_human: string[];
  stats: Stats | null;
  trades: Trade[];
  equity_curve: number[];
  error?: string | null;
  disclaimer?: string;
  cost_usd?: number;
  cached?: boolean;
};

const EXAMPLES = [
  "Buy when RSI drops below 30, sell when it goes above 65, 4% stop",
  "Go long when the 20 EMA crosses above the 50 EMA, exit on the reverse cross",
  "Buy when MACD histogram turns positive, take profit at 8%, stop at 3%",
];

function pnlColor(n?: number | null) {
  if (n == null) return "text-neutral";
  return n > 0 ? "text-bull" : n < 0 ? "text-bear" : "text-neutral";
}
function pct(n?: number | null) {
  if (n == null) return "—";
  return `${n > 0 ? "+" : ""}${n}%`;
}

// Minimal inline SVG sparkline for the equity curve (normalized to [0..1]).
function Sparkline({ data, color = "#4ade80" }: { data: number[]; color?: string }) {
  if (!data || data.length < 2) return null;
  const w = 600, h = 120;
  const min = Math.min(...data), max = Math.max(...data);
  const rng = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / rng) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const flat = h - ((1 - min) / rng) * h;   // the 1.0 baseline (starting capital)
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-28" preserveAspectRatio="none">
      <line x1="0" y1={flat} x2={w} y2={flat} stroke="#ffffff20" strokeWidth="1" strokeDasharray="4 4" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth="2" />
    </svg>
  );
}

export default function Strategy({ api, symbol, interval }: { api: Api; symbol: string; interval: string }) {
  const [prompt, setPrompt] = useState("");
  const [data, setData] = useState<StrategyResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [upgrade, setUpgrade] = useState(false);

  async function run(p?: string) {
    const idea = (p ?? prompt).trim();
    if (!idea) return;
    if (p) setPrompt(p);
    setLoading(true);
    setError(null);
    setUpgrade(false);
    try {
      const res = (await api.strategy({ prompt: idea, symbol, interval })) as StrategyResult;
      setData(res);
    } catch (e: any) {
      if (e?.status === 402) setUpgrade(true);
      else setError(e?.message || "Strategy build failed");
    } finally {
      setLoading(false);
    }
  }

  const s = data?.stats;
  const beatBuyHold = s ? s.vs_buy_hold_pct > 0 : false;

  return (
    <div>
      <div className="mb-4">
        <h2 className="text-lg font-semibold">AI Strategy Builder</h2>
        <p className="text-neutral/60 text-xs mt-0.5">
          Describe a strategy in plain English. We translate it into rules and backtest it on real {symbol.replace("_", "/")} data.
        </p>
      </div>

      <div className="bg-panel rounded-2xl border border-white/5 p-4 mb-6">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={2}
          placeholder="e.g. Buy when RSI drops below 30, sell above 65, with a 4% stop loss"
          className="w-full bg-panelhi rounded-lg px-3 py-2 text-sm outline-none focus:ring-1 ring-accent resize-none"
        />
        <div className="flex items-center justify-between gap-3 mt-3">
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((ex, i) => (
              <button key={i} onClick={() => run(ex)} disabled={loading}
                className="text-[11px] bg-panelhi hover:bg-white/10 text-neutral hover:text-white px-2 py-1 rounded transition">
                {ex.length > 40 ? ex.slice(0, 38) + "…" : ex}
              </button>
            ))}
          </div>
          <button
            onClick={() => run()}
            disabled={loading || !prompt.trim()}
            className="shrink-0 bg-accent hover:bg-blue-600 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-semibold transition"
          >
            {loading ? "Backtesting…" : "Build & backtest"}
          </button>
        </div>
      </div>

      {upgrade && (
        <div className="bg-accent/10 border border-accent/30 rounded-xl p-4 text-sm mb-4">
          The AI Strategy Builder is a <span className="font-semibold">Premium</span> feature. Upgrade to unlock it.
        </div>
      )}
      {error && (
        <div className="bg-bear/15 border border-bear/40 rounded-xl p-3 text-bear text-sm mb-4">{error}</div>
      )}

      {data && data.error && (
        <div className="bg-bear/15 border border-bear/40 rounded-xl p-3 text-bear text-sm mb-4">{data.error}</div>
      )}

      {data && s && (
        <>
          {/* Parsed rules */}
          <div className="bg-panel rounded-xl border border-white/5 p-4 mb-4">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-sm">{data.spec.name || "Strategy"}</span>
              {data.cached && <span className="text-[10px] text-neutral/50">cached</span>}
            </div>
            <ul className="mt-2 space-y-1 text-sm text-gray-300 font-mono">
              {data.rules_human.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          </div>

          {/* Headline: strategy vs buy & hold */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <Stat label="Total return" value={pct(s.total_return_pct)} valueClass={pnlColor(s.total_return_pct)} />
            <Stat label="vs Buy & Hold" value={pct(s.vs_buy_hold_pct)} valueClass={pnlColor(s.vs_buy_hold_pct)}
              hint={`B&H ${pct(s.buy_hold_return_pct)}`} />
            <Stat label="Win rate" value={s.win_rate != null ? `${Math.round(s.win_rate * 100)}%` : "—"}
              hint={`${s.wins}W / ${s.losses}L · ${s.trades} trades`} />
            <Stat label="Max drawdown" value={`${s.max_drawdown_pct}%`} valueClass="text-bear" />
          </div>

          {/* Equity curve */}
          {data.equity_curve && data.equity_curve.length > 1 && (
            <div className="bg-panel rounded-xl border border-white/5 p-4 mb-4">
              <div className="text-[11px] text-neutral/60 uppercase tracking-wide mb-1">
                Equity curve {beatBuyHold ? "· beat buy & hold" : "· underperformed buy & hold"}
              </div>
              <Sparkline data={data.equity_curve} color={s.total_return_pct >= 0 ? "#4ade80" : "#f87171"} />
            </div>
          )}

          {/* Secondary stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <Stat label="Profit factor" value={s.profit_factor != null ? String(s.profit_factor) : "—"} />
            <Stat label="Avg trade" value={pct(s.avg_trade_pct)} valueClass={pnlColor(s.avg_trade_pct)} />
            <Stat label="Avg win / loss" value={`${pct(s.avg_win_pct)} / ${pct(s.avg_loss_pct)}`} />
            <Stat label="Exposure" value={`${s.exposure_pct}%`} />
          </div>

          {/* Recent trades */}
          {data.trades && data.trades.length > 0 && (
            <div className="bg-panel rounded-xl border border-white/5 overflow-hidden">
              <div className="text-[11px] text-neutral/60 uppercase tracking-wide px-3 py-2 border-b border-white/5">
                Recent trades ({data.trades.length} shown)
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[11px] text-neutral/60 border-b border-white/5">
                    <th className="text-left px-3 py-1.5">Side</th>
                    <th className="text-right px-3 py-1.5">Entry</th>
                    <th className="text-right px-3 py-1.5">Exit</th>
                    <th className="text-right px-3 py-1.5">Return</th>
                    <th className="text-right px-3 py-1.5">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {data.trades.slice(-15).reverse().map((t, i) => (
                    <tr key={i} className="border-b border-white/5 last:border-0">
                      <td className={`px-3 py-1.5 uppercase text-xs font-bold ${t.side === "long" ? "text-bull" : "text-bear"}`}>{t.side}</td>
                      <td className="px-3 py-1.5 text-right font-mono">{t.entry_price}</td>
                      <td className="px-3 py-1.5 text-right font-mono">{t.exit_price}</td>
                      <td className={`px-3 py-1.5 text-right font-mono ${pnlColor(t.return_pct)}`}>{pct(t.return_pct)}</td>
                      <td className="px-3 py-1.5 text-right text-neutral/60 text-xs">{t.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {data.disclaimer && <p className="text-[10px] text-neutral/40 mt-4">{data.disclaimer}</p>}
        </>
      )}
    </div>
  );
}

function Stat({ label, value, hint, valueClass }: { label: string; value: string; hint?: string; valueClass?: string }) {
  return (
    <div className="bg-panel rounded-xl border border-white/5 p-4">
      <div className="text-neutral text-[11px] uppercase tracking-wide">{label}</div>
      <div className={`text-xl font-bold mt-1 ${valueClass || ""}`}>{value}</div>
      {hint && <div className="text-neutral/60 text-xs mt-0.5">{hint}</div>}
    </div>
  );
}
