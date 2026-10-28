"use client";

/** Feature Group 8: white-label branding for the host this app is served on.
 *
 *  GET /branding?host= is public, so a branded login page can show the brand
 *  before anyone signs in. The brand colour only sets --primary (and its
 *  readable foreground); a user who picks their own theme afterwards still
 *  gets their choice. */

import { useEffect, useState } from "react";
import { api } from "./api/client";
import { hexToRgb, readableTextOn } from "./theme/contrast";

export interface PublicBranding {
  white_label: boolean;
  brand_name: string;
  logo_url: string | null;
  primary_color: string | null;
  support_email: string | null;
}

export const DEFAULT_BRANDING: PublicBranding = {
  white_label: false,
  brand_name: "Vectorae",
  logo_url: null,
  primary_color: null,
  support_email: null,
};

/** The CSS variables a brand overrides (empty when not white-labelled). */
export function brandCssVars(b: PublicBranding): Record<string, string> {
  if (!b.white_label || !b.primary_color || !/^#[0-9a-fA-F]{6}$/.test(b.primary_color)) {
    return {};
  }
  return {
    "--primary": hexToRgb(b.primary_color).join(" "),
    "--primary-foreground": hexToRgb(readableTextOn(b.primary_color)).join(" "),
  };
}

let cached: PublicBranding | null = null;

export function useBranding(): PublicBranding {
  const [branding, setBranding] = useState<PublicBranding>(cached ?? DEFAULT_BRANDING);
  useEffect(() => {
    if (cached) return;
    let alive = true;
    api<PublicBranding>(`/branding?host=${encodeURIComponent(window.location.host)}`,
                        { auth: false })
      .then((b) => {
        cached = b;
        if (!alive) return;
        setBranding(b);
        if (b.white_label) {
          document.title = b.brand_name;
          for (const [name, value] of Object.entries(brandCssVars(b))) {
            document.documentElement.style.setProperty(name, value);
          }
        }
      })
      .catch(() => {
        /* unbranded is always a valid answer */
      });
    return () => {
      alive = false;
    };
  }, []);
  return branding;
}
