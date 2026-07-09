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

export type MeResponse = {
  user_id: string;
  tier: "free" | "pro" | "premium";
  daily_copilot_quota: number;
  copilot_calls_today: number;
  copilot_calls_remaining: number | null;
  scan_max_symbols: number;
  features: string[];
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
        request("/copilot", { method: "POST", body: JSON.stringify(body) }),
      scan: (body: { symbols: string[]; interval: string }) =>
        request("/scan", { method: "POST", body: JSON.stringify(body) }),
      debate: (body: { symbol: string; interval: string; include_kronos?: boolean }) =>
        request("/debate", { method: "POST", body: JSON.stringify(body) }),
      flow: (symbol: string, period = "1h") =>
        request(`/flow?symbol=${encodeURIComponent(symbol)}&period=${encodeURIComponent(period)}`),
      strategy: (body: { prompt: string; symbol: string; interval: string }) =>
        request("/strategy", { method: "POST", body: JSON.stringify(body) }),
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
      request,
    }),
    [request, isSignedIn],
  );
}
