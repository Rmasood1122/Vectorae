"use client";

/** How you actually get into the call.
 *
 * VECTORAE IS PLATFORM-AGNOSTIC, and this component is where that promise is
 * kept. Google Meet, Zoom and Teams all send `X-Frame-Options: DENY` (or the
 * CSP equivalent) on their join pages, so an iframe embed renders a blank box
 * — which is worse than no embed at all, because it looks broken rather than
 * absent. Every platform therefore gets the same two affordances: a prominent
 * button that opens the call in the tool the client already uses, and a copy
 * button for the raw link.
 *
 * The link is shown in full, not hidden behind the button, for the case that
 * actually happens on a sales call: the prospect cannot get in, and the user
 * needs to read the URL out or paste it into a chat window.
 */

import { useState } from "react";
import { Check, Copy, ExternalLink, Video } from "lucide-react";

import type { MeetingPlatform } from "@/lib/api/meetings";
import { Button } from "@/components/ui/button";

const PLATFORM_LABEL: Record<MeetingPlatform, string> = {
  google_meet: "Google Meet",
  zoom: "Zoom",
  teams: "Microsoft Teams",
  custom: "the meeting link",
};

export function PlatformLauncher({
  platform,
  meetingUrl,
}: {
  platform: MeetingPlatform;
  meetingUrl: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const label = PLATFORM_LABEL[platform] ?? PLATFORM_LABEL.custom;

  const copy = async () => {
    if (!meetingUrl) return;
    try {
      await navigator.clipboard.writeText(meetingUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard is denied in some WebViews and over plain HTTP. The link is
      // already on screen in full, so there is nothing to recover — silently
      // not confirming beats a scary error during a call.
    }
  };

  if (!meetingUrl) {
    return (
      <div className="rounded border border-dashed border-border p-4 text-center text-sm text-muted-foreground">
        <Video size={20} aria-hidden="true" className="mx-auto mb-2" />
        No join link on this meeting yet. Add one from the booking, or paste
        the link the client sent you.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <a
        href={meetingUrl}
        target="_blank"
        rel="noreferrer noopener"
        className="flex w-full items-center justify-center gap-2 rounded bg-[rgb(var(--primary))] px-4 py-3 font-medium text-primary-foreground hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <ExternalLink size={16} aria-hidden="true" />
        Open in {label}
      </a>

      <div className="flex items-center gap-2 rounded border border-border bg-muted/40 px-2 py-1.5">
        <code className="min-w-0 flex-1 truncate text-xs" title={meetingUrl}>
          {meetingUrl}
        </code>
        <Button
          size="sm"
          variant="ghost"
          onClick={copy}
          aria-label="Copy meeting link"
        >
          {copied ? (
            <Check size={14} aria-hidden="true" />
          ) : (
            <Copy size={14} aria-hidden="true" />
          )}
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        Your client can join from any device — Vectorae does not require them
        to install anything.
      </p>
    </div>
  );
}
