"use client";

/** Admin > Compliance Rules (Feature 8).
 *
 *  Overrides of the send-time compliance baseline per workspace, recipient
 *  region and channel. The server validates every value with the same function
 *  the send path uses, and resolves rules fail-closed — see
 *  app/services/compliance_rules.py. This page never widens anything by
 *  itself: caps and the bounce threshold can only tighten, and hours stay
 *  inside the hard band the API reports. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  deleteComplianceRule,
  effectiveComplianceRule,
  listComplianceRules,
  listRuleWorkspaces,
  upsertComplianceRule,
  type EffectiveRule,
} from "@/lib/api/systemAdmin";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";

type Tri = "" | "true" | "false";

const selectClass = "h-9 rounded border border-border bg-card px-2 text-sm";

function show(value: number | boolean | null): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

function EffectiveSummary({ rule }: { rule: EffectiveRule }) {
  return (
    <p className="text-sm">
      {rule.window_empty
        ? "No permitted send window — nothing sends."
        : `${rule.send_start_hour}:00–${rule.send_end_hour}:00 local${rule.skip_weekends ? ", weekdays only" : ""}`}
      {" · cap "}{rule.daily_cap ?? "none"}
      {" · consent "}{rule.consent_required ? "required" : "not required"}
      {" · bounce pause at "}{(Number(rule.bounce_pause_threshold) * 100).toFixed(1)}%
      {rule.failed_closed && <Badge className="ml-2">failed closed</Badge>}
    </p>
  );
}

export default function AdminComplianceRulesPage() {
  const qc = useQueryClient();
  const [workspaceId, setWorkspaceId] = useState("");
  const [form, setForm] = useState({
    scope: "global", region: "*", channel: "*",
    send_start_hour: "", send_end_hour: "", daily_cap: "", bounce_pause_threshold: "",
    skip_weekends: "" as Tri, consent_required: "" as Tri, note: "",
  });
  const [preview, setPreview] = useState({ region: "us", channel: "email" });

  const workspaces = useQuery({ queryKey: ["admin", "rule-workspaces"], queryFn: listRuleWorkspaces });
  const list = useQuery({
    queryKey: ["admin", "compliance-rules", workspaceId],
    queryFn: () => listComplianceRules(workspaceId || undefined),
  });
  const effective = useQuery({
    queryKey: ["admin", "compliance-effective", workspaceId, preview.region, preview.channel],
    queryFn: () => effectiveComplianceRule(workspaceId || undefined, preview.region, preview.channel),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["admin", "compliance-rules"] });
    qc.invalidateQueries({ queryKey: ["admin", "compliance-effective"] });
  };

  const save = useMutation({
    mutationFn: () => {
      const num = (v: string) => (v.trim() === "" ? null : Number(v));
      const tri = (v: Tri) => (v === "" ? null : v === "true");
      return upsertComplianceRule({
        workspace_id: form.scope === "workspace" ? workspaceId || null : null,
        region: form.region,
        channel: form.channel,
        send_start_hour: num(form.send_start_hour),
        send_end_hour: num(form.send_end_hour),
        daily_cap: num(form.daily_cap),
        bounce_pause_threshold: num(form.bounce_pause_threshold),
        skip_weekends: tri(form.skip_weekends),
        consent_required: tri(form.consent_required),
        note: form.note.trim() || null,
      });
    },
    onSuccess: () => { refresh(); toast.success("Rule saved"); },
    onError: (e) => toast.error((e as Error).message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteComplianceRule(id),
    onSuccess: () => { refresh(); toast.success("Rule deleted"); },
    onError: (e) => toast.error((e as Error).message),
  });

  const data = list.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Compliance Rules</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Send windows, caps, consent and bounce thresholds by workspace, recipient region and
          channel. Checked at the moment every message sends. An empty table is the baseline;
          a broken rule makes sending stricter, never looser.
        </p>
      </div>

      <label className="flex flex-wrap items-center gap-2 text-sm">
        Workspace
        <select className={selectClass} value={workspaceId}
                onChange={(e) => setWorkspaceId(e.target.value)} aria-label="Workspace">
          <option value="">Global rules only</option>
          {workspaces.data?.map((w) => (
            <option key={w.id} value={w.id}>{w.name} ({w.owner_email})</option>
          ))}
        </select>
      </label>

      {data && (
        <section className="space-y-1 rounded border border-border p-3">
          <h2 className="text-sm font-medium">Baseline (no rule)</h2>
          <EffectiveSummary rule={data.baseline} />
          <p className="text-xs text-muted-foreground">
            Hours must stay within {data.hard_bounds.hour_min}:00–{data.hard_bounds.hour_max}:00.
            Caps and the bounce threshold ({(data.hard_bounds.bounce_max * 100).toFixed(1)}%) can only be lowered.
          </p>
        </section>
      )}

      <section className="space-y-2 rounded border border-border p-3">
        <h2 className="text-sm font-medium">What applies to a send</h2>
        <div className="flex flex-wrap gap-2">
          <select className={selectClass} value={preview.region} aria-label="Preview region"
                  onChange={(e) => setPreview({ ...preview, region: e.target.value })}>
            {data?.regions.filter((r) => r !== "*").map((r) => <option key={r}>{r}</option>)}
          </select>
          <select className={selectClass} value={preview.channel} aria-label="Preview channel"
                  onChange={(e) => setPreview({ ...preview, channel: e.target.value })}>
            {data?.channels.filter((c) => c !== "*").map((c) => <option key={c}>{c}</option>)}
          </select>
        </div>
        {effective.data && <EffectiveSummary rule={effective.data} />}
      </section>

      {list.error && <div className="text-destructive">{(list.error as Error).message}</div>}
      {data && (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Scope</TableHead>
                <TableHead>Region</TableHead>
                <TableHead>Channel</TableHead>
                <TableHead>Hours</TableHead>
                <TableHead>Weekends</TableHead>
                <TableHead>Cap</TableHead>
                <TableHead>Consent</TableHead>
                <TableHead>Bounce</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.rules.length === 0 && (
                <TableRow><TableCell colSpan={9} className="text-muted-foreground">
                  No rules — the baseline applies everywhere.
                </TableCell></TableRow>
              )}
              {data.rules.map((rule) => (
                <TableRow key={rule.id}>
                  <TableCell>
                    {rule.scope === "global" ? "global" : "workspace"}
                    {rule.problems.length > 0 && (
                      <p className="text-xs text-destructive">Invalid: {rule.problems.join("; ")}</p>
                    )}
                  </TableCell>
                  <TableCell>{rule.region}</TableCell>
                  <TableCell>{rule.channel}</TableCell>
                  <TableCell>{show(rule.send_start_hour)}–{show(rule.send_end_hour)}</TableCell>
                  <TableCell>{rule.skip_weekends === null ? "—" : rule.skip_weekends ? "skip" : "send"}</TableCell>
                  <TableCell>{show(rule.daily_cap)}</TableCell>
                  <TableCell>{show(rule.consent_required)}</TableCell>
                  <TableCell>{show(rule.bounce_pause_threshold)}</TableCell>
                  <TableCell>
                    <Button size="sm" variant="outline" disabled={remove.isPending}
                            onClick={() => remove.mutate(rule.id)}>Delete</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {data && (
        <section className="space-y-3 rounded border border-border p-3">
          <h2 className="text-sm font-medium">Add or update a rule</h2>
          <p className="text-xs text-muted-foreground">
            Leave a field blank to inherit it. Saving the same scope, region and channel updates that rule.
            Email and LinkedIn have no consent record in Vectorae, so requiring consent there stops those sends.
          </p>
          <div className="grid gap-2 sm:grid-cols-3">
            <select className={selectClass} value={form.scope} aria-label="Scope"
                    onChange={(e) => setForm({ ...form, scope: e.target.value })}>
              <option value="global">Global</option>
              <option value="workspace" disabled={!workspaceId}>Selected workspace</option>
            </select>
            <select className={selectClass} value={form.region} aria-label="Region"
                    onChange={(e) => setForm({ ...form, region: e.target.value })}>
              {data.regions.map((r) => <option key={r} value={r}>{r === "*" ? "any region" : r}</option>)}
            </select>
            <select className={selectClass} value={form.channel} aria-label="Channel"
                    onChange={(e) => setForm({ ...form, channel: e.target.value })}>
              {data.channels.map((c) => <option key={c} value={c}>{c === "*" ? "any channel" : c}</option>)}
            </select>
            <Input placeholder="Start hour" inputMode="numeric" value={form.send_start_hour}
                   onChange={(e) => setForm({ ...form, send_start_hour: e.target.value })} />
            <Input placeholder="End hour" inputMode="numeric" value={form.send_end_hour}
                   onChange={(e) => setForm({ ...form, send_end_hour: e.target.value })} />
            <Input placeholder="Daily cap" inputMode="numeric" value={form.daily_cap}
                   onChange={(e) => setForm({ ...form, daily_cap: e.target.value })} />
            <Input placeholder="Bounce pause (e.g. 0.02)" inputMode="decimal"
                   value={form.bounce_pause_threshold}
                   onChange={(e) => setForm({ ...form, bounce_pause_threshold: e.target.value })} />
            <select className={selectClass} value={form.skip_weekends} aria-label="Weekends"
                    onChange={(e) => setForm({ ...form, skip_weekends: e.target.value as Tri })}>
              <option value="">Weekends: inherit</option>
              <option value="true">Skip weekends</option>
              <option value="false">Send on weekends</option>
            </select>
            <select className={selectClass} value={form.consent_required} aria-label="Consent"
                    onChange={(e) => setForm({ ...form, consent_required: e.target.value as Tri })}>
              <option value="">Consent: inherit</option>
              <option value="true">Consent required</option>
              <option value="false">Consent not required</option>
            </select>
            <Input placeholder="Note (why)" value={form.note}
                   onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </div>
          <Button disabled={save.isPending} onClick={() => save.mutate()}>Save rule</Button>
        </section>
      )}
    </div>
  );
}
