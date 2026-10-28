"use client";

/** F04 Reply Copilot — drafted answers for one prospect, inside the inbox
 *  thread view.
 *
 *  Email: Approve & send (or Send edited) hands the words to the ordinary send
 *  path — suppression, unsubscribe footer, send window, caps and the review
 *  queue all still apply, so "approved" can mean "queued", not "sent".
 *  LinkedIn: copy-only; Vectorae never sends it.
 *
 *  Behind `reply_copilot_enabled` (default off): the API answers 404 and this
 *  renders nothing. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Send, Sparkles, Trash2 } from "lucide-react";

import {
  approveReplyDraft,
  copyReplyDraft,
  discardReplyDraft,
  getReplyCopilotSettings,
  setReplyCopilotAnswerEarlier,
  listReplyDrafts,
  setReplyCopilotAutoSend,
} from "@/lib/api/replyCopilot";
import {
  approveLabel,
  answersEarlierReplies,
  autoSendBlockedReason,
  deliveryLabel,
  intentLabel,
  intentTone,
  isCopyOnly,
  isDisabledError,
  isEdited,
  type CopilotSettings,
  type ReplyDraft,
} from "@/lib/replyCopilot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";

export function ReplyCopilotPanel({ leadId }: { leadId: string }) {
  const drafts = useQuery({
    queryKey: ["reply-drafts", leadId],
    queryFn: () => listReplyDrafts({ status: "all", leadId, limit: 10 }),
    retry: (count, err) => !isDisabledError(err) && count < 2,
  });
  const settings = useQuery({
    queryKey: ["reply-copilot-settings"],
    queryFn: getReplyCopilotSettings,
    retry: (count, err) => !isDisabledError(err) && count < 2,
    enabled: !isDisabledError(drafts.error),
  });

  if (isDisabledError(drafts.error) || !drafts.data) return null;
  const items = drafts.data.items;
  const pending = items.filter((d) => d.status === "pending");
  const recent = items.filter((d) => d.status !== "pending").slice(0, 3);
  if (!items.length) return null;

  return (
    <Card aria-label="Reply Copilot">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Sparkles size={16} aria-hidden="true" /> Reply Copilot
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Drafted from this campaign&apos;s objection research, your approved claims and
            your booking link, then claim-checked. Nothing is sent until you approve it.
          </p>
        </div>
        <div className="flex flex-col gap-2">
          <AutoSendToggle settings={settings.data} />
          <AnswerEarlierToggle settings={settings.data} />
        </div>
      </CardHeader>
      <CardContent className="space-y-4 p-gutter pt-0">
        {pending.map((draft) => <PendingDraft key={draft.id} draft={draft} leadId={leadId} />)}
        {recent.length > 0 && (
          <ul className="space-y-1 text-xs text-muted-foreground">
            {recent.map((draft) => (
              <li key={draft.id}>
                {intentLabel(draft.intent)} · {draft.channel} · {deliveryLabel(draft)}
                {draft.edited ? " · edited" : ""}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function PendingDraft({ draft, leadId }: { draft: ReplyDraft; leadId: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [text, setText] = useState(draft.draft_body);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["reply-drafts", leadId] });
    qc.invalidateQueries({ queryKey: ["inbox"] });
    qc.invalidateQueries({ queryKey: ["inbox-thread", leadId] });
  };
  const edited = isEdited(draft, text);

  const decide = useMutation({
    mutationFn: async () => {
      if (isCopyOnly(draft)) {
        // Checked FIRST (forbidden phrases, claims, suppression); only words
        // the server accepted reach the clipboard.
        const result = await copyReplyDraft(draft.id, edited ? { body: text } : {});
        await navigator.clipboard?.writeText(result.final_body ?? text);
        return result;
      }
      return approveReplyDraft(draft.id, edited ? { body: text } : {});
    },
    onSuccess: (result) => {
      refresh();
      toast(isCopyOnly(draft) ? "Copied — paste it into LinkedIn"
        : deliveryLabel(result), "success");
    },
    onError: (e) => toast((e as Error).message, "error"),
  });
  const discard = useMutation({
    mutationFn: () => discardReplyDraft(draft.id),
    onSuccess: () => { refresh(); toast("Draft discarded", "success"); },
    onError: (e) => toast((e as Error).message, "error"),
  });

  return (
    <div className="space-y-2 rounded border border-border p-3">
      <div className="flex flex-wrap items-center gap-1 text-xs">
        <Badge tone={intentTone(draft.intent)}>{intentLabel(draft.intent)}</Badge>
        <Badge tone="default">{draft.channel === "email" ? "Email" : "LinkedIn · copy only"}</Badge>
        {draft.meeting_time_request && <Badge tone="accent">Asked for a time</Badge>}
        {draft.draft_subject && (
          <span className="truncate text-muted-foreground">{draft.draft_subject}</span>
        )}
      </div>
      <label className="sr-only" htmlFor={`draft-${draft.id}`}>Draft answer</label>
      <Textarea id={`draft-${draft.id}`} value={text} rows={6}
                onChange={(e) => setText(e.target.value)} />
      {draft.claim_flags.length > 0 && (
        <div className="text-xs text-muted-foreground">
          <p className="font-medium">Removed by claim verification:</p>
          <ul className="list-disc pl-4">
            {draft.claim_flags.map((flag, i) => <li key={i}>{flag.claim}</li>)}
          </ul>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={decide.isPending || discard.isPending || !text.trim()}
                onClick={() => decide.mutate()}>
          {isCopyOnly(draft) ? <Copy size={14} aria-hidden="true" />
            : <Send size={14} aria-hidden="true" />}
          {approveLabel(draft, text)}
        </Button>
        <Button size="sm" variant="ghost" disabled={decide.isPending || discard.isPending}
                onClick={() => discard.mutate()}>
          <Trash2 size={14} aria-hidden="true" /> Discard
        </Button>
        {edited && (
          <Button size="sm" variant="ghost" onClick={() => setText(draft.draft_body)}>
            Undo edits
          </Button>
        )}
      </div>
    </div>
  );
}

/** Founder decision 2026-10-11: the account decides whether an answer to a
 *  prospect's LATER reply may still go out when their EARLIER reply is what
 *  stopped the sequence (on, the default) or only answers to the reply that
 *  stopped it (off, strict). A newer reply always cancels a queued answer. */
function AnswerEarlierToggle({ settings }: { settings: CopilotSettings | undefined }) {
  const toast = useToast();
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: (on: boolean) => setReplyCopilotAnswerEarlier(on),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reply-copilot-settings"] }),
    onError: (e) => toast((e as Error).message, "error"),
  });
  if (!settings) return null;
  return (
    <label className="flex max-w-xs items-start gap-2 text-xs text-muted-foreground">
      <input type="checkbox" className="mt-0.5"
             checked={answersEarlierReplies(settings)}
             disabled={toggle.isPending}
             onChange={(e) => toggle.mutate(e.target.checked)} />
      <span>
        When a prospect wrote twice, still send an approved answer to the later reply
        (on by default). Off: only answers to the reply that stopped the sequence are sent.
      </span>
    </label>
  );
}

function AutoSendToggle({ settings }: { settings: CopilotSettings | undefined }) {
  const toast = useToast();
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: (on: boolean) => setReplyCopilotAutoSend(on),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reply-copilot-settings"] }),
    onError: (e) => toast((e as Error).message, "error"),
  });
  if (!settings) return null;
  const blocked = autoSendBlockedReason(settings);
  return (
    <label className="flex max-w-xs items-start gap-2 text-xs text-muted-foreground"
           title={blocked ?? undefined}>
      <input type="checkbox" className="mt-0.5"
             checked={settings.auto_send_meeting_time}
             disabled={Boolean(blocked) || toggle.isPending}
             onChange={(e) => toggle.mutate(e.target.checked)} />
      <span>
        Auto-send answers to &quot;send me a time&quot; email replies (off by default).
        {blocked ? ` ${blocked}` : ""}
      </span>
    </label>
  );
}
