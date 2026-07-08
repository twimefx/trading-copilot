"use client";

import { useEffect, useState } from "react";
import { SignInButton, SignUpButton, UserButton } from "@clerk/nextjs";
import TradingViewChart from "@/components/TradingViewChart";
import Scanner from "@/components/Scanner";
import Journal from "@/components/Journal";
import { useApi, MeResponse } from "@/lib/api";

// When Clerk isn't configured the app runs anonymously (no sign-in UI, no tier
// badge) — matching the backend's open mode. Flip on by setting the Clerk keys.
const CLERK_ENABLED = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

type Range = { low: number | null; high: number | null; source: string };
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

const CRYPTO_PRESETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"];
const FOREX_PRESETS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "XAU_USD"];

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
function convictionLabel(c?: number) {
  if (c == null) return "";
  if (c >= 67) return "High";
  if (c >= 40) return "Moderate";
  return "Low";
}
function hasRange(r?: Range) {
  return !!r && r.low != null && r.high != null;
}
// Price-aware formatter — mirrors the backend's magnitude-based precision so
// forex/metals bands (e.g. 1.1437) don't collapse to "1.14 – 1.14".
function fmtPrice(n?: number | null) {
  if (n == null) return "—";
  const v = Math.abs(n);
  const dp = v >= 100 ? 2 : v >= 1 ? 4 : v >= 0.01 ? 5 : 6;
  return n.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

export default function Home() {
  const api = useApi();
  const signedIn = api.isSignedIn === true;
  const authReady = api.isSignedIn !== undefined;
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [interval, setIntervalVal] = useState("1h");
  const [useKronos, setUseKronos] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [upgradePrompt, setUpgradePrompt] = useState(false);
  const [result, setResult] = useState<Analysis | null>(null);
  const [view, setView] = useState<"copilot" | "scanner" | "journal">("copilot");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [journalRefresh, setJournalRefresh] = useState(0);
  const [me, setMe] = useState<MeResponse | null>(null);

  async function refreshMe() {
    try {
      setMe(await api.me());
    } catch {
      /* not signed in yet, or backend cold */
    }
  }

  useEffect(() => {
    if (api.isSignedIn) refreshMe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api.isSignedIn]);

  async function upgrade(tier: "pro" | "premium") {
    try {
      const url = await api.startCheckout(tier);
      window.location.href = url;
    } catch (e: any) {
      setError(e.message || "Could not start checkout");
    }
  }

  async function analyze() {
    setLoading(true);
    setError(null);
    setUpgradePrompt(false);
    setResult(null);
    setSaveState("idle");
    try {
      const data = await api.copilot({ symbol, interval, include_kronos: useKronos });
      setResult(data as Analysis);
      refreshMe();
    } catch (e: any) {
      if (e?.status === 402) {
        setUpgradePrompt(true);
        setError(e.message || "Daily limit reached — upgrade to continue.");
      } else {
        setError(e.message || "Request failed");
      }
    } finally {
      setLoading(false);
    }
  }

  async function saveToJournal() {
    if (!result) return;
    setSaveState("saving");
    try {
      await api.journalSave({
        symbol,
        interval,
        analysis: { ...result, symbol, interval },
        status: "idea",
      });
      setSaveState("saved");
      setJournalRefresh((n) => n + 1);
    } catch {
      setSaveState("error");
    }
  }

  return (
    <main className="min-h-screen max-w-5xl mx-auto px-5 py-10">
      <header className="mb-8 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            AI Trading <span className="text-accent">Copilot</span>
          </h1>
          <p className="text-neutral mt-1 text-sm">
            Market intelligence, explained. Not financial advice.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {!CLERK_ENABLED ? (
            <span className="text-[11px] text-neutral/60 border border-white/10 rounded-lg px-2.5 py-1">
              Open preview
            </span>
          ) : signedIn ? (
            <>
              {me && <TierBadge me={me} onUpgrade={upgrade} onManage={async () => {
                try { window.location.href = await api.openBillingPortal(); } catch {}
              }} />}
              <UserButton />
            </>
          ) : (
            <>
              <SignInButton mode="modal">
                <button className="text-sm text-neutral hover:text-white px-3 py-1.5">Sign in</button>
              </SignInButton>
              <SignUpButton mode="modal">
                <button className="bg-accent hover:bg-blue-600 text-white px-4 py-1.5 rounded-lg text-sm font-semibold">
                  Get started
                </button>
              </SignUpButton>
            </>
          )}
        </div>
      </header>

      {CLERK_ENABLED && authReady && !signedIn && (
        <div className="rounded-2xl border border-white/10 bg-panel p-10 text-center">
          <h2 className="text-xl font-semibold">Sign in to run the Copilot</h2>
          <p className="text-neutral text-sm mt-2 max-w-md mx-auto">
            Get transparent, explainable market analysis on crypto and forex.
            Free plan includes 3 analyses a day — upgrade for more, the scanner,
            and the trade journal.
          </p>
          <div className="mt-5 flex justify-center gap-3">
            <SignUpButton mode="modal">
              <button className="bg-accent hover:bg-blue-600 text-white px-5 py-2 rounded-lg text-sm font-semibold">
                Create free account
              </button>
            </SignUpButton>
            <SignInButton mode="modal">
              <button className="bg-panelhi hover:bg-white/10 text-white px-5 py-2 rounded-lg text-sm font-semibold">
                Sign in
              </button>
            </SignInButton>
          </div>
        </div>
      )}

      {signedIn && (
      <>
        {/* signed-in app */}

      {/* Tabs */}
      <div className="flex gap-2 mb-6 border-b border-white/10">
        {(["copilot", "scanner", "journal"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setView(t)}
            className={`px-4 py-2 text-sm font-medium transition border-b-2 -mb-px ${
              view === t ? "border-accent text-white" : "border-transparent text-neutral hover:text-white"
            }`}
          >
            {t === "copilot" ? "AI Copilot" : t === "scanner" ? "Scanner" : "Journal"}
          </button>
        ))}
      </div>

      {view === "journal" && <Journal api={api} refreshKey={journalRefresh} />}

      {view === "scanner" && (
        <Scanner
          api={api}
          onPick={(s) => {
            setSymbol(s);
            setView("copilot");
            setResult(null);
          }}
        />
      )}

      {view === "copilot" && (
      <>
      {/* Controls */}
      <div className="bg-panel rounded-2xl border border-white/5 p-5 mb-6">
        <div className="flex flex-wrap gap-2 mb-3">
          {CRYPTO_PRESETS.map((p) => (
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
          <span className="w-px bg-white/10 mx-1" />
          {FOREX_PRESETS.map((p) => (
            <button
              key={p}
              onClick={() => setSymbol(p)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
                symbol === p ? "bg-accent text-white" : "bg-panelhi text-neutral hover:text-white"
              }`}
            >
              {p.replace("_", "/")}
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

      {/* Live chart */}
      <div className="mb-6">
        <TradingViewChart symbol={symbol} interval={interval} />
      </div>

      {error && !upgradePrompt && (
        <div className="bg-bear/15 border border-bear/40 rounded-xl p-4 text-bear text-sm mb-6">
          {error}. The analysis service may be busy — try again in a moment.
        </div>
      )}

      {upgradePrompt && (
        <div className="bg-accent/10 border border-accent/40 rounded-xl p-5 mb-6">
          <div className="font-semibold text-white">You've hit your daily free limit</div>
          <p className="text-sm text-neutral mt-1">{error}</p>
          <div className="mt-3 flex gap-2">
            <button onClick={() => upgrade("pro")}
              className="bg-accent hover:bg-blue-600 text-white px-4 py-1.5 rounded-lg text-sm font-semibold">
              Upgrade to Pro
            </button>
            <button onClick={() => upgrade("premium")}
              className="bg-panelhi hover:bg-white/10 text-white px-4 py-1.5 rounded-lg text-sm font-semibold">
              Go Premium
            </button>
          </div>
        </div>
      )}

      {loading && !result && (
        <div className="space-y-4 animate-pulse">
          <div className="rounded-2xl border border-white/5 bg-panel p-6 h-44" />
          <div className="grid md:grid-cols-2 gap-4">
            <div className="rounded-2xl border border-white/5 bg-panel h-40" />
            <div className="rounded-2xl border border-white/5 bg-panel h-40" />
          </div>
          <p className="text-center text-xs text-neutral">Opus is analyzing {symbol.replace("_", "/")}…</p>
        </div>
      )}

      {result && (
        <div className="space-y-4">
          {/* Verdict */}
          <div className={`rounded-2xl border p-6 ${leanBg(result.lean)}`}>
            <div className="flex items-baseline justify-between">
              <div>
                <span className="text-neutral text-sm">{symbol.replace("_", "/")} · {interval}</span>
                <div className={`text-3xl font-bold uppercase ${leanColor(result.lean)}`}>
                  {result.lean}
                </div>
              </div>
              <div className="text-right">
                <div className="text-neutral text-xs">CONVICTION</div>
                <div className="text-3xl font-bold">{result.conviction}<span className="text-neutral text-lg">/100</span></div>
                <div className={`text-xs font-semibold ${leanColor(result.lean)}`}>{convictionLabel(result.conviction)}</div>
              </div>
            </div>
            <div className="mt-3 h-2 bg-black/30 rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-700 ${result.lean === "bullish" ? "bg-bull" : result.lean === "bearish" ? "bg-bear" : "bg-neutral"}`}
                style={{ width: `${result.conviction ?? 0}%` }}
              />
            </div>
            <p className="mt-4 text-sm leading-relaxed text-gray-200">{result.summary}</p>

            {/* Data coverage — transparent about what fed the call */}
            <div className="mt-4 flex flex-wrap gap-2 pt-3 border-t border-white/10">
              <span className="text-[11px] text-neutral/70 mr-1 self-center">INPUTS:</span>
              <Coverage label="Technicals" on />
              <Coverage label="Funding / OI" on={false} hint="geo-restricted on host" />
              <Coverage
                label="Kronos range"
                on={result.range_24h?.source === "Kronos"}
                hint={result.range_24h?.source === "Kronos" ? undefined : "using ATR estimate"}
              />
            </div>
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
            {hasRange(result.range_24h) ? (
              <div className="bg-panel rounded-2xl border border-white/5 p-5">
                <h3 className="text-accent font-semibold text-sm mb-2">
                  24H RANGE <span className="text-neutral/60 font-normal">· {result.range_24h!.source}</span>
                </h3>
                <div className="text-lg font-mono">
                  ${fmtPrice(result.range_24h!.low)} – ${fmtPrice(result.range_24h!.high)}
                </div>
                <p className="text-xs text-neutral mt-1">
                  {result.range_24h!.source === "Kronos"
                    ? "Kronos volatility forecast — a likely band, not a direction call."
                    : "ATR-based volatility estimate. Enable Kronos range for the model forecast."}
                </p>
              </div>
            ) : (
              <div className="bg-panel rounded-2xl border border-dashed border-white/10 p-5">
                <h3 className="text-neutral font-semibold text-sm mb-2">24H RANGE</h3>
                <p className="text-xs text-neutral/70">
                  Volatility band unavailable for this analysis.
                </p>
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
            <span className="font-mono">analysis cost: ${result.cost_usd != null ? result.cost_usd.toFixed(4) : "—"}</span>
            <div className="flex items-center gap-3">
              {saveState === "saved" && <span className="text-bull">✓ Saved to Journal</span>}
              {saveState === "error" && <span className="text-bear">Save failed — retry</span>}
              <button
                onClick={saveToJournal}
                disabled={saveState === "saving" || saveState === "saved"}
                className="bg-panelhi hover:bg-white/10 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg text-xs font-semibold transition"
              >
                {saveState === "saving" ? "Saving…" : saveState === "saved" ? "Saved" : "+ Save to Journal"}
              </button>
              <span className="text-neutral/50">Opus reasoning</span>
            </div>
          </div>
          <p className="text-xs text-neutral/60 border-t border-white/5 pt-3">{result.disclaimer}</p>
        </div>
      )}
      </>
      )}
      </>
      )}
    </main>
  );
}

function TierBadge({
  me,
  onUpgrade,
  onManage,
}: {
  me: MeResponse;
  onUpgrade: (tier: "pro" | "premium") => void;
  onManage: () => void;
}) {
  const label = me.tier.toUpperCase();
  const color =
    me.tier === "premium" ? "bg-yellow-400/15 text-yellow-300 border-yellow-400/40"
    : me.tier === "pro" ? "bg-accent/15 text-accent border-accent/40"
    : "bg-white/5 text-neutral border-white/10";
  const remaining =
    me.copilot_calls_remaining == null ? "∞" : `${me.copilot_calls_remaining}`;
  return (
    <div className="flex items-center gap-2">
      <span className={`px-2.5 py-1 rounded-lg text-[11px] font-bold border ${color}`}>
        {label}
        <span className="ml-1.5 font-normal opacity-70">{remaining} left today</span>
      </span>
      {me.tier === "free" && (
        <button
          onClick={() => onUpgrade("pro")}
          className="bg-accent hover:bg-blue-600 text-white px-3 py-1 rounded-lg text-[11px] font-semibold"
        >
          Upgrade
        </button>
      )}
      {me.tier !== "free" && (
        <button
          onClick={onManage}
          className="text-[11px] text-neutral hover:text-white underline"
        >
          Manage
        </button>
      )}
    </div>
  );
}

function Coverage({ label, on, hint }: { label: string; on: boolean; hint?: string }) {
  return (
    <span
      title={hint}
      className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-medium ${
        on ? "bg-bull/10 text-bull" : "bg-white/5 text-neutral/60"
      }`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${on ? "bg-bull" : "bg-neutral/50"}`} />
      {label}
      {!on && hint && <span className="text-neutral/40">· {hint}</span>}
    </span>
  );
}
