import { useEffect, useState } from 'react'
import { API_BASE } from '../lib/base'

export default function Alliances() {
  const [rows, setRows] = useState<any[]>([])
  const [form, setForm] = useState({ name: '', tag: '', quota: 0 })
  const [mgr, setMgr] = useState<Record<number, {username: string, password: string, rank: 'R4'|'R5'}>>({})
  const [editingId, setEditingId] = useState<number|null>(null)
  const [draft, setDraft] = useState<{name: string, tag: string, quota: number}>({name:'', tag:'', quota:0})
  const load = () => fetch(`${API_BASE}/alliances`).then(r => r.json()).then(setRows)
  useEffect(() => { load() }, [])
  const onMgrChange = (aid: number, patch: Partial<{username: string, password: string, rank: 'R4'|'R5'}>) => {
    setMgr(prev => ({...prev, [aid]: {...{username:'', password:'', rank:'R4'}, ...(prev[aid]||{}), ...patch}}))
  }
  const addMgr = async (aid: number) => {
    const m = mgr[aid]
    if (!m || !m.username || !m.password) return
    await fetch(`${API_BASE}/managers`, {
      method: 'POST',
      body: new URLSearchParams({ username: m.username, password: m.password, alliance_id: String(aid), rank: m.rank })
    })
    onMgrChange(aid, {username:'', password:'', rank:'R4'})
    load()
  }
  return (
    <div className="space-y-4">
      <div className="card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur"><div className="card-body">
        <div className="font-semibold pl-3 border-l-2 border-violet-400/60">Create Alliance</div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
          <input className="input input-bordered" placeholder="Name" value={form.name} onChange={e=>setForm({...form, name:e.target.value})}/>
          <input className="input input-bordered" placeholder="TAG" value={form.tag} onChange={e=>setForm({...form, tag:e.target.value})}/>
          <input className="input input-bordered" placeholder="Quota" type="number" value={form.quota} onChange={e=>setForm({...form, quota:Number(e.target.value)})}/>
          <button className="btn btn-primary" onClick={async()=>{await fetch(`${API_BASE}/alliances`,{method:'POST',body:new URLSearchParams({name:form.name, tag:form.tag, quota:String(form.quota)})}); setForm({name:'',tag:'',quota:0}); load()}}>Create</button>
        </div>
      </div></div>
      <div className="card bg-base-100/80 shadow-2xl border border-white/10 backdrop-blur"><div className="card-body">
        <div className="font-semibold mb-2 pl-3 border-l-2 border-sky-400/60">Alliances</div>
        <div className="overflow-x-auto rounded-lg ring-1 ring-white/5">
          <table className="table table-zebra">
            <thead><tr><th>Name</th><th>Tag</th><th>Quota</th><th>Members</th><th>Managers</th><th>Add Manager</th><th>Actions</th></tr></thead>
            <tbody>
              {rows.map((a:any)=> (
                <tr key={a.id}>
                  <td>
                    {editingId===a.id ? (
                      <input className="input input-bordered input-sm w-full" value={draft.name} onChange={e=>setDraft({...draft, name:e.target.value})} />
                    ) : a.name}
                  </td>
                  <td>
                    {editingId===a.id ? (
                      <input className="input input-bordered input-sm w-24" value={draft.tag} onChange={e=>setDraft({...draft, tag:e.target.value})} />
                    ) : <span className="badge badge-neutral">{a.tag}</span>}
                  </td>
                  <td>
                    {editingId===a.id ? (
                      <input type="number" className="input input-bordered input-sm w-24" value={draft.quota} onChange={e=>setDraft({...draft, quota:Number(e.target.value)})} />
                    ) : a.quota}
                  </td>
                  <td>{a.members}</td>
                  <td className="space-x-1">
                    {(a.managers||[]).length===0 ? '—' : (a.managers||[]).map((m:any)=> <span key={m.id} className="badge">{m.username} ({m.rank})</span>)}
                  </td>
                  <td>
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
                      <input className="input input-bordered input-sm" placeholder="user" value={mgr[a.id]?.username||''} onChange={e=>onMgrChange(a.id,{username:e.target.value})}/>
                      <input type="password" className="input input-bordered input-sm" placeholder="pass" value={mgr[a.id]?.password||''} onChange={e=>onMgrChange(a.id,{password:e.target.value})}/>
                      <select className="select select-bordered select-sm" value={mgr[a.id]?.rank||'R4'} onChange={e=>onMgrChange(a.id,{rank:(e.target.value as 'R4'|'R5')})}>
                        <option>R4</option>
                        <option>R5</option>
                      </select>
                      <button className="btn btn-primary btn-sm" onClick={()=>addMgr(a.id)}>Add</button>
                    </div>
                  </td>
                  <td className="w-28">
                    {editingId===a.id ? (
                      <div className="flex gap-1">
                        <button className="btn btn-success btn-sm" title="Save" onClick={async()=>{
                          const params = new URLSearchParams({ name: draft.name, tag: draft.tag, quota: String(draft.quota) })
                          await fetch(`${API_BASE}/alliances/${a.id}`, { method:'POST', body: params })
                          setEditingId(null)
                          load()
                        }}>✔️</button>
                        <button className="btn btn-ghost btn-sm" title="Cancel" onClick={()=>setEditingId(null)}>✖️</button>
                      </div>
                    ) : (
                      <button className="btn btn-ghost btn-sm" title="Edit" onClick={()=>{ setEditingId(a.id); setDraft({ name: a.name||'', tag: a.tag||'', quota: a.quota||0 }) }}>✏️</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div></div>
    </div>
  )
}
