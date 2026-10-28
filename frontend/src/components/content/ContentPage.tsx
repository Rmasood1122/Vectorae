/**
 * components/content/ContentPage.tsx — one renderer for the public GEO pages
 * and the help center. Static server component: no API calls, no auth, and
 * not the dashboard Shell. Every block renders as plain HTML, so the full
 * text is in the static export for crawlers that do not run JavaScript.
 */

import Link from "next/link";
import type { ReactNode } from "react";

import { Footer } from "@/components/Footer";
import { JsonLd } from "@/components/seo/JsonLd";
import { RichText } from "@/lib/content/richText";
import type { Block, PageLink, Section } from "@/lib/content/types";

const NAV: PageLink[] = [
  { href: "/how-it-works", label: "How it Works" },
  { href: "/compare", label: "Compare" },
  { href: "/help", label: "Help" },
  { href: "/about", label: "About" },
  { href: "/pricing", label: "Pricing" },
  { href: "/contact", label: "Contact" },
];

export interface ContentPageProps {
  jsonLd: Record<string, unknown>;
  eyebrow: string;
  h1: string;
  lead: string[];
  /** Who wrote the page, shown above the body (comparisons say the founders wrote them). */
  disclosure?: string;
  /** ISO date the facts on the page were last checked. */
  lastChecked?: string;
  sections: Section[];
  related?: PageLink[];
  /** Current nav item, e.g. "/compare". */
  current?: string;
  children?: ReactNode;
}

export function ContentPage(props: ContentPageProps) {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <JsonLd data={props.jsonLd} />
      <MarketingNav current={props.current} />
      <main>
        <section className="px-6 pb-12 pt-20">
          <div className="mx-auto max-w-4xl">
            <p className="text-sm font-medium uppercase tracking-wider text-blue-500">{props.eyebrow}</p>
            <h1 className="mt-4 text-4xl font-bold leading-tight tracking-tight sm:text-5xl">{props.h1}</h1>
            {props.lead.map((p, i) => (
              <p key={i} className="mt-6 max-w-3xl text-lg leading-relaxed text-gray-300">
                <RichText text={p} />
              </p>
            ))}
            {(props.disclosure || props.lastChecked) && (
              <p className="mt-6 text-sm text-gray-400" data-testid="page-disclosure">
                {props.disclosure && <RichText text={props.disclosure} />}
                {props.disclosure && props.lastChecked ? " " : ""}
                {props.lastChecked ? `Last checked: ${props.lastChecked}.` : ""}
              </p>
            )}
          </div>
        </section>

        {props.sections.map((section) => (
          <section key={section.id ?? section.heading} className="border-t border-white/10 px-6 py-14">
            <div className="mx-auto max-w-4xl">
              <h2 id={section.id} className="scroll-mt-6 text-2xl font-bold tracking-tight sm:text-3xl">{section.heading}</h2>
              {section.blocks.map((block, i) => (
                <BlockView key={i} block={block} />
              ))}
            </div>
          </section>
        ))}

        {props.children}

        {props.related && props.related.length > 0 && (
          <section className="border-t border-white/10 px-6 py-14">
            <div className="mx-auto max-w-4xl">
              <h2 className="text-xl font-semibold">Related</h2>
              <ul className="mt-4 flex flex-wrap gap-x-6 gap-y-2">
                {props.related.map((l) => (
                  <li key={l.href}>
                    <Link href={l.href} className="text-blue-400 underline hover:text-blue-300">{l.label}</Link>
                  </li>
                ))}
              </ul>
            </div>
          </section>
        )}

        <section className="border-t border-white/10 px-6 py-14">
          <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-6">
            <Link href="/contact" className="rounded-md bg-blue-600 px-6 py-3 font-semibold text-white hover:bg-blue-700">
              Book a call
            </Link>
            <Link href="/how-it-works" className="font-medium text-blue-500 hover:text-blue-400">
              See how it works
            </Link>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}

export function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case "p":
      return <p className="mt-5 text-lg leading-relaxed text-gray-300"><RichText text={block.text} /></p>;
    case "h3":
      return <h3 className="mt-8 text-xl font-semibold">{block.text}</h3>;
    case "note":
      return (
        <p className="mt-5 border-l-2 border-blue-600 pl-4 leading-relaxed text-gray-300">
          <RichText text={block.text} />
        </p>
      );
    case "ul":
    case "ol": {
      const List = block.kind;
      return (
        <List className={`mt-5 space-y-2 pl-6 text-lg leading-relaxed text-gray-300 ${block.kind === "ul" ? "list-disc" : "list-decimal"}`}>
          {block.items.map((item, i) => (
            <li key={i}><RichText text={item} /></li>
          ))}
        </List>
      );
    }
    case "table":
      return (
        <div className="mt-6 overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm text-gray-300">
            <thead>
              <tr>
                {block.head.map((h) => (
                  <th key={h} scope="col" className="border-b border-white/20 px-3 py-2 font-semibold text-white">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r} className="align-top">
                  {row.map((cell, c) =>
                    c === 0 ? (
                      <th key={c} scope="row" className="border-b border-white/10 px-3 py-2 font-medium text-white">
                        <RichText text={cell} />
                      </th>
                    ) : (
                      <td key={c} className="border-b border-white/10 px-3 py-2"><RichText text={cell} /></td>
                    ),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "qa":
      return (
        <div className="mt-6 space-y-8">
          {block.items.map((item) => (
            <div key={item.q}>
              <h3 className="text-xl font-semibold">{item.q}</h3>
              <p className="mt-3 text-lg leading-relaxed text-gray-300"><RichText text={item.a} /></p>
            </div>
          ))}
        </div>
      );
  }
}

export function MarketingNav({ current }: { current?: string }) {
  return (
    <header className="border-b border-white/10 px-6 py-5">
      <nav className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-8 gap-y-3">
        <Link href="/" className="text-lg font-bold text-white">Vectorae</Link>
        <ul className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-gray-300">
          {NAV.map((item) => (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={current === item.href ? "page" : undefined}
                className={current === item.href ? "text-white" : "hover:text-white"}
              >
                {item.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </header>
  );
}
