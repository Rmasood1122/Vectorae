"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { adminApi } from "@/lib/api/admin";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { toast } from "sonner";

function pct(n: number | null) {
  if (n == null) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

export default function PlaybookPage() {
  const qc = useQueryClient();

  // The API answers {scores: [...]}, not a bare array.
  const { data: scores = [], isLoading } = useQuery({
    queryKey: ["admin", "playbook", "scores"],
    queryFn: async () => (await adminApi.listPlaybookScores()).scores ?? [],
  });

  const recomputeMutation = useMutation({
    mutationFn: () => adminApi.recomputePlaybook(),
    onSuccess: () => {
      toast.success("Aggregation job queued");
      setTimeout(() => qc.invalidateQueries({ queryKey: ["admin", "playbook", "scores"] }), 3000);
    },
    onError: () => toast.error("Failed to trigger recompute"),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Playbook Scores</h1>
          <p className="text-sm text-muted-foreground mt-1">
            A/B variant performance, one row per account and pattern
          </p>
        </div>
        <Button
          onClick={() => recomputeMutation.mutate()}
          disabled={recomputeMutation.isPending}
        >
          {recomputeMutation.isPending ? "Running…" : "Run Aggregation Now"}
        </Button>
      </div>

      {isLoading ? (
        <div className="text-muted-foreground">Loading…</div>
      ) : scores.length === 0 ? (
        <div className="rounded-md border p-8 text-center text-muted-foreground">
          No playbook scores yet. Run a campaign to generate data.
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Account</TableHead>
              <TableHead>Pattern Key</TableHead>
              <TableHead>Variant</TableHead>
              <TableHead className="text-right">Reply Rate</TableHead>
              <TableHead className="text-right">Booking Rate</TableHead>
              <TableHead className="text-right">Sample</TableHead>
              <TableHead className="text-right">Last Updated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {scores.map((s: any, i: number) => (
              <TableRow key={i}>
                <TableCell className="font-mono text-xs">
                  {s.user_id ?? "pooled (before 0093)"}
                </TableCell>
                <TableCell className="font-mono text-xs">{s.pattern_key}</TableCell>
                <TableCell className="text-sm">{s.variant}</TableCell>
                <TableCell className="text-right font-medium">{pct(s.reply_rate)}</TableCell>
                <TableCell className="text-right">{pct(s.booking_rate)}</TableCell>
                <TableCell className="text-right text-muted-foreground">{s.sample_size}</TableCell>
                <TableCell className="text-right text-sm text-muted-foreground">
                  {s.last_aggregated_at ? new Date(s.last_aggregated_at).toLocaleDateString() : "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
