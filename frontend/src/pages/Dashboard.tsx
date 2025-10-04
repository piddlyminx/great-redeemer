import { useEffect, useState } from 'react'
import { API_BASE } from '../lib/base'

export default function Dashboard() {
  const [data, setData] = useState<any>(null)
  const [alliances, setAlliances] = useState<any[]>([])
  const [fid, setFid] = useState('')
  const [name, setName] = useState('')
  const [alliance, setAlliance] = useState<number|''>('')
  const [peek, setPeek] = useState<any>(null)

  useEffect(() => {
    fetch(`${API_BASE}/summary`).then(r => r.json()).then(setData).catch(() => setData(null))
    fetch(`${API_BASE}/alliances`).then(r=>r.json()).then(setAlliances).catch(()=>setAlliances([]))
    const id = setInterval(() => {
      fetch(`${API_BASE}/summary`).then(r => r.json()).then(setData).catch(() => {})
      fetch(`${API_BASE}/worker_peek?limit=5`).then(r=>r.json()).then(setPeek).catch(()=>{})
    }, 3000)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
      {/* Signup */}
      <div className="md:col-span-4 card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur">
        <div className="card-body">
          <div className="text-sm text-base-content/60">Sign up for automatic rewards</div>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-2">
            <input className="input input-bordered" placeholder="User ID (FID)" value={fid} onChange={e=>setFid(e.target.value)} />
            <input className="input input-bordered" placeholder="Username" value={name} onChange={e=>setName(e.target.value)} />
            <select className="select select-bordered" value={alliance} onChange={e=>setAlliance(e.target.value ? Number(e.target.value) : '')}>
              <option value="">Alliance</option>
              {alliances.map(a=> <option key={a.id} value={a.id}>{a.name} ({a.tag})</option>)}
            </select>
            <button className="btn btn-primary" onClick={async()=>{
              if(!fid || !name){ return }
              await fetch(`${API_BASE}/users`, { method:'POST', body: new URLSearchParams({ fid, name, alliance_id: String(alliance||'') }) })
              setFid(''); setName(''); setAlliance('')
            }}>Sign up</button>
          </div>
        </div>
      </div>
      {[
        { k: 'users', label: 'Users', accent: 'violet' },
        { k: 'codes', label: 'Gift Codes', accent: 'sky' },
        { k: 'success', label: 'Success', accent: 'emerald' },
        { k: 'pending', label: 'Pending', accent: 'amber' },
      ].map(card => (
        <div
          key={card.k}
          className={
            'card shadow-2xl border border-white/10 backdrop-blur bg-base-100/80'
          }
        >
          <div className={'card-body relative overflow-hidden'}>
            <div
              className={
                'text-xs uppercase tracking-wide mb-1 text-base-content/60'
              }
            >
              {card.label}
            </div>
            <div className="text-3xl font-semibold tracking-tight">
              {data ? data[card.k] : '—'}
            </div>
            <div
              className={
                'absolute left-0 top-0 h-full w-1 ' +
                (card.accent === 'emerald'
                  ? 'bg-emerald-400/60'
                  : card.accent === 'sky'
                  ? 'bg-sky-400/60'
                  : card.accent === 'violet'
                  ? 'bg-violet-400/60'
                  : 'bg-amber-400/60')
              }
            />
          </div>
        </div>
      ))}
      {data?.worker_status ? (
        <div className="md:col-span-4 card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur">
          <div className="card-body">
            <div className="text-sm text-base-content/60">Worker</div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Stat label="Attempts" value={data.worker_status.attempts ?? '—'} />
              <Stat label="Successes" value={data.worker_status.successes ?? '—'} />
              <Stat label="Errors" value={data.worker_status.errors ?? '—'} />
              <Stat label="Sleep (s)" value={data.worker_status.sleep ?? '—'} />
            </div>
            <div className="text-xs text-base-content/60">
              Last update: {formatAgo(data.worker_status.ts)}
            </div>
            {/* Live queue */}
            <div className="mt-3 grid grid-cols-1 md:grid-cols-3 gap-3">
              <div className="p-3 rounded bg-base-300/20 ring-1 ring-white/5">
                <div className="text-xs text-base-content/60 mb-1">Current</div>
                {peek?.current ? (
                  <div className="flex items-center gap-2 animate-pulse">
                    <span className="badge badge-info">FID {peek.current.fid}</span>
                    <span className="badge badge-success badge-outline">{peek.current.code}</span>
                  </div>
                ) : '—'}
              </div>
              <div className="p-3 rounded bg-base-300/20 ring-1 ring-white/5">
                <div className="text-xs text-base-content/60 mb-1">Up next</div>
                <div className="space-y-1">
                  {(peek?.upcoming||[]).slice(0,2).map((p:any, i:number)=> (
                    <div key={i} className="flex items-center gap-2">
                      <span className="badge badge-info">FID {p.fid}</span>
                      <span className="badge badge-outline">{p.code}</span>
                    </div>
                  ))}
                  {(!peek || (peek.upcoming||[]).length===0) && '—'}
                </div>
              </div>
              <div className="p-3 rounded bg-base-300/20 ring-1 ring-white/5">
                <div className="text-xs text-base-content/60 mb-1">Recent</div>
                <div className="space-y-1">
                  {(peek?.recent||[]).slice(0,3).map((r:any)=> (
                    <div key={r.id} className="flex items-center gap-2 text-sm">
                      <span className="badge badge-ghost">{r.code}</span>
                      <span className="text-base-content/60">FID {r.fid}</span>
                      <span className={r.err ? 'text-error/80' : 'text-success/80'}>{r.err ? `err ${r.err}` : 'ok'}</span>
                    </div>
                  ))}
                  {(!peek || (peek.recent||[]).length===0) && '—'}
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="md:col-span-4 card bg-base-100/60 shadow border border-white/10">
          <div className="card-body py-3">
            <div className="text-sm text-base-content/70">Worker</div>
            <div className="text-sm text-base-content/50">No status yet. Start workers in compose (service "worker") or set START_WORKERS=1.</div>
          </div>
        </div>
      )}
      <div className="md:col-span-4 card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur">
        <div className="card-body">
          <div className="text-sm text-base-content/60">Heartbeats</div>
          <div className="grid grid-cols-2 gap-3 divide-x divide-white/5">
            <div>RSS: <span className={data?.rss_hb ? 'badge badge-success badge-outline' : 'badge badge-ghost'}>{data?.rss_hb ? formatAgo(data.rss_hb) : 'inactive'}</span></div>
            <div>Worker: <span className={data?.worker_hb ? 'badge badge-success badge-outline' : 'badge badge-ghost'}>{data?.worker_hb ? formatAgo(data.worker_hb) : 'inactive'}</span></div>
          </div>
        </div>
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string, value: any }) {
  return (
    <div className="p-3 rounded bg-base-300/20 ring-1 ring-white/5">
      <div className="text-xs text-base-content/60">{label}</div>
      <div className="text-xl font-semibold">{value}</div>
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
