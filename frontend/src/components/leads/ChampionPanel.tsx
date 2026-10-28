"use client";

/** F10 — Champion Tracker + Staleness Check, per prospect.
 *
 *  Answers: does this person still hold the role we are writing to, will the
 *  next message go out (and if not, why), and — if they moved — where they
 *  went and what Vectorae did about it. The actions are the only ways past a
 *  hold: re-verify now, or (owner/manager) confirm the role with a reason
 *  that goes into the compliance audit log. A recorded departure cannot be
 *  confirmed away here; it is dismissed from the move itself. */

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, UserCheck } from "lucide-react";

import { confirmRole, dismissChampionMove, getRoleCheck, refreshRoleCheck } from "@/lib/api/champions";
import {
  ageText,
  attemptText,
  canAdoptTitle,
  canConfirm,
  canRefresh,
  decisionText,
  moveOutcomeText,
  moveText,
  panelVisible,
  reasonError,
  stateLabel,
  stateTone,
  type RoleCheck,
} from "@/lib/champion-tracker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input, Label } from "@/components/ui/input";
import { AsyncState } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";

export function ChampionPanel({ leadId }: { leadId: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState("");
  const [adopt, setAdopt] = useState(false);
  const key = ["lead-role-check", leadId];

  const { data, isLoading, error } = useQuery({
    queryKey: key,
    queryFn: () => getRoleCheck(leadId),
  });

  const onError = (e: unknown) => toast((e as Error).message, "error");
  const settle = (fresh: RoleCheck, message: string) => {
    qc.setQueryData(key, fresh);
    const released = fresh.released_messages ?? fresh.result?.released ?? 0;
    toast(released ? `${message} — ${released} held message(s) released` : message, "success");
  };

  const refresh = useMutation({
    mutationFn: () => refreshRoleCheck(leadId),
    onSuccess: (fresh) => settle(fresh, "Role re-verified"),
    onError,
  });
  const confirm = useMutation({
    mutationFn: () => confirmRole(leadId, reason.trim(), adopt),
    onSuccess: (fresh) => {
      setConfirming(false);
      setReason("");
      settle(fresh, "Role confirmed");
    },
    onError,
  });
  const dismiss = useMutation({
    mutationFn: () => dismissChampionMove(data?.move?.id ?? "", reason.trim()),
    onSuccess: () => {
      setDismissing(false);
      setReason("");
      qc.invalidateQueries({ queryKey: key });
      toast("Job change dismissed", "success");
    },
    onError,
  });

  if (!isLoading && !error && !panelVisible(data)) return null;
  const err = (confirming || dismissing) ? reasonError(reason) : null;

  return (
    <Card aria-label="Role check">
      <CardHeader className="gap-1">
        <CardTitle className="text-sm">Role check</CardTitle>
        <p className="text-xs text-muted-foreground">{decisionText(data)}</p>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <AsyncState isLoading={isLoading} error={error}>
          {data && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={stateTone(data)}>{stateLabel(data)}</Badge>
                {data.champion_kind && (
                  <Badge tone="success">
                    {data.champion_kind === "customer" ? "Customer" : "Positive replier"}
                  </Badge>
                )}
                <span className="text-xs text-muted-foreground">{ageText(data)}</span>
              </div>

              {data.observed_title && data.observed_title !== data.title && (
                <p className="text-xs">
                  Enrichment now says: {data.observed_title}
                  {data.observed_company ? ` at ${data.observed_company}` : ""}
                </p>
              )}

              {data.previous?.from_company && (
                <p className="text-xs text-muted-foreground">
                  Re-engagement lead: previously {[data.previous.from_title,
                    data.previous.from_company].filter(Boolean).join(" at ")}
                  {data.previous.previous_lead_id && (
                    <> · <Link className="underline underline-offset-2"
                               href={`/leads/detail?id=${data.previous.previous_lead_id}`}>
                      earlier record</Link></>
                  )}
                </p>
              )}

              {data.move && (
                <div className="space-y-1 rounded border border-border/60 p-2 text-xs">
                  <p className="font-medium">{moveText(data.move)}</p>
                  <p className="text-muted-foreground">{moveOutcomeText(data.move)}</p>
                  {data.move.new_lead && (
                    <Link className="underline underline-offset-2"
                          href={`/leads/detail?id=${data.move.new_lead.id}`}>
                      Open the lead at {data.move.new_lead.company ?? "the new company"}
                    </Link>
                  )}
                  {data.move.dismissed_at ? (
                    <p className="text-muted-foreground">
                      Dismissed: {data.move.dismiss_reason}
                    </p>
                  ) : !dismissing && (
                    <Button size="sm" variant="outline" onClick={() => setDismissing(true)}>
                      This is wrong — dismiss
                    </Button>
                  )}
                </div>
              )}

              {data.held_messages.length > 0 && (
                <p className="text-xs">
                  {data.held_messages.length} message
                  {data.held_messages.length === 1 ? " is" : "s are"} held for this check.
                </p>
              )}

              <div className="flex flex-wrap gap-2">
                {canRefresh(data) && (
                  <Button size="sm" variant="outline" disabled={refresh.isPending}
                          onClick={() => refresh.mutate()}>
                    <RefreshCw size={14} aria-hidden="true" />
                    Re-verify now
                  </Button>
                )}
                {canConfirm(data) && !confirming && (
                  <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
                    <UserCheck size={14} aria-hidden="true" />
                    Confirm still in role
                  </Button>
                )}
              </div>

              {(confirming || dismissing) && (
                <div className="space-y-2">
                  <Label htmlFor={`role-reason-${leadId}`}>
                    {confirming
                      ? "How do you know they still hold this role? (recorded in the audit log)"
                      : "Why is this job change wrong? (recorded in the audit log)"}
                  </Label>
                  <Input id={`role-reason-${leadId}`} value={reason}
                         onChange={(e) => setReason(e.target.value)}
                         placeholder="e.g. spoke to them on the phone this week" />
                  {confirming && canAdoptTitle(data) && (
                    <label className="flex items-center gap-2 text-xs">
                      <Checkbox checked={adopt} onCheckedChange={setAdopt} />
                      Use the newer title: {data.observed_title}
                    </label>
                  )}
                  {err && reason && <p className="text-xs text-destructive">{err}</p>}
                  <div className="flex gap-2">
                    <Button size="sm" disabled={!!err || confirm.isPending || dismiss.isPending}
                            onClick={() => (confirming ? confirm.mutate() : dismiss.mutate())}>
                      {confirming ? "Confirm — release held messages" : "Dismiss job change"}
                    </Button>
                    <Button size="sm" variant="outline"
                            onClick={() => { setConfirming(false); setDismissing(false); }}>
                      Cancel
                    </Button>
                  </div>
                </div>
              )}

              {data.attempts.length > 0 && (
                <ul className="space-y-0.5 text-xs text-muted-foreground">
                  {data.attempts.slice(0, 6).map((a) => <li key={a.id}>{attemptText(a)}</li>)}
                </ul>
              )}
            </>
          )}
        </AsyncState>
      </CardContent>
    </Card>
  );
}
