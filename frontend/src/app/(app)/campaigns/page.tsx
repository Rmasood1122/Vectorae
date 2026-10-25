"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listStrategies } from "@/lib/api/strategies";
import { useCampaign, useSequences } from "@/lib/api/hooks";
import { pauseCampaign, resumeCampaign,
         updateFollowupSettings } from "@/lib/api/campaigns";
import type { SequenceOut, SequenceStepOut, StrategyOut } from "@/lib/api/types";
import { AsyncState } from "@/components/ui/skeleton";
import { Badge, statusTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { StrategyCalls } from "@/components/calls/CallHistory";
import { FunnelHeatmap } from "@/components/campaigns/FunnelHeatmap";
import { ReengagementPanel } from "@/components/campaigns/ReengagementPanel";
import { SendTimePanel } from "@/components/campaigns/SendTimePanel";
import { TemplatesPanel } from "@/components/campaigns/TemplatesPanel";
import { useAuthStore } from "@/lib/stores/authStore";
import { SentimentTrend } from "@/components/campaigns/SentimentTrend";
import { SequenceReviewPanel } from "@/components/campaigns/SequenceReviewPanel";
import { ReviewQueuePanel } from "@/components/campaigns/ReviewQueuePanel";
import { planHoldNotice } from "@/lib/planHolds";
import { pct } from "@/lib/utils";
import { bounceOverThreshold, bouncePauseText } from "@/lib/campaignBounce";

// --------------------------------------------------------------------------
// Strategy selector
// --------------------------------------------------------------------------
function useStrategyList() {
  return useQuery({ queryKey: ["strategies"], queryFn: listStrategies });
}

// --------------------------------------------------------------------------
// Per-channel stats card
// --------------------------------------------------------------------------
function ChannelCard({ label, stats }: {
  label: string;
  stats: {
    sent_total: number; delivery_rate: number | null; reply_rate: number | null;
    sends_today?: number | null; daily_cap_today?: number | null;
    window_open_count?: number; needs_template_count?: number;
  };
}) {
  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">{label}</CardTitle></CardHeader>
      <CardContent className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <p className="text-xs text-muted-foreground">Sent total</p>
          <p className="font-semibold">{stats.sent_total}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Delivery</p>
          <p className="font-semibold">{pct(stats.delivery_rate)}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Reply rate</p>
          <p className="font-semibold">{pct(stats.reply_rate)}</p>
        </div>
        {stats.sends_today != null && (
          <div>
            <p className="text-xs text-muted-foreground">Today / cap</p>
            <p className="font-semibold">{stats.sends_today} / {stats.daily_cap_today ?? "—"}</p>
          </div>
        )}
        {stats.window_open_count != null && (
          <div>
            <p className="text-xs text-muted-foreground">Windows open</p>
            <p className="font-semibold">{stats.window_open_count}</p>
          </div>
        )}
        {stats.needs_template_count != null && stats.needs_template_count > 0 && (
          <div className="col-span-2">
            <Badge tone="warning">{stats.needs_template_count} need template</Badge>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Sequence viewer + per-step follow-up settings (Engagement Hub, Feature 1)
// --------------------------------------------------------------------------

/** The toggle and delay for ONE step's automatic follow-up.
 *
 *  WHY THE COPY LABOURS THE DIFFERENCE FROM delay_days: they are two timers on
 *  the same row, and confusing them produces exactly the wrong behaviour.
 *  `delay_days` is "wait this long after this step sends, then send the next
 *  one", and it runs whatever the lead does. This is "the lead never replied
 *  to this step - now what", and it is the only one of the two that can fire
 *  on a sequence that has otherwise stalled.
 *
 *  Saved on toggle and on blur rather than behind a Save button: two controls
 *  with their own Save is more chrome than the setting deserves, and the
 *  failure case is visible - a toast, and the values revert. */
function FollowupSettings({
  sequenceId,
  step,
}: {
  sequenceId: string;
  step: SequenceStepOut;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const [enabled, setEnabled] = useState(step.followup_enabled ?? true);
  const [hours, setHours] = useState(String(step.followup_delay_hours ?? 72));

  const save = useMutation({
    mutationFn: (settings: { enabled?: boolean; delay_hours?: number }) =>
      updateFollowupSettings(sequenceId, step.step_no, settings),
    onSuccess: (result) => {
      // Trust the server's echo rather than the local state: it applies the
      // 1..2160 clamp, so a value the input allowed and the API rejected does
      // not stay on screen looking saved.
      setEnabled(result.followup_enabled);
      setHours(String(result.followup_delay_hours));
      qc.invalidateQueries({ queryKey: ["sequences"] });
    },
    onError: (e) => {
      setEnabled(step.followup_enabled ?? true);
      setHours(String(step.followup_delay_hours ?? 72));
      toast((e as Error).message, "error");
    },
  });

  const commitHours = () => {
    const parsed = Number(hours);
    if (!Number.isFinite(parsed) || parsed < 1) {
      setHours(String(step.followup_delay_hours ?? 72));
      return;
    }
    if (parsed === (step.followup_delay_hours ?? 72)) return;
    save.mutate({ delay_hours: Math.round(parsed) });
  };

  return (
    <div className="mt-1 flex flex-wrap items-center gap-3 rounded border border-border/60 bg-muted/30 px-2 py-1.5">
      <label className="flex items-center gap-1.5 text-xs">
        <input
          type="checkbox"
          checked={enabled}
          disabled={save.isPending}
          onChange={(e) => {
            setEnabled(e.target.checked);
            save.mutate({ enabled: e.target.checked });
          }}
          aria-label={"Automatic follow-up for step " + step.step_no}
          className="h-3.5 w-3.5 accent-[rgb(var(--primary))]"
        />
        Auto follow-up
      </label>

      <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
        after
        <input
          type="number"
          min={1}
          max={2160}
          value={hours}
          disabled={!enabled || save.isPending}
          onChange={(e) => setHours(e.target.value)}
          onBlur={commitHours}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
          }}
          aria-label={"Follow-up delay in hours for step " + step.step_no}
          className="h-7 w-16 rounded border border-border bg-card px-2 text-xs tabular-nums disabled:opacity-50"
        />
        hours of silence
      </label>
    </div>
  );
}

function SequencesPanel({ strategyId }: { strategyId: string }) {
  const { data, isLoading, error } = useSequences(strategyId);
  return (
    <AsyncState isLoading={isLoading} error={error}
                empty={!data?.length} emptyLabel="No sequences configured.">
      <div className="space-y-2">
        {data?.map((seq: SequenceOut) => (
          <Card key={seq.id}>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle className="text-sm">{seq.name}</CardTitle>
              <div className="flex items-center gap-2">
                <Badge>{seq.channel}</Badge>
                <Badge tone={statusTone(seq.status)}>{seq.status}</Badge>
              </div>
            </CardHeader>
            <CardContent>
              <ol className="space-y-2 text-xs text-muted-foreground">
                {seq.steps.map((s) => (
                  <li key={s.step_no} className="flex items-start gap-2">
                    <span className="w-5 shrink-0 font-medium text-foreground">
                      {s.step_no}.
                    </span>
                    <div className="min-w-0 flex-1">
                      <span>
                        {s.channel ?? seq.channel}
                        {s.whatsapp_kind ? " (" + s.whatsapp_kind + ")" : ""}
                        {s.linkedin_action ? " (" + s.linkedin_action + ")" : ""}
                        {s.delay_days > 0 ? " — +" + s.delay_days + "d" : " — day 0"}
                        {s.variant !== "A" ? " [" + s.variant + "]" : ""}
                      </span>
                      <FollowupSettings sequenceId={seq.id} step={s} />
                    </div>
                  </li>
                ))}
              </ol>
              <p className="mt-2 text-[11px] text-muted-foreground">
                The step delay above schedules the NEXT step once this one
                sends. Auto follow-up is separate: it fires when this step gets
                no reply and nothing else is queued for that lead.
              </p>
              {/* Feature A7: the red-team review that gates this sequence's launch. */}
              <div className="mt-3">
                <SequenceReviewPanel sequenceId={seq.id} />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </AsyncState>
  );
}

// --------------------------------------------------------------------------
// Main page
// --------------------------------------------------------------------------
// "review" is Part 1 Feature 5: messages held for explicit human approval.
// It sits beside the campaign tabs rather than inside a strategy, because a
// held message belongs to the person who has to approve it, not to one
// campaign's dashboard.
const TABS = ["overview", "sequences", "templates", "review", "funnel", "sentiment", "calls"] as const;
type Tab = (typeof TABS)[number];

export default function CampaignsPage() {
  const toast = useToast();
  const qc = useQueryClient();
  const { user } = useAuthStore();
  // Feature Group 3: alerts deep-link here as /campaigns?strategy=<id>&tab=<tab>
  // (the static export cannot serve /campaigns/<id>).
  const params = useSearchParams();
  const initialTab = params.get("tab");
  const [strategyId, setStrategyId] = useState<string>(params.get("strategy") ?? "");
  const [tab, setTab] = useState<Tab>(
    TABS.includes(initialTab as Tab) ? (initialTab as Tab) : "overview",
  );
  const { data: strats } = useStrategyList();
  const { data: campaign, isLoading: camLoading, error: camError } = useCampaign(strategyId);

  const { mutate: pause } = useMutation({
    mutationFn: () => pauseCampaign(strategyId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["campaign"] }); toast("Campaign paused", "info"); },
    onError: (e) => toast((e as Error).message, "error"),
  });
  const { mutate: resume } = useMutation({
    mutationFn: () => resumeCampaign(strategyId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["campaign"] }); toast("Campaign resumed", "success"); },
    onError: (e) => toast((e as Error).message, "error"),
  });

  const effectiveStratId = strategyId || (strats?.[0]?.id ?? "");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Campaigns</h1>
        <select
          className="rounded border border-border bg-card px-2 py-1 text-sm"
          value={effectiveStratId}
          onChange={e => setStrategyId(e.target.value)}
          aria-label="Select strategy"
        >
          <option value="">Select strategy…</option>
          {strats?.map(s => (
            <option key={s.id} value={s.id}>
              {(s as unknown as { product_name?: string }).product_name ?? s.id.slice(0, 8)}
            </option>
          ))}
        </select>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-border">
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)}
                  className={`pb-2 text-sm font-medium capitalize transition-colors border-b-2 ${
                    tab === t ? "border-primary text-[rgb(var(--primary))]"
                              : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}>
            {t}
          </button>
        ))}
      </div>

      {tab === "overview" && effectiveStratId && (
        <AsyncState isLoading={camLoading} error={camError}
                    empty={!campaign} emptyLabel="No campaign data yet.">
          {campaign && (
            <div className="space-y-4">
              {/* Pause/resume banner */}
              {(campaign.campaign_state === "paused_bounce_rate" ||
                campaign.campaign_state === "paused_manual" ||
                // Feature Group 9: sending domain found on a blocklist.
                campaign.campaign_state === "paused_blacklist") && (
                <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded border border-warning bg-card p-4 text-sm">
                  <div>
                    <p className="font-medium">Campaign paused</p>
                    <p className="text-muted-foreground">
                      {campaign.campaign_state === "paused_bounce_rate"
                        ? bouncePauseText(campaign)
                        : campaign.campaign_pause_reason ?? "Manually paused"}
                    </p>
                  </div>
                  <Button onClick={() => resume()} variant="outline">Resume</Button>
                </div>
              )}

              {/* Messages held by the send-time plan check */}
              {planHoldNotice(campaign.plan_holds) && (
                <div role="status" data-testid="plan-hold-notice"
                     className="rounded border border-warning bg-card p-4 text-sm">
                  <p className="font-medium">Some messages are on hold</p>
                  <p className="text-muted-foreground">{planHoldNotice(campaign.plan_holds)}</p>
                </div>
              )}

              {/* Per-channel cards */}
              <div className="grid gap-3 sm:grid-cols-2">
                <ChannelCard label="Gmail" stats={campaign.channels.email} />
                <ChannelCard label="WhatsApp" stats={campaign.channels.whatsapp} />
              </div>

              {/* Aggregate stats */}
              <Card>
                <CardContent className="grid grid-cols-2 gap-3 p-gutter text-sm sm:grid-cols-4">
                  <div>
                    <p className="text-xs text-muted-foreground">Reply rate</p>
                    <p className="font-semibold">{pct(campaign.reply_rate)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Bounce rate</p>
                    <p className={`font-semibold ${bounceOverThreshold(campaign) ? "text-destructive" : ""}`}>
                      {pct(campaign.bounce_rate)}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Meetings</p>
                    <p className="font-semibold">{campaign.meetings_booked ?? "—"}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Total sent</p>
                    <p className="font-semibold">{campaign.sent_total}</p>
                  </div>
                </CardContent>
              </Card>

              {campaign.campaign_state === "active" && (
                <Button variant="outline" onClick={() => pause()}>Pause campaign</Button>
              )}

              {/* Feature Group 3: per-campaign smart send time. */}
              <SendTimePanel strategyId={effectiveStratId} />

              {/* Feature 5: opt-in post-sequence re-engagement. */}
              <ReengagementPanel strategyId={effectiveStratId} />
            </div>
          )}
        </AsyncState>
      )}

      {tab === "sequences" && effectiveStratId && (
        <SequencesPanel strategyId={effectiveStratId} />
      )}
      {tab === "templates" && effectiveStratId && (
        <TemplatesPanel strategyId={effectiveStratId}
                        canManage={!!user?.is_admin} />
      )}
      {/* Part 1 Feature 5: the human review queue. Not scoped to a strategy —
          it is everything waiting on THIS user. */}
      {tab === "review" && <ReviewQueuePanel />}
      {/* Feature Group 6: AI call history for every lead in the campaign. */}
      {tab === "calls" && effectiveStratId && (
        <StrategyCalls strategyId={effectiveStratId} />
      )}
      {/* Feature Group 3: where the campaign converts, and how replies feel. */}
      {tab === "funnel" && effectiveStratId && (
        <FunnelHeatmap strategyId={effectiveStratId} />
      )}
      {tab === "sentiment" && effectiveStratId && (
        <SentimentTrend strategyId={effectiveStratId} />
      )}
    </div>
  );
}