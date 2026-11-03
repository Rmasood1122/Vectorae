"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { isUnverifiedError, login, me } from "@/lib/api/auth";
import { ApiError, restoreSession } from "@/lib/api/client";
import { isPhoneDeferred } from "@/lib/identity";
import { postLoginDestination } from "@/lib/post-login";
import { takeConsentReturn, withConsentReturn } from "@/lib/oauthConsent";
import { takeReturnTo, withReturnTo } from "@/lib/returnTo";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { LogoLockup } from "@/components/ui/Logo";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // True until we know there is no session to restore. The form is not shown
  // meanwhile, so a signed-in user never sees it flash before being sent on.
  const [checkingSession, setCheckingSession] = useState(true);

  // GET /auth/verify 302s back here with one of these flags. Read from
  // window.location rather than useSearchParams() because this is a static
  // export, where useSearchParams forces a Suspense boundary at build time.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const verified = params.get("verified");
    const err = params.get("error");
    if (verified === "true") {
      setNotice("Email verified. Sign in to continue.");
    } else if (verified === "already") {
      setNotice("That address is already verified. Sign in to continue.");
    } else if (err === "expired") {
      setError(
        "That verification link has expired. Sign in and we will send you a new one.",
      );
    } else if (err === "invalid") {
      setError(
        "That verification link is not valid. Sign in and we will send you a new one.",
      );
    }
  }, []);

  // Persistent sign-in: a returning visitor with a live session is signed
  // straight back in and routed exactly as an explicit sign-in would be.
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        if (!(await restoreSession())) return;
        const user = await me();
        if (mounted) {
          router.replace(withReturnTo(withConsentReturn(
            postLoginDestination(user, { phoneDeferred: isPhoneDeferred() }), takeConsentReturn()),
            takeReturnTo()));
        }
      } catch (err) {
        if (mounted && isUnverifiedError(err)) router.replace("/check-email");
        // Anything else (API unreachable): fall through to the form.
      } finally {
        if (mounted) setCheckingSession(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = await login(email, password, rememberMe);
      // Login SUCCEEDS for an unverified account on purpose -- otherwise there
      // is no signed-in state from which to ask for a new link. The routing
      // decision (verify email / verify identity / choose a plan / dashboard)
      // is made from the response, which the server computes fresh. This is the
      // ONE place the plan check runs (afterLogin); session restores skip it.
      // A pending Claude / ChatGPT connection (lib/oauthConsent.ts) goes back
      // to its consent page instead of the dashboard or pricing.
      // A page the person was sent here from (lib/returnTo.ts) comes next,
      // e.g. an /admin/growth link opened while signed out.
      router.replace(withReturnTo(withConsentReturn(
        postLoginDestination(user, { phoneDeferred: isPhoneDeferred(), afterLogin: true }),
        takeConsentReturn()), takeReturnTo()));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not sign in");
      setBusy(false);
    }
  }

  if (checkingSession) {
    return (
      <main className="flex min-h-dvh items-center justify-center p-4">
        <p role="status" className="text-sm text-muted-foreground">
          Checking your session…
        </p>
      </main>
    );
  }

  return (
    <main className="flex min-h-dvh items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          {/* Room here for the full lockup at a size where the wordmark
              actually reads, so the title drops the product name. */}
          <LogoLockup width={132} className="mb-2" />
          <CardTitle className="text-xl">Sign in</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" autoComplete="email" required value={email}
                     onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div className="space-y-1">
              <div className="flex items-baseline justify-between">
                <Label htmlFor="password">Password</Label>
                <Link href="/forgot-password" className="text-xs text-[rgb(var(--primary))] underline">
                  Forgot password?
                </Link>
              </div>
              <Input id="password" type="password" autoComplete="current-password" required
                     value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
            <label htmlFor="remember-me" className="flex items-center gap-2 text-sm">
              <input id="remember-me" type="checkbox" checked={rememberMe}
                     onChange={(e) => setRememberMe(e.target.checked)}
                     className="h-4 w-4 rounded border-border" />
              Keep me signed in
            </label>
            {notice && (
              <p role="status" className="text-sm text-[rgb(var(--primary))]">
                {notice}
              </p>
            )}
            {error && (
              <p role="alert" className="text-sm text-destructive">{error}</p>
            )}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              No account?{" "}
              <Link href="/signup" className="text-[rgb(var(--primary))] underline">
                Sign up
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
