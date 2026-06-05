import React from 'react'

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

export default function Overview({ traces, onSelect }) {
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
  const failing = traces.filter(t => t.failure_transition_step)

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

      {/* All traces table */}
      <div className="overview-table-card">
        <table className="overview-table">
          <thead>
            <tr>
              <th>Trace ID</th>
              <th>Dataset</th>
              <th>Trajectory Score</th>
              <th>Plan</th>
              <th>Avg Steps</th>
              <th>Status</th>
              <th>Root Cause</th>
            </tr>
          </thead>
          <tbody>
            {traces.map(t => (
              <tr key={t.trace_id} onClick={() => onSelect(t.trace_id)}>
                <td style={{ fontWeight: 600, color: 'var(--blue)', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>{t.trace_id}</td>
                <td style={{ color: 'var(--muted)', fontSize: 11 }}>{t.dataset_file}</td>
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
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
