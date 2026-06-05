import React, { useState, useCallback } from 'react'
import axios from 'axios'
import TraceHeader from './TraceHeader.jsx'
import StepTimeline from './StepTimeline.jsx'
import StepDetail from './StepDetail.jsx'
import PlanBreakdown from './PlanBreakdown.jsx'
import FailureCard from './FailureCard.jsx'
import ContextManifest from './ContextManifest.jsx'

function ScoreBar({ score, color }) {
  return (
    <div className="score-bar-cell">
      <div style={{ fontWeight: 700, fontSize: 12, color, width: 36 }}>
        {Math.round(score * 100)}
      </div>
      <div className="score-bar-track">
        <div className="score-bar-fill" style={{ width: `${score * 100}%`, background: color }} />
      </div>
    </div>
  )
}

function scoreColor(s) {
  if (s >= 0.85) return 'var(--green)'
  return 'var(--red)'
}

function StatusChip({ score }) {
  if (score >= 0.85) return <span className="status-chip chip-pass">PASS</span>
  return <span className="status-chip chip-fail">FAIL</span>
}

function TraceRow({ trace }) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [selectedStep, setSelectedStep] = useState(null)

  const toggle = useCallback(() => {
    if (!expanded && !detail) {
      setLoading(true)
      axios.get(`/trace/${trace.trace_id}`)
        .then(r => setDetail(r.data))
        .catch(console.error)
        .finally(() => setLoading(false))
    }
    setExpanded(e => !e)
  }, [expanded, detail, trace.trace_id])

  const t = trace
  const stepResults = detail?.step_results || []
  const fa = detail?.failure_attribution
  const selectedStepResult = stepResults.find(sr => sr.step_number === selectedStep)

  return (
    <>
      <tr onClick={toggle} style={{ cursor: 'pointer' }}>
        <td style={{ fontWeight: 600, color: 'var(--blue)', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>{t.trace_id}</td>
        <td style={{ color: 'var(--muted)', fontSize: 11 }}>{t.dataset_file}</td>
        <td style={{ color: 'var(--muted)', fontSize: 11 }}>{t.user_prompt}</td>
        <td><ScoreBar score={t.trajectory_score} color={scoreColor(t.trajectory_score)} /></td>
        <td style={{ fontSize: 11, color: scoreColor(t.plan_score) }}>{t.plan_score?.toFixed(3)}</td>
        <td style={{ fontSize: 11, color: scoreColor(t.avg_step_score) }}>{t.avg_step_score?.toFixed(3)}</td>
        <td><StatusChip score={t.trajectory_score} /></td>
        <td style={{ fontSize: 11, color: 'var(--muted)' }}>
          {t.failure_transition_step
            ? <span style={{ color: 'var(--red)' }}>Step {t.failure_transition_step} · {t.failure_type?.replace(/_/g,' ')}</span>
            : t.step_completeness_failed
              ? <span style={{ color: 'var(--red)' }}>Step completeness fail</span>
              : <span style={{ color: 'var(--green)' }}>—</span>
          }
        </td>
      </tr>
      <tr className="trajectory-toggle-row">
        <td colSpan={8} style={{ padding: 0, border: 'none' }}>
          <div
            className={`trajectory-toggle${expanded ? ' open' : ''}`}
            onClick={toggle}
          >
            <span className="trajectory-toggle-arrow">{expanded ? '▼' : '▶'}</span>
            <span className="trajectory-toggle-label">Trajectory</span>
          </div>
          {expanded && (
            <div className="trajectory-dropdown">
              {loading && (
                <div style={{ padding: 20, color: 'var(--muted)', fontSize: 12 }}>Loading trajectory…</div>
              )}
              {detail && !loading && (
                <div className="trajectory-content">
                  <TraceHeader detail={detail} />
                  <StepTimeline
                    stepResults={stepResults}
                    selectedStep={selectedStep}
                    onSelectStep={setSelectedStep}
                    failureAttribution={fa}
                  />
                  {selectedStepResult && <StepDetail stepResult={selectedStepResult} />}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 16 }}>
                    <PlanBreakdown planResult={detail.plan_result} />
                  </div>
                  {fa?.failure_transition_step && <FailureCard failureAttribution={fa} />}
                  {detail.context_result && <ContextManifest contextResult={detail.context_result} />}
                </div>
              )}
            </div>
          )}
        </td>
      </tr>
    </>
  )
}

export default function Overview({ traces }) {
  if (!traces.length) {
    return (
      <div className="empty-state">
        No results found. Run <code>python run_eval.py --mock</code> first.
      </div>
    )
  }

  const pass  = traces.filter(t => t.trajectory_score >= 0.85).length
  const amber = 0
  const fail  = traces.filter(t => t.trajectory_score < 0.85).length
  const avg   = traces.reduce((s, t) => s + t.trajectory_score, 0) / traces.length

  return (
    <>
      {/* Stat cards */}
      <div className="overview-grid">
        <div className="stat-card">
          <div className="stat-card-label">Traces Evaluated</div>
          <div className="stat-card-value stat-blue">{traces.length}</div>
          <div className="stat-card-sub">total runs</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-label">Pass Rate</div>
          <div className="stat-card-value stat-green">{Math.round(pass / traces.length * 100)}%</div>
          <div className="stat-card-sub">{pass} of {traces.length} passed (&ge;0.85)</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-label">Avg Trajectory Score</div>
          <div className="stat-card-value" style={{ color: scoreColor(avg) }}>{avg.toFixed(3)}</div>
          <div className="stat-card-sub">across all traces</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-label">Failure Rate</div>
          <div className="stat-card-value stat-red">{Math.round(fail / traces.length * 100)}%</div>
          <div className="stat-card-sub">{fail} below 0.85</div>
        </div>
      </div>

      {/* Distribution bar */}
      <div className="card">
        <div className="section-label">Score Distribution</div>
        <div style={{ display: 'flex', gap: 4, height: 28, borderRadius: 6, overflow: 'hidden' }}>
          {pass > 0 && (
            <div style={{ flex: pass, background: 'var(--green-dim)', border: '1px solid var(--green-bd)', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--green)' }}>{pass} PASS</span>
            </div>
          )}
          {amber > 0 && (
            <div style={{ flex: amber, background: 'var(--amber-dim)', border: '1px solid var(--amber-bd)', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--amber)' }}>{amber} AMBER</span>
            </div>
          )}
          {fail > 0 && (
            <div style={{ flex: fail, background: 'var(--red-dim)', border: '1px solid var(--red-bd)', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--red)' }}>{fail} FAIL</span>
            </div>
          )}
        </div>
      </div>

      {/* All traces table with expandable trajectory rows */}
      <div className="overview-table-card">
        <table className="overview-table">
          <thead>
            <tr>
              <th>Trace ID</th>
              <th>Dataset</th>
              <th>Query Text</th>
              <th>Trajectory Score</th>
              <th>Plan</th>
              <th>Avg Steps</th>
              <th>Status</th>
              <th>Root Cause</th>
            </tr>
          </thead>
          <tbody>
            {traces.map(t => (
              <TraceRow key={t.trace_id} trace={t} />
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
