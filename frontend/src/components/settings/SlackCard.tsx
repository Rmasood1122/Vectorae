"use client";

/** Feature Group 4: connect Slack and choose the channel Vectorae posts to. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  disconnectSlack, getSlack, listSlackChannels, setSlackChannel, slackAuthUrl, testSlack,
} from "@/lib/api/ecosystem";
import { AsyncState } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";

export function SlackCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, isLoading, error } = useQuery({ queryKey: ["slack"], queryFn: getSlack });
  const channels = useQuery({
    queryKey: ["slack-channels"],
    queryFn: listSlackChannels,
    enabled: !!data?.connected,
  });
  const fail = (e: unknown) => toast((e as Error).message, "error");

  const connect = async () => {
    try {
      window.location.href = (await slackAuthUrl()).auth_url;
    } catch (e) {
      fail(e);
    }
  };
  const choose = useMutation({
    mutationFn: setSlackChannel,
    onSuccess: (s) => { qc.setQueryData(["slack"], s); toast(`Posting to #${s.channel_name}`, "success"); },
    onError: fail,
  });
  const test = useMutation({
    mutationFn: testSlack,
    onSuccess: () => toast("Test message sent", "success"),
    onError: fail,
  });
  const disconnect = useMutation({
    mutationFn: disconnectSlack,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["slack"] }); toast("Slack disconnected", "info"); },
    onError: fail,
  });

  return (
    <Card>
      <CardContent className="space-y-3 p-gutter">
        <AsyncState isLoading={isLoading} error={error}>
          {data && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium">Slack</p>
                  <p className="text-xs text-muted-foreground">
                    {data.connected
                      ? `Connected to ${data.team_name ?? "your workspace"}` +
                        (data.channel_name ? ` · #${data.channel_name}` : " · choose a channel")
                      : "Meeting bookings, interested replies, campaign auto-pauses and strategy changes, in a channel."}
                  </p>
                </div>
                {data.connected ? (
                  <div className="flex gap-2">
                    <Button size="sm" variant="outline" onClick={() => test.mutate()}
                            disabled={!data.channel_id || test.isPending}>
                      Test
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => disconnect.mutate()}
                            disabled={disconnect.isPending}>
                      Disconnect
                    </Button>
                  </div>
                ) : (
                  <Button size="sm" onClick={connect} disabled={!data.configured}>Connect</Button>
                )}
              </div>
              {!data.configured && !data.connected && (
                <p className="text-xs text-muted-foreground">
                  An admin must add the Slack app under Admin › Integrations first.
                </p>
              )}
              {data.connected && (
                <div className="flex flex-wrap items-center gap-2">
                  <label htmlFor="slack-channel" className="text-xs text-muted-foreground">Channel</label>
                  <select
                    id="slack-channel"
                    className="rounded border border-border bg-card px-2 py-1 text-sm"
                    value={data.channel_id ?? ""}
                    disabled={channels.isLoading || choose.isPending}
                    onChange={(e) => e.target.value && choose.mutate(e.target.value)}
                  >
                    <option value="">{channels.isLoading ? "Loading…" : "Select a channel…"}</option>
                    {(channels.data ?? []).map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.is_private ? "🔒 " : "#"}{c.name ?? c.id}
                      </option>
                    ))}
                  </select>
                  <span className="text-[11px] text-muted-foreground">
                    Private channels need the Vectorae bot invited first.
                  </span>
                </div>
              )}
            </>
          )}
        </AsyncState>
      </CardContent>
    </Card>
  );
}
