import Link from "next/link";

export function SiteHeader() {
  return (
    <header className="site-header">
      <Link href="/" className="brand">
        Vectorae <span>Status</span>
      </Link>
      <nav aria-label="Status page">
        <Link href="/history">History</Link>
        <a href="/history.rss">RSS</a>
      </nav>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <p>
        <Link href="/history">Incident history</Link> · <a href="/history.rss">RSS feed</a> ·{" "}
        <a href="/history.atom">Atom feed</a> · <a href="https://calendarharvest.com">calendarharvest.com</a>
      </p>
      <p>
        Times are in UTC; hover a time to see it in your time zone. This page runs separately from
        Vectorae, so it stays up when Vectorae does not. Uptime history comes from independent
        external checks.
      </p>
    </footer>
  );
}
