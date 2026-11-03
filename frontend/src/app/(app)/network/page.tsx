"use client";

/** Your network: two tabs. LinkedIn -- upload your own export, see who is
 *  relevant to what you sell. Email contacts -- the same for the people you
 *  already email (contacts files, a mailbox export read for headers only, or
 *  a scan of your connected Gmail). Private to this account. Growth plan and
 *  up. The LinkedIn tab opens by default. */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/client";
import {
  draftMessage, eraseNetwork, getNetworkStatus, getRecommendations,
  importConnections, listMessages, saveMessage,
  type NetworkMatch, type NetworkMessage,
} from "@/lib/api/network";
import {
  MESSAGE_MAX, checkExportFile, importErrorMessage, importSummary,
  isReadyToSend, isSavable, messageErrorMessage, safeProfileUrl, scoreLabel, statusLabel,
  validateMessage,
} from "@/lib/linkedinNetwork";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmailNetworkPanel } from "@/components/network/EmailNetworkPanel";
import { LinkedInTasksPanel } from "@/components/linkedin/LinkedInTasksPanel";
import { taskFromNetworkMessage } from "@/lib/api/linkedinTasks";

/** One connection's message: write (or have it drafted), edit, save, then
 *  add it to YOUR LinkedIn tasks. Vectorae never sends on LinkedIn. */
function MessageBox({ match, productId, message, onChanged }: {
  match: NetworkMatch; productId: string; message: NetworkMessage | undefined;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(message?.text ?? "");
  const [err, setErr] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);

  useEffect(() => { if (message) setText(message.text); }, [message?.id, message?.text]);

  const run = useMutation({
    mutationFn: async (fn: () => Promise<unknown>) => { await fn(); },
    onSuccess: () => { setErr(null); onChanged(); },
    onError: (e) => setErr(messageErrorMessage(e)),
  });
  const save = async () => {
    const problem = validateMessage(text);
    if (problem) throw new Error(problem);
    if (!message) throw new Error("Draft first.");
    await saveMessage(message.id, text);
  };

  if (message?.status === "sent" || message?.status === "queued") {
    return <p className="text-xs text-muted-foreground">{statusLabel(message)}</p>;
  }
  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" onClick={() => setOpen(true)}>
          {message ? `Edit message (${statusLabel(message)})` : "Write message"}
        </Button>
      </div>
    );
  }
  return (
    <div className="space-y-2 rounded border p-3">
      {!message && (
        <Button disabled={run.isPending}
                onClick={() => run.mutate(async () => { await draftMessage(match.connection_id, productId); })}>
          Draft it in my voice
        </Button>
      )}
      {message && (
        <>
          <textarea className="w-full rounded border bg-background p-2 text-sm" rows={5}
                    aria-label={`Message to ${match.full_name}`}
                    value={text} onChange={(e) => setText(e.target.value)} />
          <p className="text-xs text-muted-foreground">{text.length}/{MESSAGE_MAX}</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={run.isPending}
                    onClick={() => run.mutate(async () => { await draftMessage(match.connection_id, productId); })}>
              Redraft
            </Button>
            <Button disabled={run.isPending || !isSavable(message)} onClick={() => run.mutate(save)}>
              Save
            </Button>
          </div>
          {isReadyToSend(message) && (
            <div className="space-y-2 border-t pt-2">
              <p className="text-xs text-muted-foreground">You send it yourself on LinkedIn, as a task:</p>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" disabled={run.isPending}
                        onClick={() => run.mutate(async () => {
                          await taskFromNetworkMessage(message.id);
                          setQueued(true);
                        })}>
                  Add to my LinkedIn tasks
                </Button>
                {queued && (
                  <span className="self-center text-xs text-muted-foreground">
                    Added. Approve and open it in “Your LinkedIn tasks” above.
                  </span>
                )}
              </div>
            </div>
          )}
        </>
      )}
      {message?.error && <p className="text-xs text-red-600">{message.error}</p>}
      {err && <p role="alert" className="text-xs text-red-600">{err}</p>}
    </div>
  );
}

export default function NetworkPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-4 p-4 md:p-6">
      <Tabs defaultValue="linkedin">
        <TabsList aria-label="Network source">
          <TabsTrigger value="linkedin">LinkedIn</TabsTrigger>
          <TabsTrigger value="email">Email contacts</TabsTrigger>
        </TabsList>
        <TabsContent value="linkedin"><LinkedInNetworkTab /></TabsContent>
        <TabsContent value="email">
          <header className="mb-4">
            <h1 className="text-xl font-semibold">Your email contacts</h1>
          </header>
          <EmailNetworkPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function LinkedInNetworkTab() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const status = useQuery({ queryKey: ["network-status"], queryFn: getNetworkStatus });
  const eligible = status.data?.eligible === true;
  const recs = useQuery({
    queryKey: ["network-recs"], queryFn: getRecommendations,
    enabled: eligible && (status.data?.connection_count ?? 0) > 0,
  });

  const msgs = useQuery({
    queryKey: ["network-msgs"], queryFn: listMessages,
    enabled: eligible && (status.data?.connection_count ?? 0) > 0,
  });
  const byConn = new Map((msgs.data?.messages ?? []).map((m) => [m.connection_id, m]));
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["network-status"] });
    qc.invalidateQueries({ queryKey: ["network-recs"] });
    qc.invalidateQueries({ queryKey: ["network-msgs"] });
  };

  const [basis, setBasis] = useState<string>("");
  const [attested, setAttested] = useState(false);
  const upload = useMutation({
    mutationFn: (csv: string) =>
      importConnections(csv, { version: status.data?.consent?.version ?? "", lawfulBasis: basis }),
    onSuccess: (r) => { setError(null); setMessage(importSummary(r)); refresh(); },
    onError: (e) => { setMessage(null); setError(importErrorMessage(e)); },
  });
  const erase = useMutation({
    mutationFn: eraseNetwork,
    onSuccess: () => { setError(null); setMessage("Your uploaded connections were deleted."); refresh(); },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Delete failed."),
  });

  async function onFile(file: File | undefined) {
    if (!file || !status.data) return;
    const problem = checkExportFile(file, status.data.max_bytes);
    if (problem) { setMessage(null); setError(problem); return; }
    upload.mutate(await file.text());
    if (fileRef.current) fileRef.current.value = "";
  }

  if (status.isLoading) return <p className="text-sm">Loading…</p>;
  if (status.isError || !status.data) return <p className="text-sm">Could not load your network.</p>;
  const s = status.data;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold">Your LinkedIn network</h1>
        <p className="text-sm text-muted-foreground">
          Private to your account. Nobody else can see these connections, and yours are never
          shown to anyone else.
        </p>
      </header>

      {eligible && <LinkedInTasksPanel />}

      {!eligible && (
        <Card>
          <CardContent className="space-y-2 pt-6 text-sm">
            <p>
              The LinkedIn network is included with the Growth plan and higher. You are on{" "}
              <strong>{s.plan}</strong>.
            </p>
            <a className="underline" href="/settings">Upgrade in Settings</a>
          </CardContent>
        </Card>
      )}

      {eligible && (
        <Card>
          <CardHeader><CardTitle>Upload your connections</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm">
            <ol className="list-decimal space-y-1 pl-5">
              <li>On LinkedIn: Settings &amp; Privacy → Data privacy → Get a copy of your data.</li>
              <li>Choose <strong>Connections</strong> and request the archive.</li>
              <li>Upload the <code>Connections.csv</code> file from it here.</li>
            </ol>
            <p className="text-muted-foreground">
              Only name, job title, company, profile link and connection date are stored. Email
              addresses in the file are ignored. LinkedIn has no feed for new connections, so
              upload a fresh export when your network has grown; scores are refreshed weekly.
            </p>
            <section aria-label="Before you upload" className="space-y-2 rounded border p-3">
              <p className="font-medium">Before you upload</p>
              {s.consent ? (
                <p className="whitespace-pre-wrap text-muted-foreground">{s.consent.text}</p>
              ) : (
                <p className="text-muted-foreground">The upload notice could not be loaded. Reload the page.</p>
              )}
              <label className="block">
                <span className="mr-2">Lawful basis you rely on:</span>
                <select value={basis} onChange={(e) => setBasis(e.target.value)}
                        aria-label="Lawful basis" className="rounded border px-2 py-1">
                  <option value="">Choose…</option>
                  {(s.consent?.lawful_bases ?? []).map((b) => (
                    <option key={b} value={b}>
                      {b === "legitimate_interests" ? "Legitimate interests" : "Consent"}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-start gap-2">
                <input type="checkbox" checked={attested}
                       onChange={(e) => setAttested(e.target.checked)} />
                <span>I have read this and I have that lawful basis for these people&apos;s data.</span>
              </label>
            </section>
            <input ref={fileRef} type="file" accept=".csv,text/csv"
                   aria-label="Connections.csv" disabled={!s.consent || !basis || !attested}
                   onChange={(e) => onFile(e.target.files?.[0])} />
            {upload.isPending && <p>Importing…</p>}
            {s.needs_new_export && (
              <p className="text-amber-600">
                Your export is more than a few weeks old. Upload a fresh one to see new connections.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Outside the plan gate on purpose: deleting your own uploaded data is
          never plan-gated (DELETE /linkedin-network), so a downgraded account
          must still be able to do it here. */}
      {(eligible || s.connection_count > 0) && (
        <Card>
          <CardContent className="space-y-3 pt-6 text-sm">
            <p className="text-muted-foreground">
              {s.connection_count} connection{s.connection_count === 1 ? "" : "s"} stored.
            </p>
            {s.connection_count > 0 && (
              <Button variant="outline" disabled={erase.isPending}
                      onClick={() => { if (window.confirm("Delete all uploaded connections?")) erase.mutate(); }}>
                Delete my uploaded connections
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      {message && <p role="status" className="text-sm">{message}</p>}
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}

      {eligible && recs.data?.products.map((p) => (
        <Card key={p.product_id}>
          <CardHeader>
            <CardTitle>{p.product_name} — {p.total_matches} relevant</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {p.segments.map((seg) => (
                <Badge key={seg.segment}>{seg.segment} · {seg.count}</Badge>
              ))}
            </div>
            <ul className="divide-y text-sm">
              {p.matches.map((m) => {
                const link = safeProfileUrl(m.profile_url);
                return (
                  <li key={m.connection_id} className="py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">
                        {link ? <a href={link} target="_blank" rel="noopener noreferrer">{m.full_name}</a> : m.full_name}
                      </span>
                      <Badge>{scoreLabel(m.score)}</Badge>
                    </div>
                    <div className="text-muted-foreground">
                      {[m.position, m.company].filter(Boolean).join(" · ")}
                    </div>
                    {m.reasons?.length ? (
                      <div className="text-xs text-muted-foreground">{m.reasons.join("; ")}</div>
                    ) : null}
                    <div className="mt-2">
                      <MessageBox match={m} productId={p.product_id} message={byConn.get(m.connection_id)}
                                  onChanged={refresh} />
                    </div>
                  </li>
                );
              })}
            </ul>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
