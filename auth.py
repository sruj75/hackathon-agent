"""
Supabase JWT verification helpers for FastAPI and WebSocket auth.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import jwt
from fastapi import Header, HTTPException, status
from jwt import algorithms
from jwt import exceptions as jwt_exceptions

logger = logging.getLogger(__name__)

_JWKS_CACHE: dict[str, Any] = {"expires_at": 0.0, "keys": {}}
_JWKS_TTL_SECONDS = 300


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    email: Optional[str]
    claims: dict[str, Any]


def _project_url() -> str:
    project_url = os.getenv("SUPABASE_PROJECT_URL", "").strip().rstrip("/")
    if not project_url:
        raise RuntimeError("SUPABASE_PROJECT_URL environment variable is required")
    return project_url


def _issuer() -> str:
    return f"{_project_url()}/auth/v1"


def _jwks_url() -> str:
    return f"{_issuer()}/.well-known/jwks.json"


def _expected_audience() -> str:
    # Supabase access tokens for signed-in users are commonly "authenticated".
    return os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated")


async def _get_jwks_by_kid(kid: str) -> dict[str, Any]:
    now = time.time()
    cached_keys: dict[str, Any] = _JWKS_CACHE["keys"]
    cached_expiry: float = _JWKS_CACHE["expires_at"]
    if now < cached_expiry and kid in cached_keys:
        return cached_keys[kid]

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(_jwks_url())
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error("JWKS fetch failed with status %s", exc.response.status_code)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unable to verify token",
            ) from exc
        except httpx.RequestError as exc:
            logger.error("JWKS fetch request failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unable to verify token",
            ) from exc

    keys_by_kid: dict[str, Any] = {}
    for key in payload.get("keys", []):
        key_kid = key.get("kid")
        if key_kid:
            keys_by_kid[key_kid] = key

    _JWKS_CACHE["keys"] = keys_by_kid
    _JWKS_CACHE["expires_at"] = now + _JWKS_TTL_SECONDS

    if kid not in keys_by_kid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown JWT key id",
        )
    return keys_by_kid[kid]


async def verify_supabase_jwt(access_token: str) -> AuthUser:
    """Verify a Supabase access token and return normalized user claims."""
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing access token",
        )

    try:
        header = jwt.get_unverified_header(access_token)
    except jwt_exceptions.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid JWT header",
        ) from exc

    kid = header.get("kid")
    alg = header.get("alg")
    if not kid or not alg:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed JWT header",
        )

    if alg.startswith("HS"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unsupported JWT algorithm configuration",
        )

    jwk = await _get_jwks_by_kid(kid)
    try:
        key = algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    except (jwt_exceptions.InvalidKeyError, ValueError) as exc:
        logger.warning("Invalid JWK: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token signing key",
        ) from exc

    try:
        claims = jwt.decode(
            access_token,
            key=key,
            algorithms=[alg],
            audience=_expected_audience(),
            issuer=_issuer(),
        )
    except jwt_exceptions.PyJWTError as exc:
        logger.warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
        ) from exc

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT missing subject",
        )

    email = claims.get("email")
    return AuthUser(
        user_id=user_id,
        email=email if isinstance(email, str) else None,
        claims=claims,
    )


async def get_authenticated_user(
    authorization: Optional[str] = Header(default=None),
) -> AuthUser:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization must be Bearer token",
        )
    return await verify_supabase_jwt(token.strip())
