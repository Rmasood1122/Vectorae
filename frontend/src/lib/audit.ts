/** Feature A2 — tamper-evident activity audit trail: display helpers.
 *  Pure; tested in src/tests/audit.test.ts. */

export interface ChainProblem {
  seq_no: number | null;
  problem: "sequence_gap" | "content_modified" | "broken_link" | "chain_hash_mismatch" | string;
  expected?: number;
  record_id?: string;
}

export interface ChainVerification {
  valid: boolean;
  sealed: number;
  unsealed: number;
  head_seq_no: number;
  head_hash: string;
  problems: ChainProblem[];
}

const PROBLEM_TEXT: Record<string, string> = {
  sequence_gap: "records missing from the sequence",
  content_modified: "records whose content was changed",
  broken_link: "records that no longer link to the one before",
  chain_hash_mismatch: "records whose chain hash does not match",
};

export function verificationSummary(result: ChainVerification | null | undefined): {
  tone: "success" | "destructive" | "default";
  label: string;
  detail: string;
} {
  if (!result) return { tone: "default", label: "Not verified yet", detail: "" };
  if (result.valid) {
    return {
      tone: "success",
      label: "Chain intact",
      detail: result.sealed
        ? `${result.sealed.toLocaleString("en-US")} records, head #${result.head_seq_no} ${shortHash(result.head_hash)}`
        : "No activity recorded yet",
    };
  }
  const counts = new Map<string, number>();
  for (const p of result.problems) counts.set(p.problem, (counts.get(p.problem) ?? 0) + 1);
  const detail = [...counts.entries()]
    .map(([problem, n]) => `${n} ${PROBLEM_TEXT[problem] ?? problem.replace(/_/g, " ")}`)
    .join("; ");
  return { tone: "destructive", label: "Tampering detected", detail };
}

export function shortHash(hash: string | null | undefined, length = 12): string {
  if (!hash) return "—";
  return hash.length > length ? `${hash.slice(0, length)}…` : hash;
}

export function exportFilename(format: "json" | "pdf", now: Date = new Date()): string {
  return `vectorae-audit-${now.toISOString().slice(0, 10)}.${format}`;
}
