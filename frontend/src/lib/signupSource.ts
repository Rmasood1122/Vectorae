/** Where a visitor came from, kept until they sign up (Admin > Growth).
 *
 *  First touch wins: the first page of the visit records its UTM tags, the
 *  referring site and the landing path into sessionStorage (this tab only,
 *  gone when it closes). The sign-up request sends it; the server keeps only
 *  the referrer's host and the path (app/services/signup_watch.clean_source).
 *  Never throws: a blocked storage simply means "direct". */

export interface SignupSource {
  utm_source?: string;
  utm_medium?: string;
  utm_campaign?: string;
  utm_term?: string;
  utm_content?: string;
  referrer?: string;
  landing_path?: string;
}

const KEY = "vectorae.signup_source";
const UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"] as const;

export function sourceFrom(search: string, referrer: string, pathname: string,
                           ownHost: string): SignupSource {
  const params = new URLSearchParams(search);
  const out: SignupSource = {};
  for (const key of UTM_KEYS) {
    const value = params.get(key);
    if (value) out[key] = value.slice(0, 120);
  }
  try {
    const host = referrer ? new URL(referrer).hostname : "";
    if (host && host !== ownHost) out.referrer = host;
  } catch {
    /* not a URL: no referrer */
  }
  out.landing_path = pathname.slice(0, 120);
  return out;
}

export function captureSignupSource(): void {
  if (typeof window === "undefined") return;
  try {
    if (window.sessionStorage.getItem(KEY)) return;
    const source = sourceFrom(window.location.search, document.referrer,
                              window.location.pathname, window.location.hostname);
    window.sessionStorage.setItem(KEY, JSON.stringify(source));
  } catch {
    /* storage blocked */
  }
}

export function readSignupSource(): SignupSource | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const raw = window.sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as SignupSource) : undefined;
  } catch {
    return undefined;
  }
}
