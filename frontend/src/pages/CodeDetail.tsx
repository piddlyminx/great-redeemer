import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { API_BASE } from '../lib/base'

type CodeDetail = {
  id: number
  code: string
  active: boolean
  first_seen_at?: string | null
  expires_at?: string | null
  summary: { redeemed: number, failed: number, pending: number }
  users: Array<{ user_id: number, fid: number, name?: string | null, status: string, attempt_count: number, last_at?: string | null }>
}

export default function CodeDetail() {
  const { code } = useParams()
  const [data, setData] = useState<CodeDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setErr(null)
    fetch(`${API_BASE}/codes/${encodeURIComponent(code || '')}/detail`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(setData)
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false))
  }, [code])

  const fmt = (iso?: string | null) => {
    if (!iso) return ''
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    const pad = (n:number)=> n.toString().padStart(2,'0')
    const day = pad(d.getDate())
    const mon = pad(d.getMonth()+1)
    const yr = d.getFullYear()
    const hh = pad(d.getHours())
    const mm = pad(d.getMinutes())
    const ss = pad(d.getSeconds())
    return `${day}/${mon}/${yr} ${hh}:${mm}:${ss}`
  }

  const title = useMemo(() => `Code: ${code ?? ''}` , [code])

  return (
    <div className="card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur"><div className="card-body">
      <div className="flex items-center justify-between">
        <div className="font-semibold mb-2 pl-3 border-l-2 border-sky-400/60">{title}</div>
        <Link className="btn btn-sm btn-ghost" to="/codes">Back to Codes</Link>
      </div>

      {loading && (<div className="text-sm opacity-70">Loading…</div>)}
      {err && (<div className="text-sm text-error">{err}</div>)}
      {!!data && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
            <MiniStat label="Redeemed" value={data.summary.redeemed} accent="success" />
            <MiniStat label="Failed" value={data.summary.failed} accent="error" />
            <MiniStat label="Pending" value={data.summary.pending} accent="info" />
          </div>

          <div className="overflow-x-auto rounded-lg ring-1 ring-white/5">
            <table className="table table-zebra">
              <thead><tr><th>User</th><th>Status</th><th>Attempts</th><th>Last Update</th></tr></thead>
              <tbody>
                {data.users.map(u => (
                  <tr key={u.user_id}>
                    <td>
                      <div className="flex items-center gap-2">
                        <div className="font-mono text-sm">{u.fid}</div>
                        <div className="opacity-70 text-sm">{u.name || '—'}</div>
                      </div>
                    </td>
                    <td>
                      <StatusBadge status={u.status} />
                    </td>
                    <td>{u.attempt_count}</td>
                    <td>{fmt(u.last_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div></div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'failed' ? 'badge-error' :
    (status === 'redeemed_new' || status === 'redeemed_already' || status === 'success') ? 'badge-success' :
    status === 'pending' ? 'badge-info' : 'badge-ghost'
  return <span className={`badge ${cls} badge-sm`}>{status}</span>
}

function MiniStat({ label, value, accent }: { label: string, value: number | string, accent: 'success'|'error'|'info'|'sky' }) {
  const ring = accent === 'success' ? 'ring-emerald-400/40' : accent === 'error' ? 'ring-rose-400/40' : accent === 'info' ? 'ring-sky-400/40' : 'ring-sky-400/40'
  const col = accent === 'success' ? 'text-emerald-400/90' : accent === 'error' ? 'text-rose-400/90' : accent === 'info' ? 'text-sky-300/90' : 'text-sky-300/90'
  return (
    <div className={`rounded-xl border border-white/10 ring-1 ${ring} p-3`}>
      <div className={`text-sm font-medium ${col}`}>{label}</div>
      <div className="text-2xl font-semibold">{value}</div>
    </div>
  )
}

