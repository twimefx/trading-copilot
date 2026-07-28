"use client";

import { useEffect, useState } from "react";
import type { useApi, AdminUser, AdminStats } from "@/lib/api";

type Api = ReturnType<typeof useApi>;

type AuditEvent = {
  admin_id: string;
  action: string;
  target_user: string | null;
  detail: string | null;
  created_at: number;
};

function ts(epoch?: number | null) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString();
}

function tierBadge(tier: string) {
  const base = "px-2 py-0.5 rounded-full text-[11px] font-semibold border ";
  if (tier === "premium") return base + "bg-accent/20 text-accent border-accent/40";
  if (tier === "pro") return base + "bg-bull/15 text-bull border-bull/40";
  return base + "bg-neutral/10 text-neutral border-neutral/30";
}

export default function Admin({ api }: { api: Api }) {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // user_id currently mutating
  const [showCreate, setShowCreate] = useState(false);
  const [showAudit, setShowAudit] = useState(false);

  // create form
  const [newId, setNewId] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newTier, setNewTier] = useState<"free" | "pro" | "premium">("free");

  async function refresh() {
    try {
      setError(null);
      const [s, u, a] = await Promise.all([
        api.adminStats(),
        api.adminUsers(search || undefined),
        api.adminAudit(),
      ]);
      setStats(s);
      setUsers(u);
      setAudit(a);
    } catch (e: any) {
      setError(e.message || "Failed to load admin data (are you an admin?)");
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function run(userId: string, fn: () => Promise<any>, msg: string) {
    setBusy(userId);
    setNotice(null);
    setError(null);
    try {
      await fn();
      setNotice(msg);
      await refresh();
    } catch (e: any) {
      setError(e.message || "Action failed");
    } finally {
      setBusy(null);
    }
  }

  const setTier = (id: string, tier: string) =>
    run(id, () => api.adminSetTier(id, tier), `Set ${id} → ${tier}`);

  const fund = (id: string) => {
    const raw = window.prompt(`Fund bonus Copilot credits for ${id}.\nEnter amount (negative to revoke):`, "100");
    if (raw == null) return;
    const delta = parseInt(raw, 10);
    if (isNaN(delta) || delta === 0) return;
    run(id, () => api.adminFundCredits(id, delta), `Funded ${id} by ${delta > 0 ? "+" : ""}${delta} credits`);
  };

  const resetUsage = (id: string) =>
    run(id, () => api.adminResetUsage(id), `Reset today's usage for ${id}`);

  const toggleAdmin = (u: AdminUser) =>
    run(u.user_id, () => api.adminUpdateProfile(u.user_id, { is_admin: !u.is_admin }),
      `${u.is_admin ? "Revoked" : "Granted"} admin for ${u.user_id}`);

  const editEmail = (u: AdminUser) => {
    const raw = window.prompt(`Email for ${u.user_id}:`, u.email || "");
    if (raw == null) return;
    run(u.user_id, () => api.adminUpdateProfile(u.user_id, { email: raw }), `Updated email for ${u.user_id}`);
  };

  const editNote = (u: AdminUser) => {
    const raw = window.prompt(`Note for ${u.user_id}:`, u.note || "");
    if (raw == null) return;
    run(u.user_id, () => api.adminUpdateProfile(u.user_id, { note: raw }), `Updated note for ${u.user_id}`);
  };

  const del = (u: AdminUser) => {
    if (!window.confirm(`Permanently delete ${u.user_id}?\nThis removes the user, their usage, and watchlist. This cannot be undone.`)) return;
    run(u.user_id, () => api.adminDeleteUser(u.user_id), `Deleted ${u.user_id}`);
  };

  async function createUser() {
    if (!newId.trim()) return;
    await run(newId.trim(), () =>
      api.adminCreateUser({ user_id: newId.trim(), email: newEmail || undefined, tier: newTier }),
      `Created ${newId}`);
    setShowCreate(false);
    setNewId(""); setNewEmail(""); setNewTier("free");
  }

  return (
    <div className="space-y-6">
      {/* Header stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          { label: "Total users", value: stats?.total ?? "—" },
          { label: "Paying", value: stats?.paying ?? "—" },
          { label: "Pro", value: stats?.by_tier?.pro ?? 0 },
          { label: "Premium", value: stats?.by_tier?.premium ?? 0 },
          { label: "Spend today", value: stats ? `$${stats.spend_today_usd.toFixed(2)} / $${stats.spend_cap_usd}` : "—" },
        ].map((c) => (
          <div key={c.label} className="bg-panel rounded-2xl border border-white/5 p-4">
            <div className="text-[11px] uppercase tracking-wide text-neutral">{c.label}</div>
            <div className="text-xl font-semibold mt-1">{c.value}</div>
          </div>
        ))}
      </div>

      {error && (
        <div className="bg-bear/10 border border-bear/40 text-bear rounded-xl px-4 py-3 text-sm">{error}</div>
      )}
      {notice && (
        <div className="bg-bull/10 border border-bull/40 text-bull rounded-xl px-4 py-3 text-sm">{notice}</div>
      )}

      {/* Controls */}
      <div className="bg-panel rounded-2xl border border-white/5 p-5">
        <div className="flex flex-wrap items-center gap-3">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && refresh()}
            placeholder="Search user id or email…"
            className="bg-panelhi rounded-lg px-3 py-2 text-sm flex-1 min-w-[220px] outline-none"
          />
          <button onClick={refresh}
            className="bg-panelhi hover:bg-white/10 rounded-lg px-4 py-2 text-sm font-medium transition">
            Search
          </button>
          <button onClick={() => setShowCreate((v) => !v)}
            className="bg-blue hover:bg-bluehi text-white rounded-full px-4 py-2 text-sm font-semibold transition">
            + New user
          </button>
          <button onClick={() => setShowAudit((v) => !v)}
            className="bg-panelhi hover:bg-white/10 rounded-lg px-4 py-2 text-sm font-medium transition">
            {showAudit ? "Hide" : "Show"} audit log
          </button>
        </div>

        {showCreate && (
          <div className="flex flex-wrap items-center gap-3 mt-4 pt-4 border-t border-white/5">
            <input value={newId} onChange={(e) => setNewId(e.target.value)}
              placeholder="user_id (Clerk)" className="bg-panelhi rounded-lg px-3 py-2 text-sm flex-1 min-w-[200px] outline-none" />
            <input value={newEmail} onChange={(e) => setNewEmail(e.target.value)}
              placeholder="email (optional)" type="email" className="bg-panelhi rounded-lg px-3 py-2 text-sm flex-1 min-w-[200px] outline-none" />
            <select value={newTier} onChange={(e) => setNewTier(e.target.value as any)}
              className="bg-panelhi rounded-lg px-3 py-2 text-sm outline-none">
              <option value="free">free</option>
              <option value="pro">pro</option>
              <option value="premium">premium</option>
            </select>
            <button onClick={createUser}
              className="bg-blue hover:bg-bluehi text-white rounded-full px-4 py-2 text-sm font-semibold transition">
              Create
            </button>
          </div>
        )}
      </div>

      {/* Users table */}
      <div className="bg-panel rounded-2xl border border-white/5 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wide text-neutral border-b border-white/5">
                <th className="px-4 py-3">User</th>
                <th className="px-4 py-3">Tier</th>
                <th className="px-4 py-3">Credits</th>
                <th className="px-4 py-3">Stripe</th>
                <th className="px-4 py-3">Joined</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 && (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-neutral">No users found.</td></tr>
              )}
              {users.map((u) => (
                <tr key={u.user_id} className="border-b border-white/5 hover:bg-white/[0.02]">
                  <td className="px-4 py-3">
                    <div className="font-mono text-xs">{u.user_id}</div>
                    <div className="text-neutral text-xs mt-0.5 flex items-center gap-2">
                      {u.email && <span>{u.email}</span>}
                      {u.is_admin && <span className="text-accent font-semibold">ADMIN</span>}
                      {u.note && <span className="italic">· {u.note}</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3"><span className={tierBadge(u.tier)}>{u.tier}</span></td>
                  <td className="px-4 py-3">
                    <span className={u.bonus_credits > 0 ? "text-bull font-semibold" : "text-neutral"}>
                      {u.bonus_credits}
                    </span>
                  </td>
                  <td className="px-4 py-3">{u.has_stripe ? "✓" : <span className="text-neutral">—</span>}</td>
                  <td className="px-4 py-3 text-neutral text-xs">{ts(u.created_at).split(",")[0]}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap justify-end gap-1.5">
                      <button disabled={busy === u.user_id} onClick={() => setTier(u.user_id, "pro")}
                        className="px-2 py-1 rounded bg-bull/15 text-bull text-[11px] font-medium hover:bg-bull/25 disabled:opacity-40">Pro</button>
                      <button disabled={busy === u.user_id} onClick={() => setTier(u.user_id, "premium")}
                        className="px-2 py-1 rounded bg-accent/20 text-accent text-[11px] font-medium hover:bg-accent/30 disabled:opacity-40">Premium</button>
                      <button disabled={busy === u.user_id} onClick={() => setTier(u.user_id, "free")}
                        className="px-2 py-1 rounded bg-neutral/10 text-neutral text-[11px] font-medium hover:bg-neutral/20 disabled:opacity-40">Free</button>
                      <button disabled={busy === u.user_id} onClick={() => fund(u.user_id)}
                        className="px-2 py-1 rounded bg-panelhi text-white text-[11px] font-medium hover:bg-white/10 disabled:opacity-40">Fund</button>
                      <button disabled={busy === u.user_id} onClick={() => resetUsage(u.user_id)}
                        className="px-2 py-1 rounded bg-panelhi text-white text-[11px] font-medium hover:bg-white/10 disabled:opacity-40" title="Reset today's usage">Reset</button>
                      <button disabled={busy === u.user_id} onClick={() => toggleAdmin(u)}
                        className="px-2 py-1 rounded bg-panelhi text-white text-[11px] font-medium hover:bg-white/10 disabled:opacity-40" title="Toggle admin">
                        {u.is_admin ? "Unadmin" : "Admin"}
                      </button>
                      <button disabled={busy === u.user_id} onClick={() => editEmail(u)}
                        className="px-2 py-1 rounded bg-panelhi text-white text-[11px] font-medium hover:bg-white/10 disabled:opacity-40">Email</button>
                      <button disabled={busy === u.user_id} onClick={() => editNote(u)}
                        className="px-2 py-1 rounded bg-panelhi text-white text-[11px] font-medium hover:bg-white/10 disabled:opacity-40">Note</button>
                      <button disabled={busy === u.user_id} onClick={() => del(u)}
                        className="px-2 py-1 rounded bg-bear/15 text-bear text-[11px] font-medium hover:bg-bear/25 disabled:opacity-40">Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Audit log */}
      {showAudit && (
        <div className="bg-panel rounded-2xl border border-white/5 p-5">
          <h3 className="font-semibold mb-3">Audit log</h3>
          <div className="space-y-1.5 max-h-80 overflow-y-auto">
            {audit.length === 0 && <div className="text-neutral text-sm">No admin actions recorded yet.</div>}
            {audit.map((e, i) => (
              <div key={i} className="flex items-baseline gap-3 text-xs border-b border-white/5 pb-1.5">
                <span className="text-neutral whitespace-nowrap">{ts(e.created_at)}</span>
                <span className="font-mono text-accent">{e.admin_id.slice(0, 12)}…</span>
                <span className="font-semibold">{e.action}</span>
                {e.target_user && <span className="font-mono text-neutral">→ {e.target_user}</span>}
                {e.detail && <span className="text-neutral italic">{e.detail}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
