import React, { useEffect, useState, useCallback, useRef } from 'react'
import axios from 'axios'
import Overview from './components/Overview.jsx'
import ComparisonView from './components/ComparisonView.jsx'
import EvalInsightsView from './components/EvalInsightsView.jsx'


export default function App() {
  const [mode, setMode] = useState('traces')          // 'traces' | 'comparisons' | 'insights'
  const [traces, setTraces] = useState([])
  const [loadingList, setLoadingList] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const fileRef = useRef(null)

  // comparison state
  const [comparisons, setComparisons] = useState([])
  const [selectedCmpId, setSelectedCmpId] = useState(null)
  const [cmpDetail, setCmpDetail] = useState(null)
  const [loadingCmp, setLoadingCmp] = useState(false)

  function loadTraceList() {
    return axios.get('/traces').then(r => setTraces(r.data))
  }

  function loadComparisons() {
    return axios.get('/comparisons').then(r => setComparisons(r.data))
  }

  useEffect(() => {
    loadTraceList().catch(console.error).finally(() => setLoadingList(false))
    loadComparisons().catch(console.error)
  }, [])

  const selectComparison = useCallback((id) => {
    if (id === selectedCmpId) return
    setSelectedCmpId(id)
    setCmpDetail(null)
    setLoadingCmp(true)
    axios.get(`/comparison/${id}`)
      .then(r => setCmpDetail(r.data))
      .catch(console.error)
      .finally(() => setLoadingCmp(false))
  }, [selectedCmpId])

  const uploadTrace = useCallback((file) => {
    setUploading(true)
    setUploadError(null)
    const form = new FormData()
    form.append('file', file)
    axios.post('/evaluate', form, { headers: { 'Content-Type': 'multipart/form-data' } })
      .then(r => {
        return loadTraceList().then(() => setLoadingList(false))
      })
      .catch(err => {
        const msg = err.response?.data?.error || err.message || 'Upload failed'
        setUploadError(msg)
      })
      .finally(() => setUploading(false))
  }, [])

  function handleFileChange(e) {
    const file = e.target.files?.[0]
    if (file) {
      uploadTrace(file)
      e.target.value = ''
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* Topbar */}
      <div className="topbar">
        <span className="topbar-logo">Copilot <span>Eval</span></span>
        <div className="topbar-sep" />
        {/* Mode tabs */}
        <div style={{ display: 'flex', gap: 4 }}>
          {['traces', 'comparisons', 'insights'].map(m => (
            <button key={m} onClick={() => setMode(m)} style={{
              background: mode === m ? 'var(--blue)' : 'transparent',
              color: mode === m ? '#fff' : 'var(--muted)',
              border: `1px solid ${mode === m ? 'var(--blue)' : 'var(--border)'}`,
              borderRadius: 4, padding: '3px 12px', fontSize: 11,
              fontWeight: 600, cursor: 'pointer', textTransform: 'uppercase', letterSpacing: '0.05em',
            }}>
              {m}
            </button>
          ))}
        </div>
        <div className="topbar-spacer" />
        {mode === 'traces' && (
          <>
            <input
              ref={fileRef}
              type="file"
              accept=".json"
              style={{ display: 'none' }}
              onChange={handleFileChange}
            />
            <button
              className={`upload-btn topbar-upload${uploading ? ' uploading' : ''}`}
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
              title="Upload a raw trace JSON to evaluate it"
            >
              {uploading ? 'Evaluating…' : 'Evaluate Trace'}
            </button>
            {uploadError && (
              <span style={{ fontSize: 11, color: 'var(--red)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {uploadError}
              </span>
            )}
          </>
        )}
        {mode === 'traces' && traces.length > 0 && (
          <span className="topbar-badge">{traces.length} traces</span>
        )}
        {mode === 'comparisons' && comparisons.length > 0 && (
          <span className="topbar-badge">{comparisons.length} comparisons</span>
        )}
      </div>

      {/* ── Traces mode — full-width table with expandable dropdowns ── */}
      {mode === 'traces' && (
        <div className="traces-fullwidth">
          {loadingList ? (
            <div className="empty-state">Loading…</div>
          ) : (
            <Overview traces={traces} />
          )}
        </div>
      )}

      {/* ── Comparisons mode ── */}
      <div className="app-shell" style={{ display: mode === 'comparisons' ? undefined : 'none' }}>
        <aside className="sidebar">
          <div className="sidebar-header">
            <div className="sidebar-title">Comparisons</div>
          </div>
          <div className="sidebar-list">
            {comparisons.length === 0 && (
              <div style={{ padding: '20px 16px', color: 'var(--muted)', fontSize: 12 }}>
                No comparisons yet.<br />Run:<br />
                <code style={{ fontSize: 10 }}>python run_comparison.py trace_002</code>
              </div>
            )}
            {comparisons.map(c => (
              <div key={c.comparison_id}
                className={`trace-item${selectedCmpId === c.comparison_id ? ' active' : ''}`}
                onClick={() => selectComparison(c.comparison_id)}>
                <div className="trace-info">
                  <div className="trace-id-text">{c.comparison_id}</div>
                  <div className="trace-file-text">{c.llms_compared?.join(' vs ')}</div>
                  <div className="trace-pills">
                    {c.llms_compared?.map(llm => (
                      <span key={llm} className="pill" style={{ background: '#3b82f622', color: '#60a5fa' }}>
                        {llm} {c.scores?.[llm]?.total_tokens?.toLocaleString()}t
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </aside>

        <div className="main-wrap">
          <div className="main-panel">
            {!selectedCmpId && (
              <div className="empty-state">
                Select a comparison from the sidebar.<br />
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                  Run <code>python run_comparison.py trace_002</code> first.
                </span>
              </div>
            )}
            {loadingCmp && (
              <div className="empty-state">Loading comparison…</div>
            )}
            {cmpDetail && !loadingCmp && (
              <ComparisonView data={cmpDetail} />
            )}
          </div>
        </div>
      </div>

      <div className="insights-wrap" style={{ display: mode === 'insights' ? 'flex' : 'none' }}>
        <EvalInsightsView />
      </div>
    </div>
  )
}
