"use client";

import { useState } from "react";
import type { useApi } from "@/lib/api";

type Api = ReturnType<typeof useApi>;

type AgentCard = {
  agent: string;
  name: string;
  lean: string;
  conviction: number;
  rationale: string;
  key_evidence: string[];
  ok?: boolean;
};

type Consensus = {
  lean: string;
  confidence: number;
  agreement: number;
  divided: boolean;
  score: number;
  vote_counts: { bullish: number; bearish: number; neutral: number };
  avg_conviction: number;
};

type ResearcherCase = {
  side: string;
  supporters: number;
  avg_conviction: number;
  points: string[];
  agents: string[];
};

type RiskVerdict = {
  verdict: "APPROVE" | "CAUTION" | "REJECT";
  confidence: number;
  reasons: string[];
  note?: string;
};

type DebateResult = {
  symbol: string;
  interval: string;
  consensus: Consensus;
  agents: AgentCard[];
  synthesis: string;
  dissent: string;
  what_would_change_our_mind: string;
  researchers?: { rounds: number; bull: ResearcherCase; bear: ResearcherCase };
  risk?: RiskVerdict;
  disclaimer?: string;
  cost_usd?: number;
  cached?: boolean;
};

function leanColor(lean: string) {
  return lean === "bullish" ? "text-bull" : lean === "bearish" ? "text-bear" : "text-neutral";
}
function leanBg(lean: string) {
  return lean === "bullish" ? "bg-bull/15 text-bull"
    : lean === "bearish" ? "bg-bear/15 text-bear" : "bg-white/10 text-neutral";
}
function riskStyle(v?: string) {
  if (v === "APPROVE") return "bg-bull/15 text-bull border-bull/40";
  if (v === "CAUTION") return "bg-yellow-400/10 text-yellow-300 border-yellow-400/40";
  return "bg-bear/15 text-bear border-bear/40";
}

export default function Debate({ api, symbol, interval }: { api: Api; symbol: string; interval: string }) {
  const [data, setData] = useState<DebateResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [upgrade, setUpgrade] = useState(false);

  async function run() {
    setLoading(true);
    setError(null);
    setUpgrade(false);
    try {
      const res = (await api.debate({ symbol, interval })) as DebateResult;
      setData(res);
    } catch (e: any) {
      if (e?.status === 402) setUpgrade(true);
      else setError(e?.message || "Debate failed");
    } finally {
      setLoading(false);
    }
  }

  const c = data?.consensus;

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-4">
        <div>
          <h2 className="text-lg font-semibold">Multi-Agent Debate</h2>
          <p className="text-neutral/60 text-xs mt-0.5">
            A panel of AI analysts debates {symbol.replace("_", "/")} — the disagreement itself is signal.
          </p>
        </div>
        <button
          onClick={run}
          disabled={loading}
          className="shrink-0 bg-blue hover:bg-bluehi disabled:opacity-50 text-white px-4 py-2 rounded-full text-sm font-semibold transition"
        >
          {loading ? "Convening panel…" : data ? "Re-run debate" : "Convene the panel"}
        </button>
      </div>

      {upgrade && (
        <div className="bg-accent/10 border border-accent/30 rounded-xl p-4 text-sm mb-4">
          The Multi-Agent Debate Engine is a <span className="font-semibold">Premium</span> feature. Upgrade to unlock the full analyst panel.
        </div>
      )}

      {error && (
        <div className="bg-bear/15 border border-bear/40 rounded-xl p-3 text-bear text-sm mb-4">{error}</div>
      )}

      {loading && (
        <div className="grid md:grid-cols-2 gap-3 animate-pulse">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="rounded-xl border border-white/5 bg-panel h-28" />
          ))}
        </div>
      )}

      {!data && !loading && !error && !upgrade && (
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center">
          <p className="text-neutral text-sm">
            Convene a panel of five specialist agents (trend, momentum, positioning, volatility, contrarian) to debate {symbol.replace("_", "/")}.
          </p>
        </div>
      )}

      {data && c && (
        <>
          {/* Consensus verdict */}
          <div className="bg-panel rounded-2xl border border-white/5 p-5 mb-6">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div className="flex items-center gap-3">
                <span className={`px-3 py-1 rounded-lg text-sm font-bold uppercase ${leanBg(c.lean)}`}>
                  {c.lean}
                </span>
                {c.divided && (
                  <span className="px-2 py-0.5 rounded bg-yellow-400/10 text-yellow-400 text-[11px] font-semibold uppercase">
                    Panel divided
                  </span>
                )}
                {data.cached && <span className="text-[10px] text-neutral/50">cached</span>}
              </div>
              <div className="text-right">
                <div className="text-[11px] text-neutral/60 uppercase tracking-wide">Confidence</div>
                <div className={`text-2xl font-bold ${c.confidence >= 60 ? "text-bull" : c.confidence >= 40 ? "text-yellow-400" : "text-bear"}`}>
                  {c.confidence}%
                </div>
              </div>
            </div>

            {/* Confidence + agreement bars */}
            <div className="mt-4 space-y-2">
              <Meter label="Confidence" value={c.confidence} />
              <Meter label="Panel agreement" value={c.agreement} />
            </div>

            <div className="flex gap-2 mt-4 text-xs">
              <VoteChip label="Bulls" n={c.vote_counts.bullish} cls="text-bull" />
              <VoteChip label="Bears" n={c.vote_counts.bearish} cls="text-bear" />
              <VoteChip label="Neutral" n={c.vote_counts.neutral} cls="text-neutral" />
              <span className="ml-auto text-neutral/50 self-center">
                avg conviction {c.avg_conviction} · score {c.score}
              </span>
            </div>

            {data.synthesis && <p className="text-sm text-gray-100 mt-4">{data.synthesis}</p>}
            {data.dissent && (
              <p className="text-sm text-gray-400 mt-2">
                <span className="text-neutral/60">Dissent: </span>{data.dissent}
              </p>
            )}
            {data.what_would_change_our_mind && (
              <p className="text-sm text-gray-400 mt-2">
                <span className="text-neutral/60">What would change our mind: </span>
                {data.what_would_change_our_mind}
              </p>
            )}

            {/* Risk-desk verdict (deterministic decision-support, not an order) */}
            {data.risk && (
              <div className={`mt-4 rounded-xl border px-4 py-3 ${riskStyle(data.risk.verdict)}`}>
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <span className="font-bold text-sm uppercase tracking-wide">
                    Risk desk: {data.risk.verdict}
                  </span>
                  <span className="text-[11px] opacity-75">decision-support, not an order</span>
                </div>
                {data.risk.reasons?.length > 0 && (
                  <p className="text-xs opacity-85 mt-1">{data.risk.reasons.join(" · ")}</p>
                )}
              </div>
            )}
          </div>

          {/* Bull vs Bear researcher cases */}
          {data.researchers && (
            <div className="grid md:grid-cols-2 gap-3 mb-6">
              {([data.researchers.bull, data.researchers.bear] as const).map((rc) => (
                <div key={rc.side} className={`bg-panel rounded-xl border p-4 ${rc.side === "bullish" ? "border-bull/20" : "border-bear/20"}`}>
                  <div className="flex items-center justify-between">
                    <span className={`font-semibold text-sm ${rc.side === "bullish" ? "text-bull" : "text-bear"}`}>
                      {rc.side === "bullish" ? "Bull case" : "Bear case"}
                    </span>
                    <span className="text-[11px] text-neutral/60">
                      {rc.supporters} supporter{rc.supporters === 1 ? "" : "s"} · avg {rc.avg_conviction}
                    </span>
                  </div>
                  {rc.points.length > 0 ? (
                    <ul className="mt-2 space-y-1">
                      {rc.points.slice(0, 5).map((p, i) => (
                        <li key={i} className="text-xs text-gray-300 flex gap-2">
                          <span className={rc.side === "bullish" ? "text-bull" : "text-bear"}>•</span>
                          <span>{p}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-neutral/60 mt-2">No {rc.side} supporters on the panel.</p>
                  )}
                  {rc.agents?.length > 0 && (
                    <p className="text-[10px] text-neutral/50 mt-2">via {rc.agents.join(", ")}</p>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Agent cards */}
          <div className="grid md:grid-cols-2 gap-3">
            {data.agents.map((a) => (
              <div key={a.agent} className={`bg-panel rounded-xl border p-4 ${a.agent === "contrarian" ? "border-yellow-400/20" : "border-white/5"}`}>
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-sm">{a.name}</span>
                  <span className={`text-xs font-bold uppercase ${leanColor(a.lean)}`}>
                    {a.lean} · {a.conviction}
                  </span>
                </div>
                <p className="text-sm text-gray-300 mt-2">{a.rationale}</p>
                {a.key_evidence?.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {a.key_evidence.map((e, i) => (
                      <span key={i} className="text-[10px] bg-panelhi text-neutral px-2 py-0.5 rounded">{e}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          {data.disclaimer && <p className="text-[10px] text-neutral/40 mt-4">{data.disclaimer}</p>}
          {data.cost_usd != null && (
            <p className="text-[10px] text-neutral/30 mt-1">panel cost ${data.cost_usd.toFixed(4)}</p>
          )}
        </>
      )}
    </div>
  );
}

function Meter({ label, value }: { label: string; value: number }) {
  const color = value >= 60 ? "bg-bull" : value >= 40 ? "bg-yellow-400" : "bg-bear";
  return (
    <div>
      <div className="flex justify-between text-[11px] text-neutral/60 mb-1">
        <span>{label}</span><span>{value}%</span>
      </div>
      <div className="h-1.5 bg-panelhi rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
      </div>
    </div>
  );
}

function VoteChip({ label, n, cls }: { label: string; n: number; cls: string }) {
  return (
    <span className="bg-panelhi rounded px-2 py-1">
      <span className="text-neutral/60">{label} </span>
      <span className={`font-bold ${cls}`}>{n}</span>
    </span>
  );
}
