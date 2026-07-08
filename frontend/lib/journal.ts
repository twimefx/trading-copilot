// Trade Journal shared types. All API calls now go through the authenticated
// `useApi()` hook in lib/api.ts (Clerk JWT). This file is types-only.

export type Range = { low: number | null; high: number | null; source: string | null };

export type JournalEntry = {
  id: string;
  created_at: number;
  updated_at: number;
  symbol: string;
  interval?: string;
  lean?: string;
  conviction?: number;
  summary?: string;
  range_24h: Range;
  analysis?: any;
  status: "idea" | "open" | "closed" | "cancelled";
  direction: "long" | "short" | "none";
  entry_price?: number | null;
  exit_price?: number | null;
  size?: number | null;
  stop_price?: number | null;
  target_price?: number | null;
  outcome?: "win" | "loss" | "breakeven" | null;
  pnl?: number | null;
  notes?: string | null;
};

export type JournalStats = {
  closed_trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  total_pnl: number;
};
