// Trade Journal client helpers — owner identity + API calls.
//
// No auth yet: each browser gets a stable random owner id stored in
// localStorage and sent as the X-Owner-Id header. When real auth lands this
// becomes the authenticated user id and nothing else here changes.

const OWNER_KEY = "tc_owner_id";

export function getOwnerId(): string {
  if (typeof window === "undefined") return "";
  const existing = window.localStorage.getItem(OWNER_KEY);
  if (existing) return existing;
  const id: string =
    (crypto as any)?.randomUUID?.() ??
    `o_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  window.localStorage.setItem(OWNER_KEY, id);
  return id;
}

function headers(): HeadersInit {
  return { "Content-Type": "application/json", "X-Owner-Id": getOwnerId() };
}

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

export async function saveEntry(payload: Record<string, any>): Promise<JournalEntry> {
  const res = await fetch("/api/journal", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Save failed (${res.status})`);
  return res.json();
}

export async function listEntries(status?: string): Promise<JournalEntry[]> {
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  const res = await fetch(`/api/journal${q}`, { headers: headers() });
  if (!res.ok) throw new Error(`Load failed (${res.status})`);
  return (await res.json()).entries ?? [];
}

export async function getStats(): Promise<JournalStats> {
  const res = await fetch("/api/journal/stats", { headers: headers() });
  if (!res.ok) throw new Error(`Stats failed (${res.status})`);
  return res.json();
}

export async function updateEntry(
  id: string,
  fields: Record<string, any>,
): Promise<JournalEntry> {
  const res = await fetch(`/api/journal/${id}`, {
    method: "PATCH",
    headers: headers(),
    body: JSON.stringify(fields),
  });
  if (!res.ok) throw new Error(`Update failed (${res.status})`);
  return res.json();
}

export async function deleteEntry(id: string): Promise<void> {
  const res = await fetch(`/api/journal/${id}`, {
    method: "DELETE",
    headers: headers(),
  });
  if (!res.ok && res.status !== 204) throw new Error(`Delete failed (${res.status})`);
}
