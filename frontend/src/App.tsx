import { useEffect, useState } from 'react'
import './App.css'

function App() {
  const [status, setStatus] = useState('Checking backend…')
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), 5000)
    fetch('/api/health', { signal: controller.signal })
      .then(async (response) => {
        const data = await response.json()
        if (!response.ok || data.status !== 'ok' || data.service !== 'proofline-ai') throw new Error('Invalid health response')
        if (active) setStatus('Connected — backend is healthy.')
      })
      .catch(() => { if (active) setStatus('Backend unavailable. Start the backend and refresh this page.') })
      .finally(() => window.clearTimeout(timer))
    return () => { active = false; controller.abort(); window.clearTimeout(timer) }
  }, [])
  return (
    <main>
      <p className="eyebrow">PROJECT FOUNDATION</p>
      <h1>ProofLine AI</h1>
      <p className="description">AI Data Incident Investigation &amp; Recovery Platform</p>
      <section aria-labelledby="connection-title">
        <h2 id="connection-title">Backend connection</h2>
        <p role="status">{status}</p>
      </section>
      <p className="note">Welcome to ProofLine AI. This foundation provides a starting point for the next stage of design.</p>
    </main>
  )
}
export default App
