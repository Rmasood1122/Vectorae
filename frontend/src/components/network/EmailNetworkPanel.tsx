"use client";

/** "Email contacts" tab of the Network page: upload your own contacts or a
 *  mailbox export (headers only), or scan your connected Gmail; see who fits
 *  what you sell; write a first email, save it, and use it as their first
 *  email in one of your campaigns -- or add them to a campaign. Nothing on
 *  this tab sends anything: campaign emails go out through the normal send
 *  checks (unsubscribe link, daily approval, suppression, plan). */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/client";
import {
  addContactsToCampaign, attachEmailMessage, deleteEmailContact, detachEmailMessage,
  draftEmailMessage,
  eraseEmailNetwork, getEmailNetworkStatus, getEmailRecommendations, listEmailCampaigns,
  listEmailMessages, saveEmailMessage, startGmailScan, uploadEmailNetworkFile,
  type Campaign, type EmailMatch, type EmailNetworkMessage,
} from "@/lib/api/emailNetwork";
import {
  BODY_MAX, checkUploadFile, openerDead, importErrorMessage, importSummary, isAttachable, isEditable,
  lastSeenLabel, messageErrorMessage, messageStatusLabel, parseOwnAddresses,
  relationshipBadge, scanInProgress, scanLine, scoreLabel, validateEmail,
} from "@/lib/emailNetwork";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/native-select";

/** What DELETE /email-network does to first emails already attached to a
 *  campaign: the campaign's first email to that person is HELD for review
 *  (never silently written by AI). Said before the user confirms. */
export const ERASE_CONFIRM =
  "Delete all your email contacts, scores and saved first emails? People already added to a " +
  "campaign stay there as leads. If a saved first email was attached to a campaign, that " +
  "person's first campaign email will wait in the review queue for you instead of being sent.";

/** Honest limits of the CASL check (spec §10). */
export const CASL_LIMITS =
  "Only .ca addresses are checked (Canadians on other domains are not detected), and a " +
  "two-way email exchange is only a stand-in for a business relationship.";

function CampaignPicker({ campaigns, value, onChange, label }: {
  campaigns: Campaign[]; value: string; onChange: (v: string) => void; label: string;
}) {
  return (
    <NativeSelect aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Choose a campaign…</option>
      {campaigns.map((c) => (
        <option key={c.strategy_id} value={c.strategy_id}>
          {c.product_name}{c.has_email_first_sequence ? "" : " (no email-first sequence yet)"}
        </option>
      ))}
    </NativeSelect>
  );
}

function FirstEmailBox({ match, productId, message, campaigns, onChanged }: {
  match: EmailMatch; productId: string; message: EmailNetworkMessage | undefined;
  campaigns: Campaign[]; onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState(message?.subject ?? "");
  const [body, setBody] = useState(message?.body ?? "");
  const [campaign, setCampaign] = useState("");
  const [err, setErr] = useState<string | null>(null);

  // Reset the editor only when the SAVED words change, not on every refetch
  // of the same row (that would wipe what the user is typing).
  useEffect(() => {
    if (message) { setSubject(message.subject); setBody(message.body); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [message?.id, message?.subject, message?.body]);

  const run = useMutation({
    mutationFn: async (fn: () => Promise<unknown>) => { await fn(); },
    onSuccess: () => { setErr(null); onChanged(); },
    onError: (e) => setErr(e instanceof Error && !(e instanceof ApiError) ? e.message : messageErrorMessage(e)),
  });
  const save = async () => {
    const problem = validateEmail(subject, body);
    if (problem) throw new Error(problem);
    if (!message) throw new Error("Draft first.");
    await saveEmailMessage(message.id, subject, body);
  };

  if (message && (message.status === "attached" || message.status === "used")) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{messageStatusLabel(message)}</span>
        {(message.status === "attached" || openerDead(message)) && (
          <Button variant="outline" disabled={run.isPending}
                  onClick={() => run.mutate(async () => { await detachEmailMessage(message.id); })}>
            Detach to edit
          </Button>
        )}
        {err && <span role="alert" className="text-red-600">{err}</span>}
      </div>
    );
  }
  if (!open) {
    return (
      <Button variant="outline" onClick={() => setOpen(true)}>
        {message ? `Edit first email (${messageStatusLabel(message)})` : "Write a first email"}
      </Button>
    );
  }
  return (
    <div className="space-y-2 rounded border p-3">
      {!message && (
        <Button disabled={run.isPending}
                onClick={() => run.mutate(async () => { await draftEmailMessage(match.contact_id, productId); })}>
          Draft it in my voice
        </Button>
      )}
      {message && (
        <>
          <input className="w-full rounded border bg-background p-2 text-sm" aria-label={`Subject for ${match.email}`}
                 value={subject} onChange={(e) => setSubject(e.target.value)} />
          <textarea className="w-full rounded border bg-background p-2 text-sm" rows={6}
                    aria-label={`Email to ${match.email}`} value={body} onChange={(e) => setBody(e.target.value)} />
          <p className="text-xs text-muted-foreground">
            {body.length}/{BODY_MAX}. Say only what is true about how you know them — the footer with the
            unsubscribe link and your address is added when it is sent.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={run.isPending}
                    onClick={() => run.mutate(async () => { await draftEmailMessage(match.contact_id, productId); })}>
              Redraft
            </Button>
            <Button disabled={run.isPending || !isEditable(message)} onClick={() => run.mutate(save)}>Save</Button>
          </div>
          {isAttachable(message) && (
            <div className="space-y-2 border-t pt-2">
              <p className="text-xs text-muted-foreground">
                Use this saved email as their first email in a campaign. Nothing is sent now: they are added
                to the campaign, their address is verified, and the email goes out when you launch the
                campaign — after your daily send approval and every send check.
              </p>
              {match.casl_block ? (
                <p className="text-xs text-amber-600">{match.casl_block} {CASL_LIMITS}</p>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <CampaignPicker campaigns={campaigns} value={campaign} onChange={setCampaign}
                                  label={`Campaign for ${match.email}`} />
                  <Button disabled={run.isPending || !campaign}
                          onClick={() => run.mutate(async () => { await attachEmailMessage(message.id, campaign); })}>
                    Use as their first email
                  </Button>
                </div>
              )}
            </div>
          )}
        </>
      )}
      {message?.error && <p className="text-xs text-red-600">{message.error}</p>}
      {err && <p role="alert" className="text-xs text-red-600">{err}</p>}
    </div>
  );
}

export function EmailNetworkPanel() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [attested, setAttested] = useState(false);
  const [own, setOwn] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [campaign, setCampaign] = useState("");

  const status = useQuery({
    queryKey: ["email-network-status"], queryFn: getEmailNetworkStatus,
    refetchInterval: (q) => (scanInProgress(q.state.data?.gmail.scan ?? null) ? 5000 : false),
  });
  const s = status.data;
  const usable = !!s && s.eligible && s.feature_enabled;
  const hasContacts = (s?.contact_count ?? 0) > 0;
  const recs = useQuery({ queryKey: ["email-network-recs"], queryFn: getEmailRecommendations,
                          enabled: !!s?.eligible && hasContacts });
  const msgs = useQuery({ queryKey: ["email-network-msgs"], queryFn: listEmailMessages,
                          enabled: usable && hasContacts });
  const camps = useQuery({ queryKey: ["email-network-campaigns"], queryFn: listEmailCampaigns,
                           enabled: usable && hasContacts });
  const campaigns = camps.data?.campaigns ?? [];
  const byContact = new Map((msgs.data?.messages ?? []).map((m) => [m.contact_id, m]));

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["email-network-status"] });
    qc.invalidateQueries({ queryKey: ["email-network-recs"] });
    qc.invalidateQueries({ queryKey: ["email-network-msgs"] });
  };
  const fail = (e: unknown) => { setMessage(null); setError(importErrorMessage(e)); };

  const upload = useMutation({
    mutationFn: (file: File) => uploadEmailNetworkFile(file, s?.attestation.version ?? "", own),
    onSuccess: (r) => { setError(null); setMessage(importSummary(r) + (r.cap_note ? ` ${r.cap_note}` : "")); refresh(); },
    onError: fail,
  });
  const scan = useMutation({
    mutationFn: () => startGmailScan(s?.attestation.version ?? "", parseOwnAddresses(own)),
    onSuccess: () => { setError(null); setMessage("Gmail scan started. It reads message headers only and takes a few minutes."); refresh(); },
    onError: fail,
  });
  const add = useMutation({
    mutationFn: () => addContactsToCampaign(campaign, Array.from(selected)),
    onSuccess: (r) => {
      setError(null); setSelected(new Set());
      setMessage(`${r.added} added to the campaign; their addresses are being verified.` +
        (r.skipped.length ? ` ${r.skipped.length} skipped: ${r.skipped.map((x) => x.reason).join(" ")}` : ""));
      refresh();
    },
    onError: (e) => { setMessage(null); setError(messageErrorMessage(e)); },
  });
  const erase = useMutation({
    mutationFn: eraseEmailNetwork,
    onSuccess: () => { setError(null); setMessage("Your email contacts were deleted."); refresh(); },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Delete failed."),
  });

  function onFile(file: File | undefined) {
    if (!file || !s) return;
    const problem = checkUploadFile(file, s.limits);
    if (problem) { setMessage(null); setError(problem); return; }
    upload.mutate(file);
    if (fileRef.current) fileRef.current.value = "";
  }
  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  if (status.isLoading) return <p className="text-sm">Loading…</p>;
  if (status.isError || !s) return <p className="text-sm">Could not load your email contacts.</p>;
  const scanText = scanLine(s.gmail.scan);

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        Private to your account. Only who you email, how often and how recently are kept. A
        mailbox file is read through on our server to find the From / To / Cc / Date lines and
        everything else is discarded as it is read — subjects, message text and attachments are
        never stored. Personal addresses (Gmail, Yahoo, …), role inboxes, newsletters, your
        colleagues and anyone who unsubscribed are left out.
      </p>

      {!s.eligible && (
        <Card>
          <CardContent className="space-y-2 pt-6 text-sm">
            <p>Email contacts are included with the Growth plan and higher. You are on <strong>{s.plan}</strong>.</p>
            <a className="underline" href="/settings">Upgrade in Settings</a>
          </CardContent>
        </Card>
      )}
      {s.eligible && !s.feature_enabled && (
        <Card><CardContent className="pt-6 text-sm">Email contacts are not switched on for this workspace yet.</CardContent></Card>
      )}

      {usable && (
        <Card>
          <CardHeader><CardTitle>Bring in your email contacts</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm">
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)}
                     aria-label="These are my own business contacts" />
              <span>{s.attestation.text}</span>
            </label>
            <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
              <li>Google Contacts: contacts.google.com → Export → Google CSV.</li>
              <li>Outlook: People → Manage → Export contacts (CSV).</li>
              <li>Apple / any phone: a vCard (.vcf) file.</li>
              <li>Whole mailbox: a Google Takeout .mbox (up to {Math.floor(s.limits.mbox_max_bytes / 1048576)} MB,
                {" "}{s.limits.max_messages.toLocaleString("en-US")} messages) — only the From / To / Cc / Date lines are kept.</li>
            </ul>
            <label className="block">
              <span className="text-muted-foreground">Your other addresses (so your colleagues are left out), optional:</span>
              <input className="mt-1 w-full rounded border bg-background p-2" aria-label="Your other email addresses"
                     placeholder="you@yourcompany.com" value={own} onChange={(e) => setOwn(e.target.value)} />
            </label>
            <input ref={fileRef} type="file" disabled={!attested || upload.isPending}
                   accept=".csv,.vcf,.vcard,.mbox,text/csv,text/vcard,application/mbox"
                   aria-label="Contacts or mailbox file" onChange={(e) => onFile(e.target.files?.[0])} />
            {upload.isPending && <p>Importing…</p>}
            <div className="space-y-2 border-t pt-3">
              {s.gmail.locked ? (
                <p className="text-muted-foreground">Scanning Gmail is switched off. Upload a contacts file or a Google Takeout .mbox instead.</p>
              ) : s.gmail.connected ? (
                s.gmail.reconnect_required ? (
                  <p className="text-amber-600">Your Gmail connection cannot read message headers. <a className="underline" href="/settings">Reconnect Gmail</a>.</p>
                ) : (
                  <Button variant="outline" disabled={!attested || scan.isPending || scanInProgress(s.gmail.scan)}
                          onClick={() => scan.mutate()}>
                    Scan my connected Gmail ({s.gmail.address})
                  </Button>
                )
              ) : (
                <p className="text-muted-foreground"><a className="underline" href="/settings">Connect Gmail</a> to scan it instead of uploading a file.</p>
              )}
              {scanText && <p className="text-muted-foreground" role="status">{scanText}</p>}
            </div>
          </CardContent>
        </Card>
      )}

      {s.last_import && (
        <p className="text-sm text-muted-foreground">Last upload: {importSummary(s.last_import)}</p>
      )}

      {/* Never plan- or flag-gated (DELETE /email-network): a downgraded
          account can always delete what it uploaded. */}
      {hasContacts && (
        <Card>
          <CardContent className="space-y-3 pt-6 text-sm">
            <p className="text-muted-foreground">{s.contact_count} contact{s.contact_count === 1 ? "" : "s"} stored.</p>
            <Button variant="outline" disabled={erase.isPending}
                    onClick={() => { if (window.confirm(ERASE_CONFIRM)) erase.mutate(); }}>
              Delete my email contacts
            </Button>
          </CardContent>
        </Card>
      )}

      {message && <p role="status" className="text-sm">{message}</p>}
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}

      {usable && selected.size > 0 && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-2 pt-6 text-sm">
            <span>{selected.size} selected.</span>
            <CampaignPicker campaigns={campaigns} value={campaign} onChange={setCampaign} label="Campaign to add to" />
            <Button disabled={add.isPending || !campaign} onClick={() => add.mutate()}>Add to campaign</Button>
          </CardContent>
        </Card>
      )}

      {s.eligible && recs.data?.products.map((p) => (
        <Card key={p.product_id}>
          <CardHeader><CardTitle>{p.product_name} — {p.total_matches} warm match{p.total_matches === 1 ? "" : "es"}</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {p.segments.map((seg) => <Badge key={seg.segment}>{seg.segment} · {seg.count}</Badge>)}
            </div>
            <ul className="divide-y text-sm">
              {p.matches.map((mt) => (
                <li key={mt.contact_id} className="py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {usable && !mt.casl_block && (
                      <input type="checkbox" checked={selected.has(mt.contact_id)} onChange={() => toggle(mt.contact_id)}
                             aria-label={`Select ${mt.email}`} />
                    )}
                    <span className="font-medium">{mt.full_name ?? mt.email}</span>
                    <span className="text-muted-foreground">{mt.email}</span>
                    <Badge>{scoreLabel(mt.score)} · {mt.score}</Badge>
                    <Badge>{relationshipBadge(mt)}</Badge>
                  </div>
                  <div className="text-muted-foreground">
                    {[mt.title, mt.company && (mt.company_is_guess ? `${mt.company} (from the domain)` : mt.company),
                      lastSeenLabel(mt.last_seen) && `last email ${lastSeenLabel(mt.last_seen)}`].filter(Boolean).join(" · ")}
                  </div>
                  {mt.reasons?.length ? <div className="text-xs text-muted-foreground">{mt.reasons.join("; ")}</div> : null}
                  {mt.casl_block && <div className="text-xs text-amber-600">{mt.casl_block} {CASL_LIMITS}</div>}
                  <Button variant="outline" className="mt-1"
                          onClick={() => {
                            if (window.confirm(`Delete ${mt.email} from your email contacts?`))
                              void deleteEmailContact(mt.contact_id).then(refresh, (e) => setError(messageErrorMessage(e)));
                          }}>
                    Delete this contact
                  </Button>
                  {usable && (
                    <div className="mt-2">
                      <FirstEmailBox match={mt} productId={p.product_id} message={byContact.get(mt.contact_id)}
                                     campaigns={campaigns} onChanged={refresh} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export default EmailNetworkPanel;
