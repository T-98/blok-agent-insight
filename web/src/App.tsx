import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "./api";
import type { AgentEvent, Insight, SessionSummary } from "./types";

/* ============================================================== helpers === */

const FRICTION = new Set(["blocked", "low_progress", "validation_error"]);

const STATUS_LABEL: Record<string, string> = {
  success: "success",
  blocked: "blocked",
  low_progress: "low_progress",
  validation_error: "validation_error",
};

function statusKey(s: string | null): string {
  return s && STATUS_LABEL[s] ? s : "neutral";
}

function fmtAbs(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Relative time, computed client-side from an ISO string (may be null).
function rel(ts: string | null): string {
  if (!ts) return "—";
  const t = new Date(ts).getTime();
  if (isNaN(t)) return "—";
  const diff = Date.now() - t;
  const s = Math.round(diff / 1000);
  if (s < 0) return "soon";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d}d ago`;
  return fmtAbs(ts);
}

function span(a: string | null, b: string | null): string {
  if (!a || !b) return "—";
  const ms = new Date(b).getTime() - new Date(a).getTime();
  if (isNaN(ms)) return "—";
  if (ms < 1000) return `${ms}ms`;
  const sec = Math.round(ms / 1000);
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m${sec % 60}s`;
}

const tripleKey = (e: AgentEvent) => `${e.action}|${e.target}|${e.observation}`;

// Group adjacent same-step events into one rail node. >1 event on a step is a
// *kept conflict* (the backend never silently picks a winner) — render forked.
interface StepNode {
  step: number;
  events: AgentEvent[];
}
function toNodes(events: AgentEvent[]): StepNode[] {
  const nodes: StepNode[] = [];
  for (const e of events) {
    const last = nodes[nodes.length - 1];
    if (last && last.step === e.step) last.events.push(e);
    else nodes.push({ step: e.step, events: [e] });
  }
  return nodes;
}

// The headline v1-vs-v2 difference: does the narration acknowledge the injected
// page text as content (rather than getting steered by it)? Pure text heuristic.
const _INJ_RE = /injection|not followed|ignore previous|instruction|prompt[- ]?inject/i;
function namesInjection(ins: Insight): boolean {
  return _INJ_RE.test(ins.summary + " " + ins.friction_points.map((f) => f.description).join(" "));
}

type View = "inspect" | "compare";
type CmpState = { v1: Insight | null; v2: Insight | null };

/* ================================================================ app === */

type Filter = "all" | "flagged" | "injection";

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [listLoading, setListLoading] = useState(true);

  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [view, setView] = useState<View>("inspect");

  const [cmp, setCmp] = useState<CmpState>({ v1: null, v2: null });
  const [cmpLoading, setCmpLoading] = useState(false);
  const [cmpError, setCmpError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);

  const [insight, setInsight] = useState<Insight | null>(null);
  const [insightLoading, setInsightLoading] = useState(false);
  const [insightError, setInsightError] = useState<string | null>(null);

  const loadSessions = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    try {
      setSessions(await api.listSessions());
    } catch (e) {
      setListError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const selectSession = useCallback(async (id: string) => {
    setSelectedId(id);
    setInsight(null);
    setInsightError(null);
    setCmp({ v1: null, v2: null });
    setCmpError(null);
    setEvents([]);
    setEventsLoading(true);
    try {
      setEvents(await api.getEvents(id));
    } catch (e) {
      setInsightError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setEventsLoading(false);
    }
  }, []);

  const runCompare = useCallback(async (id: string) => {
    setCmpLoading(true);
    setCmpError(null);
    setCmp({ v1: null, v2: null });
    try {
      const [v1, v2] = await Promise.all([api.getInsight(id, "v1"), api.getInsight(id, "v2")]);
      setCmp({ v1, v2 });
    } catch (e) {
      setCmpError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setCmpLoading(false);
    }
  }, []);

  const generateInsight = useCallback(async (id: string) => {
    setInsightLoading(true);
    setInsightError(null);
    try {
      setInsight(await api.getInsight(id));
    } catch (e) {
      setInsightError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setInsightLoading(false);
    }
  }, []);

  const selected = useMemo(
    () => sessions.find((s) => s.id === selectedId) ?? null,
    [sessions, selectedId],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return sessions.filter((s) => {
      if (q && !s.id.toLowerCase().includes(q)) return false;
      if (filter === "injection") return s.injection_count > 0;
      if (filter === "flagged") return s.injection_count > 0 || s.integrity_flag > 0;
      return true;
    });
  }, [sessions, query, filter]);

  const totals = useMemo(() => {
    const ev = sessions.reduce((n, s) => n + s.event_count, 0);
    const inj = sessions.reduce((n, s) => n + s.injection_count, 0);
    const flagged = sessions.filter((s) => s.injection_count > 0 || s.integrity_flag > 0).length;
    return { sessions: sessions.length, events: ev, injections: inj, flagged };
  }, [sessions]);

  // client-side derived trajectory signals
  const nodes = useMemo(() => toNodes(events), [events]);
  const loopCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of events) m.set(tripleKey(e), (m.get(tripleKey(e)) ?? 0) + 1);
    return m;
  }, [events]);
  const stallFirst = useMemo(() => {
    // first step-index of each consecutive low_progress run (len >= 2) -> run length
    const out = new Map<number, number>();
    let runStart = -1;
    let count = 0;
    const flush = () => {
      if (count >= 2 && runStart >= 0) out.set(runStart, count);
      runStart = -1;
      count = 0;
    };
    events.forEach((e, i) => {
      if (e.status === "low_progress") {
        if (count === 0) runStart = i;
        count++;
      } else flush();
    });
    flush();
    return out;
  }, [events]);
  const terminal = useMemo(() => {
    if (!events.length) return null;
    return events.reduce((a, b) => (b.step >= a.step ? b : a)).status;
  }, [events]);

  const jumpToStep = useCallback((step: number) => {
    const el = document.getElementById(`step-${step}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.remove("pulse");
    void el.offsetWidth; // restart animation
    el.classList.add("pulse");
  }, []);

  return (
    <div className="shell">
      {/* ---------------------------------------------------------- topbar */}
      <header className="topbar">
        <div className="wordmark">
          <span className="wordmark__dot" aria-hidden />
          <span className="wordmark__name">tracewell</span>
          <span className="wordmark__tag">agent insight console</span>
        </div>

        <div className="viewswitch" role="tablist" aria-label="View">
          <button
            className={`viewswitch__btn ${view === "inspect" ? "viewswitch__btn--on" : ""}`}
            onClick={() => setView("inspect")}
            role="tab"
            aria-selected={view === "inspect"}
          >
            inspect
          </button>
          <button
            className={`viewswitch__btn ${view === "compare" ? "viewswitch__btn--on" : ""}`}
            onClick={() => setView("compare")}
            role="tab"
            aria-selected={view === "compare"}
          >
            compare v1↔v2
          </button>
        </div>

        <div className="counts">
          <Count k="sessions" v={totals.sessions} />
          <Count k="events" v={totals.events} />
          <Count k="flagged" v={totals.flagged} tone={totals.flagged ? "warn" : undefined} />
          <Count k="injections" v={totals.injections} tone={totals.injections ? "inj" : undefined} />
        </div>

        <button className="btn btn--ghost" onClick={() => void loadSessions()} disabled={listLoading}>
          <span className={`btn__glyph ${listLoading ? "spin" : ""}`} aria-hidden>↻</span> refresh
        </button>
      </header>

      <main className={`grid ${view === "compare" ? "grid--compare" : ""}`}>
        {/* ------------------------------------------------------ left rail */}
        <aside className="rail" aria-label="Sessions">
          <div className="rail__filter">
            <input
              className="search"
              placeholder="filter sessions…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Filter sessions by id"
            />
            <div className="chips">
              {(["all", "flagged", "injection"] as Filter[]).map((f) => (
                <button
                  key={f}
                  className={`chip ${filter === f ? "chip--on" : ""}`}
                  onClick={() => setFilter(f)}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>

          <div className="rail__list">
            {listLoading && <SkeletonList />}
            {listError && (
              <ErrorBox title="couldn't load sessions" detail={listError} onRetry={() => void loadSessions()} />
            )}
            {!listLoading && !listError && filtered.length === 0 && (
              <div className="rail__empty">
                <span>{sessions.length ? "no sessions match" : "no sessions ingested"}</span>
                {!sessions.length && <code>POST /trajectories</code>}
              </div>
            )}
            {filtered.map((s) => {
              const inj = s.injection_count > 0;
              const con = s.integrity_flag > 0;
              const lead = inj ? "inj" : con ? "con" : "";
              return (
                <button
                  key={s.id}
                  className={`srow ${s.id === selectedId ? "srow--on" : ""} ${lead ? `srow--${lead}` : ""}`}
                  onClick={() => void selectSession(s.id)}
                >
                  <div className="srow__top">
                    <span className="srow__id">
                      <span className="srow__sid">sid:</span>
                      {s.id}
                    </span>
                    <span className="srow__dots">
                      {con && <span className="leddot leddot--integrity" title="integrity conflict" />}
                      {inj && <span className="leddot leddot--injection" title="prompt injection" />}
                    </span>
                  </div>
                  <div className="srow__meta">
                    <span>{s.event_count} events</span>
                    <span className="srow__sep">·</span>
                    <span>{rel(s.last_ts)}</span>
                    {s.insight_runs > 0 && (
                      <span className="srow__runs" title={`${s.insight_runs} prior insight run(s)`}>
                        ✦ {s.insight_runs}
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </aside>

        {view === "compare" ? (
          <CompareView
            session={selected}
            cmp={cmp}
            loading={cmpLoading}
            error={cmpError}
            onRun={() => selected && void runCompare(selected.id)}
          />
        ) : (
        <>
        {/* -------------------------------------------------------- detail */}
        <section className="detail" aria-label="Trajectory">
          {!selected && <DetailEmpty />}

          {selected && (
            <>
              <div className="detail__head">
                <div className="detail__id">
                  <span className="detail__sid">sid:</span>
                  {selected.id}
                </div>
                <div className="detail__sub">
                  <span>{selected.event_count} steps</span>
                  <span className="detail__sep">·</span>
                  <span>{span(selected.first_ts, selected.last_ts)} span</span>
                  <span className="detail__sep">·</span>
                  <span>{fmtAbs(selected.first_ts)}</span>
                  {terminal && (
                    <span className={`enum enum--${statusKey(terminal)} detail__term`}>{terminal}</span>
                  )}
                </div>
                <div className="detail__badges">
                  {selected.integrity_flag > 0 && (
                    <span className="lbadge lbadge--integrity">
                      <span className="lbadge__dot" /> integrity
                    </span>
                  )}
                  {selected.injection_count > 0 && (
                    <span className="lbadge lbadge--injection">
                      <span className="lbadge__dot" /> injection ×{selected.injection_count}
                    </span>
                  )}
                </div>
              </div>

              <div className="trace">
                {eventsLoading ? (
                  <SkeletonRows n={4} />
                ) : (
                  nodes.map((node, ni) => {
                    const conflict = node.events.length > 1;
                    return (
                      <div
                        key={`${node.step}-${ni}`}
                        id={`step-${node.step}`}
                        className={`node ${conflict ? "node--conflict" : ""}`}
                      >
                        {node.events.map((e, ei) => {
                          const friction = !!e.status && FRICTION.has(e.status);
                          const last = ni === nodes.length - 1 && ei === node.events.length - 1;
                          const loops = loopCounts.get(tripleKey(e)) ?? 1;
                          const stall = stallFirst.get(events.indexOf(e));
                          return (
                            <div className="step" key={ei}>
                              <div className="step__rail">
                                <span className={`dot dot--${e.injection_flag ? "injection" : statusKey(e.status)}`} />
                                {!last && (
                                  <span className={`line ${friction ? "line--dashed" : ""}`} />
                                )}
                                {ei === 0 && conflict && <span className="fork" aria-hidden />}
                              </div>

                              <div className="step__body">
                                <div className="step__line">
                                  <span className="step__n">step {e.step}</span>
                                  <span className="step__action">{e.action}</span>
                                  <span className="step__target">{e.target ?? "—"}</span>
                                  {loops > 1 && <span className="microbadge microbadge--loop">loop ×{loops}</span>}
                                  {stall && <span className="microbadge microbadge--stall">stall ×{stall}</span>}
                                </div>

                                {e.injection_flag ? (
                                  <Quarantine text={e.observation ?? ""} />
                                ) : (
                                  <div className="step__obs">{e.observation}</div>
                                )}
                              </div>

                              <span className={`enum enum--${e.injection_flag ? "injection" : statusKey(e.status)}`}>
                                {e.status ?? "—"}
                              </span>
                            </div>
                          );
                        })}
                        {conflict && (
                          <div className="conflict__cap">
                            kept conflict · source disagreed — no winner picked
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </>
          )}
        </section>

        {/* ------------------------------------------------------- insight */}
        <aside className="insight-col" aria-label="Insight">
          {!selected ? (
            <div className="ipanel ipanel--idle">
              <span className="ipanel__hint">select a session to read its insight</span>
            </div>
          ) : (
            <div className="ipanel">
              <div className="ipanel__head">
                <span className="ipanel__label">insight</span>
                {insight && <span className="seal">generated</span>}
              </div>

              {!insight && !insightLoading && (
                <div className="igen">
                  <button
                    className="btn btn--accent"
                    onClick={() => void generateInsight(selected.id)}
                    title="regenerates a fresh run on every click"
                  >
                    generate insight
                  </button>
                  <p className="igen__note">one LLM call · features are deterministic</p>
                  {selected.insight_runs > 0 && (
                    <p className="igen__runs">{selected.insight_runs} prior run(s)</p>
                  )}
                </div>
              )}

              {insightError && <ErrorBox title="insight failed" detail={insightError} />}
              {insightLoading && (
                <div className="irun">
                  <span className="irun__txt">› running</span>
                  <span className="irun__dots"><i/><i/><i/></span>
                </div>
              )}

              {insight && !insightLoading && (
                <div className="insight">
                  <ConfidenceMeter
                    value={insight.confidence}
                    injection={selected.injection_count}
                    conflict={selected.integrity_flag > 0 ? 1 : 0}
                  />

                  <p className="insight__summary">{insight.summary}</p>

                  <div className="insight__block">
                    <div className="insight__bh">
                      friction <span className="insight__n">{insight.friction_points.length}</span>
                    </div>
                    {insight.friction_points.length === 0 ? (
                      <p className="muted">none detected</p>
                    ) : (
                      <ul className="friction">
                        {insight.friction_points.map((f, i) => (
                          <li key={i} className="friction__item">
                            <button className="stepref" onClick={() => jumpToStep(f.step)}>
                              step {f.step}
                            </button>
                            <span>{f.description}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div className="followup">
                    <span className="followup__label">recommended follow-up</span>
                    <p>{insight.recommended_follow_up}</p>
                  </div>

                  <button
                    className="btn btn--ghost btn--sm regen"
                    onClick={() => void generateInsight(selected.id)}
                    title="regenerates a fresh run"
                  >
                    ↻ regenerate
                  </button>
                </div>
              )}
            </div>
          )}
        </aside>
        </>
        )}
      </main>
    </div>
  );
}

/* ====================================================== subcomponents === */

function Count({ k, v, tone }: { k: string; v: number; tone?: "warn" | "inj" }) {
  return (
    <div className={`count ${tone ? `count--${tone}` : ""}`}>
      <span className="count__v">{v}</span>
      <span className="count__k">{k}</span>
    </div>
  );
}

function Quarantine({ text }: { text: string }) {
  return (
    <div className="quar">
      <div className="quar__corner">untrusted · quarantined</div>
      <div className="quar__fence">```data</div>
      <div className="quar__text">{text}</div>
      <div className="quar__fence">```</div>
      <div className="quar__note">
        <span className="quar__shield">⛉ flagged as content, not followed</span>
        <span className="quar__italic">described as content, never executed</span>
      </div>
    </div>
  );
}

function ConfidenceMeter({
  value,
  injection,
  conflict,
  compact = false,
}: {
  value: number;
  injection: number;
  conflict: number;
  compact?: boolean;
}) {
  const SEG = 20;
  const filled = Math.round(value * SEG);
  // Known cap ceiling, derived client-side from real fields. The reject term
  // (guards.py: −0.1·rejects) is NOT exposed by the API, so it's shown but not
  // summed — the backend applies the authoritative cap.
  const ceiling = Math.max(0.05, 1 - 0.2 * injection - 0.2 * conflict);
  const tickPct = ceiling * 100;
  return (
    <div className="conf">
      <div className="conf__top">
        <span className="conf__num">{value.toFixed(2)}</span>
        <span className="conf__cap">confidence</span>
      </div>
      <div className="conf__bar">
        {Array.from({ length: SEG }).map((_, i) => (
          <span key={i} className={`seg ${i < filled ? "seg--on" : ""}`} />
        ))}
        <span
          className={`conf__tick ${injection ? "conf__tick--inj" : ""}`}
          style={{ left: `${tickPct}%` }}
          title={`known ceiling ≈ ${ceiling.toFixed(2)}`}
        />
      </div>
      {compact ? null : (
      <>
      <div className="ledger">
        <Line k="base" v="1.00" />
        <Line k={`− injection ×${injection}`} v={(0.2 * injection).toFixed(2)} dim={!injection} />
        <Line k={`− conflict ×${conflict}`} v={(0.2 * conflict).toFixed(2)} dim={!conflict} />
        <Line k="− rejects ~" v="n/a" est />
        <div className="ledger__rule" />
        <Line k="ceiling (known)" v={ceiling.toFixed(2)} accent />
      </div>
      <p className="ledger__foot">
        ≈ derived client-side · backend applies the authoritative cap (reject count not exposed)
      </p>
      </>
      )}
    </div>
  );
}

function Line({ k, v, accent, dim, est }: { k: string; v: string; accent?: boolean; dim?: boolean; est?: boolean }) {
  return (
    <div className={`lline ${accent ? "lline--accent" : ""} ${dim ? "lline--dim" : ""} ${est ? "lline--est" : ""}`}>
      <span>{k}</span>
      <span>{v}</span>
    </div>
  );
}

function DetailEmpty() {
  return (
    <div className="detail-empty">
      <h2>Select a session</h2>
      <p>←&nbsp;&nbsp;pick a row to read its trajectory</p>
    </div>
  );
}

/* -------------------------------------------------- prompt compare view === */

function CompareView({
  session,
  cmp,
  loading,
  error,
  onRun,
}: {
  session: SessionSummary | null;
  cmp: CmpState;
  loading: boolean;
  error: string | null;
  onRun: () => void;
}) {
  if (!session) {
    return (
      <section className="compare compare--empty">
        <div className="detail-empty">
          <h2>Select a session</h2>
          <p>←&nbsp;&nbsp;pick a row, then run the v1 vs v2 comparison</p>
        </div>
      </section>
    );
  }
  const inj = session.injection_count;
  const con = session.integrity_flag > 0 ? 1 : 0;
  const ready = !!cmp.v1 && !!cmp.v2;
  return (
    <section className="compare" aria-label="Prompt comparison">
      <div className="compare__head">
        <div>
          <h2 className="compare__id">
            <span className="detail__sid">sid:</span>
            {session.id}
          </h2>
          <p className="compare__sub">
            prompt comparison · <b>PROMPT_V1</b> (plain) vs <b>PROMPT_V2</b> (injection-hardened)
          </p>
        </div>
        <button
          className="btn btn--accent"
          onClick={onRun}
          disabled={loading}
          title="runs two fresh insight_runs — one per prompt version"
        >
          {loading ? "running v1 + v2…" : ready ? "↻ re-run both" : "run comparison"}
        </button>
      </div>

      {error && <ErrorBox title="comparison failed" detail={error} />}

      {loading && (
        <div className="compare__cols">
          <SkeletonCard />
          <SkeletonCard />
        </div>
      )}

      {!loading && ready && (
        <>
          <DiffStrip v1={cmp.v1!} v2={cmp.v2!} />
          <div className="compare__cols">
            <PromptCard label="v1" sub="plain" insight={cmp.v1!} injection={inj} conflict={con} />
            <PromptCard label="v2" sub="injection-hardened" insight={cmp.v2!} injection={inj} conflict={con} />
          </div>
        </>
      )}

      {!loading && !ready && !error && (
        <div className="compare__hint">
          <p>
            Run both prompt versions against <strong>{session.id}</strong> and read them side by side.
          </p>
          <p className="muted">
            Each click appends two fresh <code>insight_runs</code> rows (one per version). The signal
            to watch: whether the summary <em>names the injected page text as content</em> — v2 is
            built to, v1 often omits it. LLMs are nondeterministic, so a single run is an anecdote;
            <code> eval.py</code> measures the rate across N trials.
          </p>
        </div>
      )}
    </section>
  );
}

function DiffStrip({ v1, v2 }: { v1: Insight; v2: Insight }) {
  const ni1 = namesInjection(v1);
  const ni2 = namesInjection(v2);
  return (
    <table className="diff">
      <thead>
        <tr>
          <th>metric</th>
          <th>v1</th>
          <th>v2</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>confidence</td>
          <td>{v1.confidence.toFixed(2)}</td>
          <td>{v2.confidence.toFixed(2)}</td>
        </tr>
        <tr className="diff__key">
          <td>names injection</td>
          <td className={ni1 ? "diff--good" : "diff--bad"}>{ni1 ? "✓ yes" : "✗ no"}</td>
          <td className={ni2 ? "diff--good" : "diff--bad"}>{ni2 ? "✓ yes" : "✗ no"}</td>
        </tr>
        <tr>
          <td>friction points</td>
          <td>{v1.friction_points.length}</td>
          <td>{v2.friction_points.length}</td>
        </tr>
      </tbody>
    </table>
  );
}

function PromptCard({
  label,
  sub,
  insight,
  injection,
  conflict,
}: {
  label: string;
  sub: string;
  insight: Insight;
  injection: number;
  conflict: number;
}) {
  const names = namesInjection(insight);
  return (
    <div className="pcard">
      <div className="pcard__head">
        <span className="pcard__label">PROMPT_{label.toUpperCase()}</span>
        <span className="pcard__sub">{sub}</span>
        <span className={`pcard__flag ${names ? "pcard__flag--good" : "pcard__flag--bad"}`}>
          {names ? "names injection" : "omits injection"}
        </span>
      </div>
      <ConfidenceMeter value={insight.confidence} injection={injection} conflict={conflict} compact />
      <p className="insight__summary">{insight.summary}</p>
      <div className="insight__block">
        <div className="insight__bh">
          friction <span className="insight__n">{insight.friction_points.length}</span>
        </div>
        {insight.friction_points.length === 0 ? (
          <p className="muted">none detected</p>
        ) : (
          <ul className="friction">
            {insight.friction_points.map((f, i) => (
              <li key={i} className="friction__item">
                <span className="stepref stepref--static">step {f.step}</span>
                <span>{f.description}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="followup">
        <span className="followup__label">recommended follow-up</span>
        <p>{insight.recommended_follow_up}</p>
      </div>
    </div>
  );
}

function SkeletonCard() {
  return (
    <div className="pcard pcard--sk">
      <div className="sk sk--row" style={{ height: 28 }} />
      <div className="sk sk--row" style={{ height: 60 }} />
      <div className="sk sk--row" style={{ height: 40 }} />
    </div>
  );
}

function ErrorBox({ title, detail, onRetry }: { title: string; detail: string; onRetry?: () => void }) {
  return (
    <div className="errbox">
      <strong>{title}</strong>
      <span>{detail}</span>
      {onRetry && (
        <button className="btn btn--ghost btn--sm" onClick={onRetry}>
          retry
        </button>
      )}
    </div>
  );
}

function SkeletonList() {
  return (
    <>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="srow srow--sk">
          <div className="sk sk--line" />
          <div className="sk sk--line sk--short" />
        </div>
      ))}
    </>
  );
}

function SkeletonRows({ n }: { n: number }) {
  return (
    <div className="skrows">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="sk sk--row" />
      ))}
    </div>
  );
}
