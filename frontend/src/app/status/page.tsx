/**
 * app/status/page.tsx — public status page served at /status
 * (www.calendarharvest.com/status/). Design and operations:
 * docs/program/STATUS_PAGE.md.
 *
 * Deliberately outside the (app) route group and Shell.tsx: no auth, no
 * session, no dashboard chrome. The page itself is static HTML from the
 * export on Vercel, so it loads even when the API on Render is down; the live
 * part (components/status/StatusBoard.tsx) runs in the visitor's browser and
 * reports "Vectorae API unreachable — major outage" when the API does not
 * answer.
 */

import type { Metadata } from "next";
import Link from "next/link";

import { Footer } from "@/components/Footer";
import { StatusBoard } from "@/components/status/StatusBoard";

export const metadata: Metadata = {
  title: "Vectorae Status",
  description:
    "Live status of the Vectorae service: API, background processing, AI features and booking pages, plus past incidents.",
};

export default function StatusPage() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <SiteNav />

      <main className="px-4 py-12 sm:px-6 sm:py-16">
        <div className="mx-auto max-w-3xl">
          <p className="text-sm font-medium uppercase tracking-wider text-blue-500">Status</p>
          <h1 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">Vectorae service status</h1>
          <p className="mt-4 text-base leading-relaxed text-gray-300">
            Live checks of the Vectorae service, made from your browser. This page is hosted separately from
            the Vectorae API, so it keeps working when the API is down.
          </p>

          <div className="mt-10">
            <StatusBoard />
          </div>

          <p className="mt-12 text-sm text-gray-400">
            Something wrong that this page does not show? Signed-in customers can submit a ticket from the
            support panel in the app, or use the{" "}
            <Link href="/contact" className="text-blue-400 underline-offset-2 hover:underline">
              contact page
            </Link>
            .
          </p>
        </div>
      </main>

      <Footer />
    </div>
  );
}

function SiteNav() {
  return (
    <header className="border-b border-white/10 px-6 py-5">
      <nav className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-8 gap-y-3">
        <Link href="/" className="text-lg font-bold text-white">
          Vectorae
        </Link>
        <ul className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-gray-300">
          <li><Link href="/how-it-works" className="hover:text-white">How it Works</Link></li>
          <li><Link href="/about" className="hover:text-white">About</Link></li>
          <li><Link href="/pricing" className="hover:text-white">Pricing</Link></li>
          <li><Link href="/contact" className="hover:text-white">Contact</Link></li>
        </ul>
      </nav>
    </header>
  );
}
