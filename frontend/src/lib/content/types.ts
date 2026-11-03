/**
 * The content model for the public GEO pages (/what-is-vectorae/, /faq/,
 * /outbound-statistics/, /compare/…, /for/…) and the help center (/help/…).
 *
 * Page copy lives as data (src/content/geo/*.ts, src/content/help-center/*.md)
 * and one renderer (src/components/content/ContentPage.tsx) draws it, so every
 * page gets the same nav, footer, canonical, JSON-LD and disclosure rules.
 *
 * Inline text may use **bold**, `code` and [label](href). Links must be a site
 * path ("/…") or an https URL; anything else renders as plain text
 * (src/lib/content/richText.tsx).
 */

export type Block =
  | { kind: "p"; text: string }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "qa"; items: QA[] }
  | { kind: "note"; text: string }
  | { kind: "h3"; text: string };

export interface QA {
  q: string;
  a: string;
}

export interface Section {
  heading: string;
  /** Anchor id for the heading (help-center articles link to sections). */
  id?: string;
  blocks: Block[];
}

export interface PageLink {
  href: string;
  label: string;
}
