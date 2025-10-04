from __future__ import annotations

import os
import time
from typing import Optional, Dict, Any

import jwt
import requests
from jwt import PyJWKClient


class CFVerifier:
    def __init__(self, team_domain: str, audience: Optional[str] = None, cache_ttl: int = 600):
        self.team_domain = team_domain.rstrip('/')
        self.audience = audience
        self.cache_ttl = cache_ttl
        self._jwks_client: Optional[PyJWKClient] = None
        self._jwks_url: Optional[str] = None
        self._jwks_loaded_at: float = 0.0

    @property
    def jwks_url(self) -> str:
        if not self._jwks_url:
            self._jwks_url = f"https://{self.team_domain}/cdn-cgi/access/certs"
        return self._jwks_url

    def _get_jwks_client(self) -> PyJWKClient:
        # PyJWKClient handles caching JWKs internally; we keep instance around
        if self._jwks_client is None or (time.time() - self._jwks_loaded_at) > self.cache_ttl:
            self._jwks_client = PyJWKClient(self.jwks_url)
            self._jwks_loaded_at = time.time()
        return self._jwks_client

    def verify(self, token: str) -> Dict[str, Any]:
        jwk_client = self._get_jwks_client()
        signing_key = jwk_client.get_signing_key_from_jwt(token)
        options = {"verify_aud": bool(self.audience)}
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=self.audience if self.audience else None,
        )
        return decoded


_verifier: Optional[CFVerifier] = None


def get_verifier() -> Optional[CFVerifier]:
    global _verifier
    if _verifier is not None:
        return _verifier
    team = os.getenv("CF_TEAM_DOMAIN") or os.getenv("CLOUDFLARE_TEAM_DOMAIN")
    aud = os.getenv("CF_ACCESS_AUD") or os.getenv("CLOUDFLARE_ACCESS_AUD")
    if not team:
        return None
    _verifier = CFVerifier(team_domain=team, audience=aud)
    return _verifier

