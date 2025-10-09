import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { API_BASE } from '../lib/base'

export default function Codes() {
  const [rows, setRows] = useState<any[]>([])
  useEffect(() => { fetch(`${API_BASE}/codes`).then(r=>r.json()).then(setRows) }, [])
  const fmt = (iso?: string) => {
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
  return (
    <div className="card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur"><div className="card-body">
      <div className="font-semibold mb-2 pl-3 border-l-2 border-sky-400/60">Gift Codes</div>
      <div className="overflow-x-auto rounded-lg ring-1 ring-white/5">
        <table className="table table-zebra">
          <thead><tr><th>Code</th><th>Redeemed</th><th>Failed</th><th>Pending</th><th>First Seen</th><th>Expired</th></tr></thead>
          <tbody>
            {rows.map((c:any)=> (
              <tr key={c.id}>
                <td className="font-mono text-sm">
                  <Link className="link link-primary" to={`/codes/${encodeURIComponent(c.code)}`}>{c.code}</Link>
                </td>
                <td>{c.redeemed ?? '—'}</td>
                <td>{c.failed ?? '—'}</td>
                <td>{c.pending ?? '—'}</td>
                <td>{fmt(c.first_seen_at)}</td>
                <td>{fmt(c.expires_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div></div>
  )
}
