import React, { useState } from 'react'

function scoreColor(s) {
  if (s >= 0.8) return 'var(--green)'
  if (s >= 0.5) return 'var(--amber)'
  return 'var(--red)'
}

function ftColor(ft) {
  const map = {
    computation_error: 'var(--red)',
    context_loss:      'var(--amber)',
    reasoning_error:   'var(--purple)',
    parsing_error:     'var(--blue)',
    inherited:         'var(--amber)',
    null:              'var(--green)',
  }
  return map[ft] || 'var(--muted)'
}

function ftLabel(ft) {
  if (!ft || ft === 'null') return null
  return ft.replace(/_/g, ' ')
}

const ACTION_LABEL = {
  data_parsing:         'Data Parsing',
  computation:          'Computation',
  context_handoff:      'Context Handoff',
  report_structuring:   'Report Structuring',
  narrative_generation: 'Narrative Generation',
}

function CriterionCard({ criterion, gradeEntry }) {
  const [showRationale, setShowRationale] = useState(false)
  const pass   = gradeEntry?.pass !== false
  const cscore = typeof gradeEntry?.score === 'number' ? gradeEntry.score : (pass ? 1 : 0)
  const rationale = gradeEntry?.rationale

  return (
    <div className={`criterion-card ${pass ? 'c-pass' : 'c-fail'}`}>
      <div className="criterion-top">
        <div className={`criterion-pass-dot ${pass ? 'dot-pass' : 'dot-fail'}`} />
        <div style={{ flex: 1 }}>
          <span className="criterion-id">{criterion.id}</span>
          <span className="criterion-desc">{criterion.description}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {rationale && (
            <button
              className="rationale-toggle-btn"
              onClick={() => setShowRationale(v => !v)}
            >
              {showRationale ? 'Hide rationale' : 'See rationale'}
            </button>
          )}
          <span className="criterion-score-num">{cscore.toFixed(2)}</span>
        </div>
      </div>
      <div className="criterion-bar">
        <div
          className="criterion-bar-fill"
          style={{ width: `${cscore * 100}%`, background: pass ? 'var(--green)' : 'var(--red)' }}
        />
      </div>
      {showRationale && rationale && (
        <div className="criterion-rationale-box">
          <span className="criterion-rationale-label">Grader rationale</span>
          <div className="criterion-rationale">"{rationale}"</div>
        </div>
      )}
    </div>
  )
}

export default function StepDetail({ stepResult }) {
  const [section, setSection] = useState('evaluation')
  if (!stepResult) return null

  const { step_number, app, action_type, step, rubric, grade } = stepResult
  const criteria  = rubric?.criteria || []
  const cGrades   = grade?.criterion_grades || []
  const gradeMap  = Object.fromEntries(cGrades.map(g => [g.id, g]))
  const cCriteria = criteria.filter(c => c.id && c.id.startsWith('C'))
  const rubricModel = rubric?._llm_model || rubric?._model || 'unknown'
  const gradeModel = grade?._llm_model || grade?._model || 'unknown'
  const tools     = step?.tools_called || []
  const keyFacts  = step?.key_facts_produced || []
  const output    = step?.output || ''
  const stepScore = grade?.step_score ?? 0
  const stepPass  = grade?.step_pass ?? true
  const ftype     = grade?.failure_type

  const groundTruth = step?.ground_truth || {}
  const gtEntries = Object.entries(groundTruth)

  const SECTIONS = [
    { id: 'evaluation', label: 'Step Evaluation' },
    { id: 'trajectory', label: 'Agent Trajectory' },
  ]

  return (
    <div className="step-detail-card">
      {/* Header */}
      <div className="step-detail-header">
        <div>
          <div className="step-detail-title">
            Step {step_number} — {app} / {ACTION_LABEL[action_type] || action_type}
          </div>
          {step?.latency_observed && (
            <div className="step-detail-subtitle">{step.latency_observed}</div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className={`pass-fail-chip ${stepPass ? 'badge-pass' : 'badge-fail'}`}>
            {stepPass ? 'PASS' : 'FAIL'}
          </span>
          <span style={{ fontSize: 22, fontWeight: 800, color: scoreColor(stepScore) }}>
            {stepScore.toFixed(3)}
          </span>
        </div>
      </div>

      {/* Section switcher */}
      <div className="step-section-bar">
        {SECTIONS.map(s => (
          <button
            key={s.id}
            onClick={() => setSection(s.id)}
            className={`step-section-btn${section === s.id ? ' active' : ''}`}
          >
            {s.label}
          </button>
        ))}
        <div className="step-section-spacer" />
        <span className="step-section-note">
          {section === 'evaluation'
            ? 'Rubric-based scoring — how the evaluator judged this step'
            : 'What the agent actually did during this step'
          }
        </span>
      </div>

      <div className="step-detail-body">

        {/* ── Step Evaluation ── */}
        {section === 'evaluation' && (
          <>
            {/* LLM attribution row */}
            <div className="llm-attribution-row">
              <div className="llm-attr-block">
                <span className="llm-attr-label">Rubric generated by</span>
                <span className="rubric-model-tag">{rubricModel}</span>
              </div>
              <div className="llm-attr-divider" />
              <div className="llm-attr-block">
                <span className="llm-attr-label">Graded by</span>
                <span className="rubric-model-tag">{gradeModel}</span>
              </div>
              <div className="llm-attr-divider" />
              <div className="llm-attr-block">
                <span className="llm-attr-label">Criteria</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>{cCriteria.length}</span>
              </div>
            </div>

            {/* Criteria cards */}
            <div>
              <div className="detail-block-label">Evaluation Criteria &amp; Scores</div>
              <div className="criterion-cards">
                {cCriteria.map(c => (
                  <CriterionCard
                    key={c.id}
                    criterion={c}
                    gradeEntry={gradeMap[c.id]}
                  />
                ))}
              </div>
            </div>

            {/* Ground Truth Evidence */}
            {gtEntries.length > 0 && (
              <div>
                <div className="detail-block-label">Ground Truth vs Agent Output</div>
                <div style={{
                  background: 'var(--bg2)', border: '1px solid var(--border)',
                  borderRadius: 8, padding: '0.6rem 0.8rem', fontSize: 12,
                }}>
                  {gtEntries.map(([key, val]) => {
                    const label = key.replace(/^expected_/, '').replace(/_/g, ' ')
                    const display = Array.isArray(val) ? val.join(', ') : String(val)
                    const matchingFact = keyFacts.find(f =>
                      f.toLowerCase().includes(label.split(' ')[0].toLowerCase())
                    )
                    const agentVal = matchingFact
                      ? matchingFact.split(':').slice(1).join(':').trim()
                      : null
                    const mismatch = agentVal && display && agentVal.replace(/,/g, '') !== display.replace(/,/g, '')
                    return (
                      <div key={key} style={{
                        display: 'flex', gap: 8, padding: '4px 0',
                        borderBottom: '1px solid var(--border)',
                      }}>
                        <span style={{ color: 'var(--muted)', minWidth: 140, fontWeight: 600, textTransform: 'capitalize' }}>
                          {label}
                        </span>
                        <span style={{ color: 'var(--green)', fontWeight: 500 }}>
                          {display || '—'}
                        </span>
                        {agentVal && (
                          <span style={{
                            marginLeft: 'auto',
                            color: mismatch ? 'var(--red)' : 'var(--muted)',
                            fontWeight: mismatch ? 700 : 400,
                          }}>
                            Agent: {agentVal} {mismatch ? '✗' : '✓'}
                          </span>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {ftype && ftype !== 'null' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                <span style={{ color: 'var(--muted)' }}>Failure type detected:</span>
                <span style={{ fontWeight: 700, color: ftColor(ftype) }}>{ftLabel(ftype)}</span>
              </div>
            )}
          </>
        )}

        {/* ── Agent Trajectory ── */}
        {section === 'trajectory' && (
          <>
            <div>
              <div className="detail-block-label">Step Output</div>
              <div className="detail-text">{output || '—'}</div>
            </div>

            {tools.length > 0 && (
              <div>
                <div className="detail-block-label">Tools Called</div>
                <div className="tools-row">
                  {tools.map((t, i) => <span key={i} className="tool-chip">{t}</span>)}
                </div>
              </div>
            )}

            <div>
              <div className="detail-block-label">Key Facts Produced ({keyFacts.length})</div>
              {keyFacts.length > 0
                ? (
                  <div className="facts-list">
                    {keyFacts.map((f, i) => <div key={i} className="fact-chip">{f}</div>)}
                  </div>
                )
                : <div style={{ color: 'var(--muted)', fontSize: 12 }}>None recorded</div>
              }
            </div>
          </>
        )}

      </div>
    </div>
  )
}
