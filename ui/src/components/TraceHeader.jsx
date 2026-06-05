import React from 'react'

function ScoreRing({ score, size = 96 }) {
  const strokeW = 7
  const r    = (size - strokeW * 2) / 2
  const circ = 2 * Math.PI * r
  const dash = circ * score
  const color = score >= 0.8 ? 'var(--green)' : score >= 0.5 ? 'var(--amber)' : 'var(--red)'
  const cx = size / 2, cy = size / 2

  return (
    <div className="score-ring-rel" style={{ width: size, height: size }}>
      <svg width={size} height={size} style={{ display: 'block' }}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--border2)" strokeWidth={strokeW} />
        <circle
          cx={cx} cy={cy} r={r}
          fill="none" stroke={color} strokeWidth={strokeW}
          strokeDasharray={`${dash} ${circ}`}
          strokeLinecap="round"
          transform={`rotate(-90 ${cx} ${cy})`}
          style={{ transition: 'stroke-dasharray 0.6s ease' }}
        />
      </svg>
      <div className="score-ring-num" style={{ color }}>
        {Math.round(score * 100)}
      </div>
    </div>
  )
}

function SubScore({ label, value }) {
  const color = value >= 0.8 ? 'var(--green)' : value >= 0.5 ? 'var(--amber)' : 'var(--red)'
  return (
    <div className="sub-score-item">
      {label}: <b style={{ color }}>{value?.toFixed(3)}</b>
    </div>
  )
}

export default function TraceHeader({ detail }) {
  const fa     = detail.failure_attribution
  const issues = detail.plan_result?.issues || []

  return (
    <div className="trace-header-card">
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 8 }}>
          <h2 style={{ fontSize: 17, fontWeight: 700 }}>{detail.trace_id}</h2>
          {detail.run_date && (
            <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'JetBrains Mono, monospace' }}>
              {detail.run_date}
            </span>
          )}
          <span className="dataset-tag">{detail.dataset_file}</span>
        </div>

        {/* Prompt block */}
        <div className="prompt-block">
          <div className="prompt-block-label">User Prompt</div>
          <div className="prompt-block-text">{detail.user_prompt}</div>
        </div>

        {/* Score row */}
        <div className="trace-scores-row">
          <SubScore label="Plan Score" value={detail.plan_score} />
          <div className="sub-score-sep" />
          <SubScore label="Avg Step Score" value={detail.avg_step_score} />
          <div className="sub-score-sep" />
          <span style={{ fontSize: 10, color: 'var(--muted)' }}>
            trajectory = plan × 0.30 + steps × 0.70
          </span>
        </div>

        {/* Issues */}
        {issues.length > 0 && (
          <div className="issues-list">
            {issues.map((iss, i) => (
              <div key={i} className="issue-row">{iss}</div>
            ))}
          </div>
        )}

        {!fa?.failure_transition_step && (
          <div style={{ marginTop: 10, fontSize: 11, color: 'var(--green)', fontWeight: 600 }}>
            All steps passed — no failures detected
          </div>
        )}
      </div>

      <div className="score-ring-wrap">
        <ScoreRing score={detail.trajectory_score} />
        <div className="score-ring-label">Trajectory Score</div>
      </div>
    </div>
  )
}
