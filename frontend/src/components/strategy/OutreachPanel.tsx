"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { listLeads, updateLeadStatus } from "@/lib/api/leads";
import {
  createSequence, enrollSequence, getLaunchLawfulBasis, sourceLeads,
} from "@/lib/api/outreach";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DEFAULT_SOURCE_LEADS, MAX_UI_LEADS, buildSequenceSteps, canLaunchSequence,
  canSourceLeads, contactableLeads, launchErrorMessage, parseMaxLeads,
  sourceErrorMessage, sourcingProgress,
} from "@/lib/leadOutreach";
import type { StrategyOut } from "@/lib/api/types";

/** Two steps for a VERIFIED strategy: find leads, then email the verified ones.
 *  Nothing is sent from this panel until the person has read the list and
 *  ticked the confirmation. The server still applies every compliance gate. */
export function OutreachPanel({ strategy }: { strategy: StrategyOut }) {
  const queryClient = useQueryClient();
  const strategyId = strategy.id;
  const [count, setCount] = useState(String(DEFAULT_SOURCE_LEADS));
  const [started, setStarted] = useState(false);
  const [name, setName] = useState("Outreach");
  const [opening, setOpening] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [followDays, setFollowDays] = useState("3");
  const [confirmed, setConfirmed] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  // The confirmation text is the server's (it records the version ticked).
  const lawfulBasis = useQuery({ queryKey: ["launch-lawful-basis"],
                                 queryFn: getLaunchLawfulBasis, staleTime: Infinity });
  const leadsQuery = useQuery({
    queryKey: ["strategy-leads", strategyId],
    queryFn: () => listLeads(strategyId),
    enabled: canSourceLeads(strategy.status),
    // Poll only while leads are still being found, enriched and verified.
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? [];
      return started && (items.length === 0 || sourcingProgress(items).running) ? 5000 : false;
    },
  });

  const source = useMutation({
    mutationFn: (n: number) => sourceLeads(strategyId, n),
    onSuccess: () => {
      setStarted(true);
      toast.success("Finding leads. This takes a few minutes.");
      queryClient.invalidateQueries({ queryKey: ["strategy-leads", strategyId] });
    },
    onError: (err) => toast.error(sourceErrorMessage(err)),
  });

  const drop = useMutation({
    mutationFn: (id: string) => updateLeadStatus(id, "dropped"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["strategy-leads", strategyId] }),
    onError: () => toast.error("Could not remove that lead."),
  });

  const launch = useMutation({
    mutationFn: async () => {
      // Create once; if enrolling is refused the retry reuses the sequence.
      let id = createdId;
      if (!id) {
        const days = Math.min(60, Math.max(1, Math.floor(Number(followDays)) || 3));
        const seq = await createSequence(strategyId, {
          name: name.trim() || "Outreach",
          steps: buildSequenceSteps({ openingBrief: opening, followUpBrief: followUp, followUpDays: days }),
        });
        id = seq.id;
        setCreatedId(id);
      }
      return enrollSequence(id, lawfulBasis.data!.version);
    },
    onSuccess: (r) => {
      setResult(
        r.status === "pending_approval"
          ? "Sent to a manager for approval. Nothing is sent until they approve."
          : `Launched. ${r.enrolled ?? 0} lead(s) enrolled.${r.warning ? " Warning: " + r.warning : ""}`,
      );
      queryClient.invalidateQueries({ queryKey: ["strategy-leads", strategyId] });
    },
    onError: (err) => toast.error(launchErrorMessage(err)),
  });

  if (!canSourceLeads(strategy.status)) return null;

  const leads = leadsQuery.data?.items ?? [];
  const progress = sourcingProgress(leads);
  const contactable = contactableLeads(leads);
  const n = parseMaxLeads(count);
  const canLaunch = canLaunchSequence({
    status: strategy.status, contactable: contactable.length,
    openingBrief: opening, confirmed,
  }) && !!lawfulBasis.data;

  return (
    <section aria-label="Find and contact leads" className="space-y-3">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">1. Find leads</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            Finds people who match this strategy, looks up their email and checks
            it. Each lead uses paid Apollo and Hunter credits. Start small.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1">
              How many leads (1 to {MAX_UI_LEADS})
              <input
                className="w-16 rounded border border-border bg-background px-1 py-0.5"
                inputMode="numeric" value={count}
                onChange={(e) => setCount(e.target.value)}
                aria-label={`Number of leads, 1 to ${MAX_UI_LEADS}`}
              />
            </label>
            <Button size="sm" disabled={n === null || source.isPending}
                    onClick={() => n !== null && source.mutate(n)}>
              {source.isPending ? "Starting..." : "Find leads"}
            </Button>
          </div>

          {leads.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground" role="status">
                {progress.running ? "Still working... " : "Finished. "}
                {progress.total} found, {progress.counts.verified ?? 0} verified
                {progress.counts.flagged ? `, ${progress.counts.flagged} flagged` : ""}
                {progress.counts.dropped ? `, ${progress.counts.dropped} removed` : ""}.
              </p>
              <ul className="space-y-1">
                {leads.map((l) => (
                  <li key={l.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-border p-2">
                    <span>
                      {l.full_name ?? "Unnamed"}
                      <span className="block text-xs text-muted-foreground">
                        {[l.title, l.company, l.email].filter(Boolean).join(" | ")}
                      </span>
                    </span>
                    <span className="flex items-center gap-2">
                      <Badge tone={l.status === "verified" ? "success" : "default"}>{l.status}</Badge>
                      {(l.status === "verified" || l.status === "flagged") && (
                        <Button variant="outline" size="sm" disabled={drop.isPending}
                                onClick={() => drop.mutate(l.id)}>
                          Remove
                        </Button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      {contactable.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">2. Write the email and launch</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {result ? (
              <p role="status" className="font-medium">{result}</p>
            ) : (
              <>
                <p className="text-muted-foreground">
                  Describe what the first email should say. Vectorae writes each
                  email for the person it is going to. Only the {contactable.length} verified
                  lead(s) above with an email address will be contacted. Remove
                  anyone you do not want to email before you launch.
                </p>
                <label className="block">
                  Sequence name
                  <input className="mt-1 w-full rounded border border-border bg-background px-2 py-1"
                         value={name} onChange={(e) => setName(e.target.value)} />
                </label>
                <label className="block">
                  First email: what should it say?
                  <textarea className="mt-1 w-full rounded border border-border bg-background px-2 py-1"
                            rows={4} value={opening} onChange={(e) => setOpening(e.target.value)}
                            placeholder="Who we are, why we are writing to them, one clear ask." />
                </label>
                <label className="block">
                  Follow-up email (optional)
                  <textarea className="mt-1 w-full rounded border border-border bg-background px-2 py-1"
                            rows={3} value={followUp} onChange={(e) => setFollowUp(e.target.value)} />
                </label>
                <label className="flex items-center gap-1">
                  Days to wait before the follow-up
                  <input className="w-14 rounded border border-border bg-background px-1 py-0.5"
                         inputMode="numeric" value={followDays}
                         onChange={(e) => setFollowDays(e.target.value)} />
                </label>
                <label className="flex items-start gap-2">
                  <Checkbox checked={confirmed} onCheckedChange={setConfirmed}
                            aria-label="Confirm I may lawfully contact these people" />
                  <span>{lawfulBasis.data?.text ?? "Loading the confirmation text..."}</span>
                </label>
                <Button size="sm" disabled={!canLaunch || launch.isPending}
                        onClick={() => launch.mutate()}>
                  {launch.isPending ? "Launching..." : `Launch to ${contactable.length} lead(s)`}
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      )}
    </section>
  );
}
