import React from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import './styles.css'
import App from './pages/App'
import Dashboard from './pages/Dashboard'
import Alliances from './pages/Alliances'
import Users from './pages/Users'
import Codes from './pages/Codes'
import UserDetail from './pages/UserDetail'
import Monitor from './pages/Monitor'
import { getPrefix } from './lib/base'

// Determine reverse-proxy prefix dynamically using shared helper.
const basename = getPrefix()

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'alliances', element: <Alliances /> },
      { path: 'users', element: <Users /> },
      { path: 'codes', element: <Codes /> },
      { path: 'users/:id', element: <UserDetail /> },
      { path: 'monitor', element: <Monitor /> },
    ],
  },
], { basename })

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
)
