"use client";

/** F05 Guardrails Studio — this account's exclusion lists (manual, CSV, CRM
 *  sync) and the per-product approved-claims / forbidden-phrases library.
 *  Every rule here is enforced by the server at sourcing and at send time;
 *  this card only edits the lists. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, Trash2, Upload } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import {
  addExclusion,
  addLibraryEntry,
  getGuardrailsStatus,
  importExclusions,
  listExclusions,
  listGuardrailProducts,
  listLibrary,
  removeExclusion,
  removeLibraryEntry,
  syncExclusionsFromCrm,
} from "@/lib/api/guardrails";
import {
  KIND_LABELS,
  KIND_PLACEHOLDERS,
  REASON_LABELS,
  importSummary,
  reasonLabel,
  sourceLabel,
  splitLibrary,
  syncSummary,
  type ExclusionKind,
  type ExclusionReason,
  type LibraryKind,
} from "@/lib/guardrails";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

const KINDS: ExclusionKind[] = ["domain", "company", "contact"];
const REASONS = Object.keys(REASON_LABELS) as ExclusionReason[];

function errorText(err: unknown): string | null {
  if (!err) return null;
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

export function GuardrailsCard() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["guardrails", "status"], queryFn: getGuardrailsStatus });
  const enabled = status.data?.enabled === true;
  const refresh = () => qc.invalidateQueries({ queryKey: ["guardrails"] });

  return (
    <div className="space-y-5 rounded-lg border bg-card p-5">
      <div>
        <h3 className="flex items-center gap-2 font-semibold">
          <ShieldCheck size={16} aria-hidden="true" /> Guardrails Studio
          <Badge tone={enabled ? "success" : "default"}>{enabled ? "On" : "Off"}</Badge>
        </h3>
        <p className="text-sm text-muted-foreground">
          Customers, open deals, churned accounts and anyone you never want pitched. Excluded
          domains, companies and contacts are skipped when leads are sourced and blocked again at
          the moment of sending. Opt-outs (unsubscribes) are always enforced, separately.
        </p>
        {status.data && !enabled && (
          <p className="mt-2 text-sm text-muted-foreground">
            Guardrails are switched off for this deployment, so nothing here is enforced and the
            lists cannot be edited. An administrator can turn on <code>guardrails_enabled</code> in
            System settings.
          </p>
        )}
      </div>
      <ExclusionsSection enabled={enabled} onChange={refresh} />
      <LibrarySection enabled={enabled} onChange={refresh} />
    </div>
  );
}

function ExclusionsSection({ enabled, onChange }: { enabled: boolean; onChange: () => void }) {
  const [kindFilter, setKindFilter] = useState<ExclusionKind | "">("");
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<ExclusionKind>("domain");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState<ExclusionReason>("customer");
  const [csvReason, setCsvReason] = useState<ExclusionReason>("customer");
  const [messages, setMessages] = useState<string[]>([]);

  const list = useQuery({
    queryKey: ["guardrails", "exclusions", kindFilter, q],
    queryFn: () => listExclusions({ kind: kindFilter || undefined, q: q || undefined }),
  });
  const add = useMutation({
    mutationFn: () => addExclusion({ kind, value, reason }),
    onSuccess: (row) => {
      setValue("");
      setMessages([row.status === "exists" ? "Already on the list."
        : row.status === "updated" ? "Already on the list. Updated to the reason you chose."
        : "Added."]);
      onChange();
    },
  });
  const remove = useMutation({ mutationFn: removeExclusion, onSuccess: onChange });
  const upload = useMutation({
    mutationFn: (csv: string) => importExclusions(csv, csvReason),
    onSuccess: (res) => {
      setMessages([importSummary(res),
        ...res.errors.slice(0, 10).map((e) => `Row ${e.row}: ${e.error}`)]);
      onChange();
    },
  });
  const sync = useMutation({
    mutationFn: syncExclusionsFromCrm,
    onSuccess: (res) => { setMessages(syncSummary(res)); onChange(); },
  });
  const failure = errorText(add.error ?? upload.error ?? sync.error ?? remove.error);

  const onFile = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => upload.mutate(String(reader.result ?? ""));
    reader.readAsText(file);
  };

  return (
    <section className="space-y-3" aria-label="Exclusion lists">
      <h4 className="text-sm font-semibold">Exclusion lists</h4>
      <form className="grid gap-3 sm:grid-cols-6"
            onSubmit={(e) => { e.preventDefault(); if (value.trim()) add.mutate(); }}>
        <div className="space-y-1">
          <Label htmlFor="gr-kind">Type</Label>
          <NativeSelect id="gr-kind" value={kind} disabled={!enabled}
                        onChange={(e) => setKind(e.target.value as ExclusionKind)}>
            {KINDS.map((k) => <option key={k} value={k}>{KIND_LABELS[k]}</option>)}
          </NativeSelect>
        </div>
        <div className="space-y-1 sm:col-span-3">
          <Label htmlFor="gr-value">Value</Label>
          <Input id="gr-value" maxLength={320} value={value} disabled={!enabled}
                 placeholder={KIND_PLACEHOLDERS[kind]} onChange={(e) => setValue(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="gr-reason">Reason</Label>
          <NativeSelect id="gr-reason" value={reason} disabled={!enabled}
                        onChange={(e) => setReason(e.target.value as ExclusionReason)}>
            {REASONS.map((r) => <option key={r} value={r}>{REASON_LABELS[r]}</option>)}
          </NativeSelect>
        </div>
        <div className="flex items-end">
          <Button type="submit" size="sm" disabled={!enabled || add.isPending}>Add</Button>
        </div>
      </form>

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="gr-csv-reason">CSV rows without a reason are</Label>
          <NativeSelect id="gr-csv-reason" value={csvReason} disabled={!enabled}
                        onChange={(e) => setCsvReason(e.target.value as ExclusionReason)}>
            {REASONS.map((r) => <option key={r} value={r}>{REASON_LABELS[r]}</option>)}
          </NativeSelect>
        </div>
        <label className={`inline-flex h-8 cursor-pointer items-center gap-2 rounded border border-border px-3 text-xs ${
          enabled ? "hover:bg-muted" : "pointer-events-none opacity-50"}`}>
          <Upload size={14} aria-hidden="true" /> Import CSV
          <input type="file" accept=".csv,text/csv" className="sr-only" disabled={!enabled}
                 onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
        <Button type="button" size="sm" variant="outline" disabled={!enabled || sync.isPending}
                onClick={() => sync.mutate()}>
          {sync.isPending ? "Syncing…" : "Sync from CRM"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        CSV: a header row of <code>type,value,reason,note</code>, or columns named domain, company,
        email, phone, linkedin. CRM sync adds won customers and open deals from Vectorae, plus
        customers, open deals and churned accounts from a connected HubSpot or Salesforce. It never
        removes anything.
      </p>
      {failure && <p role="alert" className="text-sm text-destructive">{failure}</p>}
      {messages.length > 0 && (
        <ul className="space-y-0.5 text-sm" aria-live="polite">
          {messages.map((m, i) => <li key={i}>{m}</li>)}
        </ul>
      )}

      <div className="flex flex-wrap gap-2">
        <NativeSelect aria-label="Filter by type" className="w-40" value={kindFilter}
                      onChange={(e) => setKindFilter(e.target.value as ExclusionKind | "")}>
          <option value="">All types</option>
          {KINDS.map((k) => <option key={k} value={k}>{KIND_LABELS[k]}</option>)}
        </NativeSelect>
        <Input aria-label="Search exclusions" className="w-56" placeholder="Search…" value={q}
               maxLength={100} onChange={(e) => setQ(e.target.value)} />
        <span className="self-center text-xs text-muted-foreground">
          {list.data ? `${list.data.total} active` : ""}
        </span>
      </div>
      <ul className="divide-y divide-border text-sm">
        {list.data?.items.map((row) => (
          <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <div className="min-w-0">
              <p className="truncate font-medium">{row.display_value}</p>
              <p className="text-xs text-muted-foreground">
                {KIND_LABELS[row.kind]} · {reasonLabel(row.reason)} · {sourceLabel(row.source)}
                {row.note ? ` · ${row.note}` : ""}
              </p>
            </div>
            <Button size="sm" variant="ghost" aria-label={`Remove ${row.display_value}`}
                    disabled={!enabled || remove.isPending} onClick={() => remove.mutate(row.id)}>
              <Trash2 size={14} aria-hidden="true" />
            </Button>
          </li>
        ))}
        {list.data && list.data.items.length === 0 && (
          <li className="py-2 text-sm text-muted-foreground">Nothing excluded yet.</li>
        )}
      </ul>
    </section>
  );
}

function LibrarySection({ enabled, onChange }: { enabled: boolean; onChange: () => void }) {
  const [productId, setProductId] = useState<string>("");
  const [kind, setKind] = useState<LibraryKind>("forbidden_phrase");
  const [textValue, setTextValue] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");

  const products = useQuery({ queryKey: ["guardrails", "products"],
                              queryFn: listGuardrailProducts });
  const library = useQuery({ queryKey: ["guardrails", "library", productId],
                             queryFn: () => listLibrary(productId || null) });
  const add = useMutation({
    mutationFn: () => addLibraryEntry({ kind, text: textValue, product_id: productId || null,
                                        source_url: kind === "approved_claim" && sourceUrl
                                          ? sourceUrl : null }),
    onSuccess: () => { setTextValue(""); setSourceUrl(""); onChange(); },
  });
  const remove = useMutation({ mutationFn: removeLibraryEntry, onSuccess: onChange });
  const { claims, phrases } = splitLibrary(library.data?.items ?? []);
  const failure = errorText(add.error ?? remove.error);

  const renderList = (title: string, items: typeof claims, empty: string) => (
    <div className="space-y-1">
      <p className="text-xs font-medium uppercase text-muted-foreground">{title}</p>
      <ul className="divide-y divide-border text-sm">
        {items.map((entry) => (
          <li key={entry.id} className="flex items-start justify-between gap-2 py-1.5">
            <div className="min-w-0">
              <p>{entry.text}</p>
              <p className="text-xs text-muted-foreground">
                {entry.product_id ? "This product" : "Every product"}
                {entry.source_url ? <> · <a className="underline" href={entry.source_url}
                  target="_blank" rel="noreferrer noopener">source</a></> : null}
              </p>
            </div>
            <Button size="sm" variant="ghost" aria-label={`Remove ${entry.text}`}
                    disabled={!enabled || remove.isPending} onClick={() => remove.mutate(entry.id)}>
              <Trash2 size={14} aria-hidden="true" />
            </Button>
          </li>
        ))}
        {items.length === 0 && <li className="py-1.5 text-muted-foreground">{empty}</li>}
      </ul>
    </div>
  );

  return (
    <section className="space-y-3" aria-label="Claims and phrases library">
      <h4 className="text-sm font-semibold">Approved claims and forbidden phrases</h4>
      <p className="text-xs text-muted-foreground">
        A forbidden phrase blocks a sequence at review and any message that still contains it at
        send time. Once a product has approved claims, any claim about you (numbers, customer
        counts, “trusted by”, awards) that none of them backs is removed from that product’s
        messages before they send.
      </p>
      <div className="space-y-1">
        <Label htmlFor="gr-product">Product</Label>
        <NativeSelect id="gr-product" className="sm:w-72" value={productId}
                      onChange={(e) => setProductId(e.target.value)}>
          <option value="">Every product (account-wide)</option>
          {products.data?.items.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </NativeSelect>
      </div>
      <form className="grid gap-3 sm:grid-cols-6"
            onSubmit={(e) => { e.preventDefault(); if (textValue.trim()) add.mutate(); }}>
        <div className="space-y-1 sm:col-span-2">
          <Label htmlFor="gr-lib-kind">Add</Label>
          <NativeSelect id="gr-lib-kind" value={kind} disabled={!enabled}
                        onChange={(e) => setKind(e.target.value as LibraryKind)}>
            <option value="forbidden_phrase">Forbidden phrase</option>
            <option value="approved_claim">Approved claim</option>
          </NativeSelect>
        </div>
        <div className="space-y-1 sm:col-span-4">
          <Label htmlFor="gr-lib-text">Text</Label>
          <Input id="gr-lib-text" maxLength={2000} value={textValue} disabled={!enabled}
                 placeholder={kind === "approved_claim"
                   ? "We helped 40 agencies book 3x more meetings in 2025"
                   : "guaranteed results"}
                 onChange={(e) => setTextValue(e.target.value)} />
        </div>
        {kind === "approved_claim" && (
          <div className="space-y-1 sm:col-span-4">
            <Label htmlFor="gr-lib-url">Evidence link (optional)</Label>
            <Input id="gr-lib-url" maxLength={500} value={sourceUrl} disabled={!enabled}
                   placeholder="https://…" onChange={(e) => setSourceUrl(e.target.value)} />
          </div>
        )}
        <div className="flex items-end">
          <Button type="submit" size="sm" disabled={!enabled || add.isPending}>Add</Button>
        </div>
      </form>
      {failure && <p role="alert" className="text-sm text-destructive">{failure}</p>}
      <div className="grid gap-4 sm:grid-cols-2">
        {renderList("Approved claims", claims, "None — claims about you are not checked yet.")}
        {renderList("Forbidden phrases", phrases, "None.")}
      </div>
    </section>
  );
}
