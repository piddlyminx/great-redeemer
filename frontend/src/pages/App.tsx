import { Link, Outlet } from 'react-router-dom'

export default function App() {
  return (
    <div
      data-theme="redeemerDark"
      className="min-h-screen text-base-content bg-base-200 relative overflow-hidden"
    >
      {/* soft radial gradient background */}
      <div className="pointer-events-none absolute inset-0 opacity-50 [background:radial-gradient(1100px_520px_at_80%_-10%,rgba(125,211,252,0.10),transparent_60%),radial-gradient(800px_480px_at_10%_-20%,rgba(167,139,250,0.10),transparent_60%),radial-gradient(700px_420px_at_50%_110%,rgba(34,197,94,0.06),transparent_60%)]" />

      <div className="navbar w-full bg-base-100/80 backdrop-blur border-b border-white/5 p-0">
        <div className="container-balanced w-full">
          <div className="flex w-full flex-col md:flex-row md:items-center md:justify-between gap-2 py-2">
            <div className="font-semibold tracking-wide text-xl md:text-lg text-center md:text-left">
              <Link to="." className="hover:opacity-90">The Great Redeemer</Link>
            </div>
            <div className="flex flex-wrap justify-center gap-2 md:mt-0">
              {/* Use relative links so they honor basename ("/" vs "/admin"). */}
              <Link className="btn btn-ghost" to=".">Dashboard</Link>
              <Link className="btn btn-ghost" to="users">Users</Link>
              <Link className="btn btn-ghost" to="codes">Codes</Link>
              {/* Alliances and Monitoring links removed by request */}
            </div>
          </div>
        </div>
      </div>
      <main className="container-balanced w-full py-4">
        <Outlet />
      </main>
    </div>
  )
}
