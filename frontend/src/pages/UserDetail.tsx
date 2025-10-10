import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { API_BASE } from '../lib/base'

export default function UserDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [rows, setRows] = useState<any[]>([])
  const [err, setErr] = useState<string>('')
  useEffect(() => { if(id) fetch(`${API_BASE}/users/${id}/redemptions`).then(r=>r.json()).then(setRows) }, [id])
  return (
    <div className="space-y-4">
      <div className="card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur"><div className="card-body">
        <div className="flex items-center justify-between">
          <div className="font-semibold pl-3 border-l-2 border-violet-400/60">User {id}</div>
          <div className="flex items-center gap-2">
            <button
              className="btn btn-error"
              onClick={async()=>{
                if (!id) return
                setErr('')
                const ok = window.confirm('Delete this user and all related data? This cannot be undone.')
                if (!ok) return
                try {
                  const r = await fetch(`${API_BASE}/users/${id}`, { method: 'DELETE' })
                  if (!r.ok) {
                    let msg = `HTTP ${r.status}`
                    try { const j = await r.json(); if (j?.detail) msg = typeof j.detail === 'string' ? j.detail : msg } catch {}
                    throw new Error(msg)
                  }
                  navigate('/users')
                } catch (e:any) {
                  setErr(String(e.message || e))
                }
              }}
            >Delete User</button>
          </div>
        </div>
        {err && <div className="text-sm text-error mt-2">{err}</div>}
      </div></div>

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
    </div>
  )
}
