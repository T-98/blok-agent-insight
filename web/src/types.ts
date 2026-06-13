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
