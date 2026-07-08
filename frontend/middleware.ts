import { clerkMiddleware } from "@clerk/nextjs/server";

// Clerk middleware runs on every request so `auth()` and the client hooks work.
// We don't force protection at the edge here — the UI gates views and the
// backend independently verifies the JWT — but this wires Clerk into the app.
export default clerkMiddleware();

export const config = {
  matcher: [
    // Skip Next.js internals and static files.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API-style routes.
    "/(api|trpc)(.*)",
  ],
};
