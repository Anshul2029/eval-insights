import React from 'react'

export default function ContextManifest({ contextResult }) {
  if (!contextResult) return null

  const {
    facts_produced = [],
    facts_lost = [],
    facts_present_in_word = [],
    score = 1,
    context_loss_detected = false,
    boundary = 'Excel to Word',
    note,
  } = contextResult

  const lostSet = new Set(facts_lost.map(f => f.toLowerCase()))

  function isLost(fact) {
    const fl = fact.toLowerCase()
    return lostSet.has(fl) || facts_lost.some(l => fl.includes(l.toLowerCase().split(':')[0]))
  }

  return (
    <div className="context-card">
      <div className="section-label">Context Manifest — {boundary}</div>

      <div className="context-grid">
        <div>
          <div className="context-col-title">Facts produced in Excel (Step 2) — {facts_produced.length}</div>
          <div className="fact-list-col">
            {facts_produced.length === 0
              ? <div className="fact-row fact-none">None recorded</div>
              : facts_produced.map((f, i) => (
                  <div key={i} className={`fact-row ${isLost(f) ? 'fact-lost' : 'fact-ok'}`}>
                    <span className={`fact-indicator ${isLost(f) ? 'fi-lost' : 'fi-ok'}`}>
                      {isLost(f) ? 'LOST' : 'OK'}
                    </span>
                    {f}
                  </div>
                ))
            }
          </div>
        </div>

        <div>
          <div className="context-col-title">Present in Word output — {facts_present_in_word.length}</div>
          <div className="fact-list-col">
            {facts_present_in_word.length === 0
              ? <div className="fact-row fact-none">—</div>
              : facts_present_in_word.map((f, i) => (
                  <div key={i} className="fact-row fact-ok">
                    <span className="fact-indicator fi-ok">OK</span>
                    {f}
                  </div>
                ))
            }
          </div>

          {facts_lost.length > 0 && (
            <>
              <div className="context-col-title" style={{ marginTop: 12, color: 'var(--red)' }}>
                Lost at boundary — {facts_lost.length}
              </div>
              <div className="fact-list-col">
                {facts_lost.map((f, i) => (
                  <div key={i} className="fact-row fact-lost">
                    <span className="fact-indicator fi-lost">LOST</span>
                    {f}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="context-footer">
        <span>Context score: <span className="context-score-val">{score.toFixed(4)}</span></span>
        {context_loss_detected
          ? <span className="loss-chip">CONTEXT LOSS DETECTED</span>
          : <span className="no-loss-chip">No context loss</span>
        }
        {note && (
          <span style={{ fontSize: 10, color: 'var(--muted)', maxWidth: 480, lineHeight: 1.5 }}>
            {note}
          </span>
        )}
      </div>
    </div>
  )
}
