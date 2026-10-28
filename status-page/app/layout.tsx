import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { paletteCss } from "../lib/palette.ts";
import { siteUrl } from "../lib/config.ts";
import { SiteFooter, SiteHeader } from "../components/SiteChrome.tsx";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl()),
  title: { default: "Vectorae Status", template: "%s · Vectorae Status" },
  description: "Current status, uptime and incident history for Vectorae.",
  alternates: {
    types: {
      "application/rss+xml": "/history.rss",
      "application/atom+xml": "/history.atom",
    },
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7f9" },
    { media: "(prefers-color-scheme: dark)", color: "#0e1116" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* Colour tokens from lib/palette.ts (contrast-tested in tests/palette.test.ts). */}
        <style dangerouslySetInnerHTML={{ __html: paletteCss() }} />
      </head>
      <body>
        <a className="skip" href="#main">
          Skip to content
        </a>
        <div className="wrap">
          <SiteHeader />
          <main id="main">{children}</main>
          <SiteFooter />
        </div>
      </body>
    </html>
  );
}
