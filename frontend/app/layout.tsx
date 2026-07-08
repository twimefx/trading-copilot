import "./globals.css";
import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";

export const metadata: Metadata = {
  title: "AI Trading Copilot",
  description: "AI market intelligence for traders — analysis, not advice.",
};

// Auth degrades gracefully: only mount ClerkProvider when a publishable key is
// configured. Without it (e.g. before Clerk is wired in prod) the app renders
// anonymously — matching the backend, which stays open until CLERK_JWKS_URL is set.
const CLERK_ENABLED = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const body = (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
  return CLERK_ENABLED ? <ClerkProvider>{body}</ClerkProvider> : body;
}
