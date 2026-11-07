"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { DollarSign, Plus } from "lucide-react";

import { billingApi } from "@/lib/api/billing";
import { formatPrice } from "@/lib/pricing";
import { AsyncState } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export default function UsagePage() {
  const [showPurchaseModal, setShowPurchaseModal] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["usage"],
    queryFn: () => billingApi.usage(),
  });

  const isPPM = data?.subscription?.billing_model === "pay_per_meeting";
  const creditLimit = (data?.subscription?.ppm_credits_limit_cents || 6000) / 100;
  const amountUsed = (data?.amount_cents || 0) / 100;
  const creditsRemaining = creditLimit - amountUsed;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <DollarSign size={20} aria-hidden="true" /> Usage & Credits
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Track your credits used and meetings booked this month.
          </p>
        </div>
        {isPPM && (
          <Button
            onClick={() => setShowPurchaseModal(true)}
            className="gap-2"
          >
            <Plus size={16} />
            Purchase Credits
          </Button>
        )}
      </div>

      <AsyncState isLoading={isLoading} error={error}>
        {data && (
          <>
            {/* Credit Limit Banner for PPM */}
            {isPPM && (
              <Card className={creditsRemaining < 500 ? "border-amber-200 bg-amber-50" : ""}>
                <CardContent className="pt-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium">Monthly Credit Limit</p>
                      <p className="text-sm text-muted-foreground mt-1">
                        ${amountUsed.toFixed(2)} of ${creditLimit.toFixed(2)} used
                      </p>
                      <div className="mt-3 w-full bg-gray-200 rounded-full h-2">
                        <div
                          className={`h-2 rounded-full ${creditsRemaining > 0 ? 'bg-green-500' : 'bg-red-500'}`}
                          style={{ width: `${Math.min((amountUsed / creditLimit) * 100, 100)}%` }}
                        />
                      </div>
                    </div>
                    {creditsRemaining < 500 && (
                      <Button
                        onClick={() => setShowPurchaseModal(true)}
                        className="ml-4"
                        variant="default"
                      >
                        Purchase Now
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            )}

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

      {/* Purchase Credits Modal */}
      {showPurchaseModal && (
        <PurchaseCreditsModal
          onClose={() => setShowPurchaseModal(false)}
          currentLimit={creditLimit}
        />
      )}
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

function PurchaseCreditsModal({
  onClose,
  currentLimit
}: {
  onClose: () => void;
  currentLimit: number;
}) {
  const [amount, setAmount] = useState(600); // $6.00 default
  const { mutate: purchaseCredits, isPending } = useMutation({
    mutationFn: async (amountCents: number) => {
      const response = await billingApi.purchaseCredits(amountCents);
      if (response.checkout_url) {
        window.location.href = response.checkout_url;
      }
      return response;
    },
    onError: (error) => {
      console.error("Purchase failed:", error);
    }
  });

  const amounts = [
    { label: "$10", value: 1000 },
    { label: "$25", value: 2500 },
    { label: "$50", value: 5000 },
    { label: "$100", value: 10000 },
  ];

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <Card className="w-full max-w-md mx-4">
        <CardHeader>
          <CardTitle>Purchase Credits</CardTitle>
          <button
            onClick={onClose}
            className="absolute right-4 top-4 text-gray-500 hover:text-gray-700"
          >
            ✕
          </button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <p className="text-sm text-muted-foreground mb-2">
              Current monthly limit: ${currentLimit.toFixed(2)}
            </p>
            <p className="text-sm font-medium mb-3">Select amount:</p>
            <div className="grid grid-cols-2 gap-2">
              {amounts.map(({ label, value }) => (
                <button
                  key={value}
                  onClick={() => setAmount(value)}
                  className={`p-2 rounded border text-sm font-medium transition ${
                    amount === value
                      ? "border-blue-500 bg-blue-50 text-blue-600"
                      : "border-gray-200 hover:border-gray-300"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="bg-gray-50 p-3 rounded">
            <p className="text-xs text-muted-foreground mb-1">New monthly limit:</p>
            <p className="text-lg font-semibold">
              ${((currentLimit * 100 + amount) / 100).toFixed(2)}
            </p>
          </div>

          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={onClose}
              disabled={isPending}
              className="flex-1"
            >
              Cancel
            </Button>
            <Button
              onClick={() => purchaseCredits(amount)}
              disabled={isPending}
              className="flex-1"
            >
              {isPending ? "Processing..." : "Continue to Checkout"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
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
