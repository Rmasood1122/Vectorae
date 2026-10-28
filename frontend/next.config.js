/** @type {import('next').NextConfig} */

// ─── M7 NOTE ────────────────────────────────────────────────────────────────
// `output: 'export'` enables full static export (Option A chosen in M7 Chunk 1).
// This is what Capacitor bundles into the APK.
// Constraints that come with static export:
//   • No server components that fetch at request time (all our data is client-side
//     via React Query + typed API client — verified in M5: zero request-time SSR).
//   • No route handlers (app/api/) — none exist; all API calls go to FastAPI backend.
//   • No middleware.ts — none exists; auth is JWT client-side.
//   • next/image: `unoptimized: true` required (no Next.js image optimization server).
//   • Trailing slash set to true for clean static file serving in WebView.
// ────────────────────────────────────────────────────────────────────────────

const nextConfig = {
  // ── Static export (Capacitor / CDN deploy) ──────────────────────────────
  output: 'export',
  trailingSlash: true,          // generates /page/index.html — required for Capacitor WebView routing

  // ── Image optimization ──────────────────────────────────────────────────
  // Must be unoptimized when output:'export' — no server to transform images.
  // All images in Vectorae are either API-served URLs or CSS backgrounds,
  // so this has no visual impact.
  images: {
    unoptimized: true,
  },

  // ── TypeScript (inherited from M5, not changed) ─────────────────────────
  // Next 16 no longer lints during builds and rejects an `eslint` key here;
  // lint runs as its own step (`npm run lint`, CI verify-frontend).
  typescript: {
    ignoreBuildErrors: false,   // strict — keep this
  },

  // ── Environment variable passthrough ────────────────────────────────────
  // NEXT_PUBLIC_API_URL is the only runtime env var the app needs.
  // It is baked in at build time (static export limitation — correct for us
  // since each build targets a specific backend: dev vs prod).
  // See .env.mobile.example for both profiles.
  env: {
    NEXT_PUBLIC_BUILD_TARGET: process.env.NEXT_PUBLIC_BUILD_TARGET || 'web',
  },

  // ── Dev-server file watcher ──────────────────────────────────────────────
  // `npm run dev` (used both for local dev AND as playwright.config.ts's
  // reused webServer for E2E tests) was watching the whole project tree by
  // default, INCLUDING test-results/ and playwright-report/ — directories
  // Playwright writes new screenshots/traces/error-context.md into as each
  // E2E test finishes. Every one of those writes looked like a source
  // change to Next.js and triggered a Fast Refresh full page reload
  // mid-test-run, wiping in-memory client state (the logged-in session,
  // the intake wizard's step) and causing tests navigating right after
  // login to intermittently bounce back to /login. Excluding these
  // output-only directories stops the watcher from ever seeing them.
  // Next 16 builds with Turbopack by default and refuses a webpack config
  // under it, so every `next dev` / `next build` script passes --webpack: the
  // bundler this app has always shipped with.
  webpack: (config) => {
    config.watchOptions = {
      ...config.watchOptions,
      ignored: [
        '**/node_modules/**',
        '**/.next/**',
        '**/test-results/**',
        '**/playwright-report/**',
      ],
    };
    return config;
  },
};

/**
 * BUILD-TIME GUARD — NEXT_PUBLIC_API_URL must be set for a production build.
 *
 * This is a static export, so the API origin is compiled into the bundle. When
 * the variable was missing, `src/lib/api/client.ts` fell back to
 * "http://localhost:8000": the build SUCCEEDED, the deploy went green, every
 * health check passed, and then each visitor's browser tried to call their own
 * machine. Nothing in CI or at deploy time could see it.
 *
 * Failing the build is the only place this can be caught before a broken
 * bundle ships — a runtime check would already be inside the artefact.
 *
 * Scoped to PHASE_PRODUCTION_BUILD so `next dev` still runs with the localhost
 * default, where it is the correct value and not a shipping hazard.
 */
const { PHASE_PRODUCTION_BUILD } = require('next/constants');

module.exports = (phase) => {
  if (phase === PHASE_PRODUCTION_BUILD) {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL;
    // Unset is not the only failure mode. `frontend/.env` ships
    // NEXT_PUBLIC_API_URL=http://localhost:8000 for local development, and Next
    // loads .env into process.env BEFORE this file is evaluated — so a guard
    // that only checked for "unset" was already defeated by a file sitting in
    // the repo. A production build must never bake a loopback origin.
    const isLocal =
      !!apiUrl && /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/i.test(apiUrl);
    if (!apiUrl || isLocal) {
      throw new Error(`

  BUILD FAILED: NEXT_PUBLIC_API_URL is ${apiUrl ? `a local origin (${apiUrl})` : 'not set'}.

  This value is baked into the static export at build time and cannot be
  changed afterwards by editing an environment variable.

  Building without it produces a bundle that deploys successfully and then
  sends every visitor's browser to http://localhost:8000.

  Set it to the API origin and rebuild, e.g.
    NEXT_PUBLIC_API_URL=https://api.leadpilot.com npm run build

  On Railway this is a BUILD variable on the frontend service.
`);
    }
  }
  return nextConfig;
};
