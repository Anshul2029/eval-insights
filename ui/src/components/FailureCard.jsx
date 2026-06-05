import React from 'react'

const TYPE_LABELS = {
  computation_error: 'Computation Error',
  context_loss:      'Context Loss',
  reasoning_error:   'Reasoning Error',
  parsing_error:     'Parsing Error',
  inherited:         'Inherited Failure',
}

const DESCRIPTIONS = {
  computation_error: 'The step performed an incorrect calculation or used the wrong methodology.',
  context_loss:      'Facts computed in Excel were dropped at the Excel to Word handoff boundary.',
  reasoning_error:   'The narrative or recommendations misinterpreted correct underlying data.',
  parsing_error:     'Data loading or schema handling failed — wrong columns, types, or row counts.',
  inherited:         'This step failed only because a prior step did not produce required inputs.',
}

const TYPE_COLOR = {
  computation_error: 'var(--red)',
  context_loss:      'var(--amber)',
  reasoning_error:   'var(--purple)',
  parsing_error:     'var(--blue)',
  inherited:         'var(--amber)',
}

export default function FailureCard({ failureAttribution }) {
  const fa = failureAttribution
  if (!fa?.failure_transition_step) return null

  const desc  = DESCRIPTIONS[fa.failure_type] || ''
  const color = TYPE_COLOR[fa.failure_type] || 'var(--red)'
  const label = TYPE_LABELS[fa.failure_type] || fa.failure_type?.replace(/_/g, ' ') || 'Unknown'
  const failingCriteria = fa.failing_criteria || []
  const gtMismatches = fa.ground_truth_mismatches || []

  return (
    <div className="failure-card">
      <div className="failure-card-header">
        <div className="failure-type-indicator" style={{ background: color }} />
        <span className="failure-card-title">Failure Attribution</span>
        <span className="failure-type-chip" style={{ color, borderColor: color }}>
          {label}
        </span>
      </div>

      <div className="failure-grid">
        <div className="failure-cell">
          <div className="failure-cell-label">Root Cause Step</div>
          <div className="failure-cell-value">Step {fa.failure_transition_step}</div>
        </div>
        <div className="failure-cell">
          <div className="failure-cell-label">Application</div>
          <div className="failure-cell-value">{fa.root_cause_app}</div>
        </div>
        <div className="failure-cell">
          <div className="failure-cell-label">Failure Type</div>
          <div className="failure-cell-value" style={{ color }}>{label}</div>
        </div>
      </div>

      {desc && (
        <div className="failure-desc">{desc}</div>
      )}

      {/* Evidence: failing criteria with rationales */}
      {failingCriteria.length > 0 && (
        <div className="failure-evidence-section">
          <div className="failure-evidence-label">Evidence — Why It Failed</div>
          {failingCriteria.map((fc, i) => (
            <div key={i} className="failure-evidence-row">
              <div className="failure-evidence-crit">
                <span className="failure-evidence-cid">{fc.criterion}</span>
                <span className="failure-evidence-score" style={{
                  color: fc.score === 0 ? 'var(--red)' : fc.score < 0.5 ? 'var(--amber)' : 'var(--muted)'
                }}>
                  {fc.score.toFixed(2)}
                </span>
              </div>
              <div className="failure-evidence-rationale">"{fc.rationale}"</div>
            </div>
          ))}
        </div>
      )}

      {/* Ground truth mismatches */}
      {gtMismatches.length > 0 && (
        <div className="failure-evidence-section">
          <div className="failure-evidence-label">Ground Truth Mismatches</div>
          {gtMismatches.map((m, i) => (
            <div key={i} className="failure-gt-row">
              <span className="failure-gt-field">{m.field}</span>
              <div className="failure-gt-values">
                <span className="failure-gt-expected">Expected: {m.expected}</span>
                <span className="failure-gt-actual">Agent reported: {m.actual}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="fix-box">
        <div className="fix-label">Recommended Fix</div>
        <div className="fix-text">{fa.fix_recommendation}</div>
      </div>

      {fa.contaminated_steps?.length > 0 && (
        <div className="contaminated-row">
          <span>Contaminated steps:</span>
          {fa.contaminated_steps.map(s => (
            <span key={s} className="contaminated-pill">Step {s}</span>
          ))}
          <span style={{ color: 'var(--muted)', fontSize: 10 }}>
            — fix root cause first; these are downstream consequences
          </span>
        </div>
      )}
    </div>
  )
}
