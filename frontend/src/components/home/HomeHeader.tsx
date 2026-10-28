"use client";

/**
 * Home page header. An anonymous visitor sees "Sign in" and "Start free"; a
 * visitor who is already signed in sees "Open app", which goes exactly where
 * a restored session belongs (src/lib/homeEntry.ts signedInAppLink). Nobody
 * is redirected away from the page they asked for.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { Menu } from "lucide-react";

import { isUnverifiedError, me } from "@/lib/api/auth";
import { restoreSession } from "@/lib/api/client";
import { homeMode, isStandaloneDisplay, signedInAppLink } from "@/lib/homeEntry";
import { isPhoneDeferred } from "@/lib/identity";
import { isNative } from "@/lib/platform";
import { HERO, LOGIN_PATH, NAV, SIGNUP_PATH } from "./content";

export function HomeHeader() {
  const [appHref, setAppHref] = useState<string | null>(null);

  useEffect(() => {
    // In the app shell HomeGate is already routing; do not restore twice.
    if (homeMode({ buildTarget: process.env.NEXT_PUBLIC_BUILD_TARGET, native: isNative(), standalone: isStandaloneDisplay() }) === "app") {
      return;
    }
    let mounted = true;
    signedInAppLink({ restoreSession, me, phoneDeferred: isPhoneDeferred, isUnverifiedError })
      .then((href) => {
        if (mounted) setAppHref(href);
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <header className="sticky top-0 z-40 border-b border-white/10 bg-[#0f172a]">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-6 px-4 sm:px-6">
        <Link href="/" className="lp-wordmark flex items-center gap-2.5 text-lg font-extrabold tracking-tight text-white">
          <span aria-hidden className="h-3 w-3 rounded-[3px] bg-[#f2b544]" />
          Vectorae
        </Link>
        <nav aria-label="Main" className="hidden md:block">
          <ul className="flex items-center gap-6 text-[0.95rem] text-[#cbd5e1]">
            {NAV.map((n) => (
              <li key={n.href}>
                <a href={n.href} className="rounded transition-colors hover:text-white">
                  {n.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="ml-auto flex items-center gap-2 sm:gap-3">
          {appHref ? (
            <a href={appHref} className="lp-btn lp-btn-primary" data-testid="open-app">
              Open app
            </a>
          ) : (
            <>
              <Link href={LOGIN_PATH} className="rounded px-2 py-2 text-[0.95rem] font-medium text-[#e2e8f0] hover:text-white">
                Sign in
              </Link>
              <Link href={SIGNUP_PATH} className="lp-btn lp-btn-primary hidden sm:inline-flex">
                {HERO.primary}
              </Link>
            </>
          )}
          <details className="lp-menu relative md:hidden">
            <summary className="grid h-10 w-10 cursor-pointer list-none place-items-center rounded-lg text-white ring-1 ring-white/20 hover:bg-white/10">
              <Menu aria-hidden className="h-5 w-5" />
              <span className="sr-only">Menu</span>
            </summary>
            <nav aria-label="Main" className="absolute right-0 top-12 w-56 rounded-xl bg-white p-2 shadow-xl ring-1 ring-black/5">
              <ul>
                {NAV.map((n) => (
                  <li key={n.href}>
                    <a href={n.href} className="block rounded-lg px-3 py-2.5 text-[#0f172a] hover:bg-[#f1f5f9]">
                      {n.label}
                    </a>
                  </li>
                ))}
                {!appHref && (
                  <li>
                    <Link href={SIGNUP_PATH} className="mt-1 block rounded-lg bg-[#1d4ed8] px-3 py-2.5 font-semibold text-white">
                      {HERO.primary}
                    </Link>
                  </li>
                )}
              </ul>
            </nav>
          </details>
        </div>
      </div>
    </header>
  );
}
