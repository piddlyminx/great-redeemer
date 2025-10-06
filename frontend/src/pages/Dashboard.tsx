import { useEffect, useState } from 'react'
import { API_BASE } from '../lib/base'
import { BTN_SOFT } from '../lib/ui'

export default function Dashboard() {
  const [data, setData] = useState<any>(null)
  const [alliances, setAlliances] = useState<any[]>([])
  const [name, setName] = useState('')
  const [fid, setFid] = useState('')
  const [alliance, setAlliance] = useState<number|''>('')
  const [peek, setPeek] = useState<any>(null)
  const [codes, setCodes] = useState<any[]>([])
  const btnClass = `${BTN_SOFT} w-full`

  useEffect(() => {
    fetch(`${API_BASE}/summary`).then(r => r.json()).then(setData).catch(() => setData(null))
    fetch(`${API_BASE}/alliances`).then(r=>r.json()).then(setAlliances).catch(()=>setAlliances([]))
    fetch(`${API_BASE}/codes`).then(r=>r.json()).then(setCodes).catch(()=>setCodes([]))

    let es: EventSource | null = null
    let pollId: any = null

    const startPoll = () => {
      if (pollId) return
      // Immediate tick to avoid initial delay
      fetch(`${API_BASE}/summary`).then(r => r.json()).then(setData).catch(() => {})
      fetch(`${API_BASE}/worker_peek?limit=5`).then(r=>r.json()).then(setPeek).catch(()=>{})
      pollId = setInterval(() => {
        fetch(`${API_BASE}/summary`).then(r => r.json()).then(setData).catch(() => {})
        fetch(`${API_BASE}/worker_peek?limit=5`).then(r=>r.json()).then(setPeek).catch(()=>{})
      }, 3000)
    }

    try {
      es = new EventSource(`${API_BASE}/worker_events`)
      es.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.summary) setData(msg.summary)
          if (msg.peek) setPeek(msg.peek)
        } catch {}
      }
      es.onerror = () => {
        try { es?.close() } catch {}
        es = null
        startPoll()
      }
    } catch {
      // If EventSource construction fails (unsupported), fallback to polling
      startPoll()
    }

    if (!es) startPoll()

    return () => { if (pollId) clearInterval(pollId); try { es?.close() } catch {} }
  }, [])
  return (
    <div className="space-y-4">
      {/* HERO: primary blurb + sign‑up form */}
      <section className="relative overflow-hidden rounded-2xl ring-1 ring-white/10 bg-gradient-to-b from-base-100/80 to-base-200/80 shadow-2xl">
        {/* subtle lighting to draw the eye */}
        <div className="pointer-events-none absolute inset-0 opacity-50 [background:radial-gradient(800px_420px_at_80%_10%,rgba(56,189,248,0.18),transparent_60%),radial-gradient(700px_380px_at_10%_90%,rgba(167,139,250,0.16),transparent_60%)]" />
        <div className="relative grid grid-cols-1 md:grid-cols-3 gap-6 p-6 md:p-8">
          {/* Mobile-only heading before paragraph */}
          <div className="order-0 md:hidden">
            <div className="flex items-center gap-2 text-sky-300/90 text-sm font-medium mb-2"><span className="inline-block">🎁</span><span>Automatic Gift Code Redemption</span></div>
            <h1 className="text-3xl font-semibold tracking-tight">Never miss a reward again.</h1>
          </div>
          {/* Mobile-only lead paragraph placed above the form */}
          <div className="order-1 md:hidden">
            <p className="mt-2 text-base text-base-content/70">
              Enrol your chief(s) into The Great Redeemer's Miraculous Manifestation  program and from this day forth each and every gift code that comes out will be redeemed automagically for you to collect from your in game system mailbox.
            </p>
          </div>
          {/* Blurb / value prop */}
          <div className="md:col-span-2 order-3 md:order-1">
            <div className="hidden md:flex items-center gap-2 text-sky-300/90 text-sm font-medium mb-2"><span className="inline-block">🎁</span><span>Automatic Gift Code Redemption</span></div>
            <h1 className="hidden md:block text-3xl md:text-4xl font-semibold tracking-tight">Never miss a reward again.</h1>
            {/* Hide this paragraph on mobile since we render it above the form there */}
            <p className="hidden md:block mt-2 md:text-lg text-base-content/70 max-w-2xl">
              Enrol your chief(s) into The Great Redeemer's Miraculous Manifestation  program and from this day forth each and every gift code that comes out will be redeemed automagically for you to collect from your in game system mailbox.
            </p>
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <span className="badge badge-ghost">Free</span>
              <span className="badge badge-ghost">No email</span>
              <span className="badge badge-ghost">No spam</span>
            </div>
            <ul className="mt-3 text-sm md:text-base text-base-content/70 space-y-2">
              <li className="flex items-center gap-2"><span className="text-emerald-400">✓</span><span>Auto‑redeems codes as they appear</span></li>
              <li className="flex items-center gap-2"><span className="text-emerald-400">✓</span><span>Works while you play — or sleep</span></li>
              <li className="flex items-center gap-2"><span className="text-emerald-400">✓</span><span>Set it once with your Username, User ID, and Alliance</span></li>
            </ul>
            {/* Removed small inline count; moved prominent counter under the form per feedback */}
          </div>
          {/* Form card */}
          <div className="md:col-span-1 order-2 md:order-2">
            <div className="card shadow-xl bg-base-100/95 ring-1 ring-sky-300/40 border border-white/10">
              <div className="card-body">
                <div className="grid grid-cols-1 gap-2 md:gap-3">
                  <input className="input input-bordered" placeholder="Username" value={name} onChange={e=>setName(e.target.value)} />
                  <input className="input input-bordered" placeholder="User ID" value={fid} onChange={e=>setFid(e.target.value)} />
                  <select className="select select-bordered" value={alliance} onChange={e=>setAlliance(e.target.value ? Number(e.target.value) : '')}>
                    <option value="">Alliance</option>
                    {alliances.map(a=> <option key={a.id} value={a.id}>{a.name} ({a.tag})</option>)}
                  </select>
                  <button className={btnClass} onClick={async()=>{
                    if(!fid || !name){ return }
                    await fetch(`${API_BASE}/users`, { method:'POST', body: new URLSearchParams({ fid, name, alliance_id: String(alliance||'') }) })
                    setFid(''); setName(''); setAlliance('')
                  }}>Activate</button>
                </div>
                <div className="text-center mt-3" aria-live="polite">
                  <span className="text-lg font-semibold tabular-nums">{data?.success ?? 0}</span>
                  <span className="ml-2 text-sm text-base-content/70">codes redeemed so far</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* SECONDARY: three columns with more contrast */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Left: Overview 2x2 grid (compact) */}
        <div className="card bg-base-100/90 ring-1 ring-white/10 shadow-xl">
          <div className="card-body">
            <div className="grid grid-cols-2 gap-3">
              <MiniStat label="Users" value={data?.users ?? '—'} accent="violet" />
              <MiniStat label="Gift Codes" value={data?.codes ?? '—'} accent="sky" />
              <MiniStat label="Redeemed" value={data?.success ?? '—'} accent="emerald" />
              <MiniStat label="Pending" value={data?.pending ?? '—'} accent="amber" />
            </div>
          </div>
        </div>

        {/* Middle: Activity (no nested inner box) */}
        <div className="card bg-base-100/90 ring-1 ring-white/10 shadow-xl">
          <div className="card-body py-4">
            <ActivityCarousel peek={peek} />
          </div>
        </div>

        {/* Right: Active codes */}
        <div className="card bg-base-100/90 ring-1 ring-white/10 shadow-xl">
          <div className="card-body">
            <div className="text-xs text-base-content/60 mb-2">Active codes</div>
            <div className="flex flex-wrap gap-2">
              {(codes.filter((c:any)=>c.active).slice(0,10)).map((c:any)=> (
                <span key={c.id} className="badge badge-outline">{c.code}</span>
              ))}
              {(!codes || codes.filter((c:any)=>c.active).length===0) && <span className="text-sm text-base-content/50">No active codes yet.</span>}
            </div>
          </div>
        </div>
      </section>

      
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
  type Item = { key: string, text: string, status: 'active'|'success'|'error'|'queued' }
  const up: any[] = Array.isArray(peek?.upcoming) ? peek.upcoming.slice(0, 2) : []
  const cur: any | null = peek?.current || null
  const rc: any[] = Array.isArray(peek?.recent) ? peek.recent.slice(0, 2) : []

  const rows: Item[] = [
    { key: 'up-0', text: '—', status: 'queued' },
    { key: 'up-1', text: '—', status: 'queued' },
    { key: 'cur', text: '—', status: 'queued' },
    { key: 'rc-0', text: '—', status: 'queued' },
    { key: 'rc-1', text: '—', status: 'queued' },
  ]

  // Rows represents the five slots shown in the carousel.

  if (up[0]) rows[0] = { key: `up-${up[0].fid}-${up[0].code}`, text: `${up[0].name || `User ID ${up[0].fid}`} — ${up[0].code}`, status: 'queued' }
  if (up[1]) rows[1] = { key: `up-${up[1].fid}-${up[1].code}`, text: `${up[1].name || `User ID ${up[1].fid}`} — ${up[1].code}`, status: 'queued' }
  if (cur) rows[2] = { key: `cur-${cur.fid}-${cur.code}`, text: `${cur.name || `User ID ${cur.fid}`} — ${cur.code}`, status: 'active' }
  if (rc[0]) rows[3] = { key: `rc-${rc[0].id}`, text: `${rc[0].name || `User ID ${rc[0].fid}`} — ${rc[0].code}`, status: rc[0].err ? 'error' : 'success' }
  if (rc[1]) rows[4] = { key: `rc-${rc[1].id}`, text: `${rc[1].name || `User ID ${rc[1].fid}`} — ${rc[1].code}`, status: rc[1].err ? 'error' : 'success' }

  return (
    <div className="mt-0">
      <div className="text-xs text-base-content/60 mb-1">Currently processing</div>
      <div className="grid grid-rows-5 gap-2">
        {rows.slice(0,5).map((it, idx)=> {
          const badgeClass = it.status==='success' ? 'badge-success' : it.status==='error' ? 'badge-error' : it.status==='active' ? 'badge-info' : 'badge-ghost'
          const activeRing = idx===2 ? 'ring-2 ring-primary/70' : ''
          return (
            <div key={it.key} className={`relative flex items-center justify-center px-2 py-1 rounded-full text-sm animate-slide-down-fade ${activeRing}`}>
              <div className={`badge ${badgeClass} gap-2 whitespace-nowrap`}>{it.text}</div>
              {idx===2 && it.status==='active' && (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-primary/90">
                  <span className="inline-block motion-safe:animate-spin-slow" aria-hidden="true">⚙️</span>
                  <span className="sr-only">Processing</span>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function MiniStat({ label, value, accent }: { label: string, value: any, accent: 'emerald'|'sky'|'violet'|'amber' }) {
  const bar = accent === 'emerald' ? 'bg-emerald-400/60' : accent === 'sky' ? 'bg-sky-400/60' : accent === 'violet' ? 'bg-violet-400/60' : 'bg-amber-400/60'
  return (
    <div className="relative overflow-hidden rounded-lg ring-1 ring-white/5 bg-base-300/20 p-2 text-center">
      <div className="text-xs uppercase tracking-wide mb-1 text-base-content/60">{label}</div>
      <div className="text-base font-semibold">{value}</div>
      <div className={`absolute left-0 top-0 h-full w-1 ${bar}`} />
    </div>
  )
}
