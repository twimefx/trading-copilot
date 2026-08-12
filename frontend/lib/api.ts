// Authenticated API client for the FastAPI backend (proxied via /api/*).
//
// Auth: every request carries the Clerk session JWT as `Authorization: Bearer *** *** The backend verifies it and derives the user id. We expose a `useApi()` hook
// that binds Clerk's `getToken()` into thin fetch wrappers, so components never
// touch tokens directly.

"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useMemo } from "react";

// Auth degrades gracefully. When Clerk isn't configured (no publishable key),
// we skip Clerk entirely: no token is attached and the app is treated as signed
// in anonymously — mirroring the backend, which stays open until Clerk is set.
const CLERK_ENABLED = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

// A no-op stand-in for Clerk's useAuth when Clerk is disabled, so useApi() can
// call a hook unconditionally (Rules of Hooks) without a ClerkProvider present.
function useNoAuth() {
  return {
    getToken: useCallback(async () => null as string | null, []),
    isSignedIn: true as boolean | undefined,
  };
}
const useAuthImpl = CLERK_ENABLED ? useAuth : useNoAuth;

export type Range = { low: number | null; high: number | null; source: string | null };

export type DataProvenance = {
  provider?: string;
  symbol?: string;
  interval?: string;
  as_of?: string;
  retrieved_at?: string;
  status?: "live" | "delayed" | "historical" | "provider_timestamp_only" | string;
  // Kept for responses generated before the explicit lineage contract landed.
  freshness?: string;
};

export type Availability = {
  available?: boolean;
  note?: string;
};

export type EquityMetadata = Availability & {
  exchange?: string;
  currency?: string;
  market_state?: string;
  regular_market_price?: number;
};

export type KronosConsensusComponent = {
  component_type: string;
  score: number;
  confidence: number;
  weight: number;
  evidence: string[];
  source?: string;
  as_of?: string | null;
};

export type KronosConsensus = {
  signal: string;
  overall_score: number;
  consensus_confidence: number;
  model_probability: number | null;
  confidence_note: string;
  as_of?: string | null;
  components: KronosConsensusComponent[];
};

export type CopilotAnalysis = {
  lean?: string;
  conviction?: number;
  summary?: string;
  drivers?: string[];
  risks?: string[];
  range_24h?: Range;
  suggested_invalidation?: string;
  disclaimer?: string;
  cost_usd?: number;
  track_record?: string | null;
  data_provenance?: DataProvenance;
  fundamentals?: EquityMetadata;
  news?: Availability;
  kronos_consensus?: KronosConsensus;
  raw?: string;
};

export type MeResponse = {
  user_id: string;
  tier: "free" | "pro" | "premium";
  is_admin?: boolean;
  bonus_credits?: number;
  daily_copilot_quota: number;
  copilot_calls_today: number;
  copilot_calls_remaining: number | null;
  scan_max_symbols: number;
  features: string[];
};

export type AdminUser = {
  user_id: string;
  tier: string;
  is_admin: boolean;
  bonus_credits: number;
  email: string | null;
  note: string | null;
  has_stripe: boolean;
  created_at: number;
  updated_at: number;
  copilot_calls_today?: number;
  daily_copilot_quota?: number;
};

export type AdminStats = {
  total: number;
  by_tier: Record<string, number>;
  admins: number;
  paying: number;
  spend_today_usd: number;
  spend_cap_usd: number;
};

export class ApiError extends Error {
  status: number;
  body: any;
  constructor(status: number, message: string, body?: any) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function parseError(res: Response): Promise<ApiError> {
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error */
  }
  const detail = body?.detail || `Request failed (${res.status})`;
  return new ApiError(res.status, detail, body);
}

export function useApi() {
  const { getToken, isSignedIn } = useAuthImpl();

  const authHeaders = useCallback(
    async (extra?: HeadersInit): Promise<HeadersInit> => {
      const token = await getToken();
      return {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...extra,
      };
    },
    [getToken],
  );

  const request = useCallback(
    async <T = any>(path: string, init: RequestInit = {}): Promise<T> => {
      const headers = await authHeaders(init.headers);
      const res = await fetch(`/api${path}`, { ...init, headers });
      if (!res.ok) throw await parseError(res);
      if (res.status === 204) return undefined as T;
      return res.json();
    },
    [authHeaders],
  );

  return useMemo(
    () => ({
      isSignedIn,
      me: () => request<MeResponse>("/me"),
      copilot: (body: { symbol: string; interval: string; include_kronos: boolean }) =>
        request<CopilotAnalysis>("/copilot", { method: "POST", body: JSON.stringify(body) }),
      scan: (body: { symbols: string[]; interval: string }) =>
        request("/scan", { method: "POST", body: JSON.stringify(body) }),
      debate: (body: { symbol: string; interval: string; include_kronos?: boolean }) =>
        request("/debate", { method: "POST", body: JSON.stringify(body) }),
      flow: (symbol: string, period = "1h") =>
        request(`/flow?symbol=${encodeURIComponent(symbol)}&period=${encodeURIComponent(period)}`),
      strategy: (body: { prompt: string; symbol: string; interval: string }) =>
        request("/strategy", { method: "POST", body: JSON.stringify(body) }),
      replay: (body: { symbol: string; interval: string; as_of: number; mode?: string; include_kronos?: boolean }) =>
        request("/replay", { method: "POST", body: JSON.stringify(body) }),
      // Billing
      startCheckout: async (tier: "pro" | "premium") => {
        const { url } = await request<{ url: string }>("/billing/checkout", {
          method: "POST",
          body: JSON.stringify({ tier }),
        });
        return url;
      },
      openBillingPortal: async () => {
        const { url } = await request<{ url: string }>("/billing/portal", { method: "POST" });
        return url;
      },
      // Journal
      journalList: (status?: string) =>
        request<{ entries: any[] }>(
          `/journal${status ? `?status=${encodeURIComponent(status)}` : ""}`,
        ).then((r) => r.entries ?? []),
      journalStats: () => request("/journal/stats"),
      journalCoaching: () => request("/journal/coaching"),
      portfolio: () => request("/portfolio"),
      journalSave: (payload: Record<string, any>) =>
        request("/journal", { method: "POST", body: JSON.stringify(payload) }),
      journalUpdate: (id: string, fields: Record<string, any>) =>
        request(`/journal/${id}`, { method: "PATCH", body: JSON.stringify(fields) }),
      journalDelete: (id: string) => request(`/journal/${id}`, { method: "DELETE" }),
      // Watchlist
      watchlistGet: () => request<{ symbols: string[] }>("/watchlist"),
      watchlistPut: (symbols: string[]) =>
        request<{ symbols: string[] }>("/watchlist", {
          method: "PUT",
          body: JSON.stringify({ symbols }),
        }),
      // Alerts
      alertsList: () => request<{ rules: any[] }>("/alerts").then((r) => r.rules ?? []),
      alertCreate: (payload: { kind: string; config: Record<string, any>; cooldown_s?: number }) =>
        request("/alerts", { method: "POST", body: JSON.stringify(payload) }),
      alertUpdate: (id: string, fields: Record<string, any>) =>
        request(`/alerts/${id}`, { method: "PATCH", body: JSON.stringify(fields) }),
      alertDelete: (id: string) => request(`/alerts/${id}`, { method: "DELETE" }),
      alertTest: (id: string) => request(`/alerts/${id}/test`, { method: "POST", body: "{}" }),
      alertEvents: () => request<{ events: any[] }>("/alerts/events").then((r) => r.events ?? []),
      // Track record
      signalStats: () => request("/signals/stats"),
      signalHistory: (symbol?: string) =>
        request<{ signals: any[] }>(
          `/signals/history${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`,
        ).then((r) => r.signals ?? []),
      // Admin
      adminStats: () => request<AdminStats>("/admin/stats"),
      adminUsers: (search?: string) =>
        request<{ users: AdminUser[] }>(
          `/admin/users${search ? `?search=${encodeURIComponent(search)}` : ""}`,
        ).then((r) => r.users ?? []),
      adminGetUser: (id: string) => request<AdminUser>(`/admin/users/${encodeURIComponent(id)}`),
      adminCreateUser: (payload: { user_id: string; email?: string; tier?: string }) =>
        request<AdminUser>("/admin/users", { method: "POST", body: JSON.stringify(payload) }),
      adminSetTier: (id: string, tier: string) =>
        request(`/admin/users/${encodeURIComponent(id)}/tier`, { method: "POST", body: JSON.stringify({ tier }) }),
      adminFundCredits: (id: string, delta: number) =>
        request(`/admin/users/${encodeURIComponent(id)}/credits`, { method: "POST", body: JSON.stringify({ delta }) }),
      adminUpdateProfile: (id: string, fields: { email?: string; note?: string; is_admin?: boolean }) =>
        request<AdminUser>(`/admin/users/${encodeURIComponent(id)}/profile`, { method: "POST", body: JSON.stringify(fields) }),
      adminResetUsage: (id: string) =>
        request(`/admin/users/${encodeURIComponent(id)}/reset-usage`, { method: "POST", body: "{}" }),
      adminDeleteUser: (id: string) =>
        request(`/admin/users/${encodeURIComponent(id)}`, { method: "DELETE" }),
      adminAudit: () =>
        request<{ events: any[] }>("/admin/audit").then((r) => r.events ?? []),
      request,
    }),
    [request, isSignedIn],
  );
}
