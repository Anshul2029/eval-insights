import React, { useState } from 'react'

const LLM_COLORS = {
  gemini:    '#4285F4',
  groq:      '#F55036',
  qwen:      '#8B5CF6',
  mixtral:   '#A855F7',
  ollama:    '#6B7280',
  gpt4omini: '#10A37F',
}

const STEP_LABELS = {
  0: 'planning',
  1: 'data_parsing',
  2: 'computation',
  3: 'context_handoff',
  4: 'report_structuring',
  5: 'narrative_generation',
}

function scoreColor(s) {
  if (s >= 0.8) return 'var(--green)'
  if (s >= 0.5) return 'var(--amber)'
  return 'var(--red)'
}

function llmColor(llm) {
  return LLM_COLORS[llm] || '#888'
}

function LLMBadge({ llm, model }) {
  return (
    <span style={{
      background: llmColor(llm) + '22',
      color: llmColor(llm),
      border: `1px solid ${llmColor(llm)}55`,
      borderRadius: 4, padding: '2px 8px',
      fontSize: 11, fontWeight: 700, fontFamily: 'monospace',
    }}>
      {llm.toUpperCase()}
      {model && <span style={{ fontWeight: 400, opacity: 0.8 }}> / {model}</span>}
    </span>
  )
}

function ScoreBar({ score, width = 120, height = 8 }) {
  const pct = Math.min(100, score * 100)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width, height, background: '#ffffff11', borderRadius: 4, overflow: 'hidden', flexShrink: 0 }}>
        <div style={{ width: `${pct}%`, height: '100%', background: scoreColor(score), borderRadius: 4 }} />
      </div>
      <span style={{ fontSize: 11, fontFamily: 'monospace', color: scoreColor(score), fontWeight: 700 }}>
        {score.toFixed(3)}
      </span>
    </div>
  )
}

const API = 'http://localhost:5001'

export default function ComparisonView({ data }) {
  const [expandedStep, setExpandedStep] = useState(null)

  if (!data) return <div className="empty-state">Loading comparison…</div>

  const llms = data.llms_compared || []
  const compId = data.comparison_id

  // Detect which token columns actually have data
  const allPerStep = llms.flatMap(llm => data[llm]?.token_usage?.per_step || [])
  const hasThinking = allPerStep.some(s => (s.thinking_tokens || 0) > 0)
  const hasCached   = allPerStep.some(s => (s.cached_tokens  || 0) > 0)

  // All step numbers across all LLMs (token steps)
  const allStepNums = [...new Set(allPerStep.map(s => s.step_number))].sort((a, b) => a - b)

  // All evaluated step numbers (from step_results)
  const evalStepNums = [...new Set(
    llms.flatMap(llm => (data[llm]?.step_results || []).map(r => r.step_number))
  )].sort((a, b) => a - b)

  const totalMax = Math.max(...llms.map(llm => data[llm]?.token_usage?.total?.total_tokens || 0))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* ── Header ── */}
      <div className="card">
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 700, fontSize: 16, color: 'var(--fg)' }}>
            {data.comparison_id}
          </span>
          {llms.map(llm => (
            <LLMBadge key={llm} llm={llm} model={data[llm]?.llm_model} />
          )).reduce((acc, el) => [acc, <span key="vs" style={{ color: 'var(--muted)', fontSize: 12 }}> vs </span>, el])}
        </div>
        {data[llms[0]]?.trace?.user_prompt && (
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--muted)', fontStyle: 'italic' }}>
            "{data[llms[0]].trace.user_prompt}"
          </div>
        )}
        {data[llms[0]]?.trace?.dataset_file && (
          <div style={{ marginTop: 4, fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace' }}>
            dataset: {data[llms[0]].trace.dataset_file}
          </div>
        )}
        {/* Grader info */}
        {(() => {
          const graderModels = [...new Set(llms.flatMap(llm =>
            (data[llm]?.step_results || []).map(sr => sr.grade?._model).filter(Boolean)
          ))]
          const rubricModels = [...new Set(llms.flatMap(llm =>
            (data[llm]?.step_results || []).map(sr => sr.rubric?._model).filter(Boolean)
          ))]
          return (graderModels.length > 0 || rubricModels.length > 0) ? (
            <div style={{ marginTop: 8, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
              {rubricModels.length > 0 && (
                <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                  Rubric by: <span style={{ fontFamily: 'monospace', color: 'var(--fg)' }}>{rubricModels.join(', ')}</span>
                </span>
              )}
              {graderModels.length > 0 && (
                <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                  Graded by: <span style={{ fontFamily: 'monospace', color: 'var(--fg)' }}>{graderModels.join(', ')}</span>
                </span>
              )}
            </div>
          ) : null
        })()}
        {/* Download buttons */}
        <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {llms.map(llm => {
            const docxPath = data[llm]?.trace?.word_doc_path
            if (!docxPath) return null
            return (
              <a key={llm}
                href={`${API}/comparison/${compId}/docx/${llm}`}
                download
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                  padding: '4px 10px', borderRadius: 4, fontSize: 11, fontWeight: 600,
                  background: llmColor(llm) + '22', color: llmColor(llm),
                  border: `1px solid ${llmColor(llm)}55`,
                  textDecoration: 'none', cursor: 'pointer',
                }}>
                ↓ {llm.toUpperCase()} Word doc
              </a>
            )
          })}
        </div>
      </div>

      {/* ── Score summary cards ── */}
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${llms.length}, 1fr)`, gap: 12 }}>
        {llms.map(llm => {
          const traj  = data[llm]?.trajectory_score || 0
          const plan  = data[llm]?.plan_score || 0
          const steps = data[llm]?.avg_step_score || 0
          const tok   = data[llm]?.token_usage?.total || {}
          const fa    = data[llm]?.failure_attribution || {}

          return (
            <div key={llm} className="card" style={{ borderTop: `3px solid ${llmColor(llm)}` }}>
              <LLMBadge llm={llm} model={data[llm]?.llm_model} />

              {/* scores */}
              <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {[['Trajectory', traj], ['Plan Score', plan], ['Avg Step', steps]].map(([label, val]) => (
                  <div key={label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                    <span style={{ color: 'var(--muted)' }}>{label}</span>
                    <span style={{ fontWeight: 700, color: scoreColor(val) }}>{val.toFixed(3)}</span>
                  </div>
                ))}

                {/* tokens */}
                <div style={{ borderTop: '1px solid var(--border)', marginTop: 4, paddingTop: 6 }} />
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                  <span style={{ color: 'var(--muted)' }}>Total Tokens</span>
                  <span style={{ fontWeight: 700, color: 'var(--blue)', fontFamily: 'monospace' }}>
                    {(tok.total_tokens || 0).toLocaleString()}
                  </span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                  <span style={{ color: 'var(--muted)' }}>Input</span>
                  <span style={{ fontFamily: 'monospace', color: 'var(--fg)' }}>{(tok.input_tokens || 0).toLocaleString()}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                  <span style={{ color: 'var(--muted)' }}>Output</span>
                  <span style={{ fontFamily: 'monospace', color: 'var(--fg)' }}>{(tok.output_tokens || 0).toLocaleString()}</span>
                </div>
                {hasThinking && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                    <span style={{ color: 'var(--muted)' }}>Thinking</span>
                    <span style={{ fontFamily: 'monospace', color: 'var(--amber)' }}>{(tok.thinking_tokens || 0).toLocaleString()}</span>
                  </div>
                )}
                {hasCached && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                    <span style={{ color: 'var(--muted)' }}>Cached</span>
                    <span style={{ fontFamily: 'monospace', color: 'var(--muted)' }}>{(tok.cached_tokens || 0).toLocaleString()}</span>
                  </div>
                )}

                {/* failure attribution */}
                {fa.failure_transition_step && (
                  <div style={{ borderTop: '1px solid var(--border)', marginTop: 4, paddingTop: 6 }}>
                    <div style={{ fontSize: 11, color: 'var(--red)', fontWeight: 700 }}>
                      Root cause: Step {fa.failure_transition_step} ({fa.failure_type})
                    </div>
                    {fa.fix_recommendation && (
                      <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>
                        {fa.fix_recommendation}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* ── Step-by-step comparison with scores ── */}
      {evalStepNums.length > 0 && (
        <div className="card">
          <div className="section-label">Step-by-Step Comparison</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0, marginTop: 8 }}>
            {evalStepNums.map(sn => {
              const isExpanded = expandedStep === sn
              return (
                <div key={sn} style={{ borderBottom: '1px solid var(--border-dim)' }}>
                  {/* Step header row — click to expand */}
                  <div
                    onClick={() => setExpandedStep(isExpanded ? null : sn)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 12, padding: '10px 4px',
                      cursor: 'pointer', userSelect: 'none',
                    }}
                  >
                    <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg)', minWidth: 150 }}>
                      Step {sn}: {STEP_LABELS[sn] || '?'}
                    </span>
                    <div style={{ display: 'flex', gap: 20, flex: 1, flexWrap: 'wrap' }}>
                      {llms.map(llm => {
                        const sr = (data[llm]?.step_results || []).find(r => r.step_number === sn)
                        if (!sr) return null
                        const score = sr.grade?.step_score || 0
                        const pass  = sr.grade?.step_pass
                        return (
                          <div key={llm} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <span style={{ fontSize: 10, fontWeight: 700, color: llmColor(llm), minWidth: 40 }}>
                              {llm.toUpperCase()}
                            </span>
                            <ScoreBar score={score} width={80} />
                            <span style={{ fontSize: 10, color: pass ? 'var(--green)' : 'var(--red)' }}>
                              {pass ? 'PASS' : 'FAIL'}
                            </span>
                            {sr.grade?.failure_type && sr.grade.failure_type !== 'null' && (
                              <span style={{ fontSize: 9, color: 'var(--amber)', fontFamily: 'monospace' }}>
                                [{sr.grade.failure_type}]
                              </span>
                            )}
                          </div>
                        )
                      })}
                    </div>
                    <span style={{ fontSize: 11, color: 'var(--muted)' }}>{isExpanded ? '▲' : '▼'}</span>
                  </div>

                  {/* Expanded detail */}
                  {isExpanded && (
                    <div style={{ paddingBottom: 16 }}>
                      {llms.map(llm => {
                        const sr      = (data[llm]?.step_results || []).find(r => r.step_number === sn)
                        const traceStep = (data[llm]?.trace?.steps || []).find(s => s.step_number === sn)
                        if (!sr && !traceStep) return null

                        const criteria  = sr?.rubric?.criteria || []
                        const cgMap     = Object.fromEntries((sr?.grade?.criterion_grades || []).map(g => [g.id, g]))
                        const output    = traceStep?.output || sr?.step?.output || ''
                        const whatDid   = traceStep?.what_agent_did || sr?.step?.what_agent_did || ''
                        const keyFacts  = traceStep?.key_facts_produced || sr?.step?.key_facts_produced || []
                        const toolsCalled = traceStep?.tools_called || sr?.step?.tools_called || []
                        const latency   = traceStep?.latency_observed || sr?.step?.latency_observed || ''
                        // token breakdown for this step
                        const stepTok   = (data[llm]?.token_usage?.per_step || []).find(x => x.step_number === sn)
                        const rubricModel = sr?.rubric?._model
                        const gradeModel  = sr?.grade?._model

                        return (
                          <div key={llm} style={{
                            margin: '8px 0', padding: '12px',
                            background: llmColor(llm) + '06',
                            border: `1px solid ${llmColor(llm)}22`,
                            borderRadius: 6,
                          }}>
                            {/* header row */}
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
                              <LLMBadge llm={llm} model={data[llm]?.llm_model} />
                              {sr && (
                                <span style={{ fontSize: 12, fontWeight: 700, color: scoreColor(sr.grade?.step_score || 0) }}>
                                  {(sr.grade?.step_score || 0).toFixed(3)}
                                </span>
                              )}
                              {/* token pill for this step */}
                              {stepTok && (
                                <span style={{
                                  fontSize: 10, fontFamily: 'monospace',
                                  background: '#ffffff11', borderRadius: 4, padding: '2px 7px',
                                  color: 'var(--blue)', border: '1px solid #ffffff22',
                                }}>
                                  {stepTok.total_tokens.toLocaleString()} tok
                                  <span style={{ color: 'var(--muted)', marginLeft: 4 }}>
                                    (in {stepTok.input_tokens} / out {stepTok.output_tokens}
                                    {stepTok.cached_tokens > 0 ? ` / cached ${stepTok.cached_tokens}` : ''}
                                    {stepTok.thinking_tokens > 0 ? ` / think ${stepTok.thinking_tokens}` : ''})
                                  </span>
                                </span>
                              )}
                              {/* grader badges */}
                              {rubricModel && (
                                <span style={{ fontSize: 9, color: 'var(--muted)', fontFamily: 'monospace' }}>
                                  rubric: {rubricModel}
                                </span>
                              )}
                              {gradeModel && (
                                <span style={{ fontSize: 9, color: 'var(--muted)', fontFamily: 'monospace' }}>
                                  grader: {gradeModel}
                                </span>
                              )}
                            </div>
                            {/* tools + latency */}
                            {(toolsCalled.length > 0 || latency) && (
                              <div style={{ display: 'flex', gap: 16, marginBottom: 8, flexWrap: 'wrap' }}>
                                {toolsCalled.length > 0 && (
                                  <div style={{ fontSize: 10, color: 'var(--muted)' }}>
                                    <span style={{ fontWeight: 600 }}>Tools: </span>
                                    {toolsCalled.join(', ')}
                                  </div>
                                )}
                                {latency && (
                                  <div style={{ fontSize: 10, color: 'var(--muted)' }}>
                                    <span style={{ fontWeight: 600 }}>Latency: </span>{latency}
                                  </div>
                                )}
                              </div>
                            )}

                            {whatDid && (
                              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, fontStyle: 'italic' }}>
                                {whatDid}
                              </div>
                            )}

                            {output && (
                              <pre style={{
                                fontSize: 10, color: 'var(--fg)', background: '#0008',
                                padding: 8, borderRadius: 4, overflow: 'auto',
                                maxHeight: 200, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                marginBottom: 8, fontFamily: 'monospace',
                              }}>
                                {output}
                              </pre>
                            )}

                            {keyFacts.length > 0 && (
                              <div style={{ marginBottom: 8 }}>
                                <div style={{ fontSize: 10, color: 'var(--muted)', fontWeight: 600, marginBottom: 4 }}>
                                  KEY FACTS
                                </div>
                                {keyFacts.map((f, i) => (
                                  <div key={i} style={{ fontSize: 10, color: 'var(--fg)', fontFamily: 'monospace', paddingLeft: 8 }}>
                                    • {f}
                                  </div>
                                ))}
                              </div>
                            )}

                            {criteria.filter(c => c.id && c.id.startsWith('C')).length > 0 && (
                              <div>
                                <div style={{ fontSize: 10, color: 'var(--muted)', fontWeight: 600, marginBottom: 4 }}>
                                  RUBRIC CRITERIA
                                </div>
                                {criteria.filter(c => c.id && c.id.startsWith('C')).map(c => {
                                  const cg = cgMap[c.id] || {}
                                  const cscore = cg.score ?? 0
                                  const cpass  = cg.pass ?? true
                                  return (
                                    <div key={c.id} style={{
                                      padding: '6px 8px', marginBottom: 4,
                                      background: (cpass ? 'var(--green)' : 'var(--red)') + '11',
                                      borderLeft: `3px solid ${cpass ? 'var(--green)' : 'var(--red)'}`,
                                      borderRadius: '0 4px 4px 0',
                                    }}>
                                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                                        <span style={{ fontSize: 10, fontWeight: 700, color: cpass ? 'var(--green)' : 'var(--red)' }}>
                                          {c.id} — {cpass ? 'PASS' : 'FAIL'} ({cscore.toFixed(2)})
                                        </span>
                                      </div>
                                      <div style={{ fontSize: 10, color: 'var(--fg)' }}>{c.description}</div>
                                      {cg.rationale && (
                                        <div style={{ fontSize: 10, color: 'var(--muted)', fontStyle: 'italic', marginTop: 2 }}>
                                          {cg.rationale}
                                        </div>
                                      )}
                                    </div>
                                  )
                                })}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── Token usage per step table ── */}
      <div className="card">
        <div className="section-label">Token Usage Per Step</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={{ textAlign: 'left', padding: '6px 8px', color: 'var(--muted)', fontWeight: 600 }}>Step</th>
              {llms.map(llm => (
                <th key={llm}
                  colSpan={2 + (hasThinking ? 1 : 0) + (hasCached ? 1 : 0) + 1}
                  style={{ textAlign: 'center', padding: '6px 8px', color: llmColor(llm), fontWeight: 700 }}>
                  {llm.toUpperCase()}
                </th>
              ))}
            </tr>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '4px 8px' }} />
              {llms.map(llm => (
                <React.Fragment key={llm}>
                  <th style={{ textAlign: 'right', padding: '4px 6px', color: 'var(--muted)', fontWeight: 500, fontSize: 10 }}>IN</th>
                  <th style={{ textAlign: 'right', padding: '4px 6px', color: 'var(--muted)', fontWeight: 500, fontSize: 10 }}>OUT</th>
                  {hasThinking && <th style={{ textAlign: 'right', padding: '4px 6px', color: 'var(--amber)', fontWeight: 500, fontSize: 10 }}>THINK</th>}
                  {hasCached   && <th style={{ textAlign: 'right', padding: '4px 6px', color: 'var(--muted)', fontWeight: 500, fontSize: 10 }}>CACHED</th>}
                  <th style={{ textAlign: 'right', padding: '4px 6px', color: 'var(--muted)', fontWeight: 500, fontSize: 10 }}>TOTAL</th>
                </React.Fragment>
              ))}
            </tr>
          </thead>
          <tbody>
            {allStepNums.map(sn => {
              const label = `Step ${sn}: ${STEP_LABELS[sn] || '?'}`
              return (
                <tr key={sn} style={{ borderBottom: '1px solid var(--border-dim)' }}>
                  <td style={{ padding: '7px 8px', fontWeight: 600, color: 'var(--fg)', fontSize: 11 }}>
                    {label}
                  </td>
                  {llms.map(llm => {
                    const s     = (data[llm]?.token_usage?.per_step || []).find(x => x.step_number === sn)
                    const inp   = s?.input_tokens    || 0
                    const out   = s?.output_tokens   || 0
                    const think = s?.thinking_tokens || 0
                    const cache = s?.cached_tokens   || 0
                    const tot   = s?.total_tokens    || 0
                    return (
                      <React.Fragment key={llm}>
                        <td style={{ textAlign: 'right', padding: '7px 6px', fontFamily: 'monospace', color: 'var(--fg)' }}>{inp}</td>
                        <td style={{ textAlign: 'right', padding: '7px 6px', fontFamily: 'monospace', color: 'var(--fg)' }}>{out}</td>
                        {hasThinking && <td style={{ textAlign: 'right', padding: '7px 6px', fontFamily: 'monospace', color: think > 0 ? 'var(--amber)' : 'var(--muted)' }}>{think || '—'}</td>}
                        {hasCached   && <td style={{ textAlign: 'right', padding: '7px 6px', fontFamily: 'monospace', color: cache > 0 ? 'var(--blue)' : 'var(--muted)' }}>{cache || '—'}</td>}
                        <td style={{ textAlign: 'right', padding: '7px 6px', fontFamily: 'monospace', fontWeight: 600, color: llmColor(llm) }}>{tot}</td>
                      </React.Fragment>
                    )
                  })}
                </tr>
              )
            })}
            {/* Total row */}
            <tr style={{ borderTop: '2px solid var(--border)', background: '#ffffff08' }}>
              <td style={{ padding: '8px 8px', fontWeight: 700, fontSize: 12 }}>TOTAL</td>
              {llms.map(llm => {
                const t = data[llm]?.token_usage?.total || {}
                return (
                  <React.Fragment key={llm}>
                    <td style={{ textAlign: 'right', padding: '8px 6px', fontFamily: 'monospace', fontWeight: 700, color: llmColor(llm) }}>{t.input_tokens || 0}</td>
                    <td style={{ textAlign: 'right', padding: '8px 6px', fontFamily: 'monospace', fontWeight: 700, color: llmColor(llm) }}>{t.output_tokens || 0}</td>
                    {hasThinking && <td style={{ textAlign: 'right', padding: '8px 6px', fontFamily: 'monospace', fontWeight: 700, color: (t.thinking_tokens || 0) > 0 ? 'var(--amber)' : 'var(--muted)' }}>{t.thinking_tokens || '—'}</td>}
                    {hasCached   && <td style={{ textAlign: 'right', padding: '8px 6px', fontFamily: 'monospace', fontWeight: 700, color: (t.cached_tokens || 0) > 0 ? 'var(--blue)' : 'var(--muted)' }}>{t.cached_tokens || '—'}</td>}
                    <td style={{ textAlign: 'right', padding: '8px 6px', fontFamily: 'monospace', fontWeight: 700, color: llmColor(llm) }}>{t.total_tokens || 0}</td>
                  </React.Fragment>
                )
              })}
            </tr>
          </tbody>
        </table>
      </div>

      {/* ── Total token bar comparison ── */}
      <div className="card">
        <div className="section-label">Total Token Comparison</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
          {llms.map(llm => {
            const t     = data[llm]?.token_usage?.total || {}
            const total = t.total_tokens || 0
            const inp   = t.input_tokens || 0
            const out   = t.output_tokens || 0
            const think = t.thinking_tokens || 0
            const cache = t.cached_tokens || 0
            const pct   = totalMax > 0 ? (total / totalMax) * 100 : 0
            return (
              <div key={llm}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
                  <div style={{ width: 60, fontSize: 11, fontWeight: 700, color: llmColor(llm) }}>{llm.toUpperCase()}</div>
                  <div style={{ flex: 1, height: 20, background: '#ffffff11', borderRadius: 4, overflow: 'hidden' }}>
                    <div style={{ width: `${pct}%`, height: '100%', background: llmColor(llm), borderRadius: 4, transition: 'width 0.4s' }} />
                  </div>
                  <div style={{ width: 70, fontSize: 11, fontFamily: 'monospace', color: 'var(--fg)', textAlign: 'right', fontWeight: 700 }}>
                    {total.toLocaleString()}
                  </div>
                </div>
                <div style={{ paddingLeft: 72, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'monospace' }}>in: {inp.toLocaleString()}</span>
                  <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'monospace' }}>out: {out.toLocaleString()}</span>
                  {hasThinking && think > 0 && <span style={{ fontSize: 10, color: 'var(--amber)', fontFamily: 'monospace' }}>think: {think.toLocaleString()}</span>}
                  {hasCached   && cache > 0  && <span style={{ fontSize: 10, color: 'var(--blue)', fontFamily: 'monospace' }}>cached: {cache.toLocaleString()}</span>}
                </div>
              </div>
            )
          })}
        </div>
      </div>

    </div>
  )
}
