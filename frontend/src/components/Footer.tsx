/**
 * components/Footer.tsx — footer for the public pages
 * (/about, /contact, /how-it-works, /privacy, /terms, /pricing, /status, and the
 * GEO and help pages). Its Who it's for / Compare / Resources columns keep every
 * one of those pages one click from any public page.
 *
 * Server component. Not used by the dashboard, which has its own shell.
 * One footer everywhere so the Legal links and the Status link (when live)
 * cannot drift between pages. `tone="theme"` is for pages drawn with the app
 * theme tokens (/pricing) instead of the dark marketing palette.
 */

import Link from "next/link";

import { SHOW_STATUS_LINK, statusPageLink } from "@/lib/statusPage";

const COLUMNS = [
  {
    heading: "Company",
    links: [
      { href: "/about", label: "About" },
      { href: "/contact", label: "Contact" },
      { href: "/pricing", label: "Pricing" },
    ],
  },
  {
    heading: "Who it's for",
    links: [
      { href: "/for/agencies", label: "Agencies" },
      { href: "/for/b2b-service-firms", label: "B2B service firms" },
      { href: "/for/local-businesses", label: "Local businesses" },
    ],
  },
  {
    heading: "Compare",
    links: [
      { href: "/compare/leadpilot-vs-apollo", label: "vs Apollo" },
      { href: "/compare/leadpilot-vs-instantly", label: "vs Instantly" },
      { href: "/compare/leadpilot-vs-smartlead", label: "vs Smartlead" },
      { href: "/compare/leadpilot-vs-clay", label: "vs Clay" },
      { href: "/compare/leadpilot-vs-artisan", label: "vs Artisan" },
      { href: "/compare", label: "All comparisons" },
    ],
  },
  {
    heading: "Resources",
    links: [
      { href: "/what-is-leadpilot", label: "What is Vectorae" },
      { href: "/faq", label: "FAQ" },
      { href: "/help", label: "Help center" },
      { href: "/outbound-statistics", label: "Outbound statistics" },
    ],
  },
  {
    heading: "Legal",
    links: [
      { href: "/privacy", label: "Privacy Policy" },
      { href: "/terms", label: "Terms of Service" },
    ],
  },
];

const TONES = {
  dark: {
    border: "border-white/10",
    heading: "text-white",
    text: "text-gray-400",
    hover: "hover:text-white",
  },
  theme: {
    border: "border-border",
    heading: "text-foreground",
    text: "text-muted-foreground",
    hover: "hover:text-foreground",
  },
} as const;

export function Footer({
  showStatusLink = SHOW_STATUS_LINK,
  tone = "dark",
}: { showStatusLink?: boolean; tone?: keyof typeof TONES } = {}) {
  // The /status page on this site (src/lib/statusPage.ts).
  const status = statusPageLink(showStatusLink);
  const t = TONES[tone];
  return (
    <footer className={`border-t ${t.border} px-6 py-12`}>
      <div className="mx-auto max-w-6xl">
        <div className="grid grid-cols-2 gap-8 sm:grid-cols-3 lg:grid-cols-5">
          {COLUMNS.map((col) => (
            <nav key={col.heading} aria-label={col.heading}>
              <h2 className={`text-sm font-semibold uppercase tracking-wider ${t.heading}`}>{col.heading}</h2>
              <ul className={`mt-4 space-y-3 text-sm ${t.text}`}>
                {col.links.map((link) => (
                  <li key={link.href}>
                    <Link href={link.href} className={t.hover}>
                      {link.label}
                    </Link>
                  </li>
                ))}
                {col.heading === "Company" && status && (
                  <li>
                    <a href={status.href} className={t.hover} data-testid="footer-status-link">
                      {status.label}
                    </a>
                  </li>
                )}
              </ul>
            </nav>
          ))}
        </div>
        <p className={`mt-10 border-t ${t.border} pt-6 text-sm ${t.text}`}>
          &copy; 2026 Rehan Masood. Vectorae is operated by Rehan Masood.
        </p>
      </div>
    </footer>
  );
}
