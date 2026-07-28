"use client";

import { useState } from "react";
import type { useApi } from "@/lib/api";

type Api = ReturnType<typeof useApi>;

type Position = {
  id?: string;
  symbol: string;
  asset_class: string;
  direction: string;
  size: number | null;
  entry_price: number | null;
  mark: number | null;
  notional: number | null;
  unrealized_pnl: number | null;
  pct_move: number | null;
  complete: boolean;
};

type Profile = {
  open_positions: number;
  priced_positions?: number;
  gross_exposure?: number;
  long_exposure?: number;
  short_exposure?: number;
  net_exposure?: number;
  net_bias?: string;
  total_unrealized_pnl?: number;
  largest_position?: string | null;
  largest_position_share?: number | null;
  asset_class_share?: Record<string, number>;
  positions?: Position[];
};

type Read = {
  headline?: string;
  risks?: { risk: string; detail: string; action?: string }[];
  suggestions?: string[];
};

type PortfolioResult = {
  has_positions: boolean;
  message?: string;
  profile: Profile;
  flags?: { risk: string; detail: string }[];
  read?: Read | null;
  disclaimer?: string;
  cached?: boolean;
};

function fmtNum(n?: number | null) {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
function dirColor(d: string) {
  return d === "long" ? "text-bull" : d === "short" ? "text-bear" : "text-neutral";
}
function pnlColor(n?: number | null) {
  if (n == null) return "text-neutral";
  return n > 0 ? "text-bull" : n < 0 ? "text-bear" : "text-neutral";
}

// Portfolio Copilot: AI risk read over the user's OPEN positions (from the
// journal). LLM-backed, so run on demand (a button), not on mount.
export default function Portfolio({ api, refreshKey }: { api: Api; refreshKey?: number }) {
  const [data, setData] = useState<PortfolioResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [upgrade, setUpgrade] = useState(false);

  async function run() {
    setLoading(true);
    setError(null);
    setUpgrade(false);
    try {
      const res = (await api.portfolio()) as PortfolioResult;
      setData(res);
    } catch (e: any) {
      if (e?.status === 402) setUpgrade(true);
      else setError(e?.message || "Portfolio analysis failed");
    } finally {
      setLoading(false);
    }
  }

  const prof = data?.profile;
  const positions = prof?.positions ?? [];
  const read = data?.read;

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-4">
        <div>
          <h2 className="text-lg font-semibold">Portfolio Copilot</h2>
          <p className="text-neutral/60 text-xs mt-0.5">
            AI risk read over your open positions — exposure, concentration, and directional bias.
          </p>
        </div>
        <button
          onClick={run}
          disabled={loading}
          className="shrink-0 bg-blue hover:bg-bluehi disabled:opacity-50 text-white px-4 py-2 rounded-full text-sm font-semibold transition"
        >
          {loading ? "Analyzing…" : data ? "Re-run" : "Analyze my book"}
        </button>
      </div>

      {upgrade && (
        <div className="bg-accent/10 border border-accent/30 rounded-xl p-4 text-sm mb-4">
          Portfolio Copilot is a <span className="font-semibold">Pro</span> feature. Upgrade to unlock it.
        </div>
      )}

      {error && (
        <div className="bg-bear/15 border border-bear/40 rounded-xl p-3 text-bear text-sm mb-4">{error}</div>
      )}

      {!data && !loading && !error && !upgrade && (
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center">
          <p className="text-neutral text-sm">Track your open positions in the Journal, then analyze your book here.</p>
        </div>
      )}

      {data && !data.has_positions && (
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center">
          <p className="text-neutral text-sm">
            {data.message || "No open positions logged. Mark journal entries as 'open' to track your book."}
          </p>
        </div>
      )}

      {data && data.has_positions && prof && (
        <>
          {/* Exposure summary */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <Stat label="Gross exposure" value={`$${fmtNum(prof.gross_exposure)}`} />
            <Stat
              label="Net exposure"
              value={`$${fmtNum(prof.net_exposure)}`}
              hint={(prof.net_bias || "").replace("_", " ")}
            />
            <Stat
              label="Unrealized P&L"
              value={`$${fmtNum(prof.total_unrealized_pnl)}`}
              valueClass={pnlColor(prof.total_unrealized_pnl)}
            />
            <Stat
              label="Top position"
              value={prof.largest_position || "—"}
              hint={prof.largest_position_share != null ? `${Math.round(prof.largest_position_share * 100)}% of gross` : undefined}
            />
          </div>

          {/* AI risk read */}
          {read && (
            <div className="bg-panel rounded-xl border border-white/5 p-4 mb-6">
              <div className="text-sm font-semibold flex items-center gap-2">
                <span>🧠 AI Risk Read</span>
                {data.cached && <span className="text-[10px] text-neutral/50 font-normal">cached</span>}
              </div>
              {read.headline && <p className="text-sm text-gray-100 mt-2">{read.headline}</p>}
              {read.risks && read.risks.length > 0 && (
                <div className="space-y-2 mt-3">
                  {read.risks.map((rk, i) => (
                    <div key={i} className="bg-panelhi rounded-lg p-3">
                      <div className="text-xs font-semibold text-bear uppercase tracking-wide">
                        {rk.risk.replace(/_/g, " ")}
                      </div>
                      {rk.detail && <p className="text-sm text-gray-300 mt-1">{rk.detail}</p>}
                      {rk.action && (
                        <p className="text-sm text-bull mt-1.5">
                          <span className="text-neutral/60">Adjust: </span>
                          {rk.action}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {read.suggestions && read.suggestions.length > 0 && (
                <ul className="mt-3 space-y-1 text-sm text-gray-400 list-disc list-inside">
                  {read.suggestions.map((s, i) => <li key={i}>{s}</li>)}
                </ul>
              )}
              {data.disclaimer && <p className="text-[10px] text-neutral/40 mt-3">{data.disclaimer}</p>}
            </div>
          )}

          {/* Positions table */}
          <div className="bg-panel rounded-xl border border-white/5 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] text-neutral/60 uppercase tracking-wide border-b border-white/5">
                  <th className="text-left px-3 py-2">Symbol</th>
                  <th className="text-left px-3 py-2">Dir</th>
                  <th className="text-right px-3 py-2">Size</th>
                  <th className="text-right px-3 py-2">Entry</th>
                  <th className="text-right px-3 py-2">Mark</th>
                  <th className="text-right px-3 py-2">Notional</th>
                  <th className="text-right px-3 py-2">uPnL</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={p.id || i} className="border-b border-white/5 last:border-0">
                    <td className="px-3 py-2 font-medium">{p.symbol.replace("_", "/")}</td>
                    <td className={`px-3 py-2 uppercase text-xs font-bold ${dirColor(p.direction)}`}>{p.direction}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtNum(p.size)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtNum(p.entry_price)}</td>
                    <td className="px-3 py-2 text-right font-mono">{p.mark == null ? "—" : fmtNum(p.mark)}</td>
                    <td className="px-3 py-2 text-right font-mono">{p.notional == null ? "—" : `$${fmtNum(p.notional)}`}</td>
                    <td className={`px-3 py-2 text-right font-mono ${pnlColor(p.unrealized_pnl)}`}>
                      {p.unrealized_pnl == null ? "—" : `${p.unrealized_pnl > 0 ? "+" : ""}${fmtNum(p.unrealized_pnl)}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {prof.priced_positions != null && prof.priced_positions < prof.open_positions && (
            <p className="text-[11px] text-neutral/50 mt-2">
              {prof.open_positions - prof.priced_positions} position(s) missing entry/size — excluded from P&L math.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value, hint, valueClass }: { label: string; value: string; hint?: string; valueClass?: string }) {
  return (
    <div className="bg-panel rounded-xl border border-white/5 p-4">
      <div className="text-neutral text-[11px] uppercase tracking-wide">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${valueClass || ""}`}>{value}</div>
      {hint && <div className="text-neutral/60 text-xs mt-0.5 capitalize">{hint}</div>}
    </div>
  );
}
