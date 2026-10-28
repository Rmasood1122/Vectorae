"use client";

/** AI call history: the campaign's Calls tab (every lead) and the lead page's
 *  Calls tab (one lead, with phone consent and "Call now").
 *
 *  Each call shows its outcome, duration, the recording and the voicemail
 *  that was left (both playable), the transcript, and Claude's analysis --
 *  objections and interest signals quoted from the prospect. */

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PhoneCall } from "lucide-react";

import {
  OUTCOME_LABEL,
  callNow,
  directionLabel,
  formatDuration,
  getCall,
  listInboundCalls,
  listLeadCalls,
  listStrategyCalls,
  outcomeTone,
  recordPhoneConsent,
  revokePhoneConsent,
  type CallDetail,
  type CallSummary,
} from "@/lib/api/calls";
import type { LeadOut } from "@/lib/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { AsyncState } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";

function CallRow({ call, detail, showLead }: {
  call: CallSummary;
  detail?: CallDetail;
  showLead?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const full = useQuery({
    queryKey: ["call", call.id],
    queryFn: () => getCall(call.id),
    enabled: open && !detail,
  });
  const d = detail ?? full.data;
  const a = call.analysis_json;
  return (
    <li className="space-y-2 rounded-lg border border-border bg-card p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={outcomeTone(call.outcome)}>
          {call.outcome ? OUTCOME_LABEL[call.outcome] : call.status.replace(/_/g, " ")}
        </Badge>
        <Badge tone="default">{directionLabel(call)}</Badge>
        {call.voice_clone_used && <Badge tone="accent">Your voice (AI)</Badge>}
        {showLead && call.lead_id && (
          <Link href={`/leads/detail?id=${call.lead_id}&tab=calls`} className="font-medium underline underline-offset-2">
            {call.lead_name ?? "Lead"}
          </Link>
        )}
        {showLead && !call.lead_id && (
          <span className="font-medium">{call.from_number ?? "Unknown caller"}</span>
        )}
        <span className="text-muted-foreground">
          {new Date(call.created_at).toLocaleString()} · {formatDuration(call.duration_seconds)} · {call.provider}
        </span>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setOpen((o) => !o)}>
          {open ? "Hide" : "Details"}
        </Button>
      </div>
      {a?.summary && <p>{a.summary}</p>}
      {call.error && <p className="text-destructive">{call.error}</p>}
      {open && (
        <div className="space-y-3">
          {call.recording_url && (
            <div>
              <p className="text-xs text-muted-foreground">Recording</p>
              <audio controls preload="none" src={call.recording_url} className="w-full" />
            </div>
          )}
          {call.voicemail_audio_url && (
            <div>
              <p className="text-xs text-muted-foreground">Voicemail prepared for this call</p>
              <audio controls preload="none" src={call.voicemail_audio_url} className="w-full" />
            </div>
          )}
          {a && (a.objections.length > 0 || a.interest_signals.length > 0) && (
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <p className="text-xs font-medium text-muted-foreground">Objections</p>
                <ul className="list-disc pl-5">{a.objections.map((o, i) => <li key={i}>{o}</li>)}</ul>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground">Interest signals</p>
                <ul className="list-disc pl-5">{a.interest_signals.map((o, i) => <li key={i}>{o}</li>)}</ul>
              </div>
            </div>
          )}
          {a?.next_step && <p><span className="font-medium">Next step:</span> {a.next_step}</p>}
          {d?.script_json && (
            <details>
              <summary className="cursor-pointer text-xs text-muted-foreground">Script</summary>
              <p className="mt-1 italic">&ldquo;{d.script_json.first_message}&rdquo;</p>
              <ul className="mt-1 list-disc pl-5">
                {(d.script_json.talking_points ?? []).map((p, i) => <li key={i}>{p}</li>)}
              </ul>
            </details>
          )}
          {(d?.script_json?.messages_taken ?? []).map((m, i) => (
            <p key={i}>
              <span className="font-medium">Message{m.caller_name ? ` from ${m.caller_name}` : ""}:</span>{" "}
              {m.message}{m.callback_number ? ` (call back ${m.callback_number})` : ""}
            </p>
          ))}
          {d?.transcript && (
            <details>
              <summary className="cursor-pointer text-xs text-muted-foreground">Transcript</summary>
              <pre className="mt-1 max-h-72 overflow-y-auto whitespace-pre-wrap text-xs">{d.transcript}</pre>
            </details>
          )}
        </div>
      )}
    </li>
  );
}

/** The campaign's Calls tab. */
export function StrategyCalls({ strategyId }: { strategyId: string }) {
  const query = useQuery({
    queryKey: ["strategy-calls", strategyId],
    queryFn: () => listStrategyCalls(strategyId),
    refetchInterval: 30_000,
  });
  return (
    <AsyncState isLoading={query.isLoading} error={query.error}
                empty={!query.data?.length}
                emptyLabel="No AI calls for this campaign yet. Add a phone step to a sequence, or use Call now on a lead.">
      <ul className="space-y-2">
        {(query.data ?? []).map((call) => <CallRow key={call.id} call={call} showLead />)}
      </ul>
    </AsyncState>
  );
}

/** Voice twin: inbound calls your AI assistant answered (Settings > Voice). */
export function InboundCalls() {
  const query = useQuery({ queryKey: ["inbound-calls"], queryFn: listInboundCalls, refetchInterval: 60_000 });
  return (
    <AsyncState isLoading={query.isLoading} error={query.error}
                empty={!query.data?.length}
                emptyLabel="No inbound calls answered by your AI assistant yet.">
      <ul className="space-y-2">
        {(query.data ?? []).map((call) => <CallRow key={call.id} call={call} showLead />)}
      </ul>
    </AsyncState>
  );
}

/** The lead page's Calls tab: consent, Call now, and this lead's calls. */
export function LeadCalls({ lead }: { lead: LeadOut & { phone_consent_at?: string | null } }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [source, setSource] = useState("");
  const calls = useQuery({ queryKey: ["lead-calls", lead.id], queryFn: () => listLeadCalls(lead.id) });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["lead-calls", lead.id] });
    qc.invalidateQueries({ queryKey: ["lead", lead.id] });
  };
  const onError = (e: unknown) => toast((e as Error).message, "error");
  const consent = useMutation({
    mutationFn: () => recordPhoneConsent(lead.id, source.trim()),
    onSuccess: () => { setSource(""); refresh(); toast("Phone consent recorded", "success"); },
    onError,
  });
  const revoke = useMutation({ mutationFn: () => revokePhoneConsent(lead.id), onSuccess: refresh, onError });
  const call = useMutation({
    mutationFn: () => callNow(lead.id),
    onSuccess: () => { refresh(); toast("Calling — the result appears here when the call ends", "info"); },
    onError,
  });
  const hasConsent = !!lead.phone_consent_at;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2"><PhoneCall size={16} aria-hidden="true" /> AI calls</CardTitle>
          <Button size="sm" disabled={!lead.phone || !hasConsent || call.isPending} onClick={() => call.mutate()}>
            {call.isPending ? "Placing call…" : "Call now"}
          </Button>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p className="text-muted-foreground">
            An AI voice call to {lead.phone ?? "(no phone number)"}. Calls open by saying they are an AI
            assistant calling for you. AI-voice calls to US numbers need the person&apos;s prior consent,
            so Vectorae only calls leads with consent recorded.
          </p>
          {hasConsent ? (
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="success">Consent recorded</Badge>
              <span className="text-xs text-muted-foreground">
                {new Date(lead.phone_consent_at as string).toLocaleDateString()}
              </span>
              <Button size="sm" variant="ghost" onClick={() => revoke.mutate()}>Revoke</Button>
            </div>
          ) : (
            <div className="flex gap-2">
              <Input aria-label="How consent was obtained" placeholder="How was consent obtained? e.g. signed form, 2026-09-01"
                     value={source} onChange={(e) => setSource(e.target.value)} />
              <Button size="sm" variant="outline" disabled={source.trim().length < 3 || consent.isPending}
                      onClick={() => consent.mutate()}>Record consent</Button>
            </div>
          )}
        </CardContent>
      </Card>
      <AsyncState isLoading={calls.isLoading} error={calls.error}
                  empty={!calls.data?.length} emptyLabel="No calls with this lead yet.">
        <ul className="space-y-2">
          {(calls.data ?? []).map((c) => <CallRow key={c.id} call={c} detail={c} />)}
        </ul>
      </AsyncState>
    </div>
  );
}
