"use client";

/** Settings > Integrations > LinkedIn — the accounts LinkedIn outreach
 *  rotates across, each with today's usage against its daily ceiling.
 *
 *  Connecting goes through Unipile's hosted page: the user signs in to
 *  LinkedIn THERE and Vectorae receives only an account id, never a LinkedIn
 *  password. Linking an existing Unipile account id is the fallback for
 *  deployments where the hosted callback cannot reach the API. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw, Trash2 } from "lucide-react";

import {
  deleteLinkedInAccount,
  linkedInConnectUrl,
  linkLinkedInAccount,
  listLinkedInAccounts,
  refreshLinkedInAccount,
  setLinkedInAccountActive,
  usageFraction,
  type LinkedInAccount,
} from "@/lib/api/linkedin";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";

export function LinkedInAccountsCard() {
  const toast = useToast();
  const qc = useQueryClient();
  const key = ["linkedin-accounts"];
  const query = useQuery({ queryKey: key, queryFn: listLinkedInAccounts, retry: false });
  const [accountId, setAccountId] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: key });
  const onError = (e: unknown) => toast((e as Error).message, "error");

  const connect = useMutation({
    mutationFn: linkedInConnectUrl,
    onSuccess: ({ url }) => { window.location.href = url; },
    onError,
  });
  const link = useMutation({
    mutationFn: () => linkLinkedInAccount(accountId.trim()),
    onSuccess: () => { setAccountId(""); refresh(); toast("LinkedIn account linked", "success"); },
    onError,
  });

  const accounts = query.data ?? [];
  return (
    <Card>
      <CardContent className="space-y-3 p-gutter">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium">LinkedIn</p>
            <p className="text-xs text-muted-foreground">
              Connection requests, messages and InMail. Sends rotate across your accounts so none
              exceeds its daily limit.
            </p>
          </div>
          <Button size="sm" disabled={connect.isPending} onClick={() => connect.mutate()}>
            <ExternalLink size={14} aria-hidden="true" /> Connect an account
          </Button>
        </div>

        {accounts.length > 0 && (
          <ul className="space-y-2">
            {accounts.map((account) => <AccountRow key={account.id} account={account} onChange={refresh} />)}
          </ul>
        )}

        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">
            Link an existing Unipile account id instead
          </summary>
          <div className="mt-2 flex gap-2">
            <Input aria-label="Unipile account id" placeholder="Unipile account id"
                   value={accountId} onChange={(e) => setAccountId(e.target.value)} />
            <Button size="sm" variant="outline" disabled={accountId.trim().length < 3 || link.isPending}
                    onClick={() => link.mutate()}>Link</Button>
          </div>
        </details>
      </CardContent>
    </Card>
  );
}

function UsageBar({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = Math.round(usageFraction(used, limit) * 100);
  return (
    <div className="min-w-32 flex-1">
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{label}</span>
        <span className="tabular-nums">{used}/{limit} today</span>
      </div>
      <div className="mt-1 h-1.5 rounded bg-muted" aria-hidden="true">
        <div className={`h-1.5 rounded ${pct >= 100 ? "bg-destructive" : "bg-[rgb(var(--primary))]"}`}
             style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function AccountRow({ account, onChange }: { account: LinkedInAccount; onChange: () => void }) {
  const toast = useToast();
  const onError = (e: unknown) => toast((e as Error).message, "error");
  const toggle = useMutation({
    mutationFn: () => setLinkedInAccountActive(account.id, !account.is_active),
    onSuccess: onChange, onError,
  });
  const refresh = useMutation({ mutationFn: () => refreshLinkedInAccount(account.id), onSuccess: onChange, onError });
  const remove = useMutation({ mutationFn: () => deleteLinkedInAccount(account.id), onSuccess: onChange, onError });

  return (
    <li className="space-y-2 rounded border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium">{account.display_name ?? account.unipile_account_id}</span>
          {account.has_premium && <Badge tone="accent">Premium</Badge>}
          {!account.is_active && <Badge>Paused</Badge>}
          {account.status !== "ok" && <Badge tone="warning">{account.status.replace(/_/g, " ")}</Badge>}
        </div>
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" disabled={refresh.isPending} onClick={() => refresh.mutate()}
                  aria-label="Refresh account details"><RefreshCw size={14} aria-hidden="true" /></Button>
          <Button size="sm" variant="outline" disabled={toggle.isPending} onClick={() => toggle.mutate()}>
            {account.is_active ? "Pause" : "Resume"}
          </Button>
          <Button size="sm" variant="ghost" disabled={remove.isPending} onClick={() => remove.mutate()}
                  aria-label="Remove account"><Trash2 size={14} aria-hidden="true" /></Button>
        </div>
      </div>
      <div className="flex flex-wrap gap-4">
        <UsageBar label="Connection requests" used={account.usage_today.connect} limit={account.limits.connect} />
        <UsageBar label="Messages + InMail" used={account.usage_today.message} limit={account.limits.message} />
      </div>
      {account.has_premium && (
        <p className="text-[11px] text-muted-foreground">
          InMail credits: {account.inmail_credits ?? "unknown"} · sent {account.inmail_sent_total}
        </p>
      )}
    </li>
  );
}
