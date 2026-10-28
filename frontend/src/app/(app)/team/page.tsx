"use client";

/** Feature Group 8: the team — members and roles, invitations, campaign
 *  approvals, workspace settings and white label. Every action is also
 *  enforced by the API; the UI only hides what the role cannot do. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Trash2 } from "lucide-react";
import {
  approveSequence, changeRole, getBranding, getCurrentWorkspace, invite, listApprovals,
  listInvitations, listMembers, rejectSequence, removeMember, revokeInvitation,
  updateBranding, updateWorkspace, uploadLogo, verifyDomain,
  type PrivateBranding,
} from "@/lib/api/workspaces";
import {
  ROLE_LABELS, assignableRoles, atLeast, canManage, type Role,
} from "@/lib/workspace";
import { AsyncState } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { SequenceReviewPanel } from "@/components/campaigns/SequenceReviewPanel";

const selectClass = "rounded border border-border bg-card px-2 py-1 text-sm";

function useFail() {
  const toast = useToast();
  return (e: unknown) => toast((e as Error).message, "error");
}

export default function TeamPage() {
  const { data: ws, isLoading, error } = useQuery({
    queryKey: ["workspace-current"],
    queryFn: getCurrentWorkspace,
  });
  const role = ws?.role;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Team</h1>
        {ws && (
          <p className="text-sm text-muted-foreground">
            {ws.name} · you are {role ? ROLE_LABELS[role] : ""}
          </p>
        )}
      </div>
      <AsyncState isLoading={isLoading} error={error}>
        {ws && role && (
          <Tabs defaultValue="members">
            <TabsList>
              <TabsTrigger value="members">Members</TabsTrigger>
              <TabsTrigger value="approvals">Approvals</TabsTrigger>
              {role === "owner" && <TabsTrigger value="settings">Settings</TabsTrigger>}
              {role === "owner" && <TabsTrigger value="branding">White label</TabsTrigger>}
            </TabsList>
            <TabsContent value="members"><MembersPanel role={role} /></TabsContent>
            <TabsContent value="approvals"><ApprovalsPanel role={role} /></TabsContent>
            {role === "owner" && (
              <TabsContent value="settings">
                <SettingsPanel name={ws.name} approvalRequired={ws.approval_required} />
              </TabsContent>
            )}
            {role === "owner" && <TabsContent value="branding"><BrandingPanel /></TabsContent>}
          </Tabs>
        )}
      </AsyncState>
    </div>
  );
}

// --------------------------------------------------------------------------
// Members + invitations
// --------------------------------------------------------------------------

function MembersPanel({ role }: { role: Role }) {
  const qc = useQueryClient();
  const toast = useToast();
  const fail = useFail();
  const members = useQuery({ queryKey: ["members"], queryFn: listMembers });
  const invitations = useQuery({
    queryKey: ["invitations"], queryFn: listInvitations, enabled: atLeast(role, "manager"),
  });
  const [email, setEmail] = useState("");
  const [newRole, setNewRole] = useState<Role>(assignableRoles(role).includes("sdr") ? "sdr" : "viewer");
  const [link, setLink] = useState<string | null>(null);

  const setRole = useMutation({
    mutationFn: ({ id, r }: { id: string; r: Role }) => changeRole(id, r),
    onSuccess: (rows) => qc.setQueryData(["members"], rows),
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: removeMember,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["members"] }),
    onError: fail,
  });
  const send = useMutation({
    mutationFn: () => invite(email, newRole),
    onSuccess: (r) => {
      setLink(r.link);
      setEmail("");
      qc.invalidateQueries({ queryKey: ["invitations"] });
      toast(`Invitation sent to ${r.email}`, "success");
    },
    onError: fail,
  });
  const revoke = useMutation({
    mutationFn: revokeInvitation,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invitations"] }),
    onError: fail,
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><CardTitle className="text-sm">Members</CardTitle></CardHeader>
        <CardContent>
          <AsyncState isLoading={members.isLoading} error={members.error}>
            <ul className="divide-y divide-border text-sm">
              {members.data?.map((m) => (
                <li key={m.user_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span className="min-w-0 truncate">{m.email}</span>
                  <div className="flex items-center gap-2">
                    {canManage(role, m.role) ? (
                      <select aria-label={`Role for ${m.email}`} className={selectClass}
                              value={m.role}
                              onChange={(e) => setRole.mutate({ id: m.user_id, r: e.target.value as Role })}>
                        {Array.from(new Set([m.role, ...assignableRoles(role)])).map((r) => (
                          <option key={r} value={r}>{ROLE_LABELS[r]}</option>
                        ))}
                      </select>
                    ) : (
                      <span className="text-xs text-muted-foreground">{ROLE_LABELS[m.role]}</span>
                    )}
                    {canManage(role, m.role) && (
                      <button type="button" aria-label={`Remove ${m.email}`}
                              className="rounded p-1 text-muted-foreground hover:text-[rgb(var(--destructive))]"
                              onClick={() => remove.mutate(m.user_id)}>
                        <Trash2 className="h-4 w-4" aria-hidden />
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </AsyncState>
          <p className="mt-3 text-[11px] text-muted-foreground">
            Managers approve SDR launches and manage SDRs and viewers. SDRs work leads and
            campaigns but cannot change integrations, costs or delete anything. Viewers read only.
          </p>
        </CardContent>
      </Card>

      {atLeast(role, "manager") && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Invite</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <form className="flex flex-wrap items-end gap-2"
                  onSubmit={(e) => { e.preventDefault(); send.mutate(); }}>
              <div className="min-w-[14rem] flex-1 space-y-1">
                <Label htmlFor="invite-email">Email</Label>
                <Input id="invite-email" type="email" required value={email}
                       onChange={(e) => setEmail(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="invite-role">Role</Label>
                <select id="invite-role" className={selectClass} value={newRole}
                        onChange={(e) => setNewRole(e.target.value as Role)}>
                  {assignableRoles(role).map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                </select>
              </div>
              <Button type="submit" size="sm" disabled={!email || send.isPending}>Send invite</Button>
            </form>
            {link && <CopyLine label="Invitation link (also emailed)" value={link} />}
            {(invitations.data?.length ?? 0) > 0 && (
              <ul className="divide-y divide-border text-sm">
                {invitations.data?.map((i) => (
                  <li key={i.id} className="flex items-center justify-between gap-2 py-2">
                    <span>
                      {i.email} · {ROLE_LABELS[i.role]}
                      <span className="ml-2 text-xs text-muted-foreground">
                        {i.expired ? "expired" : `expires ${new Date(i.expires_at).toLocaleDateString()}`}
                      </span>
                    </span>
                    <Button size="sm" variant="outline" onClick={() => revoke.mutate(i.id)}>Revoke</Button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function CopyLine({ label, value }: { label: string; value: string }) {
  const toast = useToast();
  return (
    <div className="space-y-1 text-xs">
      <p className="text-muted-foreground">{label}</p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 break-all rounded bg-muted px-2 py-1 font-mono">{value}</code>
        <Button size="sm" variant="outline" aria-label="Copy"
                onClick={() => navigator.clipboard.writeText(value).then(
                  () => toast("Copied", "success"), () => toast("Copy failed", "error"))}>
          <Copy className="h-3.5 w-3.5" aria-hidden />
        </Button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Approvals
// --------------------------------------------------------------------------

function ApprovalsPanel({ role }: { role: Role }) {
  const qc = useQueryClient();
  const toast = useToast();
  const fail = useFail();
  const { data, isLoading, error } = useQuery({ queryKey: ["approvals"], queryFn: listApprovals });
  const [declining, setDeclining] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["approvals"] });
    qc.invalidateQueries({ queryKey: ["sequences"] });
  };
  const approve = useMutation({
    mutationFn: approveSequence,
    onSuccess: (r) => { refresh(); toast(`Approved — ${r.enrolled} leads enrolled`, "success"); },
    onError: fail,
  });
  const decline = useMutation({
    mutationFn: ({ id, n }: { id: string; n: string }) => rejectSequence(id, n),
    onSuccess: () => { refresh(); setDeclining(null); setNote(""); toast("Declined", "info"); },
    onError: fail,
  });
  const approver = atLeast(role, "manager");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Campaign launches</CardTitle>
        <p className="text-xs text-muted-foreground">
          An SDR&apos;s first launch of a sequence waits here until a manager approves it.
        </p>
      </CardHeader>
      <CardContent>
        <AsyncState isLoading={isLoading} error={error} empty={!data || data.length === 0}
                    emptyLabel="Nothing waiting for approval.">
          <ul className="divide-y divide-border text-sm">
            {data?.map((a) => (
              <li key={a.sequence_id} className="space-y-2 py-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="font-medium">{a.sequence_name} <span className="text-muted-foreground">· {a.campaign}</span></p>
                    <p className="text-xs text-muted-foreground">
                      {a.state === "pending" ? "Requested" : "Declined"} by {a.requested_by ?? "an SDR"}
                      {a.requested_at && ` · ${new Date(a.requested_at).toLocaleString()}`}
                      {a.lead_statuses.length > 0 && ` · enroll ${a.lead_statuses.join(", ")} leads`}
                    </p>
                    {a.note && <p className="mt-1 text-xs">Note: {a.note}</p>}
                  </div>
                  {approver && a.state === "pending" && (
                    <div className="flex gap-2">
                      <Button size="sm" onClick={() => approve.mutate(a.sequence_id)}
                              disabled={approve.isPending}>Approve</Button>
                      <Button size="sm" variant="outline"
                              onClick={() => setDeclining(declining === a.sequence_id ? null : a.sequence_id)}>
                        Decline
                      </Button>
                    </div>
                  )}
                </div>
                {/* Feature A7: approval is refused while the review is blocked;
                    the findings (and the override) are right here. */}
                {a.state === "pending" && <SequenceReviewPanel sequenceId={a.sequence_id} />}
                {declining === a.sequence_id && (
                  <form className="space-y-2"
                        onSubmit={(e) => { e.preventDefault(); decline.mutate({ id: a.sequence_id, n: note }); }}>
                    <Label htmlFor={`note-${a.sequence_id}`}>What should change? (sent to the SDR)</Label>
                    <Textarea id={`note-${a.sequence_id}`} maxLength={1000} rows={2} value={note}
                              onChange={(e) => setNote(e.target.value)} />
                    <Button type="submit" size="sm" variant="outline" disabled={decline.isPending}>
                      Send decline
                    </Button>
                  </form>
                )}
              </li>
            ))}
          </ul>
        </AsyncState>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Owner: settings + white label
// --------------------------------------------------------------------------

function SettingsPanel({ name, approvalRequired }: { name: string; approvalRequired: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [value, setValue] = useState(name);
  const save = useMutation({
    mutationFn: (body: { name?: string; approval_required?: boolean }) => updateWorkspace(body),
    onSuccess: (ws) => { qc.setQueryData(["workspace-current"], ws); toast("Saved", "success"); },
    onError: useFail(),
  });
  return (
    <Card>
      <CardContent className="space-y-4 p-gutter">
        <form className="flex flex-wrap items-end gap-2"
              onSubmit={(e) => { e.preventDefault(); save.mutate({ name: value }); }}>
          <div className="min-w-[14rem] flex-1 space-y-1">
            <Label htmlFor="ws-name">Workspace name</Label>
            <Input id="ws-name" maxLength={200} value={value} onChange={(e) => setValue(e.target.value)} />
          </div>
          <Button type="submit" size="sm" disabled={!value.trim() || save.isPending}>Save</Button>
        </form>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={approvalRequired}
                 onChange={(e) => save.mutate({ approval_required: e.target.checked })} />
          SDR campaign launches need a manager&apos;s approval
        </label>
      </CardContent>
    </Card>
  );
}

function BrandingPanel() {
  const qc = useQueryClient();
  const toast = useToast();
  const fail = useFail();
  const { data, isLoading, error } = useQuery({ queryKey: ["branding"], queryFn: getBranding });
  const set = (b: PrivateBranding) => qc.setQueryData(["branding"], b);
  const save = useMutation({ mutationFn: updateBranding, onSuccess: (b) => { set(b); toast("Branding saved", "success"); }, onError: fail });
  const logo = useMutation({ mutationFn: uploadLogo, onSuccess: set, onError: fail });
  const verify = useMutation({
    mutationFn: verifyDomain,
    onSuccess: (b) => { set(b); toast(b.verified ? "Domain verified" : "Record not found yet — DNS can take a while", b.verified ? "success" : "info"); },
    onError: fail,
  });

  return (
    <AsyncState isLoading={isLoading} error={error}>
      {data && <BrandingForm key={JSON.stringify(data)} data={data}
                             onSave={(b) => save.mutate(b)} saving={save.isPending}
                             onLogo={(f) => logo.mutate(f)} onVerify={() => verify.mutate()}
                             verifying={verify.isPending} />}
    </AsyncState>
  );
}

function BrandingForm({ data, onSave, saving, onLogo, onVerify, verifying }: {
  data: PrivateBranding;
  onSave: (b: Parameters<typeof updateBranding>[0]) => void;
  saving: boolean;
  onLogo: (f: File) => void;
  onVerify: () => void;
  verifying: boolean;
}) {
  const [enabled, setEnabled] = useState(data.white_label);
  const [brand, setBrand] = useState(data.white_label ? data.brand_name : "");
  const [color, setColor] = useState(data.primary_color ?? "#1d4ed8");
  const [support, setSupport] = useState(data.support_email ?? "");
  const [domain, setDomain] = useState(data.custom_domain ?? "");

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-3 p-gutter">
          <form className="space-y-3" onSubmit={(e) => {
            e.preventDefault();
            onSave({ white_label_enabled: enabled, brand_name: brand || null, primary_color: color,
                     support_email: support || null, custom_domain: domain || null });
          }}>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              White label this workspace (your name, logo and colour instead of Vectorae&apos;s)
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="brand-name">Brand name</Label>
                <Input id="brand-name" maxLength={100} value={brand} onChange={(e) => setBrand(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="brand-color">Primary colour</Label>
                <div className="flex gap-2">
                  <input id="brand-color" type="color" value={color} aria-label="Primary colour picker"
                         className="h-9 w-12 rounded border border-border bg-card"
                         onChange={(e) => setColor(e.target.value)} />
                  <Input value={color} maxLength={7} aria-label="Primary colour hex"
                         onChange={(e) => setColor(e.target.value)} />
                </div>
              </div>
              <div className="space-y-1">
                <Label htmlFor="brand-support">Support email</Label>
                <Input id="brand-support" type="email" value={support} onChange={(e) => setSupport(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="brand-domain">Custom domain</Label>
                <Input id="brand-domain" placeholder="app.youragency.com" value={domain}
                       onChange={(e) => setDomain(e.target.value)} />
              </div>
            </div>
            <Button type="submit" size="sm" disabled={saving}>Save branding</Button>
          </form>
          <div className="space-y-1">
            <Label htmlFor="brand-logo">Logo (PNG, JPEG or WebP, up to 1 MB)</Label>
            <div className="flex items-center gap-3">
              {data.logo_url && <img src={data.logo_url} alt="Current logo" className="h-8 w-auto rounded border border-border" />}
              <input id="brand-logo" type="file" accept="image/png,image/jpeg,image/webp"
                     className="text-sm"
                     onChange={(e) => { const f = e.target.files?.[0]; if (f) onLogo(f); }} />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm">Where your clients reach it</CardTitle></CardHeader>
        <CardContent className="space-y-3 text-sm">
          {data.subdomain && <p>Subdomain: <code className="font-mono">{data.subdomain}</code></p>}
          {data.custom_domain ? (
            <>
              <p>
                <code className="font-mono">{data.custom_domain}</code> —{" "}
                {data.domain_verified
                  ? <span className="text-[rgb(var(--success))]">verified</span>
                  : <span className="text-[rgb(var(--warning))]">not verified</span>}
              </p>
              {!data.domain_verified && data.verification_record && (
                <div className="space-y-2 text-xs">
                  <p className="text-muted-foreground">Add this DNS record, then verify:</p>
                  <div className="overflow-x-auto">
                    <table className="text-left" aria-label="DNS verification record">
                      <thead><tr className="text-muted-foreground"><th className="pr-4">Type</th><th className="pr-4">Name</th><th>Value</th></tr></thead>
                      <tbody><tr className="font-mono">
                        <td className="pr-4">{data.verification_record.type}</td>
                        <td className="pr-4 break-all">{data.verification_record.name}</td>
                        <td className="break-all">{data.verification_record.value}</td>
                      </tr></tbody>
                    </table>
                  </div>
                  {data.cname_target && (
                    <p className="text-muted-foreground">
                      Point the domain itself at <code className="font-mono">{data.cname_target}</code> with a CNAME
                      (that also verifies it).
                    </p>
                  )}
                  <Button size="sm" variant="outline" onClick={onVerify} disabled={verifying}>Verify domain</Button>
                </div>
              )}
            </>
          ) : (
            <p className="text-muted-foreground">No custom domain set.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
