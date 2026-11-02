"use client";

/** The lead page's "Personalization" tab: exactly what the next email will be
 *  written from -- their recent LinkedIn posts, their company's news -- and
 *  the personal-video workflow for high-likelihood leads. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, ExternalLink, RefreshCw, Video } from "lucide-react";

import {
  getLeadPersonalization,
  recordLoom,
  refreshLeadPersonalization,
  setLeadLinkedInUrl,
  skipLoom,
  writeLoomScript,
  type LeadPersonalization,
} from "@/lib/api/personalization";
import { getLeadLinkedIn } from "@/lib/api/linkedin";
import { fetchedAgo, isLinkedInProfileUrl, isLoomShareUrl } from "@/lib/personalization";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { AsyncState } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";

export function PersonalizationPanel({ leadId, score }: { leadId: string; score: number | null | undefined }) {
  const toast = useToast();
  const qc = useQueryClient();
  const key = ["lead-personalization", leadId];
  const query = useQuery({ queryKey: key, queryFn: () => getLeadPersonalization(leadId) });
  const set = (data: LeadPersonalization) => qc.setQueryData(key, data);
  const onError = (e: unknown) => toast((e as Error).message, "error");

  const refresh = useMutation({
    mutationFn: () => refreshLeadPersonalization(leadId),
    onSuccess: (d) => { set(d); toast("Refreshed", "success"); },
    onError,
  });

  const data = query.data;
  return (
    <AsyncState isLoading={query.isLoading} error={query.error}>
      {data && (
        <div className="grid gap-4 lg:grid-cols-2">
          <LinkedInCard data={data} leadId={leadId} onSaved={set} />
          <Card>
            <CardHeader className="flex-row items-center justify-between gap-2">
              <CardTitle>Company news</CardTitle>
              <Button size="sm" variant="ghost" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
                <RefreshCw size={14} aria-hidden="true" /> Refresh all
              </Button>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p className="text-xs text-muted-foreground">
                Last 30 days, only articles that name the company. Checked {fetchedAgo(data.company_news_fetched_at)}.
              </p>
              {data.company_news?.length ? (
                <ul className="space-y-2">
                  {data.company_news.map((n, i) => (
                    <li key={i}>
                      {n.url ? (
                        <a href={n.url} target="_blank" rel="noreferrer" className="font-medium underline underline-offset-2">
                          {n.headline}
                        </a>
                      ) : <span className="font-medium">{n.headline}</span>}
                      <p className="text-muted-foreground">
                        {n.source} · {new Date(n.published_at).toLocaleDateString()}
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted-foreground">
                  {data.company_news === null ? "Not checked yet." : "No recent news naming this company."}
                </p>
              )}
            </CardContent>
          </Card>
          <LoomCard data={data} leadId={leadId} score={score} onSaved={set} />
        </div>
      )}
    </AsyncState>
  );
}

function LinkedInCard({ data, leadId, onSaved }: {
  data: LeadPersonalization;
  leadId: string;
  onSaved: (d: LeadPersonalization) => void;
}) {
  const toast = useToast();
  const [url, setUrl] = useState(data.linkedin_url ?? "");
  // Feature Group 5: where this lead stands on the LinkedIn channel.
  const state = useQuery({ queryKey: ["lead-linkedin", leadId], queryFn: () => getLeadLinkedIn(leadId) });
  const save = useMutation({
    mutationFn: () => setLeadLinkedInUrl(leadId, url.trim() || null),
    onSuccess: (d) => { onSaved(d); toast("LinkedIn URL saved", "success"); },
    onError: (e) => toast((e as Error).message, "error"),
  });
  const invalid = url.trim() !== "" && !isLinkedInProfileUrl(url);
  return (
    <Card>
      <CardHeader><CardTitle>LinkedIn profile</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex gap-2">
          <Input aria-label="LinkedIn profile URL" placeholder="https://www.linkedin.com/in/…"
                 value={url} onChange={(e) => setUrl(e.target.value)} aria-invalid={invalid} />
          <Button size="sm" variant="outline" disabled={invalid || save.isPending || url === (data.linkedin_url ?? "")}
                  onClick={() => save.mutate()}>Save</Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Used as the link in this lead&apos;s LinkedIn tasks, which you send yourself. Vectorae does
          not read LinkedIn posts or profiles.
        </p>
        {state.data && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-muted-foreground">LinkedIn channel:</span>
            <Badge tone={state.data.connection_status === "connected" ? "success"
                        : state.data.connection_status === "pending" ? "warning" : "default"}>
              {state.data.connection_status === "connected" ? "Connected"
                : state.data.connection_status === "pending" ? "Request pending" : "Not contacted"}
            </Badge>
            {state.data.is_premium && <Badge tone="accent">Premium (InMail)</Badge>}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function LoomCard({ data, leadId, score, onSaved }: {
  data: LeadPersonalization;
  leadId: string;
  score: number | null | undefined;
  onSaved: (d: LeadPersonalization) => void;
}) {
  const toast = useToast();
  const [shareUrl, setShareUrl] = useState(data.loom.share_url ?? "");
  const onError = (e: unknown) => toast((e as Error).message, "error");
  const script = useMutation({ mutationFn: () => writeLoomScript(leadId), onSuccess: onSaved, onError });
  const record = useMutation({
    mutationFn: () => recordLoom(leadId, shareUrl.trim()),
    onSuccess: (d) => { onSaved(d); toast("Video attached — sequence step 2 will link to it", "success"); },
    onError,
  });
  const skip = useMutation({ mutationFn: () => skipLoom(leadId), onSuccess: onSaved, onError });
  const loom = data.loom;

  return (
    <Card className="lg:col-span-2">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2">
          <Video size={16} aria-hidden="true" /> Personal video
          {loom.status && <Badge tone={loom.status === "recorded" ? "success" : "default"}>{loom.status}</Badge>}
        </CardTitle>
        <Button size="sm" variant="ghost" disabled={script.isPending} onClick={() => script.mutate()}>
          {loom.script ? "Rewrite script" : "Write a script"}
        </Button>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          Leads scoring above 75 get a recording script automatically{score != null ? ` (this one: ${score})` : ""}.
          Record it in Loom, paste the share link, and step 2 of the sequence links to a page with their
          name and company that plays it. No link is sent until a video exists.
        </p>
        {loom.script && (
          <div className="rounded border border-border p-3">
            <div className="mb-1 flex items-center justify-between">
              <p className="font-medium">{loom.title}</p>
              <Button size="sm" variant="ghost" aria-label="Copy script"
                      onClick={() => { void navigator.clipboard?.writeText(loom.script ?? ""); toast("Script copied", "success"); }}>
                <Copy size={14} aria-hidden="true" />
              </Button>
            </div>
            <p className="whitespace-pre-wrap">{loom.script}</p>
            {loom.on_screen && <p className="mt-2 text-xs text-muted-foreground">On screen: {loom.on_screen}</p>}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <a href="https://www.loom.com/record" target="_blank" rel="noreferrer"
             className="inline-flex h-8 items-center gap-1 rounded border border-border px-3 text-xs font-medium hover:bg-muted">
            <ExternalLink size={14} aria-hidden="true" /> Record in Loom
          </a>
          <Input className="min-w-64 flex-1" aria-label="Loom share link"
                 placeholder="https://www.loom.com/share/…" value={shareUrl}
                 onChange={(e) => setShareUrl(e.target.value)}
                 aria-invalid={shareUrl.trim() !== "" && !isLoomShareUrl(shareUrl)} />
          <Button size="sm" disabled={!isLoomShareUrl(shareUrl) || record.isPending} onClick={() => record.mutate()}>
            Attach video
          </Button>
          {loom.status === "suggested" && (
            <Button size="sm" variant="ghost" disabled={skip.isPending} onClick={() => skip.mutate()}>Skip</Button>
          )}
        </div>
        {data.loom_page_url && (
          <p className="text-xs">
            Prospect page:{" "}
            <a href={data.loom_page_url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
              preview
            </a>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
