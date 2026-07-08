// Authenticated API client for the FastAPI backend (proxied via /api/*).
//
// Auth: every request carries the Clerk session JWT as `Authorization: Bearer *** *** The backend verifies it and derives the user id. We expose a `useApi()` hook
// that binds Clerk's `getToken()` into thin fetch wrappers, so components never
// touch tokens directly.

"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useMemo } from "react";

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
  const { getToken, isSignedIn } = useAuth();

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
