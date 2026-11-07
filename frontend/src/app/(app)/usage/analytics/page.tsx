"use client";

import { useQuery } from "@tanstack/react-query";
import { TrendingUp, DollarSign, Calendar, Zap } from "lucide-react";
import { billingApi, type CreditAnalyticsData } from "@/lib/api/billing";
import { formatPrice } from "@/lib/pricing";
import { AsyncState } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  LineChart, Line, PieChart, Pie, Cell, XAxis, YAxis, Tooltip,
  Legend, ResponsiveContainer, BarChart, Bar,
} from "recharts";

const COLORS = ["#3b82f6", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981"];

export default function CreditAnalyticsPage(): JSX.Element {
  const { data, isLoading, error } = useQuery({
    queryKey: ["credit-analytics"],
    queryFn: () => billingApi.creditAnalytics(),
  });

  const monthlyData = data?.monthly_trend ?? [];
  const transactionData = data ? Object.entries(data.transaction_summary).map(([type, stats]: [string, { count: number; total_usd: string }]) => ({
    name: type,
    value: parseFloat((stats as { total_usd: string }).total_usd.replace("$", "")),
    count: (stats as { count: number }).count,
  })) : [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <TrendingUp size={20} aria-hidden="true" /> Credit Analytics
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Track your spending patterns and credit usage trends.
        </p>
      </div>

      <AsyncState isLoading={isLoading} error={error}>
        {data && (
          <>
            {/* Key Metrics */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <MetricCard
                icon={<TrendingUp size={18} />}
                label="Projected Monthly Burn"
                value={formatPrice(data.projected_monthly_burn_cents, "USD")}
              />
              <MetricCard
                icon={<DollarSign size={18} />}
                label="Total Spent"
                value={data.transaction_summary.block ? data.transaction_summary.block.total_usd : "$0"}
              />
              {data.days_until_expiration !== null && (
                <MetricCard
                  icon={<Calendar size={18} />}
                  label="Days Until Expiration"
                  value={String(data.days_until_expiration)}
                />
              )}
            </div>

            {/* Monthly Trend Chart */}
            {monthlyData.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Monthly Spending Trend (Last 6 Months)</CardTitle>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={300}>
                    <LineChart data={monthlyData}>
                      <XAxis dataKey="period" tick={{ fontSize: 12 }} />
                      <YAxis tick={{ fontSize: 12 }} />
                      <Tooltip
                        formatter={(value: unknown) => formatPrice(Math.round((value as number) * 100), "USD")}
                        labelStyle={{ color: "#000" }}
                      />
                      <Legend />
                      <Line
                        type="monotone"
                        dataKey="spent_cents"
                        name="Spent"
                        stroke="#3b82f6"
                        dot={{ r: 4 }}
                        activeDot={{ r: 6 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            )}

            {/* Transaction Type Distribution */}
            {transactionData.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Spending by Transaction Type</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <ResponsiveContainer width="100%" height={250}>
                      <PieChart>
                        <Pie
                          data={transactionData}
                          cx="50%"
                          cy="50%"
                          labelLine={false}
                          label={({ name, count }: { name: string; count: number }) => `${name} (${count})`}
                          outerRadius={80}
                          fill="#8884d8"
                          dataKey="value"
                        >
                          {transactionData.map((_, index) => (
                            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip
                          formatter={(value: unknown) => formatPrice(Math.round((value as number) * 100), "USD")}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                  </CardContent>
                </Card>

                {/* Transaction Summary Table */}
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Transaction Summary</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-border">
                            <th className="text-left py-2 px-2 font-medium">Type</th>
                            <th className="text-right py-2 px-2 font-medium">Count</th>
                            <th className="text-right py-2 px-2 font-medium">Total</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(data.transaction_summary).map(([type, stats]) => (
                            <tr key={type} className="border-b border-border/40">
                              <td className="py-2 px-2 capitalize">{type}</td>
                              <td className="py-2 px-2 text-right">{stats.count}</td>
                              <td className="py-2 px-2 text-right font-mono">{stats.total_usd}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </CardContent>
                </Card>
              </div>
            )}

            {/* Transaction Count Trend */}
            {monthlyData.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Transaction Count Trend</CardTitle>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={250}>
                    <BarChart data={monthlyData}>
                      <XAxis dataKey="period" tick={{ fontSize: 12 }} />
                      <YAxis tick={{ fontSize: 12 }} />
                      <Tooltip labelStyle={{ color: "#000" }} />
                      <Legend />
                      <Bar
                        dataKey="transaction_count"
                        name="Transactions"
                        fill="#8b5cf6"
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            )}
          </>
        )}
      </AsyncState>
    </div>
  );
}

function MetricCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}): JSX.Element {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <p className="text-xs text-muted-foreground mb-1">{label}</p>
            <p className="text-lg font-semibold">{value}</p>
          </div>
          <div className="text-muted-foreground ml-2">{icon}</div>
        </div>
      </CardContent>
    </Card>
  );
}
