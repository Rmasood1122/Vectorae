"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { signup } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { CLOSING, HERO, STAGES } from "@/components/home/content";

export default function SignupPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const bundle = await signup(email, password);
      // NOT /pipeline any more. The account exists and the tokens are real,
      // but every dashboard request would come back 403 EMAIL_NOT_VERIFIED
      // until the emailed link is clicked -- so sending them to the dashboard
      // would show a broken page instead of an explanation.
      // `sent=false` tells the next screen the mail transport failed, so it
      // leads with "resend" instead of "check your inbox".
      const params = new URLSearchParams({ email });
      if (bundle.verification_email_sent === false) params.set("sent", "false");
      router.replace(`/check-email?${params.toString()}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not sign up");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="lp-auth flex min-h-dvh flex-col bg-[#f8fafc] text-[#0f172a] lg:flex-row">
      {/* Same palette and story as the home page (src/components/home). */}
      <aside className="relative overflow-hidden bg-[#0f172a] px-6 py-8 text-white sm:px-10 lg:flex lg:w-[44%] lg:flex-col lg:justify-between lg:py-12">
        <div aria-hidden className="lp-grid absolute inset-0" />
        <div className="relative">
          <Link href="/" className="inline-flex items-center gap-2.5 text-lg font-extrabold tracking-tight">
            <span aria-hidden className="h-3 w-3 rounded-[3px] bg-[#f2b544]" />
            Vectorae
          </Link>
          <p className="mt-6 max-w-[22ch] text-2xl font-extrabold leading-tight tracking-[-0.03em] sm:text-3xl lg:mt-16 lg:text-[2.6rem] lg:leading-[1.05]">
            {CLOSING.title}
          </p>
          <p className="mt-3 max-w-[42ch] text-[0.95rem] leading-relaxed text-[#cbd5e1] lg:mt-5 lg:text-base">
            {CLOSING.body}
          </p>
        </div>
        <ol className="relative mt-10 hidden space-y-4 lg:block" aria-label="What happens after you sign up">
          {STAGES.map((stage, i) => (
            <li key={stage.id} className="grid grid-cols-[1.25rem_6.5rem_1fr] items-baseline gap-x-2 text-[#cbd5e1]">
              <span className="text-sm tabular-nums text-[#64748b]">{i + 1}</span>
              <span className="font-semibold text-white">{stage.name}</span>
              <span className="text-sm leading-relaxed">{stage.you}</span>
            </li>
          ))}
        </ol>
      </aside>

      <main className="flex flex-1 items-start justify-center px-4 py-10 sm:px-6 lg:items-center">
        <div className="w-full max-w-sm">
          <h1 className="text-[1.75rem] font-extrabold tracking-[-0.025em]">Create your account</h1>
          <p className="mt-2 text-[0.95rem] text-[#475569]">{HERO.note}</p>
          <form onSubmit={submit} className="mt-8 space-y-5">
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" required value={email} autoComplete="email"
                     className="h-11 text-base"
                     onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password (min 8 characters)</Label>
              <Input id="password" type="password" minLength={8} required autoComplete="new-password"
                     className="h-11 text-base"
                     value={password}
                     onChange={(e) => setPassword(e.target.value)} />
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive">{error}</p>
            )}
            <Button type="submit" className="h-11 w-full text-base" disabled={busy}>
              {busy ? "Creating…" : "Create account"}
            </Button>
            <p className="text-center text-sm text-[#475569]">
              Already registered?{" "}
              <Link href="/login" className="font-medium text-[#1d4ed8] underline underline-offset-4">
                Sign in
              </Link>
            </p>
          </form>
        </div>
      </main>
    </div>
  );
}
