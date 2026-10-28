/**
 * static-server.js — serves the Next.js static export in ./out
 *
 * WHY THIS EXISTS
 * Vectorae's frontend ships as `output: 'export'` (see next.config.js) —
 * a pile of static HTML/JS that Capacitor bundles into the APK. There is no
 * Node server in production. Running the E2E suite against `next dev`
 * therefore tested an artifact that never ships, and did so unreliably:
 *
 *   • `next dev` compiles each route ON DEMAND, on first navigation. With
 *     playwright.config.ts's 2 parallel workers competing for one dev
 *     server, the first client-side navigation to /pipeline regularly took
 *     longer than expect()'s 5s timeout. Next's App Router holds the old
 *     URL until the destination route is ready, so the test saw the URL
 *     pinned at /login and reported "login redirect failed" — a pure
 *     dev-server compile-latency artifact, not an app bug. Confirmed:
 *     the same test passes with --workers=1 and fails with --workers=2.
 *   • `next dev` also runs Fast Refresh, whose file watcher previously
 *     reloaded pages mid-test when Playwright wrote screenshots/traces
 *     into test-results/ (worked around in next.config.js's
 *     webpack.watchOptions.ignored).
 *
 * Serving the built export removes both failure classes at the source:
 * no compilation, no watcher, no Fast Refresh — and the bytes under test
 * are the bytes that ship.
 *
 * Deliberately dependency-free (no `serve`/`http-server` package) so the
 * E2E harness adds nothing to the production dependency tree or to
 * `npm audit` surface. Node's stdlib is already a hard requirement.
 *
 * Usage:  node scripts/static-server.js [--port 3000] [--dir out]
 */

const http = require("http");
const fs = require("fs");
const path = require("path");

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const PORT = Number(arg("port", process.env.E2E_PORT || 3000));
const ROOT = path.resolve(__dirname, "..", arg("dir", "out"));

if (!fs.existsSync(ROOT)) {
  console.error(
    `[static-server] Missing export directory: ${ROOT}\n` +
      `Run \`npm run build\` first (npm run test:e2e does this for you).`,
  );
  process.exit(1);
}

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".map": "application/json; charset=utf-8",
  ".webmanifest": "application/manifest+json; charset=utf-8",
};

/** Resolve a URL pathname to a file inside ROOT, or null if it escapes. */
function resolveFile(pathname) {
  // Decode, then normalize away any ../ before joining — prevents a
  // request like /../../etc/passwd from reading outside the export.
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  const safe = path.normalize(decoded).replace(/^(\.\.[/\\])+/, "");
  const target = path.join(ROOT, safe);
  if (target !== ROOT && !target.startsWith(ROOT + path.sep)) return null;

  // next.config.js sets trailingSlash: true, so routes are emitted as
  // <route>/index.html. Try, in order: the exact file, then the
  // directory's index.html, then "<route>.html".
  const candidates = [];
  if (path.extname(target)) {
    candidates.push(target);
  } else {
    candidates.push(path.join(target, "index.html"), `${target}.html`);
  }
  for (const c of candidates) {
    if (fs.existsSync(c) && fs.statSync(c).isFile()) return c;
  }
  return null;
}

const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, `http://localhost:${PORT}`).pathname;
  const file = resolveFile(pathname);

  if (!file) {
    const notFound = path.join(ROOT, "404.html");
    const body = fs.existsSync(notFound)
      ? fs.readFileSync(notFound)
      : "404 Not Found";
    res.writeHead(404, { "Content-Type": "text/html; charset=utf-8" });
    res.end(body);
    return;
  }

  const type = TYPES[path.extname(file).toLowerCase()] || "application/octet-stream";
  // Immutable hashed assets can be cached; HTML must not be, so a rerun
  // after a rebuild never serves a stale page.
  const cache = pathname.startsWith("/_next/static/")
    ? "public, max-age=31536000, immutable"
    : "no-store";
  res.writeHead(200, { "Content-Type": type, "Cache-Control": cache });
  fs.createReadStream(file).pipe(res);
});

server.listen(PORT, () => {
  console.log(`[static-server] serving ${ROOT} at http://localhost:${PORT}`);
});
