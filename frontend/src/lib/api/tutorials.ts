import { api } from "./client";
import type { Tutorial, TutorialCatalogue } from "./types";

/**
 * Learn Vectorae — tutorial API (Feature 2).
 *
 * Search and level filtering happen on the SERVER, not in the browser. Nine
 * items would filter fine client-side today, but the moment the catalogue
 * grows the two implementations diverge, and the one users hit is whichever
 * the page happened to call. One implementation, in app/services/tutorials.py.
 */

export async function listTutorials(
  params: { q?: string; level?: string } = {},
): Promise<TutorialCatalogue> {
  const search = new URLSearchParams();
  // Empty strings are omitted rather than sent: `?q=` would otherwise reach the
  // backend as a filter and read as "search for nothing".
  if (params.q?.trim()) search.set("q", params.q.trim());
  if (params.level) search.set("level", params.level);
  const qs = search.toString();
  return api<TutorialCatalogue>(`/tutorials${qs ? `?${qs}` : ""}`);
}

export function getTutorial(slug: string): Promise<Tutorial> {
  return api<Tutorial>(`/tutorials/${encodeURIComponent(slug)}`);
}

/**
 * Report a playback position.
 *
 * Safe to call repeatedly — the backend upserts one row per user per video and
 * takes the latest position but the MAXIMUM percent, so a user scrubbing
 * backwards never loses watched progress.
 */
export function updateTutorialProgress(
  slug: string,
  positionSeconds: number,
  durationSeconds?: number,
): Promise<Tutorial> {
  return api<Tutorial>(`/tutorials/${encodeURIComponent(slug)}/progress`, {
    method: "PUT",
    body: {
      position_seconds: Math.max(0, Math.floor(positionSeconds)),
      ...(durationSeconds
        ? { duration_seconds: Math.max(1, Math.floor(durationSeconds)) }
        : {}),
    },
  });
}

/** Mark finished without watching to the threshold. */
export function completeTutorial(slug: string): Promise<Tutorial> {
  return api<Tutorial>(`/tutorials/${encodeURIComponent(slug)}/complete`, {
    method: "POST",
  });
}

/** Reset one tutorial to not-started. The only thing that revokes a badge. */
export function resetTutorialProgress(slug: string): Promise<Tutorial> {
  return api<Tutorial>(`/tutorials/${encodeURIComponent(slug)}/progress`, {
    method: "DELETE",
  });
}
