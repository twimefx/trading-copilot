import Link from "next/link";

// ─── Landing page (public) ──────────────────────────────────────────────────
// Design intent: institutional trading terminal, not generic SaaS. Deep-space
// background, a single gold accent for "intelligence", vivid bull/bear only
// where market direction is shown. Real product features — no filler sections.

const FREE = {
  name: "Free",
  price: "$0",
  cadence: "forever",
  tagline: "Feel the terminal.",
  features: [
    "AI Market Copilot — 3 analyses / day",
    "Crypto + Forex coverage",
    "Explainable bullish / bearish / neutral calls",
    "24h volatility range + invalidation",
  ],
  cta: "Start free",
  href: "/app",
  accent: false,
};

const PRO = {
  name: "Pro",
  price: "$49",
  cadence: "/mo",
  tagline: "For the disciplined trader.",
  features: [
    "Unlimited Copilot analyses",
    "Market Scanner — ranked opportunities",
    "Trade Journal + AI behavioral coaching",
    "Portfolio Copilot — open-book risk read",
    "Price & scanner alerts to Telegram",
  ],
  cta: "Go Pro",
  href: "/app",
  accent: false,
};

const PREMIUM = {
  name: "Premium",
  price: "$199",
  cadence: "/mo",
  tagline: "The full intelligence desk.",
  features: [
    "Everything in Pro",
    "Multi-Agent Debate — 7 agents + judge",
    "Bull/Bear researcher rounds + risk verdict",
    "Institutional Flow — funding, OI, positioning",
    "AI Strategy Builder — NL to real backtest",
    "Kronos 24h volatility range on GPU",
  ],
  cta: "Go Premium",
  href: "/app",
  accent: true,
};

function Logo() {
  return (
    <span className="inline-flex items-center gap-2.5">
      <span className="relative inline-flex h-7 w-7 items-center justify-center rounded-md bg-accent/15 ring-1 ring-accent/40">
        {/* candlestick mark */}
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" aria-hidden>
          <path d="M7 8v8M7 6v2M7 16v2" stroke="#e8c468" strokeWidth="1.6" strokeLinecap="round" />
          <rect x="5.6" y="9" width="2.8" height="6" rx="0.8" fill="#16c784" />
          <path d="M17 6v9M17 4v2M17 15v3" stroke="#e8c468" strokeWidth="1.6" strokeLinecap="round" />
          <rect x="15.6" y="7" width="2.8" height="8" rx="0.8" fill="#ea3943" />
        </svg>
      </span>
      <span className="text-[15px] font-semibold tracking-tight text-ink">
        AI Trading <span className="text-accent">Copilot</span>
      </span>
    </span>
  );
}

function Nav() {
  return (
    <header className="sticky top-0 z-40 border-b border-white/5 bg-bg/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5">
        <Link href="/" aria-label="Home"><Logo /></Link>
        <nav className="hidden items-center gap-7 text-sm text-neutral md:flex">
          <a href="#features" className="hover:text-ink transition-colors">Features</a>
          <a href="#how" className="hover:text-ink transition-colors">How it works</a>
          <a href="#pricing" className="hover:text-ink transition-colors">Pricing</a>
        </nav>
        <div className="flex items-center gap-3">
          <Link href="/app" className="text-sm text-neutral hover:text-ink transition-colors">
            Sign in
          </Link>
          <Link
            href="/app"
            className="rounded-full bg-accent px-4 py-2 text-sm font-semibold text-bg shadow-glow transition hover:bg-accenthi"
          >
            Open the terminal
          </Link>
        </div>
      </div>
    </header>
  );
}

function Ticker() {
  // Decorative, honest placeholder ticks (no fake live data claims).
  const items = [
    ["BTCUSDT", "+2.4%", "bull"], ["ETHUSDT", "+1.1%", "bull"], ["SOLUSDT", "-0.8%", "bear"],
    ["EUR/USD", "+0.2%", "bull"], ["XAU/USD", "+0.6%", "bull"], ["GBP/USD", "-0.3%", "bear"],
    ["BNBUSDT", "+0.9%", "bull"], ["USD/JPY", "-0.1%", "bear"], ["XRPUSDT", "+3.2%", "bull"],
  ] as const;
  const row = [...items, ...items];
  return (
    <div className="relative overflow-hidden border-y border-white/5 bg-panel/40 py-3">
      <div className="ticker-track flex w-max gap-10 whitespace-nowrap px-5">
        {row.map(([s, c, d], i) => (
          <span key={i} className="inline-flex items-center gap-2 text-[13px] font-mono">
            <span className="text-neutral">{s}</span>
            <span className={d === "bull" ? "text-bull" : "text-bear"}>{c}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function Hero() {
  return (
    <section className="relative overflow-hidden">
      {/* soft radial glow, not a heavy gradient */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(60% 50% at 50% 0%, rgba(232,196,104,0.08), transparent 70%), radial-gradient(50% 40% at 80% 30%, rgba(22,199,132,0.05), transparent 70%)",
        }}
      />
      <div className="relative mx-auto max-w-6xl px-5 pt-24 pb-16 text-center">
        <span className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-3.5 py-1.5 text-xs font-medium text-accenthi">
          <span className="h-1.5 w-1.5 rounded-full bg-bull" />
          Market intelligence, explained — not advice
        </span>
        <h1 className="mx-auto mt-6 max-w-3xl text-5xl font-semibold leading-[1.05] tracking-tightest text-ink md:text-7xl">
          Trade with a{" "}
          <span className="bg-gradient-to-r from-accenthi via-accent to-accenthi bg-clip-text text-transparent">
            research desk
          </span>{" "}
          in your corner.
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-neutral">
          The Copilot reads price, positioning, and volatility across crypto and forex,
          then explains its reasoning in plain language — every call backed by evidence,
          never a black box.
        </p>
        <div className="mt-10 flex items-center justify-center gap-4">
          <Link
            href="/app"
            className="rounded-full bg-accent px-7 py-3.5 text-base font-semibold text-bg shadow-glow transition hover:bg-accenthi"
          >
            Start analyzing free
          </Link>
          <a
            href="#pricing"
            className="rounded-full border border-white/15 px-7 py-3.5 text-base font-semibold text-ink transition hover:border-accent/50 hover:text-accenthi"
          >
            See pricing
          </a>
        </div>
        <p className="mt-5 text-xs text-neutral/70">
          Free plan · 3 analyses a day · no card required
        </p>
      </div>
      <Ticker />
    </section>
  );
}

function FeatureCard({
  kicker, title, body, tone,
}: { kicker: string; title: string; body: string; tone?: "gold" | "bull" | "bear" }) {
  const ring =
    tone === "gold" ? "hover:border-accent/40" : tone === "bull" ? "hover:border-bull/40" : tone === "bear" ? "hover:border-bear/40" : "hover:border-white/20";
  const chip =
    tone === "gold" ? "text-accent bg-accent/10 border-accent/30" : tone === "bull" ? "text-bull bg-bull/10 border-bull/30" : tone === "bear" ? "text-bear bg-bear/10 border-bear/30" : "text-neutral bg-white/5 border-white/15";
  return (
    <div className={`group rounded-xl2 border border-white/10 bg-panel p-7 shadow-card transition ${ring}`}>
      <span className={`inline-block rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${chip}`}>
        {kicker}
      </span>
      <h3 className="mt-4 text-xl font-semibold tracking-tight text-ink">{title}</h3>
      <p className="mt-2.5 text-[15px] leading-relaxed text-neutral">{body}</p>
    </div>
  );
}

function Features() {
  return (
    <section id="features" className="mx-auto max-w-6xl px-5 py-24">
      <div className="max-w-2xl">
        <p className="text-sm font-semibold uppercase tracking-widest text-accent">The desk</p>
        <h2 className="mt-3 text-4xl font-semibold tracking-tightest text-ink md:text-5xl">
          Six instruments. One intelligence layer.
        </h2>
        <p className="mt-4 text-lg text-neutral">
          Each tool is deterministic where it counts and explainable where it matters —
          the model computes the real numbers first, then reasons over them.
        </p>
      </div>
      <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
        <FeatureCard tone="gold" kicker="Copilot"
          title="AI Market Copilot"
          body="A grounded read on any symbol — momentum, trend, positioning, and a 24h volatility range — with the evidence cited and the reasoning shown." />
        <FeatureCard kicker="Scanner"
          title="Market Scanner"
          body="Rank a whole watchlist by signal strength with a 4-state market-regime gate, so you see when the tape favors risk and when it doesn't." />
        <FeatureCard kicker="Debate"
          title="Multi-Agent Debate"
          body="Seven specialist agents argue bull and bear cases, a judge weighs them, and a risk desk issues an approve / caution / reject verdict." />
        <FeatureCard kicker="Flow"
          title="Institutional Flow"
          body="Funding, open interest, and positioning — the derivatives tape institutions watch — interpreted into a plain-language regime." />
        <FeatureCard kicker="Strategy"
          title="AI Strategy Builder"
          body="Describe a strategy in plain English; deterministic, look-ahead-safe code backtests it and shows the honest result against buy-and-hold." />
        <FeatureCard tone="bull" kicker="Journal"
          title="Journal + Coaching"
          body="Log trades, and an AI coach reads your behavior — patterns in sizing, timing, and discipline — not just P&L." />
      </div>
    </section>
  );
}

function HowItWorks() {
  const steps = [
    ["01", "Real data first", "Live crypto and forex feeds, derivatives positioning, and a Kronos volatility model on GPU. No fabricated numbers."],
    ["02", "Deterministic core", "Indicators, ranges, and backtests are computed by code — auditable and reproducible — before any model speaks."],
    ["03", "Explainable reasoning", "The AI only interprets the verified numbers and shows its evidence, so you can judge the call yourself."],
  ] as const;
  return (
    <section id="how" className="border-y border-white/5 bg-panel/30">
      <div className="mx-auto max-w-6xl px-5 py-24">
        <p className="text-sm font-semibold uppercase tracking-widest text-accent">Why it’s different</p>
        <h2 className="mt-3 max-w-2xl text-4xl font-semibold tracking-tightest text-ink md:text-5xl">
          Built to be trusted, not just believed.
        </h2>
        <div className="mt-12 grid gap-5 md:grid-cols-3">
          {steps.map(([n, t, b]) => (
            <div key={n} className="rounded-xl2 border border-white/10 bg-bg/60 p-7">
              <span className="font-mono text-sm text-accent">{n}</span>
              <h3 className="mt-3 text-lg font-semibold text-ink">{t}</h3>
              <p className="mt-2 text-[15px] leading-relaxed text-neutral">{b}</p>
            </div>
          ))}
        </div>
        <p className="mt-8 max-w-3xl text-sm leading-relaxed text-neutral/80">
          Honest by design: the Copilot is decision-support, not financial advice. It never
          promises returns, and it tells you when the data isn’t there.
        </p>
      </div>
    </section>
  );
}

function TierCard({ t }: { t: typeof FREE }) {
  return (
    <div
      className={`relative flex flex-col rounded-xl2 border p-8 shadow-card transition ${
        t.accent
          ? "border-accent/50 bg-gradient-to-b from-panelhi to-panel shadow-glow"
          : "border-white/10 bg-panel hover:border-white/20"
      }`}
    >
      {t.accent && (
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-accent px-3 py-0.5 text-[11px] font-bold uppercase tracking-wider text-bg">
          Most powerful
        </span>
      )}
      <h3 className="text-lg font-semibold text-ink">{t.name}</h3>
      <p className="mt-1 text-sm text-neutral">{t.tagline}</p>
      <div className="mt-5 flex items-baseline gap-1">
        <span className="text-4xl font-semibold tracking-tight text-ink">{t.price}</span>
        <span className="text-sm text-neutral">{t.cadence}</span>
      </div>
      <ul className="mt-6 flex-1 space-y-3">
        {t.features.map((f) => (
          <li key={f} className="flex gap-2.5 text-[15px] text-neutral">
            <svg viewBox="0 0 20 20" className={`mt-0.5 h-4 w-4 shrink-0 ${t.accent ? "text-accent" : "text-bull"}`} fill="currentColor" aria-hidden>
              <path fillRule="evenodd" d="M16.7 5.3a1 1 0 010 1.4l-7 7a1 1 0 01-1.4 0l-3-3a1 1 0 111.4-1.4l2.3 2.3 6.3-6.3a1 1 0 011.4 0z" clipRule="evenodd" />
            </svg>
            {f}
          </li>
        ))}
      </ul>
      <Link
        href={t.href}
        className={`mt-8 rounded-full py-3 text-center text-sm font-semibold transition ${
          t.accent
            ? "bg-accent text-bg hover:bg-accenthi"
            : "border border-white/15 text-ink hover:border-accent/50 hover:text-accenthi"
        }`}
      >
        {t.cta}
      </Link>
    </div>
  );
}

function Pricing() {
  return (
    <section id="pricing" className="mx-auto max-w-6xl px-5 py-24">
      <div className="mx-auto max-w-2xl text-center">
        <p className="text-sm font-semibold uppercase tracking-widest text-accent">Pricing</p>
        <h2 className="mt-3 text-4xl font-semibold tracking-tightest text-ink md:text-5xl">
          Pay for the desk you actually use.
        </h2>
        <p className="mt-4 text-lg text-neutral">
          Start free. Upgrade when the terminal earns its seat.
        </p>
      </div>
      <div className="mt-14 grid gap-6 md:grid-cols-3">
        <TierCard t={FREE} />
        <TierCard t={PRO} />
        <TierCard t={PREMIUM} />
      </div>
      <p className="mt-8 text-center text-xs text-neutral/70">
        Prices in USD. Cancel anytime. Analysis is decision-support, not financial advice.
      </p>
    </section>
  );
}

function FinalCta() {
  return (
    <section className="border-t border-white/5">
      <div className="mx-auto max-w-6xl px-5 py-24 text-center">
        <h2 className="mx-auto max-w-2xl text-4xl font-semibold tracking-tightest text-ink md:text-5xl">
          Your next read is one click away.
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-lg text-neutral">
          Open the terminal and run your first analysis in under a minute.
        </p>
        <Link
          href="/app"
          className="mt-9 inline-block rounded-full bg-accent px-9 py-4 text-base font-semibold text-bg shadow-glow transition hover:bg-accenthi"
        >
          Open the terminal
        </Link>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t border-white/5">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-5 py-10 text-sm text-neutral md:flex-row">
        <Logo />
        <p className="text-xs text-neutral/70">
          AI Trading Copilot — market intelligence, explained. Not financial advice.
        </p>
        <Link href="/app" className="hover:text-accenthi transition-colors">
          Open the terminal →
        </Link>
      </div>
    </footer>
  );
}

export default function Landing() {
  return (
    <main className="min-h-screen bg-bg text-ink antialiased">
      <Nav />
      <Hero />
      <Features />
      <HowItWorks />
      <Pricing />
      <FinalCta />
      <Footer />
    </main>
  );
}
