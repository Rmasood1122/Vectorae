"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { toast } from "sonner";
import { resumeStrategy, resumeStrategyErrorMessage } from "@/lib/api/strategies";
import { Button } from "@/components/ui/button";
import type { StrategyOut } from "@/lib/api/types";

/** Statuses from which a pipeline can be restarted. `verified`,
 *  `needs_human_review` and `executing` are refused by the server too. */
export const RESUMABLE_STATUSES: StrategyOut["status"][] = [
  "pending", "researching", "verifying", "failed", "budget_exhausted",
];

/** A run that is (apparently) still going. Resuming one that is really alive
 *  can start a second worker on it, so these ask for confirmation first. */
export function needsLiveRunWarning(status: StrategyOut["status"]): boolean {
  return status === "pending" || status === "researching" || status === "verifying";
}

export function ResumeStrategyButton({ strategy }: { strategy: StrategyOut }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [raise, setRaise] = useState("");

  const resume = useMutation({
    mutationFn: () => {
      const usd = raise.trim() !== "" ? Number(raise) : undefined;
      return resumeStrategy(strategy.id, usd);
    },
    onSuccess: () => {
      setConfirming(false);
      toast.success("Resumed. Steps already finished are not repeated.");
      queryClient.invalidateQueries({ queryKey: ["strategy", strategy.id] });
    },
    onError: (err) => toast.error(resumeStrategyErrorMessage(err)),
  });

  if (!RESUMABLE_STATUSES.includes(strategy.status)) return null;

  // The budget box is offered for EVERY resumable status, not only
  // `budget_exhausted`: POST /resume accepts max_budget_usd from any of them,
  // and a run that dies mid-verification (a deploy restarting the worker) is
  // left in `verifying`, where a budget raise was otherwise unreachable.
  const invalidBudget = raise.trim() !== "" && !(Number(raise) > 0);

  if (!confirming) {
    return (
      <Button variant="outline" size="sm" className="gap-1"
              onClick={() => setConfirming(true)}>
        <Play size={14} aria-hidden="true" />
        Resume
      </Button>
    );
  }

  return (
    <div role="group" aria-label="Confirm resume"
         className="flex flex-wrap items-center gap-2 rounded border border-border p-2 text-xs">
      <span className="max-w-xs text-muted-foreground">
        {needsLiveRunWarning(strategy.status)
          ? "Only resume if progress has stopped. Resuming a run that is still working can duplicate work."
          : "Continues from the last finished step. Finished steps are not repeated."}
      </span>
      <label className="flex items-center gap-1">
        New budget $
        <input
          className="w-16 rounded border border-border bg-background px-1 py-0.5"
          inputMode="decimal"
          placeholder="optional"
          value={raise}
          onChange={(e) => setRaise(e.target.value)}
          aria-label="New budget in dollars"
        />
      </label>
      <Button size="sm" disabled={resume.isPending || invalidBudget}
              onClick={() => resume.mutate()}>
        {resume.isPending ? "Resuming..." : "Confirm resume"}
      </Button>
      <Button variant="outline" size="sm" disabled={resume.isPending}
              onClick={() => setConfirming(false)}>
        Cancel
      </Button>
    </div>
  );
}
