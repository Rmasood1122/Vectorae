"use client";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";

interface SubjectLineCardProps {
  channel?: string;
}

const PATTERN_LABELS: Record<string, string> = {
  question:             "Ends with a question",
  number:               "Contains a number",
  personalized_company: "Personalized with company name",
  pain_point_hook:      "Pain point hook",
  curiosity_gap:        "Curiosity gap opener",
  social_proof:         "Social proof mention",
  direct_offer:         "Direct offer (trial, demo, free)",
  short_under_6_words:  "Short (≤ 6 words)",
  long_over_10_words:   "Long (> 10 words)",
};

export function SubjectLineCard({ channel = "gmail" }: SubjectLineCardProps) {
  const { data: patterns = [], isLoading } = useQuery({
    queryKey: ["subject-patterns", channel],
    queryFn: () =>
      apiClient
        .get(`/playbook/subject-patterns?channel=${channel}&top_n=5`)
        ,
  });

  return (
    <div className="rounded-lg border bg-card p-4 space-y-3">
      <h3 className="font-semibold text-sm">What makes subject lines work</h3>

      {isLoading ? (
        <div className="text-xs text-muted-foreground">Loading…</div>
      ) : patterns.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No reliable pattern data yet. Send more campaigns to unlock insights.
        </p>
      ) : (
        <div className="space-y-2">
          {patterns.map((p: any, i: number) => (
            <div key={i} className="space-y-0.5">
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium">
                  {PATTERN_LABELS[p.pattern_type] ?? p.pattern_type}
                </span>
                <span className="text-xs text-muted-foreground">
                  {(p.avg_reply_rate * 100).toFixed(1)}% reply
                </span>
              </div>
              {/* Mini progress bar */}
              <div className="h-1 rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full bg-primary rounded-full"
                  style={{ width: `${Math.min(100, p.avg_reply_rate * 400)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-[10px] text-muted-foreground">
        Rates pooled across every account on this install, from {channel} campaigns.
      </p>
    </div>
  );
}
