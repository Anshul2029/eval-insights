import React from 'react'

const ACTION_SHORT = {
  data_parsing:         'Parsing',
  computation:          'Computation',
  context_handoff:      'Handoff',
  report_structuring:   'Structuring',
  narrative_generation: 'Narrative',
}

function stepClass(score) {
  if (score >= 0.8) return 'step-card sc-green'
  if (score >= 0.5) return 'step-card sc-amber'
  return 'step-card sc-red'
}

function dotColor(pass) {
  return pass ? 'var(--green)' : 'var(--red)'
}

function CriterionDots({ stepResult }) {
  const allCriteria  = stepResult.rubric?.criteria || []
  const criteria     = allCriteria.filter(c => c.id && c.id.startsWith('C'))
  const criteriaGrades = stepResult.grade?.criterion_grades || []
  const gradeMap     = Object.fromEntries(criteriaGrades.map(g => [g.id, g]))

  if (criteria.length === 0) return null

  return (
    <div className="step-criterion-dots" title="Click step to see full rubric detail">
      {criteria.map(c => {
        const g    = gradeMap[c.id] || {}
        const pass = g.pass !== false
        const score = typeof g.score === 'number' ? g.score : (pass ? 1 : 0)
        return (
          <div
            key={c.id}
            className="criterion-dot-wrap"
            title={`${c.id}: ${c.description} — score ${score.toFixed(2)}`}
          >
            <div
              className="criterion-dot"
              style={{ background: dotColor(pass) }}
            />
            <span className="criterion-dot-label">{c.id}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function StepTimeline({ stepResults, selectedStep, onSelectStep, failureAttribution }) {
  if (!stepResults?.length) return null
  const rootStep     = failureAttribution?.failure_transition_step
  const contaminated = new Set(failureAttribution?.contaminated_steps || [])

  return (
    <div className="timeline-card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div className="section-label" style={{ marginBottom: 0 }}>Step Timeline</div>
        <span style={{ fontSize: 10, color: 'var(--muted)', fontStyle: 'italic' }}>
          Click any step card to inspect its rubric and evaluation detail
        </span>
      </div>

      <div className="step-track">
        {stepResults.map((sr, i) => {
          const score       = sr.grade?.step_score ?? 0
          const pass        = sr.grade?.step_pass ?? true
          const isRoot      = sr.step_number === rootStep
          const isInherited = contaminated.has(sr.step_number)
          const isSelected  = selectedStep === sr.step_number
          const latency     = sr.step?.latency_observed || ''

          return (
            <React.Fragment key={sr.step_number}>
              {i > 0 && (
                <div className="step-connector">
                  <div className="connector-arrow" />
                </div>
              )}
              <div
                className={`${stepClass(score)}${isSelected ? ' selected' : ''}`}
                onClick={() => onSelectStep(isSelected ? null : sr.step_number)}
              >
                {isRoot      && <div className="root-cause-badge">ROOT CAUSE</div>}
                {isInherited && !isRoot && <div className="inherited-badge">INHERITED</div>}

                <div className="step-num-label">Step {sr.step_number}</div>
                <div className="step-app-label">{sr.app}</div>
                <div className="step-action-label">{ACTION_SHORT[sr.action_type] || sr.action_type}</div>

                <div className="step-score-row2">
                  <div className="step-big-score">{score.toFixed(2)}</div>
                  <span className={`pass-fail-chip ${pass ? 'badge-pass' : 'badge-fail'}`}>
                    {pass ? 'PASS' : 'FAIL'}
                  </span>
                </div>

                {/* Criterion dots — quick rubric status at a glance */}
                <CriterionDots stepResult={sr} />

                {latency && <div className="step-latency">{latency}</div>}
              </div>
            </React.Fragment>
          )
        })}
      </div>

    </div>
  )
}
