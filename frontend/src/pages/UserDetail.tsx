import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { API_BASE } from '../lib/base'

export default function UserDetail() {
  const { id } = useParams()
  const [rows, setRows] = useState<any[]>([])
  useEffect(() => { if(id) fetch(`${API_BASE}/users/${id}/redemptions`).then(r=>r.json()).then(setRows) }, [id])
  return (
    <div className="card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur"><div className="card-body">
      <div className="font-semibold mb-2 pl-3 border-l-2 border-violet-400/60">Redemptions</div>
      <div className="overflow-x-auto rounded-lg ring-1 ring-white/5">
        <table className="table table-zebra">
          <thead><tr><th>Code</th><th>Status</th><th>Attempts</th><th>Last Attempt</th><th>Err</th></tr></thead>
          <tbody>
            {rows.map(r=> (
              <tr key={r.id}><td>{r.code}</td><td>{r.status}</td><td>{r.attempt_count}</td><td>{r.last_attempt_at||'—'}</td><td>{r.err_code||'—'}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div></div>
  )
}
