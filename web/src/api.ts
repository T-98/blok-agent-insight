import type { AgentEvent, Insight, SessionSummary } from "./types";

// Same-origin via the Vite dev proxy by default; override for a hosted backend.
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError(0, `Can't reach the API at ${BASE}. Is the server running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listSessions: () => req<SessionSummary[]>("/sessions"),
  getEvents: (id: string) => req<AgentEvent[]>(`/sessions/${encodeURIComponent(id)}/events`),
  getInsight: (id: string) => req<Insight>(`/sessions/${encodeURIComponent(id)}/insight`),
};

export { ApiError };
