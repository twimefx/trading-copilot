import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI Trading Copilot",
  description: "AI market intelligence for traders — analysis, not advice.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
