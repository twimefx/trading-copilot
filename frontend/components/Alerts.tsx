"use client";

import { useEffect, useState } from "react";
import type { useApi } from "@/lib/api";

type Api = ReturnType<typeof useApi>;

type Rule = {
  id: string;
  kind: string;
  config: Record<string, any>;
  active: boolean;
  cooldown_s: number;
  last_fired_at?: number | null;
  created_at: number;
};

type AlertEvent = {
  id: string;
  rule_id: string;
  symbol?: string | null;
  message: string;
  channels: string[];
  created_at: number;
};

function ts(epoch?: number | null) {
  if (!epoch) return "never";
  return new Date(epoch * 1000).toLocaleString();
}

function describe(rule: Rule) {
  const c = rule.config;
  if (rule.kind === "price_above") return `${c.symbol} above ${c.value}`;
  if (rule.kind === "price_below") return `${c.symbol} below ${c.value}`;
  if (rule.kind === "scanner_lean")
    return `Scanner ${c.lean} on ${(c.symbols || []).join(", ")} (${c.interval || "1h"})`;
  return rule.kind;
}

export default function Alerts({ api, watchlist }: { api: Api; watchlist: string[] }) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // form state
  const [kind, setKind] = useState<"price_above" | "price_below" | "scanner_lean">("price_above");
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [value, setValue] = useState("");
  const [lean, setLean] = useState<"bullish" | "bearish">("bullish");
  const [chatId, setChatId] = useState("");
  const [email, setEmail] = useState("");

  async function refresh() {
    try {
      setRules(await api.alertsList());
      setEvents(await api.alertEvents());
    } catch (e: any) {
      setError(e.message || "Failed to load alerts");
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function createRule() {
    setError(null);
    setNotice(null);
    setLoading(true);
    try {
      const config: Record<string, any> = {};
      if (kind === "price_above" || kind === "price_below") {
        const v = parseFloat(value);
        if (!isFinite(v)) throw new Error("Enter a numeric price threshold.");
        config.symbol = symbol.trim().toUpperCase();
        config.value = v;
      } else {
        config.symbols = watchlist.length ? watchlist : [symbol.trim().toUpperCase()];
        config.lean = lean;
        config.interval = "1h";
      }
      if (chatId.trim()) config.telegram_chat_id = chatId.trim();
      if (email.trim()) config.email = email.trim();
      await api.alertCreate({ kind, config });
      setValue("");
      setNotice("Alert rule created.");
      await refresh();
    } catch (e: any) {
      setError(e.message || "Could not create rule");
    } finally {
      setLoading(false);
    }
  }

  async function toggle(rule: Rule) {
    await api.alertUpdate(rule.id, { active: !rule.active });
    await refresh();
  }

  async function remove(rule: Rule) {
    await api.alertDelete(rule.id);
    await refresh();
  }

  async function test(rule: Rule) {
    setNotice(null);
    try {
      await api.alertTest(rule.id);
      setNotice("Test notification attempted — check your Telegram/email, and the event log below.");
      await refresh();
    } catch (e: any) {
      setError(e.message || "Test failed");
    }
  }

  return (
    <div className="space-y-6">
      <div className="bg-panel rounded-2xl border border-white/5 p-5">
        <h3 className="font-semibold mb-1">Create alert</h3>
        <p className="text-xs text-neutral mb-4">
          Alerts are checked every 15 minutes by the scheduler and delivered via Telegram and/or
          email. Leave both blank to just log events here.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as any)}
            className="bg-panelhi rounded-lg px-3 py-2 text-sm outline-none"
          >
            <option value="price_above">Price rises above</option>
            <option value="price_below">Price falls below</option>
            <option value="scanner_lean">Scanner turns bullish/bearish</option>
          </select>
          {kind !== "scanner_lean" ? (
            <>
              <input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                className="bg-panelhi rounded-lg px-3 py-2 text-sm w-32 outline-none"
                placeholder="BTCUSDT"
              />
              <input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className="bg-panelhi rounded-lg px-3 py-2 text-sm w-32 outline-none"
                placeholder="70000"
                inputMode="decimal"
              />
            </>
          ) : (
            <select
              value={lean}
              onChange={(e) => setLean(e.target.value as any)}
              className="bg-panelhi rounded-lg px-3 py-2 text-sm outline-none"
            >
              <option value="bullish">Bullish lean</option>
              <option value="bearish">Bearish lean</option>
            </select>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3 mt-3">
          <input
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
            className="bg-panelhi rounded-lg px-3 py-2 text-sm flex-1 min-w-[180px] outline-none"
            placeholder="Telegram chat ID (optional)"
          />
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="bg-panelhi rounded-lg px-3 py-2 text-sm flex-1 min-w-[180px] outline-none"
            placeholder="Email (optional)"
            type="email"
          />
          <button
            onClick={createRule}
            disabled={loading}
            className="bg-blue hover:bg-bluehi disabled:opacity-50 text-white px-4 py-2 rounded-full text-sm font-semibold"
          >
            {loading ? "Creating…" : "Create alert"}
          </button>
        </div>
        {kind === "scanner_lean" && (
          <p className="text-[11px] text-neutral/70 mt-2">
            Scans your saved watchlist{watchlist.length ? ` (${watchlist.join(", ")})` : " (empty — will use the symbol box)"}.
          </p>
        )}
        {error && <p className="text-bear text-sm mt-3">{error}</p>}
        {notice && <p className="text-bull text-sm mt-3">{notice}</p>}
      </div>

      <div className="bg-panel rounded-2xl border border-white/5 p-5">
        <h3 className="font-semibold mb-3">Your rules ({rules.length})</h3>
        {rules.length === 0 && <p className="text-sm text-neutral">No alert rules yet.</p>}
        <div className="space-y-2">
          {rules.map((r) => (
            <div
              key={r.id}
              className="flex flex-wrap items-center justify-between gap-2 bg-panelhi rounded-xl px-4 py-3"
            >
              <div>
                <div className="text-sm font-medium">{describe(r)}</div>
                <div className="text-[11px] text-neutral">
                  last fired {ts(r.last_fired_at)} · cooldown {Math.round(r.cooldown_s / 60)}m
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => test(r)}
                  className="text-xs px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10"
                >
                  Test
                </button>
                <button
                  onClick={() => toggle(r)}
                  className={`text-xs px-3 py-1.5 rounded-lg ${
                    r.active ? "bg-bull/15 text-bull" : "bg-white/5 text-neutral"
                  }`}
                >
                  {r.active ? "Active" : "Paused"}
                </button>
                <button
                  onClick={() => remove(r)}
                  className="text-xs px-3 py-1.5 rounded-lg bg-bear/15 text-bear hover:bg-bear/25"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-panel rounded-2xl border border-white/5 p-5">
        <h3 className="font-semibold mb-3">Recent events ({events.length})</h3>
        {events.length === 0 && <p className="text-sm text-neutral">Nothing fired yet.</p>}
        <div className="space-y-2">
          {events.slice(0, 20).map((e) => (
            <div key={e.id} className="bg-panelhi rounded-xl px-4 py-3">
              <div className="text-sm">{e.message}</div>
              <div className="text-[11px] text-neutral mt-1">
                {ts(e.created_at)}
                {e.channels?.length ? ` · via ${e.channels.join(", ")}` : " · logged only"}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
