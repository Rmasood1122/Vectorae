"use client";

/** Profile: display name, plan (with the online Cancel plan button), password,
 *  download your data and delete the account (GDPR Art. 15/20 and 17). */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { changePassword, deleteMyAccount, exportMyData, getMe, saveName } from "@/lib/api/profile";
import { clearSession } from "@/lib/api/client";
import { resetAuthStore } from "@/lib/stores/authStore";
import {
  DELETE_ACCOUNT_COPY, DELETE_CONFIRMATION, accountExportFilename, canDeleteAccount,
  deleteAccountErrorMessage, exportErrorMessage, passwordErrorMessage, validateName,
  validatePasswordChange,
} from "@/lib/profile";
import { PlanCard } from "@/components/billing/PlanCard";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";

export default function ProfilePage() {
  const router = useRouter();
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const [name, setName] = useState("");
  const [nameMsg, setNameMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [pwError, setPwError] = useState<string | null>(null);
  const [exportPw, setExportPw] = useState("");
  const [exportMsg, setExportMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [deletePw, setDeletePw] = useState("");
  const [deleteTyped, setDeleteTyped] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => { if (me.data) setName(me.data.full_name ?? ""); }, [me.data]);

  const saveMut = useMutation({
    mutationFn: saveName,
    onSuccess: (u) => {
      resetAuthStore();
      qc.setQueryData(["me"], u);
      setNameMsg({ ok: true, text: "Saved." });
    },
    onError: () => setNameMsg({ ok: false, text: "Could not save. Try again." }),
  });

  const pwMut = useMutation({
    mutationFn: () => changePassword(cur, next),
    // The server ended every session, including this one.
    onSuccess: () => {
      clearSession();
      resetAuthStore();
      router.replace("/login");
    },
    onError: (e) => setPwError(passwordErrorMessage(e)),
  });

  const exportMut = useMutation({
    mutationFn: () => exportMyData(exportPw),
    onSuccess: (data) => {
      const blob = new Blob([JSON.stringify(data, null, 1)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = accountExportFilename();
      link.click();
      URL.revokeObjectURL(url);
      setExportPw("");
      setExportMsg({ ok: true, text: "Your data has been downloaded." });
    },
    onError: (e) => setExportMsg({ ok: false, text: exportErrorMessage(e) }),
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteMyAccount(deletePw, deleteTyped),
    // The account no longer exists: every session was ended server-side.
    onSuccess: () => {
      clearSession();
      resetAuthStore();
      qc.clear();
      router.replace("/login");
    },
    onError: (e) => setDeleteError(deleteAccountErrorMessage(e)),
  });

  if (me.isLoading) return <p className="p-6 text-sm">Loading…</p>;
  if (me.isError || !me.data) return <p className="p-6 text-sm">Could not load your profile.</p>;

  return (
    <div className="mx-auto max-w-2xl space-y-6 p-4 md:p-6">
      <h1 className="text-xl font-semibold">Profile</h1>

      <Card>
        <CardHeader><CardTitle>Your name</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div>
            <Label htmlFor="profile-email">Email</Label>
            <Input id="profile-email" value={me.data.email} disabled readOnly />
          </div>
          <div>
            <Label htmlFor="profile-name">Name</Label>
            <Input id="profile-name" value={name} maxLength={120}
                   onChange={(e) => { setName(e.target.value); setNameMsg(null); }} />
          </div>
          {nameMsg && (
            <p role={nameMsg.ok ? "status" : "alert"}
               className={`text-sm ${nameMsg.ok ? "" : "text-red-600"}`}>{nameMsg.text}</p>
          )}
          <Button disabled={saveMut.isPending}
                  onClick={() => {
                    const problem = validateName(name);
                    if (problem) { setNameMsg({ ok: false, text: problem }); return; }
                    saveMut.mutate(name);
                  }}>
            Save name
          </Button>
        </CardContent>
      </Card>

      <PlanCard plan={me.data.plan} />

      <Card>
        <CardHeader><CardTitle>Change password</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div>
            <Label htmlFor="pw-cur">Current password</Label>
            <Input id="pw-cur" type="password" autoComplete="current-password"
                   value={cur} onChange={(e) => setCur(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="pw-new">New password</Label>
            <Input id="pw-new" type="password" autoComplete="new-password"
                   value={next} onChange={(e) => setNext(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="pw-again">Repeat new password</Label>
            <Input id="pw-again" type="password" autoComplete="new-password"
                   value={again} onChange={(e) => setAgain(e.target.value)} />
          </div>
          <p className="text-xs text-muted-foreground">
            Changing your password signs you out on every device and revokes your
            personal API keys.
          </p>
          {pwError && <p role="alert" className="text-sm text-red-600">{pwError}</p>}
          <Button disabled={pwMut.isPending}
                  onClick={() => {
                    const problem = validatePasswordChange(cur, next, again);
                    if (problem) { setPwError(problem); return; }
                    setPwError(null);
                    pwMut.mutate();
                  }}>
            Change password
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Download your data</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            One JSON file with everything this account holds: profile, products, strategies,
            leads, messages, meetings and settings. Passwords and connection secrets are
            left out.
          </p>
          <div>
            <Label htmlFor="export-pw">Confirm with your password</Label>
            <Input id="export-pw" type="password" autoComplete="current-password"
                   value={exportPw}
                   onChange={(e) => { setExportPw(e.target.value); setExportMsg(null); }} />
          </div>
          {exportMsg && (
            <p role={exportMsg.ok ? "status" : "alert"}
               className={`text-sm ${exportMsg.ok ? "" : "text-red-600"}`}>{exportMsg.text}</p>
          )}
          <Button disabled={exportMut.isPending || !exportPw}
                  onClick={() => { setExportMsg(null); exportMut.mutate(); }}>
            {exportMut.isPending ? "Preparing…" : "Download my data"}
          </Button>
        </CardContent>
      </Card>

      <Card className="border-red-600/60">
        <CardHeader><CardTitle className="text-red-600">Delete account</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm">{DELETE_ACCOUNT_COPY.erased}</p>
          <div className="text-sm text-muted-foreground">
            <p>Kept after deletion:</p>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              {DELETE_ACCOUNT_COPY.kept.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </div>
          <div className="text-sm text-muted-foreground">
            <p>Outside Vectorae&apos;s database:</p>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              {DELETE_ACCOUNT_COPY.outside.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </div>
          <p className="text-sm text-muted-foreground">{DELETE_ACCOUNT_COPY.workspace}</p>
          <p className="text-sm text-muted-foreground">
            Want a copy first? Use Download your data above before deleting.
          </p>
          <div>
            <Label htmlFor="delete-pw">Confirm with your password</Label>
            <Input id="delete-pw" type="password" autoComplete="current-password"
                   value={deletePw}
                   onChange={(e) => { setDeletePw(e.target.value); setDeleteError(null); }} />
          </div>
          <div>
            <Label htmlFor="delete-typed">Type {DELETE_CONFIRMATION} to confirm</Label>
            <Input id="delete-typed" autoComplete="off" value={deleteTyped}
                   onChange={(e) => { setDeleteTyped(e.target.value); setDeleteError(null); }} />
          </div>
          {deleteError && <p role="alert" className="text-sm text-red-600">{deleteError}</p>}
          <Button variant="destructive"
                  disabled={deleteMut.isPending || !canDeleteAccount(deletePw, deleteTyped)}
                  onClick={() => { setDeleteError(null); deleteMut.mutate(); }}>
            {deleteMut.isPending ? "Deleting…" : "Delete my account permanently"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
