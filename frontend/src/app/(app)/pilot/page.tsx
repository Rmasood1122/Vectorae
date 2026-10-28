"use client";

/** F09 — 30-Day Pilot Mode.
 *
 *  No pilot   → the setup form: the buyer writes their own targets (nothing is
 *               pre-filled), optionally narrows the campaigns, and starts.
 *  Active     → day N of 30, each criterion against the numbers so far, the
 *               scorecards sent, "End pilot now".
 *  Ended      → the verdict page: criterion by criterion with the raw F08
 *               numbers, plan at start and end, and the end-of-pilot downloads
 *               (verdict PDF/CSV + the full account export).
 *
 *  Criteria are locked once the pilot starts — there is no edit control.
 *  Behind `pilot_mode_enabled` (default off): a 409 renders one quiet line. */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Info } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import {
  downloadPilotExport,
  endPilot,
  getCurrentPilot,
  getPilotTemplate,
  listPilots,
  setScorecardEmails,
  startPilot,
} from "@/lib/api/pilots";
import { exportMyData } from "@/lib/api/profile";
import { getRevenue } from "@/lib/api/revenue";
import { getCurrentWorkspace } from "@/lib/api/workspaces";
import {
  buildStartBody,
  dayLabel,
  endReasonLabel,
  formatActual,
  formatTarget,
  initialForm,
  isDisabledError,
  pilotDates,
  pilotExportFilename,
  statusLabel,
  statusTone,
  verdictHeadline,
  type CriteriaForm,
  type CriterionResult,
  type Pilot,
  type PilotExportFormat,
  type PilotExportView,
  type PilotFigures,
  type PilotTemplate,
} from "@/lib/pilot";
import { accountExportFilename, exportErrorMessage, planLabel } from "@/lib/profile";
import { formatMoney } from "@/lib/revenue";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input, Label } from "@/components/ui/input";
import { Modal } from "@/components/ui/dialog";
import { AsyncState } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { StatTile } from "@/components/crm/dashboard/primitives";

const TONE = { good: "success", bad: "destructive", neutral: "default" } as const;

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export default function PilotPage() {
  const current = useQuery({
    queryKey: ["pilot-current"],
    queryFn: getCurrentPilot,
    retry: (count, err) => !isDisabledError(err) && count < 2,
  });
  const history = useQuery({
    queryKey: ["pilots"],
    queryFn: listPilots,
    enabled: current.isSuccess,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);

  if (isDisabledError(current.error)) {
    return (
      <div className="mx-auto max-w-xl space-y-2 py-6">
        <h1 className="text-xl font-semibold">30-day pilot</h1>
        <p className="text-sm text-muted-foreground">
          Pilot mode is off. An administrator can turn it on in Admin › System Settings
          (<code>pilot_mode_enabled</code>).
        </p>
      </div>
    );
  }

  const latest = current.data?.pilot ?? null;
  const shown = (selected && history.data?.items.find((p) => p.id === selected)) || latest;
  const showSetup = !latest || (latest.status === "ended" && setupOpen);

  return (
    <div className="mx-auto max-w-3xl space-y-4 py-2">
      <div>
        <h1 className="text-xl font-semibold">30-day pilot</h1>
        <p className="text-sm text-muted-foreground">
          Write down what success means before the results come in. Every number is the
          meetings-per-dollar ledger&apos;s. Ending a pilot changes nothing in your plan or billing.
        </p>
      </div>
      <AsyncState isLoading={current.isLoading} error={current.error}>
        {showSetup ? (
          <SetupCard onStarted={() => { setSelected(null); setSetupOpen(false); }} />
        ) : shown ? (
          <>
            {shown.status === "active" ? <ActivePilot pilot={shown} /> : <VerdictView pilot={shown} />}
            {latest?.status === "ended" && (
              <Button variant="outline" onClick={() => setSetupOpen(true)}>Start a new pilot</Button>
            )}
          </>
        ) : null}
      </AsyncState>
      {(history.data?.items.length ?? 0) > 1 && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Earlier pilots</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-sm">
            {history.data!.items.map((p) => (
              <button key={p.id} type="button" onClick={() => { setSelected(p.id); setSetupOpen(false); }}
                      className="block w-full rounded px-2 py-1 text-left hover:bg-muted">
                {p.start_date} → {p.end_date} · {p.status === "active" ? dayLabel(p)
                  : p.verdict ? verdictHeadline(p.verdict) : endReasonLabel(p.end_reason)}
              </button>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Setup (day 1)
// ---------------------------------------------------------------------------

function SetupCard({ onStarted }: { onStarted: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const template = useQuery({ queryKey: ["pilot-template"], queryFn: getPilotTemplate });
  const today = new Date().toISOString().slice(0, 10);
  const campaigns = useQuery({
    queryKey: ["pilot-campaigns", today],
    queryFn: () => getRevenue(today, today),
  });
  const [form, setForm] = useState<CriteriaForm | null>(null);
  const [allCampaigns, setAllCampaigns] = useState(true);
  const [chosen, setChosen] = useState<string[]>([]);
  const [emails, setEmails] = useState(true);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (template.data && form === null) setForm(initialForm(template.data));
  }, [template.data, form]);

  const start = useMutation({
    mutationFn: startPilot,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pilot-current"] });
      qc.invalidateQueries({ queryKey: ["pilots"] });
      toast("Pilot started. Your criteria are locked in.", "success");
      onStarted();
    },
    onError: (e) => setProblem(errorText(e, "Could not start the pilot. Try again.")),
  });

  const currency = campaigns.data?.currency ?? "USD";
  const dates = pilotDates(new Date(), template.data?.pilot_days ?? 30);

  function submit(t: PilotTemplate) {
    if (!form) return;
    const result = buildStartBody(t, form, {
      strategyIds: allCampaigns ? null : chosen,
      scorecardEmails: emails,
    });
    if ("error" in result) { setProblem(result.error); return; }
    setProblem(null);
    start.mutate(result.body);
  }

  return (
    <Card aria-label="Set up a pilot">
      <CardHeader>
        <CardTitle className="text-sm">Day 1: set your success criteria</CardTitle>
        <p className="text-xs text-muted-foreground">
          Tick what you will judge Vectorae on and type your own target. Nothing is pre-filled.
          The criteria are <strong>locked once the pilot starts</strong>.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <AsyncState isLoading={template.isLoading} error={template.error}>
          {template.data && form && (
            <>
              <div className="space-y-3">
                {template.data.criteria.map((c) => (
                  <div key={c.key} className="flex flex-wrap items-center gap-3">
                    <Checkbox id={`crit-${c.key}`} checked={form[c.key]?.selected ?? false}
                              onCheckedChange={(on) => setForm({ ...form, [c.key]: { ...form[c.key], selected: on } })} />
                    <Label htmlFor={`crit-${c.key}`} className="min-w-[14rem] text-sm">
                      {c.label} {c.kind === "min" ? "— at least" : "— at most"}
                    </Label>
                    <Input aria-label={`${c.label} target`} inputMode={c.unit === "money" ? "decimal" : "numeric"}
                           className="w-36" disabled={!form[c.key]?.selected}
                           placeholder={c.unit === "money" ? `amount in ${currency}` : "number"}
                           value={form[c.key]?.value ?? ""}
                           onChange={(e) => setForm({ ...form, [c.key]: { ...form[c.key], value: e.target.value } })} />
                  </div>
                ))}
              </div>

              <div className="space-y-2 text-sm">
                <label className="flex items-center gap-2">
                  <Checkbox checked={allCampaigns} onCheckedChange={setAllCampaigns} />
                  Count every campaign
                </label>
                {!allCampaigns && (
                  <div className="ml-6 space-y-1">
                    {(campaigns.data?.campaigns ?? []).map((c) => (
                      <label key={c.strategy_id} className="flex items-center gap-2">
                        <Checkbox checked={chosen.includes(c.strategy_id)}
                                  onCheckedChange={(on) => setChosen(on
                                    ? [...chosen, c.strategy_id]
                                    : chosen.filter((id) => id !== c.strategy_id))} />
                        {c.name}
                      </label>
                    ))}
                    {campaigns.data && campaigns.data.campaigns.length === 0 && (
                      <p className="text-muted-foreground">No campaigns yet.</p>
                    )}
                  </div>
                )}
                <label className="flex items-center gap-2">
                  <Checkbox checked={emails} onCheckedChange={setEmails} />
                  Email me a weekly scorecard (after 7, 14, 21 and 28 days)
                </label>
              </div>

              <p className="flex items-start gap-2 text-xs text-muted-foreground">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                <span>
                  Day 1 is today ({dates.start}, UTC) and the pilot runs {template.data.pilot_days} UTC
                  calendar days, to {dates.end}. Starting late in the UTC day gives you a short day 1.
                  After day {template.data.pilot_days} the verdict stays pending for a grace period so
                  late-recorded meetings and replies still count, then you get a verdict page and can
                  download everything. {template.data.billing_statement}
                </span>
              </p>
              {problem && <p role="alert" className="text-sm text-red-600">{problem}</p>}
              <Button disabled={start.isPending} onClick={() => submit(template.data!)}>
                Start the 30-day pilot
              </Button>
            </>
          )}
        </AsyncState>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Active pilot
// ---------------------------------------------------------------------------

function CriteriaTable({ rows, currency, final }: {
  rows: CriterionResult[]; currency: string; final: boolean;
}) {
  const money = (cents: number) => formatMoney(cents, currency);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="py-1 pr-3 font-medium">Criterion</th>
            <th className="py-1 pr-3 font-medium">Target</th>
            <th className="py-1 pr-3 font-medium">{final ? "Result" : "So far"}</th>
            <th className="py-1 font-medium" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-border align-top">
              <td className="py-2 pr-3">{r.label}</td>
              <td className="py-2 pr-3">{formatTarget(r, money)}</td>
              <td className="py-2 pr-3">
                {formatActual(r, money)}
                {final && <p className="text-xs text-muted-foreground">{r.note}</p>}
              </td>
              <td className="py-2"><Badge tone={TONE[statusTone(r.status)]}>{statusLabel(r.status, final)}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Figures({ totals, currency }: { totals: PilotFigures; currency: string }) {
  const money = (cents: number | null) => formatMoney(cents, currency);
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <StatTile label="Total cost" value={money(totals.total_cost_cents)} />
      <StatTile label="Qualified conversations" value={String(totals.qualified_conversations)} />
      <StatTile label="Meetings booked" value={String(totals.meetings_booked)} />
      <StatTile label="Meetings held" value={String(totals.meetings_held)} />
      <StatTile label="Per qualified conversation" value={money(totals.cost_per_qualified_conversation_cents)} />
      <StatTile label="Per booked meeting" value={money(totals.cost_per_meeting_booked_cents)} />
      <StatTile label="Per held meeting" value={money(totals.cost_per_meeting_held_cents)} />
    </div>
  );
}

function ActivePilot({ pilot }: { pilot: Pilot }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["pilot-current"] });
    qc.invalidateQueries({ queryKey: ["pilots"] });
  };
  const end = useMutation({
    mutationFn: () => endPilot(pilot.id),
    onSuccess: () => { setConfirm(false); refresh(); },
    onError: (e) => toast(errorText(e, "Could not end the pilot."), "error"),
  });
  const emails = useMutation({
    mutationFn: (on: boolean) => setScorecardEmails(pilot.id, on),
    onSuccess: refresh,
    onError: (e) => toast(errorText(e, "Could not save."), "error"),
  });
  const progress = pilot.progress;
  const currency = progress?.ledger_currency ?? pilot.currency;
  const pending = pilot.phase === "verdict_pending";

  return (
    <Card aria-label="Pilot in progress">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle className="text-sm">{dayLabel(pilot)}</CardTitle>
          <p className="text-xs text-muted-foreground">
            {pilot.start_date} → {pilot.end_date}.{" "}
            {pending
              ? "The 30 days are over. Meetings and replies recorded late still count until the time above; then the verdict is frozen."
              : `The verdict is computed after day ${pilot.pilot_days} against the criteria set on day 1.`}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setConfirm(true)}>
          {pending ? "Finalize the verdict now" : "End pilot now"}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {progress ? (
          <>
            <CriteriaTable rows={progress.criteria} currency={currency} final={false} />
            <Figures totals={progress.totals} currency={currency} />
          </>
        ) : null}
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <label className="flex items-center gap-2">
            <Checkbox checked={pilot.scorecard_emails} disabled={emails.isPending}
                      onCheckedChange={(on) => emails.mutate(on)} />
            Email me a weekly scorecard
          </label>
          <span className="text-xs text-muted-foreground">
            Sent: {pilot.scorecards.filter((s) => s.status === "sent").map((s) => `week ${s.week}`).join(", ") || "none yet"}
          </span>
        </div>
      </CardContent>
      <Modal open={confirm} onClose={() => setConfirm(false)}
             title={pending ? "Finalize the verdict now?" : "End the pilot now?"}>
        <div className="space-y-3 text-sm">
          <p>
            {pending
              ? `The verdict is frozen now over the full ${pilot.pilot_days} days; anything recorded later will not count.`
              : `The verdict is computed now, over ${pilot.start_date} to today, against the criteria set on day 1.`}
            {" "}Your plan and billing do not change.
          </p>
          <div className="flex gap-2">
            <Button variant="destructive" disabled={end.isPending} onClick={() => end.mutate()}>
              End and see the verdict
            </Button>
            <Button variant="ghost" onClick={() => setConfirm(false)}>Keep running</Button>
          </div>
        </div>
      </Modal>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Verdict (day 30)
// ---------------------------------------------------------------------------

function VerdictView({ pilot }: { pilot: Pilot }) {
  const toast = useToast();
  const workspace = useQuery({ queryKey: ["workspace-current"], queryFn: getCurrentWorkspace });
  const isOwner = workspace.data ? workspace.data.role === "owner" : false;
  const [password, setPassword] = useState("");
  const [exportMsg, setExportMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const verdict = pilot.verdict;
  const currency = verdict?.ledger_currency ?? pilot.currency;

  const download = useMutation({
    mutationFn: async ({ format, view }: { format: PilotExportFormat; view: PilotExportView }) => {
      saveBlob(await downloadPilotExport(pilot.id, format, view),
               pilotExportFilename(pilot.start_date, format, view));
    },
    onError: (e) => toast(errorText(e, "Could not download."), "error"),
  });
  const account = useMutation({
    mutationFn: () => exportMyData(password),
    onSuccess: (data) => {
      saveBlob(new Blob([JSON.stringify(data, null, 1)], { type: "application/json" }),
               accountExportFilename());
      setPassword("");
      setExportMsg({ ok: true, text: "Your account data has been downloaded." });
    },
    onError: (e) => setExportMsg({ ok: false, text: exportErrorMessage(e) }),
  });

  const headline = useMemo(() => (verdict ? verdictHeadline(verdict) : ""), [verdict]);
  if (!verdict) return null;

  return (
    <Card aria-label="Pilot verdict">
      <CardHeader>
        <CardTitle className="text-sm">Verdict — {endReasonLabel(pilot.end_reason)}</CardTitle>
        <p className="text-xs text-muted-foreground">
          {verdict.date_from} → {verdict.date_to} · computed {verdict.computed_at.slice(0, 16).replace("T", " ")} UTC
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className={verdict.outcome === "met" ? "font-medium text-success" : "font-medium"} role="status">
          {headline}
        </p>
        <CriteriaTable rows={verdict.criteria} currency={currency} final />
        <Figures totals={verdict.ledger.totals} currency={currency} />
        <p className="text-sm">
          Plan at start: <strong>{planLabel(verdict.plan_at_start ?? "—")}</strong> · plan at end:{" "}
          <strong>{planLabel(verdict.plan_at_end ?? "—")}</strong>. {verdict.billing_statement}
        </p>

        <div className="space-y-2 border-t border-border pt-3">
          <p className="text-sm font-medium">Take everything with you</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={download.isPending}
                    onClick={() => download.mutate({ format: "pdf", view: "verdict" })}>
              <Download className="mr-1 h-3.5 w-3.5" aria-hidden /> Verdict PDF
            </Button>
            <Button size="sm" variant="outline" disabled={download.isPending}
                    onClick={() => download.mutate({ format: "csv", view: "verdict" })}>
              <Download className="mr-1 h-3.5 w-3.5" aria-hidden /> Verdict CSV
            </Button>
            <Button size="sm" variant="outline" disabled={download.isPending}
                    onClick={() => download.mutate({ format: "csv", view: "campaigns" })}>
              <Download className="mr-1 h-3.5 w-3.5" aria-hidden /> Per-campaign CSV
            </Button>
          </div>
          {isOwner ? (
            <div className="flex flex-wrap items-end gap-2">
              <div>
                <Label htmlFor="pilot-export-pw" className="text-xs">
                  Full account data (JSON) — confirm your password
                </Label>
                <Input id="pilot-export-pw" type="password" autoComplete="current-password"
                       className="w-56" value={password} onChange={(e) => setPassword(e.target.value)} />
              </div>
              <Button size="sm" disabled={!password || account.isPending} onClick={() => account.mutate()}>
                Download all my data
              </Button>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              Only the workspace owner can download the account&apos;s full data (from their{" "}
              <Link className="underline" href="/profile">Profile</Link>).
            </p>
          )}
          {exportMsg && (
            <p role={exportMsg.ok ? "status" : "alert"}
               className={`text-sm ${exportMsg.ok ? "" : "text-red-600"}`}>{exportMsg.text}</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
