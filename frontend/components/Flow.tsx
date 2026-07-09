"use client";

import { useState } from "react";
import type { useApi } from "@/lib/api";

type Api = ReturnType<typeof useApi>;

type Stream = { available: boolean; [k: string]: any };
type FlowResult = {
  symbol: string;
  period: string;
  available: boolean;
  message?: string;
  funding: Stream;
  open_interest: Stream;
  long_short: Stream;
  taker_flow: Stream;
  positioning: { signals: string[]; squeeze_risk: string | null };
  narrative?: { headline?: string; key_points?: string[]; squeeze_watch?: string } | null;
  disclaimer?: string;
  cost_usd?: number;
  cached?: boolean;
};

function trendIcon(t?: string) {
  return t === "rising" ? "↑" : t === "falling" ? "↓" : "→";
}
function regimeColor(r?: string) {
  if (!r) return "text-neutral";
  if (r.includes("long")) return "text-bull";
  if (r.includes("short")) return "text-bear";
  return "text-neutral";
}

export default function Flow({ api, symbol, period }: { api: Api; symbol: string; period: string }) {
  const [data, setData] = useState<FlowResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [upgrade, setUpgrade] = useState(false);

  async function run() {
    setLoading(true);
    setError(null);
    setUpgrade(false);
    try {
      const res = (await api.flow(symbol, period)) as FlowResult;
      setData(res);
    } catch (e: any) {
      if (e?.status === 402) setUpgrade(true);
      else setError(e?.message || "Flow load failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-4">
        <div>
          <h2 className="text-lg font-semibold">Institutional Flow</h2>
          <p className="text-neutral/60 text-xs mt-0.5">
            Perp funding, open interest, retail long/short, and aggressive taker flow for {symbol.replace("_", "/")}.
          </p>
        </div>
        <button
          onClick={run}
          disabled={loading}
          className="shrink-0 bg-accent hover:bg-blue-600 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-semibold transition"
        >
          {loading ? "Loading flow…" : data ? "Refresh" : "Load flow"}
        </button>
      </div>

      {upgrade && (
        <div className="bg-accent/10 border border-accent/30 rounded-xl p-4 text-sm mb-4">
          The Institutional Flow Dashboard is a <span className="font-semibold">Premium</span> feature. Upgrade to unlock it.
        </div>
      )}
      {error && (
        <div className="bg-bear/15 border border-bear/40 rounded-xl p-3 text-bear text-sm mb-4">{error}</div>
      )}

      {!data && !loading && !error && !upgrade && (
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center">
          <p className="text-neutral text-sm">Load derivatives positioning &amp; flow intelligence for {symbol.replace("_", "/")}.</p>
        </div>
      )}

      {data && !data.available && (
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center">
          <p className="text-neutral text-sm">{data.message || "Flow data unavailable."}</p>
        </div>
      )}

      {data && data.available && (
        <>
          {/* Narrative */}
          {data.narrative && (
            <div className="bg-panel rounded-xl border border-white/5 p-4 mb-6">
              <div className="text-sm font-semibold flex items-center gap-2">
                <span>🏦 Positioning Read</span>
                {data.cached && <span className="text-[10px] text-neutral/50 font-normal">cached</span>}
              </div>
              {data.narrative.headline && <p className="text-sm text-gray-100 mt-2">{data.narrative.headline}</p>}
              {data.narrative.key_points && data.narrative.key_points.length > 0 && (
                <ul className="mt-2 space-y-1 text-sm text-gray-300 list-disc list-inside">
                  {data.narrative.key_points.map((p, i) => <li key={i}>{p}</li>)}
                </ul>
              )}
              {data.narrative.squeeze_watch && (
                <p className="text-sm text-yellow-400 mt-2">
                  <span className="text-neutral/60">Squeeze watch: </span>{data.narrative.squeeze_watch}
                </p>
              )}
            </div>
          )}

          {/* Stream cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <FlowCard title="Funding" available={data.funding.available}>
              {data.funding.available && (
                <>
                  <Big className={regimeColor(data.funding.regime)}>{data.funding.latest_pct}%</Big>
                  <Sub>{trendIcon(data.funding.trend)} {String(data.funding.regime || "").replace(/_/g, " ")}</Sub>
                </>
              )}
            </FlowCard>

            <FlowCard title="Open Interest" available={data.open_interest.available}>
              {data.open_interest.available && (
                <>
                  <Big className={data.open_interest.trend === "rising" ? "text-bull" : data.open_interest.trend === "falling" ? "text-bear" : ""}>
                    {trendIcon(data.open_interest.trend)}
                  </Big>
                  <Sub>{data.open_interest.change_pct != null ? `${data.open_interest.change_pct}%` : ""} {data.open_interest.trend}</Sub>
                </>
              )}
            </FlowCard>

            <FlowCard title="Retail L/S" available={data.long_short.available}>
              {data.long_short.available && (
                <>
                  <Big className={regimeColor(data.long_short.regime)}>{data.long_short.ratio}</Big>
                  <Sub>{data.long_short.long_pct}% long · {data.long_short.short_pct}% short</Sub>
                </>
              )}
            </FlowCard>

            <FlowCard title="Taker Flow" available={data.taker_flow.available}>
              {data.taker_flow.available && (
                <>
                  <Big className={data.taker_flow.flow === "buyers_aggressive" ? "text-bull" : data.taker_flow.flow === "sellers_aggressive" ? "text-bear" : ""}>
                    {data.taker_flow.latest_ratio}
                  </Big>
                  <Sub>{String(data.taker_flow.flow || "").replace(/_/g, " ")}</Sub>
                </>
              )}
            </FlowCard>
          </div>

          {/* Deterministic positioning signals */}
          {data.positioning?.signals?.length > 0 && (
            <div className="mt-4 bg-panel rounded-xl border border-white/5 p-4">
              <div className="text-[11px] text-neutral/60 uppercase tracking-wide mb-2">Positioning signals</div>
              <ul className="space-y-1.5 text-sm text-gray-300">
                {data.positioning.signals.map((s, i) => (
                  <li key={i} className="flex gap-2"><span className="text-yellow-400">⚠</span>{s}</li>
                ))}
              </ul>
            </div>
          )}

          {data.disclaimer && <p className="text-[10px] text-neutral/40 mt-4">{data.disclaimer}</p>}
        </>
      )}
    </div>
  );
}

function FlowCard({ title, available, children }: { title: string; available: boolean; children: React.ReactNode }) {
  return (
    <div className="bg-panel rounded-xl border border-white/5 p-4">
      <div className="text-neutral text-[11px] uppercase tracking-wide">{title}</div>
      {available ? <div className="mt-1">{children}</div>
        : <div className="mt-1 text-neutral/40 text-sm">unavailable</div>}
    </div>
  );
}
function Big({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={`text-2xl font-bold ${className || ""}`}>{children}</div>;
}
function Sub({ children }: { children: React.ReactNode }) {
  return <div className="text-neutral/60 text-xs mt-0.5 capitalize">{children}</div>;
}
