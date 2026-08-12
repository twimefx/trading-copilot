"use client";

import { memo, useEffect, useRef } from "react";

// Maps our interval values to TradingView's interval codes.
const TV_INTERVAL: Record<string, string> = {
  "15m": "15",
  "1h": "60",
  "4h": "240",
  "1d": "D",
};

// Maps a Binance-style symbol to a TradingView symbol.
// Crypto USDT pairs -> BINANCE:<sym>. Forex (EUR_USD / EURUSD) -> OANDA:EURUSD.
// Plain US-equity tickers (for example NVDA) -> NASDAQ:<ticker>.
function toTvSymbol(symbol: string): string {
  const s = symbol.toUpperCase();
  if (s.endsWith("USDT") || s.endsWith("USDC") || s.endsWith("BTC")) {
    return `BINANCE:${s}`;
  }
  // Forex / metals: strip separators -> OANDA:EURUSD
  const cleaned = s.replace(/[_/-]/g, "");
  if (cleaned.length === 6) {
    return `OANDA:${cleaned}`;
  }
  return `NASDAQ:${s}`;
}

function TradingViewChart({ symbol, interval }: { symbol: string; interval: string }) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) return;
    container.current.innerHTML = "";

    const script = document.createElement("script");
    script.src =
      "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.type = "text/javascript";
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol: toTvSymbol(symbol),
      interval: TV_INTERVAL[interval] || "60",
      timezone: "Etc/UTC",
      theme: "dark",
      style: "1",
      locale: "en",
      backgroundColor: "#121826",
      gridColor: "rgba(255,255,255,0.05)",
      hide_top_toolbar: false,
      hide_legend: false,
      allow_symbol_change: false,
      save_image: false,
      studies: ["STD;RSI", "STD;MACD"],
      support_host: "https://www.tradingview.com",
    });
    container.current.appendChild(script);
  }, [symbol, interval]);

  return (
    <div
      key={`${symbol}-${interval}`}
      className="rounded-2xl overflow-hidden border border-white/5 bg-panel"
      style={{ height: 460 }}
    >
      {/* TradingView's embed script injects its widget into this container.
          We do NOT render a React-managed child here: React reconciliation
          would otherwise wipe the script-injected DOM on parent re-renders
          (e.g. when an analysis result loads), blanking the chart. */}
      <div ref={container} className="tradingview-widget-container" style={{ height: "100%", width: "100%" }} />
    </div>
  );
}

// Memoized so parent state changes (loading / analysis result) don't force a
// remount of the chart — only a symbol/interval change re-runs the embed.
export default memo(TradingViewChart);
