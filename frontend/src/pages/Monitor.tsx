import { useEffect, useState } from 'react'
import { API_BASE } from '../lib/base'
import { BTN_SOFT } from '../lib/ui'

export default function Monitor() {
  const [rows, setRows] = useState<any[]>([])
  const [summary, setSummary] = useState<any>(null)
  const load = () => fetch(`${API_BASE}/attempts?limit=50`).then(r=>r.json()).then(setRows)
  useEffect(() => {
    load()
    fetch(`${API_BASE}/summary`).then(r=>r.json()).then(setSummary).catch(()=>{})
  }, [])
  return (
    <div className="space-y-4">
      
      <div className="card bg-base-100/90 ring-1 ring-white/10 shadow-xl"><div className="card-body">
        <div className="text-sm text-base-content/60">Heartbeats</div>
        <div className="grid grid-cols-2 gap-3 divide-x divide-white/5">
          <div>RSS: <span className={summary?.rss_hb ? 'badge badge-success badge-outline' : 'badge badge-ghost'}>{summary?.rss_hb ? formatAgo(summary.rss_hb) : 'inactive'}</span></div>
          <div>Worker: <span className={summary?.worker_hb ? 'badge badge-success badge-outline' : 'badge badge-ghost'}>{summary?.worker_hb ? formatAgo(summary.worker_hb) : 'inactive'}</span></div>
        </div>
      </div></div>

      <div className="card bg-base-100/90 ring-1 ring-white/10 shadow-xl"><div className="card-body">
        <div className="flex items-center">
          <div className="font-semibold pl-3 border-l-2 border-amber-400/60">Recent Attempts</div>
          <button className={`${BTN_SOFT} btn-sm ml-auto`} onClick={load}>Refresh</button>
        </div>
        <div className="overflow-x-auto rounded-lg ring-1 ring-white/5">
          <table className="table table-sm table-zebra">
            <thead><tr><th>When</th><th>User ID</th><th>Code</th><th>Captcha</th><th>Err</th><th>Msg</th></tr></thead>
            <tbody>
              {rows.map((a:any)=> (
                <tr key={a.id}>
                  <td>{a.created_at}</td>
                  <td>{a.user_fid ?? '—'}</td>
                  <td>{a.code ?? '—'}</td>
                  <td>{a.captcha ?? '—'}</td>
                  <td>{a.err_code ?? '—'}</td>
                  <td><code className="text-xs text-base-content/70">{a.result_msg ? String(a.result_msg).slice(0,120) : '—'}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div></div>
    </div>
  )
}

function formatAgo(iso?: string) {
  if (!iso) return '—'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return iso
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000))
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  return `${h}h ago`
}
