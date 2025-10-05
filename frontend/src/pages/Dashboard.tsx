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
        <div className="card-body pt-4 md:pt-5">
          <div className="text-base md:text-lg font-medium text-base-content/80 text-center md:text-left mb-3">Sign up for automatic rewards</div>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-2 md:gap-3">
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
          <div className={'card-body relative overflow-hidden items-center text-center py-4'}>
            <div
              className={
                'text-xs uppercase tracking-wide mb-1 text-base-content/60'
              }
            >
              {card.label}
            </div>
            <div className="text-2xl font-semibold tracking-tight">
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
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-center">
              <Stat label="Attempts" value={data.worker_status.attempts ?? '—'} />
              <Stat label="Successes" value={data.worker_status.successes ?? '—'} />
              <Stat label="Errors" value={data.worker_status.errors ?? '—'} />
              <Stat label="Sleep (s)" value={data.worker_status.sleep ?? '—'} />
            </div>
            <div className="text-xs text-base-content/60 text-center md:text-left">
              Last update: {formatAgo(data.worker_status.ts)}
            </div>
            {/* Activity carousel */}
            <ActivityCarousel peek={peek} />
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
    <div className="p-3 rounded bg-base-300/20 ring-1 ring-white/5 text-center">
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

function ActivityCarousel({ peek }: { peek: any }) {
  const items: Array<{ key: string, label: string, sub: string, status: 'active'|'success'|'error'|'queued' }>= []
  if (peek?.upcoming?.length) {
    const up = [...peek.upcoming].slice(0,2).reverse()
    up.forEach((u:any, idx:number)=> items.push({ key: `up-${idx}-${u.fid}-${u.code}`, label: u.name || `FID ${u.fid}` , sub: u.code, status: 'queued' }))
  }
  if (peek?.current) {
    items.push({ key: `cur-${peek.current.fid}-${peek.current.code}`, label: peek.current.name || `FID ${peek.current.fid}`, sub: peek.current.code, status: 'active' })
  } else {
    items.push({ key: 'cur-none', label: '—', sub: '', status: 'queued' })
  }
  if (peek?.recent?.length) {
    const rc = [...peek.recent].slice(0,2)
    rc.forEach((r:any)=> items.push({ key: `rc-${r.id}`, label: r.name || `FID ${r.fid}`, sub: r.code, status: r.err ? 'error' : 'success' }))
  }
  while (items.length < 5) items.push({ key: `pad-${items.length}`, label: '—', sub: '', status: 'queued' })

  return (
    <div className="mt-4">
      <div className="text-xs text-base-content/60 text-center md:text-left mb-2">Activity</div>
      <div className="relative mx-auto md:mx-0 max-w-sm">
        <div className="overflow-hidden rounded-lg ring-1 ring-white/5 bg-base-300/20 px-3 py-2">
          <div className="grid grid-rows-5 gap-2 relative">
            {items.slice(0,5).map((it, idx)=> (
              <div key={it.key} className={`flex items-center justify-between px-2 py-1 rounded-full text-sm animate-slide-down-fade ${idx===2 ? 'ring-2 ring-primary/70 relative' : ''}`}>
                <div className={`badge ${it.status==='success' ? 'badge-success' : it.status==='error' ? 'badge-error' : 'badge-ghost'} gap-2`}>{it.label}</div>
                <div className={`badge ${it.status==='active' ? 'badge-info' : 'badge-outline'}`}>{it.sub}</div>
                {idx===2 && (
                  <div className="absolute -right-1 -top-1 md:right-2 md:top-2 text-primary opacity-80">
                    <span className="inline-block animate-spin-slow">⚙️</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
