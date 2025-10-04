import { useEffect, useState } from 'react'
import { API_BASE } from '../lib/base'

export default function Monitor() {
  const [rows, setRows] = useState<any[]>([])
  const load = () => fetch(`${API_BASE}/attempts?limit=50`).then(r=>r.json()).then(setRows)
  useEffect(() => { load() }, [])
  return (
    <div className="card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur"><div className="card-body">
      <div className="flex items-center">
        <div className="font-semibold pl-3 border-l-2 border-amber-400/60">Recent Attempts</div>
        <button className="btn btn-sm ml-auto" onClick={load}>Refresh</button>
      </div>
      <div className="overflow-x-auto rounded-lg ring-1 ring-white/5">
        <table className="table table-sm table-zebra">
          <thead><tr><th>When</th><th>FID</th><th>Code</th><th>Captcha</th><th>Err</th><th>Msg</th></tr></thead>
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
  )
}
