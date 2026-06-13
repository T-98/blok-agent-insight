import type {
  AgentEvent,
  Features,
  IngestSummary,
  Insight,
  RejectRow,
  RunRow,
  SessionSummary,
} from "./types";

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

export type PromptVersion = "v1" | "v2";

const enc = encodeURIComponent;

export const api = {
  listSessions: () => req<SessionSummary[]>("/sessions"),
  getEvents: (id: string) => req<AgentEvent[]>(`/sessions/${enc(id)}/events`),
  getFeatures: (id: string) => req<Features>(`/sessions/${enc(id)}/features`),
  getRejects: (id: string) => req<RejectRow[]>(`/sessions/${enc(id)}/rejects`),
  getRuns: (id: string) => req<RunRow[]>(`/sessions/${enc(id)}/runs`),
  getInsight: (id: string, version?: PromptVersion) =>
    req<Insight>(`/sessions/${enc(id)}/insight${version ? `?version=${version}` : ""}`),
  ingest: (events: unknown[]) =>
    req<IngestSummary>("/trajectories", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(events),
    }),
};

export { ApiError };
