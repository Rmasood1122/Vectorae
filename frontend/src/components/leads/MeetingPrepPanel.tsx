"use client";

/** The "Meeting Prep" tab: the brief generated when this lead booked.
 *
 * Rendered from the brief's STRUCTURED halves (sections_json, profile_json)
 * as cards rather than from its Markdown, so the opening script can sit at the
 * top with a copy button and the profile renders as a real table. The Markdown
 * is still one click away ("Copy as Markdown") for pasting into notes.
 *
 * Polls while the worker is writing the brief and stops the moment it settles
 * -- a failed brief does not poll forever. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Copy, ExternalLink, FileText, RefreshCw } from "lucide-react";

import {
  getLeadPrep,
  regeneratePrep,
  requestLeadPrep,
  type MeetingPrepBrief,
} from "@/lib/api/meetingPrep";
import { isBriefInFlight, meetingWhen, profileRows } from "@/lib/meeting-prep";
import { CallScriptPanel } from "@/components/leads/CallScriptPanel";
import { RoleplayPanel } from "@/components/leads/RoleplayPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AsyncState } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";

const SOURCE_LABEL: Record<string, string> = {
  calendly: "Booked via Calendly",
  leadpilot_calendar: "Booked on your Vectorae calendar",
  manual: "Prepared on request",
};

const POLL_MS = 4_000;

export function MeetingPrepPanel({ leadId }: { leadId: string }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["meeting-prep", leadId],
    queryFn: () => getLeadPrep(leadId),
    refetchInterval: (q) => (isBriefInFlight(q.state.data?.brief) ? POLL_MS : false),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["meeting-prep", leadId] });

  const request = useMutation({
    mutationFn: () => requestLeadPrep(leadId),
    onSuccess: () => {
      refresh();
      toast("Writing your prep brief — this takes about a minute", "info");
    },
    onError: (e) => toast((e as Error).message, "error"),
  });
  const regenerate = useMutation({
    mutationFn: (briefId: string) => regeneratePrep(briefId),
    onSuccess: () => {
      refresh();
      toast("Regenerating the brief", "info");
    },
    onError: (e) => toast((e as Error).message, "error"),
  });

  const brief = query.data?.brief ?? null;

  return (
    <AsyncState isLoading={query.isLoading} error={query.error}>
      {!brief ? (
        <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm">
          <FileText className="mx-auto mb-2 text-muted-foreground" aria-hidden="true" />
          <p className="font-medium">No meeting prep brief yet</p>
          <p className="mx-auto mt-1 max-w-md text-muted-foreground">
            A brief is written automatically when this lead books a meeting. You can also
            prepare one now from everything we know about them.
          </p>
          <Button className="mt-4" disabled={request.isPending} onClick={() => request.mutate()}>
            {request.isPending ? "Starting…" : "Generate prep brief"}
          </Button>
          {/* Part 2: practice does NOT need a brief. Rehearsing a prospect you
              have not booked yet is a legitimate thing to want, and gating it
              behind a booking would make the standalone tool unreachable from
              the one screen people look for it on. */}
          <div className="mt-6 text-left">
            <RoleplayPanel leadId={leadId} />
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <BriefView
            brief={brief}
            busy={regenerate.isPending}
            onRegenerate={() => regenerate.mutate(brief.id)}
            onCopy={() => {
              void navigator.clipboard?.writeText(brief.content_md ?? "");
              toast("Brief copied as Markdown", "success");
            }}
          />
          {/* Part 2: the editable script + the pre-meeting checklist, then the
              rehearsal. In that order because that is the order the work
              happens in -- read, make it yours, practise it. */}
          <CallScriptPanel briefId={brief.id} />
          <RoleplayPanel leadId={leadId} briefId={brief.id} />
        </div>
      )}
    </AsyncState>
  );
}

function BriefView({
  brief,
  busy,
  onRegenerate,
  onCopy,
}: {
  brief: MeetingPrepBrief;
  busy: boolean;
  onRegenerate: () => void;
  onCopy: () => void;
}) {
  const inFlight = isBriefInFlight(brief);
  const s = brief.sections_json;
  const rows = profileRows(brief.profile_json);
  const posts = brief.profile_json?.linkedin_posts ?? [];
  const news = brief.profile_json?.company_news ?? [];

  return (
    <div className="space-y-4">
      {/* Meeting header */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card p-4">
        <div className="space-y-1 text-sm">
          <p className="flex items-center gap-2 font-medium">
            <CalendarClock size={16} aria-hidden="true" />
            {meetingWhen(brief.meeting_start_at)}
            {brief.cancelled_at && <Badge tone="destructive">Cancelled</Badge>}
          </p>
          <p className="text-muted-foreground">
            {SOURCE_LABEL[brief.source] ?? brief.source}
            {brief.generated_at && ` · written ${new Date(brief.generated_at).toLocaleString()}`}
          </p>
          <div className="flex flex-wrap gap-1.5 pt-1">
            {brief.reminder_24h_sent_at && <Badge>Day-before reminder sent</Badge>}
            {brief.reminder_1h_sent_at && <Badge>1-hour reminder sent</Badge>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {brief.meeting_url && (
            <a
              href={brief.meeting_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-8 items-center gap-1 rounded border border-border px-3 text-xs font-medium hover:bg-muted"
            >
              <ExternalLink size={14} aria-hidden="true" /> Join link
            </a>
          )}
          {brief.content_md && (
            <Button size="sm" variant="ghost" onClick={onCopy}>
              <Copy size={14} aria-hidden="true" /> Copy as Markdown
            </Button>
          )}
          <Button size="sm" variant="outline" disabled={busy || inFlight} onClick={onRegenerate}>
            <RefreshCw size={14} aria-hidden="true" /> Regenerate
          </Button>
        </div>
      </div>

      {inFlight && (
        <div role="status" className="rounded-lg border border-border bg-muted p-4 text-sm">
          Writing your brief from the lead&apos;s profile, their replies, the strategy and the
          competitor research. It usually takes under a minute; this page updates on its own.
        </div>
      )}

      {brief.status === "failed" && (
        <div role="alert" className="rounded-lg border border-destructive/40 bg-card p-4 text-sm">
          <p className="font-medium text-destructive">The brief couldn&apos;t be written</p>
          <p className="text-muted-foreground">{brief.error ?? "Unknown error"}</p>
        </div>
      )}

      {brief.status === "ready" && s && (
        <>
          {s.opening_60_seconds && (
            <Card className="border-[rgb(var(--primary))]">
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle>Your opening 60 seconds</CardTitle>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void navigator.clipboard?.writeText(s.opening_60_seconds)}
                  aria-label="Copy opening script"
                >
                  <Copy size={14} aria-hidden="true" />
                </Button>
              </CardHeader>
              <CardContent>
                <blockquote className="whitespace-pre-wrap text-sm leading-relaxed">
                  {s.opening_60_seconds}
                </blockquote>
              </CardContent>
            </Card>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <Section title="Why they booked" text={s.why_they_booked} />

            <Card>
              <CardHeader><CardTitle>Lead profile</CardTitle></CardHeader>
              <CardContent>
                {rows.length ? (
                  <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1.5 text-sm">
                    {rows.map((row) => (
                      <div key={row.key} className="contents">
                        <dt className="text-muted-foreground">{row.label}</dt>
                        <dd className="break-words">
                          {row.href ? (
                            <a href={row.href} target="_blank" rel="noreferrer"
                               className="underline underline-offset-2">
                              {row.value}
                            </a>
                          ) : row.value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <Empty />
                )}
                <p className="mt-3 text-xs text-muted-foreground">
                  Copied from your records, not written by AI.
                </p>
              </CardContent>
            </Card>

            <Section title="Company overview" text={s.company_overview} />

            <Card>
              <CardHeader><CardTitle>Recent LinkedIn activity</CardTitle></CardHeader>
              <CardContent className="space-y-3 text-sm">
                {posts.length ? (
                  <ul className="space-y-2">
                    {posts.map((post, i) => (
                      <li key={i} className="rounded border border-border p-2">
                        <p className="line-clamp-4 whitespace-pre-wrap">{post.text}</p>
                        {post.posted_at && (
                          <p className="mt-1 text-xs text-muted-foreground">
                            {new Date(post.posted_at).toLocaleDateString()}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-muted-foreground">No LinkedIn posts on record.</p>
                )}
                {s.recent_activity && <p>{s.recent_activity}</p>}
                {news.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-medium text-muted-foreground">Company news</p>
                    <ul className="list-disc space-y-1 pl-5">
                      {news.map((item, i) => (
                        <li key={i}>
                          {item.url ? (
                            <a href={item.url} target="_blank" rel="noreferrer"
                               className="underline underline-offset-2">{item.headline}</a>
                          ) : item.headline}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </CardContent>
            </Card>

            <ListSection
              title="Likely pain points"
              items={s.pain_points}
              note="Hypotheses from the ICP, the strategy and their activity. Confirm them on the call."
            />

            <Card>
              <CardHeader><CardTitle>Likely objections</CardTitle></CardHeader>
              <CardContent>
                {s.likely_objections.length ? (
                  <ul className="space-y-2 text-sm">
                    {s.likely_objections.map((item, i) => (
                      <li key={i}>
                        <p className="font-medium">{item.objection}</p>
                        {item.response && <p className="text-muted-foreground">{item.response}</p>}
                      </li>
                    ))}
                  </ul>
                ) : <Empty />}
              </CardContent>
            </Card>

            <ListSection title="Talking points" items={s.talking_points} />
            <ListSection title="Discovery questions" items={s.discovery_questions} ordered />
            <Section title="Competitive landscape" text={s.competitive_landscape} />
            <ListSection title="Recommended next steps" items={s.next_steps} />
            <Section title="Deal structure" text={s.deal_structure} />
          </div>
        </>
      )}
    </div>
  );
}

function Empty() {
  return <p className="text-sm text-muted-foreground">Not covered.</p>;
}

function Section({ title, text }: { title: string; text: string }) {
  return (
    <Card>
      <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
      <CardContent>
        {text ? <p className="whitespace-pre-wrap text-sm leading-relaxed">{text}</p> : <Empty />}
      </CardContent>
    </Card>
  );
}

function ListSection({
  title,
  items,
  note,
  ordered,
}: {
  title: string;
  items: string[];
  note?: string;
  ordered?: boolean;
}) {
  const List = ordered ? "ol" : "ul";
  return (
    <Card>
      <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
      <CardContent>
        {note && <p className="mb-2 text-xs text-muted-foreground">{note}</p>}
        {items.length ? (
          <List className={`space-y-1 pl-5 text-sm ${ordered ? "list-decimal" : "list-disc"}`}>
            {items.map((item, i) => <li key={i}>{item}</li>)}
          </List>
        ) : <Empty />}
      </CardContent>
    </Card>
  );
}
