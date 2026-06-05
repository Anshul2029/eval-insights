import React, { useRef } from 'react'

function scoreClass(s) {
  if (s >= 0.85) return 'g'
  return 'r'
}

function statusLabel(t) {
  if (t.trajectory_score >= 0.85) return 'PASS'
  return 'FAIL'
}

export default function Sidebar({ traces, selectedId, onSelect, loading, onUpload, uploading, uploadError }) {
  const fileRef = useRef(null)
  const pass  = traces.filter(t => t.trajectory_score >= 0.85).length
  const amber = 0
  const fail  = traces.filter(t => t.trajectory_score < 0.85).length

  function handleFileChange(e) {
    const file = e.target.files?.[0]
    if (file) {
      onUpload(file)
      e.target.value = ''
    }
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-title">Traces</div>
        {traces.length > 0 && (
          <div className="sidebar-stats">
            <span className="sidebar-stat s-green"><b>{pass}</b> pass</span>
            <span className="sidebar-stat s-amber"><b>{amber}</b> amber</span>
            <span className="sidebar-stat s-red"><b>{fail}</b> fail</span>
          </div>
        )}

        <input
          ref={fileRef}
          type="file"
          accept=".json"
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />
        <button
          className={`upload-btn${uploading ? ' uploading' : ''}`}
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          title="Upload a raw trace JSON to evaluate it"
        >
          {uploading ? 'Evaluating…' : 'Evaluate Trace'}
        </button>
        {uploadError && (
          <div className="upload-error">{uploadError}</div>
        )}
      </div>

      <div className="sidebar-list">
        {loading && (
          <div style={{ padding: '20px 16px', color: 'var(--muted)', fontSize: 12 }}>
            Loading…
          </div>
        )}
        {traces.map(t => (
          <div
            key={t.trace_id}
            className={`trace-item${selectedId === t.trace_id ? ' active' : ''}`}
            onClick={() => onSelect(t.trace_id)}
          >
            <div className={`score-badge ${t.step_completeness_failed ? 'r' : scoreClass(t.trajectory_score)}`}>
              {Math.round(t.trajectory_score * 100)}
            </div>
            <div className="trace-info">
              <div className="trace-id-text">{t.trace_id}</div>
              <div className="trace-file-text">{t.dataset_file}</div>
              <div className="trace-pills">
                {t.failure_transition_step
                  ? <span className="pill pill-fail">Step {t.failure_transition_step} fail · {t.failure_type?.replace(/_/g,' ')}</span>
                  : <span className="pill pill-pass">All steps pass</span>
                }
                {t.step_completeness_failed && (
                  <span className="pill pill-fail">Step completeness fail</span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}
