import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { API_BASE } from '../lib/base'
import { BTN_SOFT } from '../lib/ui'

export default function Users() {
  const [rows, setRows] = useState<any[]>([])
  const [fid, setFid] = useState('')
  const [name, setName] = useState('')
  const [alliance, setAlliance] = useState<number|''>('')
  const [alliances, setAlliances] = useState<any[]>([])
  const [err, setErr] = useState<string>('')
  const [q, setQ] = useState('')
  const load = () => fetch(`${API_BASE}/users${q?`?q=${encodeURIComponent(q)}`:''}`).then(r=>r.json()).then(setRows)
  // fetch alliances once
  useEffect(() => { fetch(`${API_BASE}/alliances`).then(r=>r.json()).then(setAlliances) }, [])
  // debounce user search as you type
  useEffect(() => {
    const t = setTimeout(() => { load() }, 300)
    return () => clearTimeout(t)
  }, [q])
  return (
    <div className="space-y-4">
      <div className="card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur"><div className="card-body">
        <div className="font-semibold flex items-center gap-3 pl-3 border-l-2 border-violet-400/60">
          <span>Add user</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-2">
          <input className="input input-bordered" placeholder="User ID" value={fid} onChange={e=>setFid(e.target.value)}/>
          <input className="input input-bordered" placeholder="Name" value={name} onChange={e=>setName(e.target.value)}/>
          <select className="select select-bordered" aria-invalid={alliance==='' ? true : undefined} required value={alliance} onChange={e=>setAlliance(e.target.value ? Number(e.target.value) : '')}>
            <option value="">Alliance (required)</option>
            {alliances.map(a=> <option key={a.id} value={a.id}>{a.name} ({a.tag})</option>)}
          </select>
          <button
            type="button"
            className={BTN_SOFT}
            disabled={!fid.trim() || alliance === ''}
            onClick={async()=>{
              setErr('')
              if (!fid.trim()) { setErr('User ID is required'); return }
              if (alliance === '') { setErr('Please select an alliance'); return }
              try {
                const form = new URLSearchParams({ fid: fid.trim(), name, alliance_id: String(alliance) })
                const r = await fetch(`${API_BASE}/users`, { method:'POST', body: form })
                if (!r.ok) {
                  let msg = `HTTP ${r.status}`
                  try {
                    const j = await r.json()
                    if (j?.detail) {
                      if (Array.isArray(j.detail) && j.detail.length) msg = j.detail[0]?.msg || msg
                      else if (typeof j.detail === 'string') msg = j.detail
                    }
                  } catch {
                    const t = await r.text(); if (t) msg = t
                  }
                  // Normalize common validation cases
                  if (r.status === 400 || r.status === 422) {
                    if (msg.toLowerCase().includes('alliance')) msg = 'Alliance is required — please select an alliance.'
                  }
                  throw new Error(msg)
                }
                setFid(''); setName(''); setAlliance(''); load()
              } catch (e:any) {
                setErr(String(e.message || e))
              }
            }}
          >Add</button>
        </div>
        {err && <div className="text-sm text-error mt-2">{err}</div>}
      </div></div>
      <div className="card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur"><div className="card-body">
        <div className="font-semibold mb-2 pl-3 border-l-2 border-sky-400/60">Users</div>
        <div className="flex items-center gap-2 mb-3">
          <input className="input input-bordered w-full md:w-96" placeholder="Search by User ID or Name" value={q} onChange={e=>setQ(e.target.value)} />
          <button className={BTN_SOFT} onClick={load}>Search</button>
        </div>
        <div className="overflow-x-auto rounded-lg ring-1 ring-white/5">
          <table className="table table-zebra">
            <thead>
              <tr>
                <th>User ID</th><th>Name</th><th>Alliance</th><th>Active</th><th>Created</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(u=> (
                <tr key={u.id}>
                  <td><Link className="link" to={`${u.id}`}>{u.fid}</Link></td>
                  <td>{u.name||'—'}</td>
                  <td>{u.alliance? `${u.alliance} (${u.tag})`:'—'}</td>
                  <td>{u.active? 'Yes':'No'}</td>
                  <td>{u.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div></div>
    </div>
  )
}
