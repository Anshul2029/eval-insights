import React, { useEffect, useState, useCallback, useRef } from 'react'
import axios from 'axios'
import Sidebar from './components/Sidebar.jsx'
import Overview from './components/Overview.jsx'
import TraceHeader from './components/TraceHeader.jsx'
import PlanBreakdown from './components/PlanBreakdown.jsx'
import StepTimeline from './components/StepTimeline.jsx'
import StepDetail from './components/StepDetail.jsx'
import FailureCard from './components/FailureCard.jsx'
import ContextManifest from './components/ContextManifest.jsx'
import ComparisonView from './components/ComparisonView.jsx'
import EvalInsightsView from './components/EvalInsightsView.jsx'


export default function App() {
  const [mode, setMode] = useState('traces')          // 'traces' | 'comparisons' | 'insights'
  const [traces, setTraces] = useState([])
  const [loadingList, setLoadingList] = useState(true)
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [selectedStep, setSelectedStep] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const stepDetailRef = useRef(null)

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

  const selectTrace = useCallback((id) => {
    if (id === selectedId) return
    setSelectedId(id)
    setDetail(null)
    setSelectedStep(null)
    setLoadingDetail(true)
    axios.get(`/trace/${id}`)
      .then(r => setDetail(r.data))
      .catch(console.error)
      .finally(() => setLoadingDetail(false))
  }, [selectedId])

  // Auto-scroll to StepDetail whenever a step is selected
  useEffect(() => {
    if (selectedStep !== null && stepDetailRef.current) {
      setTimeout(() => {
        stepDetailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      }, 50)
    }
  }, [selectedStep])

  const uploadTrace = useCallback((file) => {
    setUploading(true)
    setUploadError(null)
    const form = new FormData()
    form.append('file', file)
    axios.post('/evaluate', form, { headers: { 'Content-Type': 'multipart/form-data' } })
      .then(r => {
        const newId = r.data.trace_id
        return loadTraceList().then(() => {
          setLoadingList(false)
          selectTrace(newId)
        })
      })
      .catch(err => {
        const msg = err.response?.data?.error || err.message || 'Upload failed'
        setUploadError(msg)
      })
      .finally(() => setUploading(false))
  }, [selectTrace])

  const stepResults = detail?.step_results || []
  const fa = detail?.failure_attribution
  const selectedStepResult = stepResults.find(sr => sr.step_number === selectedStep)

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
        {mode === 'traces' && traces.length > 0 && (
          <span className="topbar-badge">{traces.length} traces</span>
        )}
        {mode === 'comparisons' && comparisons.length > 0 && (
          <span className="topbar-badge">{comparisons.length} comparisons</span>
        )}
      </div>

      <div className="app-shell" style={{ display: mode === 'insights' ? 'none' : undefined }}>
        {mode === 'comparisons' ? (
          /* ── Comparisons sidebar ── */
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
        ) : (
          <Sidebar
            traces={traces}
            selectedId={selectedId}
            onSelect={selectTrace}
            loading={loadingList}
            onUpload={uploadTrace}
            uploading={uploading}
            uploadError={uploadError}
          />
        )}

        <div className="main-wrap">
          <div className="main-panel">

            {/* ── Comparisons mode ── */}
            {mode === 'comparisons' && !selectedCmpId && (
              <div className="empty-state">
                Select a comparison from the sidebar.<br />
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                  Run <code>python run_comparison.py trace_002</code> first.
                </span>
              </div>
            )}
            {mode === 'comparisons' && loadingCmp && (
              <div className="empty-state">Loading comparison…</div>
            )}
            {mode === 'comparisons' && cmpDetail && !loadingCmp && (
              <ComparisonView data={cmpDetail} />
            )}

            {/* ── Traces mode ── */}
            {mode === 'traces' && !selectedId && !loadingList && (
              <Overview traces={traces} onSelect={selectTrace} />
            )}
            {mode === 'traces' && loadingDetail && (
              <div className="empty-state">Loading trace…</div>
            )}
            {mode === 'traces' && detail && !loadingDetail && (
              <>
                <TraceHeader detail={detail} />
                <StepTimeline
                  stepResults={stepResults}
                  selectedStep={selectedStep}
                  onSelectStep={setSelectedStep}
                  failureAttribution={fa}
                />
                <div ref={stepDetailRef}>
                  {selectedStepResult
                    ? <StepDetail stepResult={selectedStepResult} />
                    : (
                      <div className="step-detail-placeholder">
                        Select a step card above to see its rubric criteria, scores, grader rationale, and agent trajectory
                      </div>
                    )
                  }
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                  <PlanBreakdown planResult={detail.plan_result} />
                </div>
                {fa?.failure_transition_step && <FailureCard failureAttribution={fa} />}
                {detail.context_result && <ContextManifest contextResult={detail.context_result} />}
              </>
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
