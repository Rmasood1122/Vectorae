"use client";
/** Who signed up and from where, newest first, with refused attempts
 *  (VPN blocks). Admins only: GET /admin/signups (signup_watch.py). */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { adminApi, type SignupRow } from "@/lib/api/admin";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";

const PAGE_SIZE = 50;

export default function AdminSignupsPage() {
  const [page, setPage] = useState(1);
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ["admin", "signups", page],
    queryFn: () => adminApi.listSignups(page, PAGE_SIZE),
  });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">Sign-ups</h1>
        <p className="text-sm text-muted-foreground">
          Every account created and every refused attempt, newest first. The country comes
          from the sign-up address and appears once it has been looked up.
        </p>
      </div>
      {isLoading ? (
        <div className="text-muted-foreground">Loading…</div>
      ) : rows.length === 0 && page === 1 ? (
        <div className="text-muted-foreground">No sign-ups recorded yet.</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Address</TableHead>
              <TableHead>Country</TableHead>
              <TableHead>Result</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row: SignupRow, i: number) => (
              <TableRow key={`${row.ts}-${i}`}>
                <TableCell className="text-sm">{new Date(row.ts).toLocaleString()}</TableCell>
                <TableCell className="font-medium">{row.email ?? "—"}</TableCell>
                <TableCell className="text-sm">{row.ip ?? "—"}</TableCell>
                <TableCell className="text-sm">{row.country ?? "—"}</TableCell>
                <TableCell>
                  <Badge variant={row.event === "signup" ? "secondary" : "destructive"}>
                    {row.outcome}
                  </Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {!isLoading && (
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" disabled={page === 1}
                onClick={() => setPage((p) => p - 1)}>Newer</Button>
        <span className="text-sm text-muted-foreground">Page {page}</span>
        <Button size="sm" variant="outline" disabled={rows.length < PAGE_SIZE}
                onClick={() => setPage((p) => p + 1)}>Older</Button>
      </div>
      )}
    </div>
  );
}
