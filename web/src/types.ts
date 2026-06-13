// Mirrors app/schemas.py — keep in sync with the FastAPI response models.

export interface SessionSummary {
  id: string;
  first_ts: string | null;
  last_ts: string | null;
  integrity_flag: number;
  event_count: number;
  injection_count: number;
  insight_runs: number;
}

export type EventStatus =
  | "success"
  | "blocked"
  | "low_progress"
  | "validation_error"
  | string;

export interface AgentEvent {
  step: number;
  ts: string | null;
  action: string | null;
  target: string | null;
  observation: string | null;
  status: EventStatus | null;
  injection_flag: number;
}

export interface FrictionPoint {
  step: number;
  description: string;
}

export interface Insight {
  session_id: string;
  summary: string;
  friction_points: FrictionPoint[];
  confidence: number; // 0..1
  recommended_follow_up: string;
}

export interface FrictionEvent {
  step: number;
  status: string | null;
  observation: string | null;
}

// Mirrors features.extract_features — the deterministic layer the LLM narrates.
export interface Features {
  progress_ratio: number;
  loop_score: number;
  stall_streak: number;
  terminal_status: string | null;
  injection_count: number;
  conflict_count: number;
  friction_events: FrictionEvent[];
}

export interface RejectRow {
  id: number;
  reason: "duplicate_event" | "conflicting_step" | "bad_timestamp" | "missing_required_target" | string;
  raw_json: string;
  created_at: string | null;
}

export interface RunRow {
  id: number;
  prompt_version: string | null;
  model: string | null;
  validation_status: string | null;
  latency_ms: number | null;
  created_at: string | null;
}

export interface IngestSummary {
  accepted: number;
  rejected: number;
  flagged: number;
  sessions: string[];
}
