"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { adminApi } from "@/lib/api/admin";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { toast } from "sonner";

export default function AdminUsersPage() {
  const [search, setSearch] = useState("");
  const qc = useQueryClient();

  const { data: users = [], isLoading } = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => adminApi.listUsers(),
  });

  const suspendMutation = useMutation({
    mutationFn: ({ id, suspend }: { id: string; suspend: boolean }) =>
      suspend ? adminApi.suspendUser(id, "Admin action") : adminApi.unsuspendUser(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.success("User updated");
    },
    onError: () => toast.error("Action failed"),
  });

  const filtered = users.filter((u: any) =>
    !search || u.email.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Users</h1>
        <Input
          placeholder="Filter by email…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-64"
        />
      </div>

      {isLoading ? (
        <div className="text-muted-foreground">Loading…</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Email</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Strategies</TableHead>
              <TableHead className="text-right">Active Campaigns</TableHead>
              <TableHead>Signed up from</TableHead>
              <TableHead>Country (stated / company)</TableHead>
              <TableHead className="text-right">Joined</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((user: any) => (
              <TableRow key={user.id}>
                <TableCell className="font-medium">
                  {user.email}
                  {user.is_admin && (
                    <Badge variant="outline" className="ml-2 text-xs">Admin</Badge>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={user.is_suspended ? "destructive" : "secondary"}>
                    {user.is_suspended ? "Suspended" : "Active"}
                  </Badge>
                </TableCell>
                <TableCell className="text-right">{user.strategy_count}</TableCell>
                <TableCell className="text-right">{user.active_campaign_count}</TableCell>
                <TableCell className="text-sm">
                  <div>{user.signup_country ?? "—"}</div>
                  <div className="text-xs text-muted-foreground">{user.signup_ip ?? "—"}</div>
                </TableCell>
                <TableCell className="text-sm">
                  <div>{user.declared_country ?? "—"}</div>
                  {user.company_name && (
                    <div className="text-xs text-muted-foreground">
                      {user.company_name}{user.company_country ? ` (${user.company_country})` : ""}
                    </div>
                  )}
                </TableCell>
                <TableCell className="text-right text-sm text-muted-foreground">
                  {user.created_at ? new Date(user.created_at).toLocaleDateString() : "—"}
                </TableCell>
                <TableCell>
                  {!user.is_admin && (
                    <Button
                      size="sm"
                      variant={user.is_suspended ? "outline" : "destructive"}
                      disabled={suspendMutation.isPending}
                      onClick={() =>
                        suspendMutation.mutate({ id: user.id, suspend: !user.is_suspended })
                      }
                    >
                      {user.is_suspended ? "Unsuspend" : "Suspend"}
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
