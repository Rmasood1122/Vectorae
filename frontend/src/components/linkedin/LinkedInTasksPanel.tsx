"use client";

/** L0 queue: LinkedIn tasks YOU perform. Vectorae never sends on LinkedIn.
 *  Approve one task (valid 24 hours) -> Open profile on LinkedIn (copies the
 *  text, opens the plain profile link) -> send it yourself -> mark it. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/client";
import {
  approveTask, completeTask, listTasks, openTask, safeTaskProfileUrl, taskStatusLabel,
  type LinkedInTask, type TaskOutcome,
} from "@/lib/api/linkedinTasks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const OPEN = "pending,approved,opened";

function TaskRow({ task, onChanged }: { task: LinkedInTask; onChanged: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: (fn: () => Promise<unknown>) => fn(),
    onSuccess: () => { setErr(null); onChanged(); },
    onError: (e) => { setErr(e instanceof ApiError ? e.message : "That did not work."); onChanged(); },
  });
  const done = (o: TaskOutcome) => run.mutate(() => completeTask(task.id, o));

  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge>{task.kind === "connection_note" ? "Connection note" : "Message"}</Badge>
        <span>{taskStatusLabel(task)}</span>
      </div>
      <p className="whitespace-pre-wrap rounded border p-2 text-sm">{task.text}</p>
      <div className="flex flex-wrap gap-2">
        {(task.status === "pending" || (task.status === "approved" && task.approval_expired)) && (
          <Button disabled={run.isPending} onClick={() => run.mutate(() => approveTask(task.id))}>
            Approve this one
          </Button>
        )}
        {task.status === "approved" && !task.approval_expired && (
          <Button disabled={run.isPending}
                  onClick={() => run.mutate(async () => {
                    const opened = await openTask(task.id);
                    await navigator.clipboard?.writeText(opened.text).catch(() => undefined);
                    const link = safeTaskProfileUrl(opened.profile_url);
                    if (link) window.open(link, "_blank", "noopener,noreferrer");
                  })}>
            Open profile on LinkedIn
          </Button>
        )}
        {task.status === "opened" && (
          <>
            <Button disabled={run.isPending} onClick={() => done("done")}>I sent it</Button>
            <Button variant="outline" disabled={run.isPending} onClick={() => done("replied")}>
              They replied
            </Button>
          </>
        )}
        {(task.status === "pending" || task.status === "approved" || task.status === "opened") && (
          <Button variant="outline" disabled={run.isPending} onClick={() => done("skipped")}>
            Skip
          </Button>
        )}
      </div>
      {err && <p role="alert" className="text-xs text-red-600">{err}</p>}
    </li>
  );
}

export function LinkedInTasksPanel() {
  const qc = useQueryClient();
  const tasks = useQuery({ queryKey: ["linkedin-tasks"], queryFn: () => listTasks(OPEN) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["linkedin-tasks"] });
  const list = tasks.data?.tasks ?? [];
  if (tasks.isLoading || list.length === 0) return null;
  const limits = tasks.data?.limits;

  return (
    <Card>
      <CardHeader><CardTitle>Your LinkedIn tasks</CardTitle></CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="text-muted-foreground">
          Vectorae drafts; you send on LinkedIn. Approve each task yourself (an approval lasts{" "}
          {tasks.data?.approval_hours ?? 24} hours), open the profile, paste the text, send it,
          and mark it here. To protect your LinkedIn account, at most{" "}
          {limits?.connection_note ?? 20} connection notes and {limits?.message ?? 50} messages
          open per day, during business hours.
        </p>
        <ul className="divide-y">
          {list.map((t) => <TaskRow key={t.id} task={t} onChanged={refresh} />)}
        </ul>
      </CardContent>
    </Card>
  );
}
