import React, { useState } from 'react'

const METRICS = [
  {
    key:     'step_completeness',
    backendKey: 'anomaly_recall',
    label:   'Step Completeness',
    weight:  0.20,
    description: 'Did the agent perform all required action types for this task?',
  },
  {
    key:     'goal_adherence',
    backendKey: 'plan_quality',
    label:   'Goal Adherence',
    weight:  0.20,
    description: 'Did every step in the trajectory serve the user\'s original goal?',
  },
  {
    key:     'sequence_validity',
    backendKey: 'sequence_validity',
    label:   'Sequence Validity',
    weight:  0.20,
    description: 'Did the agent do things in the right order?',
  },
  {
    key:     'source_completeness',
    backendKey: 'app_coverage',
    label:   'Source Completeness',
    weight:  0.20,
    description: 'Did the agent consult all the sources the task required?',
  },
  {
    key:     'hallucination_plan',
    backendKey: 'false_positive_check',
    label:   'Hallucination at Plan Level',
    weight:  0.20,
    description: 'Did the agent invent entities, attributes, or claims not in the source data?',
  },
]

function metricColor(v) {
  if (v >= 0.8) return 'var(--green)'
  if (v >= 0.5) return 'var(--amber)'
  return 'var(--red)'
}

function MetricRow({ metric, value }) {
  const [open, setOpen] = useState(false)
  const color = metricColor(value)
  const pct   = Math.round(value * 100)
  const isFail = value === 0

  return (
    <div className={`plan-metric-block${isFail ? ' plan-metric-block--fail' : ''}`}>
      <div className="plan-metric-row">
        <span className="plan-metric-label">{metric.label}</span>
        <button
          className="metric-info-btn"
          onClick={() => setOpen(o => !o)}
          title="What does this metric mean?"
        >
          {open ? 'Hide' : 'What is this?'}
        </button>
        <span className="plan-metric-pct" style={{ color }}>{pct}%</span>
        <span className="plan-metric-weight">x{metric.weight.toFixed(2)}</span>
      </div>
      <div className="plan-metric-bar-track">
        <div
          className="plan-metric-bar-fill"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      {open && (
        <div className="metric-desc-box">
          {metric.description}
        </div>
      )}
    </div>
  )
}

export default function PlanBreakdown({ planResult }) {
  if (!planResult) return null
  const mb = planResult.metric_breakdown || {}

  const score = planResult.plan_score ?? 0
  const scoreColor = metricColor(score)

  return (
    <div className="plan-breakdown-card" style={{ gridColumn: '1 / -1' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
        <div className="section-label" style={{ marginBottom: 0 }}>Plan Evaluation</div>
        <div style={{ fontSize: 11, color: 'var(--muted)' }}>
          Plan score: <span style={{ fontWeight: 700, color: scoreColor }}>{score.toFixed(4)}</span>
        </div>
      </div>

      <div className="plan-metrics">
        {METRICS.map(m => (
          <MetricRow key={m.key} metric={m} value={mb[m.backendKey] ?? 0} />
        ))}
      </div>

      <div className="plan-formula-box">
        <b>plan_score</b> = step_completeness×0.20 + goal_adherence×0.20 + sequence×0.20 + source×0.20 + hallucination×0.20
        {' = '}<b style={{ color: scoreColor }}>{score.toFixed(4)}</b>
      </div>
    </div>
  )
}
