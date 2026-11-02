"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useTheme } from "@/lib/theme/ThemeProvider";
import { saveTheme, uploadBackground } from "@/lib/api/themes";
import { useIntegrations, useSuppression } from "@/lib/api/hooks";
import { testConnection, addSuppression, gmailAuthUrl } from "@/lib/api/integrations";
import { PRESETS, PRESET_LABELS } from "@/lib/theme/presets";
import { DEFAULT_THEME } from "@/lib/theme/types";
import type { Theme } from "@/lib/theme/types";
import { themeContrastWarnings, suggestPassingColor } from "@/lib/theme/contrast";
import { AsyncState, Skeleton } from "@/components/ui/skeleton";
import { Badge, statusTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { useToast } from "@/components/ui/toast";
import { VoiceSettings } from "@/components/settings/VoiceSettings";
import { VoiceTwinCard } from "@/components/settings/VoiceTwinCard";
import { SlackCard } from "@/components/settings/SlackCard";
import { CrmCards } from "@/components/settings/CrmCards";
import { ApiKeysCard, WebhooksCard } from "@/components/settings/WebhooksCard";
import { oauthResultMessage } from "@/lib/api/ecosystem";
import { ComplianceAuditCard, DeliverabilityCard } from "@/components/settings/DeliverabilityCard";
import { MailboxHealthCard } from "@/components/settings/MailboxHealthCard";
import { AuditTrailCard } from "@/components/settings/AuditTrailCard";
import { GuardrailsCard } from "@/components/settings/GuardrailsCard";
import { ShareLinksCard } from "@/components/settings/ShareLinksCard";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/lib/stores/authStore";
import {
  SETTINGS_TABS, resolveSettingsTab, visibleSettingsTabs, type SettingsTab,
} from "@/lib/settings-tabs";

// ------------------------------------------------------------------ Theme --

function ColorPicker({ label, value, onChange }: {
  label: string; value: string; onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      <div className="flex items-center gap-2">
        <input type="color" value={value}
               onChange={e => onChange(e.target.value)}
               className="h-9 w-12 cursor-pointer rounded border border-border p-0.5"
               aria-label={label} />
        <Input className="w-28 font-mono text-sm" value={value}
               onChange={e => onChange(e.target.value)} maxLength={7} />
      </div>
    </div>
  );
}

const GOOGLE_FONTS = [
  "Inter", "Lora", "Merriweather", "Nunito", "Outfit", "Playfair Display",
  "Poppins", "Raleway", "Roboto", "Sora", "Space Grotesk",
];

function AppearanceSettings() {
  const { theme, setTheme } = useTheme();
  const toast = useToast();
  const [draft, setDraft] = useState<Theme>(theme);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const warnings: string[] = themeContrastWarnings(draft);

  const update = (patch: Partial<Theme>) => {
    const next: Theme = { ...draft, ...patch, preset: null, contrast_warnings: [] as string[] };
    next.contrast_warnings = themeContrastWarnings(next);
    setDraft(next);
    setTheme(next); // live preview
  };

  async function handleBgUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const url = await uploadBackground(file);
      update({ background_image_url: url });
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setUploading(false);
    }
  }

  async function save() {
    setSaving(true);
    try {
      await saveTheme(draft);
      toast("Theme saved", "success");
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setSaving(false);
    }
  }

  function applyPreset(key: string) {
    const p = PRESETS[key];
    if (!p) return;
    setDraft(p);
    setTheme(p);
  }

  function reset() {
    setDraft(theme);
    setTheme(theme);
  }

  return (
    <div className="space-y-6">
      {/* Preset picker */}
      <div className="space-y-2">
        <Label>Preset</Label>
        <div className="flex flex-wrap gap-2">
          {Object.entries(PRESET_LABELS).map(([key, label]) => (
            <button
              key={key}
              onClick={() => applyPreset(key)}
              aria-pressed={draft.preset === key}
              className={cn(
                "rounded border px-3 py-1.5 text-sm transition-colors",
                draft.preset === key
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border bg-card hover:bg-muted",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Color pickers */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <ColorPicker label="Background" value={draft.background_color}
                     onChange={v => update({ background_color: v })} />
        <ColorPicker label="Primary" value={draft.primary_color}
                     onChange={v => update({ primary_color: v })} />
        <ColorPicker label="Accent" value={draft.accent_color}
                     onChange={v => update({ accent_color: v })} />
      </div>

      {/* WCAG AA warnings */}
      {warnings.length > 0 && (
        <div role="alert" className="space-y-2 rounded border border-warning bg-card p-4">
          <p className="text-sm font-medium text-warning">Contrast warnings</p>
          {warnings.map((w, i) => {
            const match = w.match(/fails WCAG AA — ([\d.]+):1/);
            const ratio = match ? parseFloat(match[1]) : null;
            const suggested = ratio && ratio < 4.5
              ? suggestPassingColor(draft.primary_color, draft.background_color)
              : null;
            return (
              <div key={i} className="text-xs text-muted-foreground">
                {w}
                {suggested && (
                  <span> — suggestion:{" "}
                    <button onClick={() => update({ primary_color: suggested })}
                            className="underline">
                      try {suggested}
                    </button>
                  </span>
                )}
              </div>
            );
          })}
          <p className="text-xs text-muted-foreground">
            This is a warning only — you can save this theme, and the
            warnings are recorded with it.
          </p>
        </div>
      )}

      {/* Background image */}
      <div className="space-y-2">
        <Label>Background image</Label>
        <div className="flex flex-wrap items-center gap-3">
          {draft.background_image_url && (
            <div className="h-16 w-24 overflow-hidden rounded border border-border">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={draft.background_image_url} alt="Background preview"
                   className="h-full w-full object-cover" />
            </div>
          )}
          <label className={cn(
            "cursor-pointer rounded border border-border px-3 py-1.5 text-sm hover:bg-muted",
            uploading && "opacity-50",
          )}>
            {uploading ? "Uploading…" : "Upload image"}
            <input type="file" accept="image/png,image/jpeg,image/webp"
                   className="sr-only" onChange={handleBgUpload}
                   disabled={uploading} />
          </label>
          {draft.background_image_url && (
            <Button variant="ghost" size="sm"
                    onClick={() => update({ background_image_url: null })}>
              Remove
            </Button>
          )}
        </div>
        {draft.background_image_url && (
          <Slider label="Readability overlay (dim)"
                  value={draft.background_overlay} min={0} max={0.9} step={0.05}
                  onChange={v => update({ background_overlay: v })}
                  format={v => `${Math.round(v * 100)}%`} />
        )}
      </div>

      {/* Typography */}
      <div className="space-y-4">
        <div className="space-y-1">
          <Label htmlFor="font">Font family</Label>
          <select id="font" value={draft.font_family}
                  onChange={e => update({ font_family: e.target.value })}
                  className="rounded border border-border bg-card px-2 py-1.5 text-sm">
            {GOOGLE_FONTS.map(f => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </div>
        <Slider label="Font size scale" value={draft.font_size_scale}
                min={0.8} max={1.4} step={0.05}
                onChange={v => update({ font_size_scale: v })}
                format={v => `${Math.round(v * 100)}%`} />
        <Slider label="Border radius" value={draft.radius_px}
                min={0} max={24} step={1}
                onChange={v => update({ radius_px: v })}
                format={v => `${v}px`} />
        <div className="space-y-1">
          <Label>Density</Label>
          <div className="flex gap-3">
            {(["comfortable", "compact"] as const).map(d => (
              <label key={d} className="flex items-center gap-2 text-sm capitalize">
                <input type="radio" name="density" checked={draft.density === d}
                       onChange={() => update({ density: d })} />
                {d}
              </label>
            ))}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save theme"}
        </Button>
        <Button variant="outline" onClick={reset}>Reset</Button>
        <Button variant="ghost" onClick={() => {
          const base = PRESETS["light"];
          setDraft(base);
          setTheme(base);
        }}>Restore Light preset</Button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------- Integrations

function IntegrationsSettings() {
  const toast = useToast();
  const { data, isLoading, error } = useIntegrations();
  const [testing, setTesting] = useState<string | null>(null);

  async function test(provider: string) {
    setTesting(provider);
    try {
      const r = await testConnection(provider);
      toast(`${provider}: ${r.healthy ? "healthy ✓" : "unhealthy ✗"}`,
            r.healthy ? "success" : "error");
    } catch {
      toast(`${provider}: connection test failed`, "error");
    } finally {
      setTesting(null);
    }
  }

  async function connectGmail() {
    try {
      const { auth_url } = await gmailAuthUrl();
      if (!auth_url) throw new Error("Gmail connection is not configured on the server");
      window.location.href = auth_url;
    } catch (err) {
      toast((err as Error).message, "error");
    }
  }

  return (
    <AsyncState isLoading={isLoading} error={error}>
      {data && (
        <div className="space-y-3">
          {/* Gmail */}
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 p-gutter">
              <div>
                <p className="font-medium text-sm">Gmail</p>
                <p className="text-xs text-muted-foreground">
                  {data.gmail.connected
                    ? `Connected: ${data.gmail.email}`
                    : "Not connected"}
                </p>
                {data.gmail.connected && data.gmail.calendar_access === "missing" && (
                  <p role="status" className="mt-1 text-xs text-[rgb(var(--warning))]">
                    Calendar access missing — reconnect to create Google Meet links.
                    Your Gmail access is kept.
                  </p>
                )}
              </div>
              <div className="flex gap-2">
                {data.gmail.connected && (
                  <Button size="sm" variant="outline" disabled={testing === "gmail"}
                          onClick={() => test("gmail")}>
                    {testing === "gmail" ? "Testing…" : "Test"}
                  </Button>
                )}
                <Button size="sm" onClick={connectGmail}>
                  {!data.gmail.connected
                    ? "Connect"
                    : data.gmail.calendar_access === "missing"
                      ? "Reconnect for Calendar"
                      : "Reconnect"}
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* WhatsApp */}
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 p-gutter">
              <div>
                <p className="font-medium text-sm">WhatsApp Business</p>
                <p className="text-xs text-muted-foreground">
                  {data.whatsapp.configured
                    ? `Phone number ID: ${data.whatsapp.phone_number_id}`
                    : "Not configured — set WHATSAPP_* env vars on the server"}
                </p>
              </div>
              {data.whatsapp.configured && (
                <Button size="sm" variant="outline" disabled={testing === "whatsapp"}
                        onClick={() => test("whatsapp")}>
                  {testing === "whatsapp" ? "Testing…" : "Test"}
                </Button>
              )}
            </CardContent>
          </Card>

          {/* Apollo */}
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 p-gutter">
              <div>
                <p className="font-medium text-sm">Apollo.io</p>
                <p className="text-xs text-muted-foreground font-mono">
                  {data.apollo.key_set ? data.apollo.masked : "Key not set"}
                </p>
              </div>
              {data.apollo.key_set && (
                <Button size="sm" variant="outline" disabled={testing === "apollo"}
                        onClick={() => test("apollo")}>
                  {testing === "apollo" ? "Testing…" : "Test"}
                </Button>
              )}
            </CardContent>
          </Card>

          {/* Hunter */}
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 p-gutter">
              <div>
                <p className="font-medium text-sm">Hunter.io</p>
                <p className="text-xs text-muted-foreground font-mono">
                  {data.hunter.key_set ? data.hunter.masked : "Key not set"}
                </p>
              </div>
              {data.hunter.key_set && (
                <Button size="sm" variant="outline" disabled={testing === "hunter"}
                        onClick={() => test("hunter")}>
                  {testing === "hunter" ? "Testing…" : "Test"}
                </Button>
              )}
            </CardContent>
          </Card>

          {/* LinkedIn (Feature Group 5) */}

          {/* Feature Group 4: Slack, HubSpot, Salesforce */}
          <SlackCard />
          <CrmCards />

          {/* Calendly */}
          <Card>
            <CardContent className="p-gutter">
              <p className="font-medium text-sm">Calendly</p>
              <p className="text-xs text-muted-foreground">
                {data.calendly.token_set
                  ? "API token configured"
                  : "Not configured — set CALENDLY_API_TOKEN env var"}
              </p>
            </CardContent>
          </Card>

          {/* Feature Group 4: Zapier / Make */}
          <WebhooksCard />
          <ApiKeysCard />
        </div>
      )}
    </AsyncState>
  );
}

// --------------------------------------------------------------- Suppression

function SuppressionViewer() {
  const toast = useToast();
  const qc = useQueryClient();
  const { data, isLoading, error } = useSuppression();
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");

  async function add() {
    if (!email && !phone) return;
    try {
      await addSuppression({ email: email || undefined, phone: phone || undefined, reason: 'manual' });
      toast("Added to suppression list (permanent)", "success");
      qc.invalidateQueries({ queryKey: ["suppression"] });
      setEmail(""); setPhone("");
    } catch (err) {
      toast((err as Error).message, "error");
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><CardTitle className="text-sm">Add entry</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input placeholder="Email" value={email}
                   onChange={e => setEmail(e.target.value)} />
            <Input placeholder="Phone (E.164)" value={phone}
                   onChange={e => setPhone(e.target.value)} />
            <Button onClick={add} disabled={!email && !phone}>Add</Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Suppression entries are permanent by design — there is no delete
            endpoint. This is intentional compliance behaviour.
          </p>
        </CardContent>
      </Card>
      <AsyncState isLoading={isLoading} error={error}
                  empty={!data?.entries.length}
                  emptyLabel="Suppression list is empty.">
        <div className="overflow-x-auto rounded border border-border">
          <table className="w-full text-sm" aria-label="Suppression list">
            <thead className="border-b border-border bg-muted/40">
              <tr>
                <th className="px-3 py-2 text-left text-xs text-muted-foreground">Email</th>
                <th className="px-3 py-2 text-left text-xs text-muted-foreground">Phone</th>
                <th className="px-3 py-2 text-left text-xs text-muted-foreground">Reason</th>
                <th className="px-3 py-2 text-left text-xs text-muted-foreground">When</th>
              </tr>
            </thead>
            <tbody>
              {data?.entries.map((e, i) => (
                <tr key={i} className="border-b border-border/40">
                  <td className="px-3 py-1.5">{e.email ?? "—"}</td>
                  <td className="px-3 py-1.5 font-mono text-xs">{e.phone ?? "—"}</td>
                  <td className="px-3 py-1.5">{e.reason}</td>
                  <td className="px-3 py-1.5 text-xs text-muted-foreground">
                    {e.ts ? new Date(e.ts).toLocaleDateString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </AsyncState>
    </div>
  );
}

// -------------------------------------------------------------------  Main

export default function SettingsPage() {
  // Feature Group 4: the Gmail / Slack / HubSpot / Salesforce OAuth callbacks land on
  // /settings?tab=integrations&<provider>=connected|error.
  const params = useSearchParams();
  const toast = useToast();
  const requested = params.get("tab") as SettingsTab | null;
  const [selected, setTab] = useState<SettingsTab>(
    requested && SETTINGS_TABS.includes(requested) ? requested : "appearance",
  );
  // F-SEC-04: GET/POST /suppression are admin-only on the server, so the
  // Suppression tab is hidden from everyone else (same check the admin
  // layout uses). `tab` is what is actually shown: a non-admin who follows
  // ?tab=suppression lands on the default tab, and SuppressionViewer -- and
  // the request it makes -- never mounts for them.
  const { user } = useAuthStore();
  const tabs = visibleSettingsTabs(user?.is_admin);
  const tab = resolveSettingsTab(selected, user?.is_admin);
  useEffect(() => {
    const result = oauthResultMessage(params);
    if (result) toast(result.text, result.ok ? "success" : "error");
    // Once per landing -- the params do not change while the page is open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Settings</h1>
      <div className="flex gap-2 border-b border-border">
        {tabs.map(t => (
          <button key={t} onClick={() => setTab(t)}
                  className={`pb-2 text-sm font-medium capitalize transition-colors border-b-2 ${
                    tab === t
                      ? "border-primary text-[rgb(var(--primary))]"
                      : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}>
            {t}
          </button>
        ))}
      </div>
      {tab === "appearance" && <AppearanceSettings />}
      {/* Feature Group 2: the voice every outreach message is written in. */}
      {tab === "voice" && (
        <div className="space-y-6">
          <VoiceSettings />
          {/* Voice twin: your cloned voice on AI calls, AI answering, take-over. */}
          <VoiceTwinCard />
        </div>
      )}
      {tab === "integrations" && <IntegrationsSettings />}
      {/* Feature Group 9: email health + the compliance audit log. */}
      {tab === "deliverability" && (
        <div className="space-y-3">
          {/* Part 1 Feature 4: per-MAILBOX health and the auto-throttle,
              above the per-domain card because it is the one that acts. */}
          <MailboxHealthCard />
          <DeliverabilityCard />
          <ComplianceAuditCard />
          {/* F05: exclusion lists + approved-claims / forbidden-phrases library. */}
          <GuardrailsCard />
          {/* Feature A2: the hash-chained activity trail + signed export. */}
          <AuditTrailCard />
        </div>
      )}
      {/* Feature A6: client-facing ROI dashboard links. */}
      {tab === "sharing" && <ShareLinksCard />}
      {tab === "suppression" && <SuppressionViewer />}
    </div>
  );
}
