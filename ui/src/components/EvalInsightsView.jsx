export default function EvalInsightsView() {
  const streamlitUrl = `${window.location.protocol}//${window.location.hostname}:8502/insights/`
  return (
    <iframe
      src={streamlitUrl}
      className="insights-iframe"
      title="Eval Insights"
    />
  )
}
