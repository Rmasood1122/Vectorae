"use client";

/** Daily send approval — decide today's automatic sending, per channel.
 *
 *  Founder decision 2026-10-11 (docs/features/daily-send-approval.md): nothing
 *  Vectorae sends AUTOMATICALLY goes out on a day nobody approved it for that
 *  channel. LinkedIn is a separate, explicit choice that is never pre-ticked,
 *  with its own acknowledgement of LinkedIn's restriction risk. Messages a
 *  person approved one by one (send-review queue, Reply Copilot) are not
 *  affected. Members without owner/manager see the state read-only. */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import {
  approveSendApproval,
  declineSendApproval,
  getSendApproval,
} from "@/lib/api/sendApproval";
import {
  buildApprovePayload,
  canApprove,
  channelLabel,
  gatedChannels,
  initialSelection,
  statusLabel,
  statusTone,
  timezoneNote,
  type ApprovalChannel,
  type SendApprovalState,
} from "@/lib/sendApproval";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { AsyncState } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

export default function SendApprovalPage() {
  const query = useQuery({ queryKey: ["send-approval"], queryFn: getSendApproval });
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="text-xl font-semibold">Today&apos;s automatic sending</h1>
      <AsyncState isLoading={query.isLoading} error={query.error}>
        {query.data && <Decision state={query.data} />}
      </AsyncState>
    </div>
  );
}

function Decision({ state }: { state: SendApprovalState }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [selected, setSelected] = useState<Set<ApprovalChannel>>(() => initialSelection(state));
  const [linkedinAck, setLinkedinAck] = useState(false);

  // A fresh state from the server (new day, someone else decided) resets the
  // form. LinkedIn is never carried over as ticked.
  useEffect(() => {
    setSelected(initialSelection(state));
    setLinkedinAck(false);
  }, [state.date, state.status, state.decided_at]); // eslint-disable-line react-hooks/exhaustive-deps

  const done = (next: SendApprovalState, message: string) => {
    qc.setQueryData(["send-approval"], next);
    toast(message, "success");
  };
  const approve = useMutation({
    mutationFn: () => approveSendApproval(buildApprovePayload(state, selected, linkedinAck)),
    onSuccess: (next) => done(next, `Approved for today. ${(next.released ?? 0) + (next.released_network ?? 0)} waiting message(s) released.`),
    onError: (err) => toast(errorText(err, "Could not save the approval."), "error"),
  });
  const decline = useMutation({
    mutationFn: () => declineSendApproval(),
    onSuccess: (next) => done(next, "Declined. Nothing automatic will be sent today."),
    onError: (err) => toast(errorText(err, "Could not save the decline."), "error"),
  });

  const channels = gatedChannels(state);
  const check = canApprove(selected, linkedinAck);
  const busy = approve.isPending || decline.isPending;
  const toggle = (channel: ApprovalChannel, on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(channel);
      else next.delete(channel);
      return next;
    });
    if (channel === "linkedin" && !on) setLinkedinAck(false);
  };

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            {state.date}
            <Badge tone={statusTone(state)}>{statusLabel(state)}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p>
            Vectorae does not send anything automatically until you approve it for the day.
            An approval is valid until the end of today; tomorrow Vectorae asks again.
          </p>
          <p className="text-muted-foreground">{timezoneNote(state)}</p>
          <p className="text-muted-foreground">
            Messages a person approved one by one (the send-review queue, Reply Copilot
            answers you approved) are not affected.
          </p>
          {state.status === "approved" && (
            <p>Approved today: {state.approved_channels.map((c) => channelLabel(state, c)).join(", ")}.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Queued for today</CardTitle>
        </CardHeader>
        <CardContent>
          <fieldset disabled={!state.can_decide || busy} className="space-y-3">
            <legend className="sr-only">Channels to approve for automatic sending today</legend>
            {channels.map((channel) => (
              <label key={channel} className="flex items-center justify-between gap-3 text-sm">
                <span className="flex items-center gap-2">
                  <Checkbox
                    checked={selected.has(channel)}
                    onCheckedChange={(on) => toggle(channel, on)}
                    aria-describedby={channel === "linkedin" ? "linkedin-warning" : undefined}
                  />
                  {channelLabel(state, channel)}
                  {state.approved_channels.includes(channel) && (
                    <Badge tone="success">approved</Badge>
                  )}
                </span>
                <span className="tabular-nums text-muted-foreground">
                  {state.queued[channel] ?? 0} queued
                </span>
              </label>
            ))}
          </fieldset>
        </CardContent>
      </Card>

      {channels.includes("linkedin") && (
        <Card className="border-warning/60">
          <CardContent className="space-y-3 pt-4 text-sm">
            <p id="linkedin-warning" className="flex gap-2">
              <AlertTriangle size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-warning" />
              <span>{state.linkedin_warning.text}</span>
            </p>
            {selected.has("linkedin") && (
              <label className="flex items-start gap-2">
                <Checkbox
                  checked={linkedinAck}
                  onCheckedChange={setLinkedinAck}
                  disabled={!state.can_decide || busy}
                />
                <span>
                  I understand LinkedIn may restrict or suspend my LinkedIn account, and I want
                  to proceed with automatic LinkedIn sending today.
                </span>
              </label>
            )}
          </CardContent>
        </Card>
      )}

      {state.can_decide ? (
        <div className="space-y-2">
          {!check.ok && <p className="text-sm text-muted-foreground">{check.reason}</p>}
          <div className="flex flex-wrap gap-2">
            <Button disabled={!check.ok || busy} onClick={() => approve.mutate()}>
              Approve selected channels for today
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => decline.mutate()}>
              Decline for today
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          Only a workspace owner or manager can approve or decline automatic sending.
        </p>
      )}
    </>
  );
}
