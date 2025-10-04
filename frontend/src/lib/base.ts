// Utility to detect a reverse-proxy path prefix (e.g., "/great-redeemer" or "/admin").
// If the first URL segment is not one of the app's top-level routes,
// treat it as the deployment prefix.
// NOTE: Do not include 'admin' here — when the app is hosted under
// a path prefix like "/admin", we want that to be detected as the
// prefix so API calls go to "/admin/api".
const KNOWN_TOP = new Set(['', 'alliances', 'users', 'codes', 'monitor'])

export function getPrefix(): string {
  const parts = window.location.pathname.split('/').filter(Boolean)
  return parts.length > 0 && !KNOWN_TOP.has(parts[0]) ? '/' + parts[0] : ''
}

export const API_BASE = getPrefix() + '/api'
