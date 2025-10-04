import { useEffect, useState } from 'react'
import { API_BASE } from '../lib/base'

export default function Codes() {
  const [rows, setRows] = useState<any[]>([])
  useEffect(() => { fetch(`${API_BASE}/codes`).then(r=>r.json()).then(setRows) }, [])
  return (
    <div className="card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur"><div className="card-body">
      <div className="font-semibold mb-2 pl-3 border-l-2 border-sky-400/60">Gift Codes</div>
      <div className="overflow-x-auto rounded-lg ring-1 ring-white/5">
        <table className="table table-zebra">
          <thead><tr><th>Code</th><th>Active</th><th>First Seen</th></tr></thead>
          <tbody>
            {rows.map(c=> (
              <tr key={c.id}><td><span className="badge badge-primary">{c.code}</span></td><td>{c.active? 'Yes':'No'}</td><td>{c.first_seen_at||'—'}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div></div>
  )
}
