"use client";

import { useQuery } from "@tanstack/react-query";
import { DollarSign } from "lucide-react";

import { billingApi } from "@/lib/api/billing";
import { formatPrice } from "@/lib/pricing";
import { AsyncState } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export default function UsagePage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["usage"],
    queryFn: () => billingApi.usage(),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <DollarSign size={20} aria-hidden="true" /> Usage & Credits
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Track your credits used and meetings booked this month.
        </p>
      </div>

      <AsyncState isLoading={isLoading} error={error}>
        {data && (
          <>
            {/* Summary Stats */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <StatCard
                label="Meetings Booked"
                value={String(data.meetings_booked)}
              />
              <StatCard
                label="Cost This Month"
                value={formatPrice(data.amount_cents, data.currency)}
              />
              <StatCard
                label="Price Per Meeting"
                value={formatPrice(data.price_per_meeting_cents, data.currency)}
              />
              <StatCard
                label="Status Breakdown"
                value={Object.entries(data.by_status)
                  .map(([status, count]) => `${count} ${status}`)
                  .join(", ")}
              />
            </div>

            {/* Meetings Table */}
            {data.meetings.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Recent Meetings</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-border">
                          <th className="text-left py-2 px-3 font-medium">Date</th>
                          <th className="text-left py-2 px-3 font-medium">Source</th>
                          <th className="text-left py-2 px-3 font-medium">Amount</th>
                          <th className="text-left py-2 px-3 font-medium">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.meetings.map((meeting) => (
                          <tr key={meeting.id} className="border-b border-border">
                            <td className="py-2 px-3">
                              {new Date(meeting.occurred_at).toLocaleDateString()}
                            </td>
                            <td className="py-2 px-3">{meeting.source}</td>
                            <td className="py-2 px-3 font-mono">
                              {formatPrice(meeting.amount_cents, meeting.currency)}
                            </td>
                            <td className="py-2 px-3">
                              <Badge tone={getStatusTone(meeting.status)}>
                                {meeting.status}
                              </Badge>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            )}

            {data.meetings.length === 0 && (
              <Card>
                <CardContent className="py-8 text-center text-muted-foreground">
                  No meetings booked yet this month.
                </CardContent>
              </Card>
            )}
          </>
        )}
      </AsyncState>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <p className="text-xs text-muted-foreground mb-1">{label}</p>
        <p className="text-lg font-semibold truncate">{value}</p>
      </CardContent>
    </Card>
  );
}

function getStatusTone(status: string): "default" | "success" | "warn" | "error" | "info" {
  switch (status) {
    case "charged":
      return "success";
    case "pending":
      return "warn";
    case "waived":
    case "disputed":
      return "info";
    case "failed":
    case "blocked_by_credit_limit":
      return "error";
    default:
      return "default";
  }
}
