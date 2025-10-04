#!/usr/bin/env python3
import argparse
import os
import sys
import json
from typing import List, Dict, Any, Optional

import requests


API_BASE = "https://api.cloudflare.com/client/v4"


def _try_token(value: str) -> bool:
    try:
        r = requests.get(f"{API_BASE}/user/tokens/verify", headers={"Authorization": f"Bearer {value}"}, timeout=15)
        return r.status_code == 200 and r.json().get("success") is True
    except Exception:
        return False


def cf_headers() -> Dict[str, str]:
    # Prefer explicit token
    token = os.getenv("CLOUDFLARE_API_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # If only CLOUDFLARE_API_KEY is provided, auto-detect if it's really a token
    key = os.getenv("CLOUDFLARE_API_KEY")
    email = os.getenv("CLOUDFLARE_EMAIL")
    if key:
        if _try_token(key):  # it's actually a token value
            return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if email:  # treat as global API key
            return {"X-Auth-Key": key, "X-Auth-Email": email, "Content-Type": "application/json"}
        print("CLOUDFLARE_API_KEY looks like a global API key. Please also set CLOUDFLARE_EMAIL, or provide CLOUDFLARE_API_TOKEN instead.", file=sys.stderr)
        sys.exit(2)

    print("Set CLOUDFLARE_API_TOKEN, or set CLOUDFLARE_API_KEY with CLOUDFLARE_EMAIL.", file=sys.stderr)
    sys.exit(2)


def get_account_id(headers: Dict[str, str], explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    r = requests.get(f"{API_BASE}/accounts", headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(data)
    accounts = data.get("result", [])
    if not accounts:
        raise RuntimeError("No Cloudflare accounts visible to token")
    if len(accounts) > 1:
        print("Multiple accounts found; pass --account-id to select one", file=sys.stderr)
        for a in accounts:
            print(" -", a.get("id"), a.get("name"), file=sys.stderr)
        sys.exit(2)
    return accounts[0]["id"]


def ensure_app(headers: Dict[str, str], account_id: str, name: str, domain: str, session_duration: str = "24h") -> Dict[str, Any]:
    # Create a self-hosted Access application with a primary domain (host + optional path)
    # Try to find existing by name
    r = requests.get(f"{API_BASE}/accounts/{account_id}/access/apps", headers=headers, timeout=30)
    r.raise_for_status()
    apps = r.json().get("result", [])
    for app in apps:
        if app.get("name") == name:
            return app
    payload = {
        "name": name,
        "type": "self_hosted",
        "domain": domain,
        "session_duration": session_duration,
        "path_cookie_attribute": True,
        "app_launcher_visible": False,
    }
    r = requests.post(f"{API_BASE}/accounts/{account_id}/access/apps", headers=headers, data=json.dumps(payload), timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(data)
    return data["result"]


def ensure_policy(headers: Dict[str, str], account_id: str, app_id: str, name: str, decision: str, precedence: int) -> Dict[str, Any]:
    # include everyone
    base = f"{API_BASE}/accounts/{account_id}/access/apps/{app_id}/policies"
    r = requests.get(base, headers=headers, timeout=30)
    r.raise_for_status()
    for p in r.json().get("result", []):
        if p.get("name") == name:
            return p
    payload = {
        "name": name,
        "precedence": precedence,
        # Cloudflare uses 'non_identity' to bypass authentication on paths
        "decision": decision,
        "include": [{"everyone": {}}],
        # Note: path-scoped policies may require `resource_path` (not used here)
    }
    r = requests.post(base, headers=headers, data=json.dumps(payload), timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(data)
    return data["result"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Setup Cloudflare Access apps/policies for WOS Redeemer")
    ap.add_argument("--host", default=os.getenv("CF_HOST", "wos.ratme.org"))
    ap.add_argument("--prefix", default=os.getenv("CF_PREFIX", "/great-redeemer"))
    ap.add_argument("--account-id", default=os.getenv("CLOUDFLARE_ACCOUNT_ID"))
    ap.add_argument("--team-domain", default=os.getenv("CF_TEAM_DOMAIN") or os.getenv("CLOUDFLARE_TEAM_DOMAIN"))
    args = ap.parse_args()

    headers = cf_headers()
    account_id = get_account_id(headers, args.account_id)
    prefix = "/" + args.prefix.strip("/")

    # Public app for read-only pages (bypass)
    # Public app: set the domain to the exact public entry point
    public_app = ensure_app(headers, account_id, name="WOS Redeemer Public", domain=f"{args.host}{prefix}")
    # Use 'non_identity' (bypass) decision for public
    ensure_policy(headers, account_id, public_app["id"], name="Bypass public", decision="non_identity", precedence=1)

    # Protected app for everything else under the prefix (require login)
    protected_app = ensure_app(headers, account_id, name="WOS Redeemer Admin", domain=f"{args.host}{prefix}/*")
    ensure_policy(headers, account_id, protected_app["id"], name="Require identity", decision="allow", precedence=1)

    aud = protected_app.get("aud") or protected_app.get("audience_tag")
    if not aud:
        print("Warning: could not determine audience (AUD) from Access app response", file=sys.stderr)

    # Write .env.local with CF vars for the app to verify JWT
    team = args.team_domain or ""
    with open(".env.local", "w") as f:
        if team:
            f.write(f"CF_TEAM_DOMAIN={team}\n")
        if aud:
            f.write(f"CF_ACCESS_AUD={aud}\n")
    print("Created/verified Cloudflare Access apps.")
    print(" - Public app:", public_app.get("id"))
    print(" - Protected app:", protected_app.get("id"))
    if aud:
        print(" - AUD:", aud)
        print("Wrote .env.local with CF_TEAM_DOMAIN/CF_ACCESS_AUD (if provided).")


if __name__ == "__main__":
    main()
