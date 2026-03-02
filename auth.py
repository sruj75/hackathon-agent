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
_ALLOWED_JWT_ALGORITHMS = frozenset({"ES256", "EdDSA"})


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


def _is_allowed_algorithm(alg: str) -> bool:
    return alg in _ALLOWED_JWT_ALGORITHMS


def _key_from_jwk(alg: str, jwk: dict[str, Any]) -> Any:
    algorithm_impl = algorithms.get_default_algorithms().get(alg)
    if algorithm_impl is None:
        logger.warning("Unsupported JWT algorithm handler: alg=%s", alg)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unsupported JWT algorithm configuration",
        )
    try:
        return algorithm_impl.from_jwk(json.dumps(jwk))
    except (jwt_exceptions.InvalidKeyError, ValueError, TypeError) as exc:
        logger.warning("Invalid JWK for alg=%s: %s", alg, exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token signing key",
        ) from exc


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
            logger.warning("JWKS fetch failed with status %s", exc.response.status_code)
            if kid in cached_keys:
                logger.warning(
                    "Using stale cached JWKS key after HTTP status failure for kid=%s",
                    kid,
                )
                return cached_keys[kid]
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unable to verify token",
            ) from exc
        except httpx.RequestError as exc:
            logger.warning("JWKS fetch request failed: %s", exc)
            if kid in cached_keys:
                logger.warning(
                    "Using stale cached JWKS key after request failure for kid=%s",
                    kid,
                )
                return cached_keys[kid]
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
        logger.info("Unknown JWT key id: kid=%s", kid)
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
        logger.info("Invalid JWT header: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid JWT header",
        ) from exc

    kid = header.get("kid")
    alg = header.get("alg")
    if not kid or not alg:
        logger.info("Malformed JWT header: missing kid or alg")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed JWT header",
        )

    if alg.startswith("HS"):
        logger.warning("Rejected unsupported symmetric JWT algorithm: alg=%s", alg)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unsupported JWT algorithm configuration",
        )

    if not _is_allowed_algorithm(alg):
        logger.warning("Rejected non-allowlisted JWT algorithm: alg=%s", alg)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unsupported JWT algorithm configuration",
        )

    jwk = await _get_jwks_by_kid(kid)

    jwk_use = jwk.get("use")
    if jwk_use is not None and jwk_use != "sig":
        logger.warning("Invalid JWK use for kid=%s: use=%s", kid, jwk_use)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token signing key",
        )

    jwk_alg = jwk.get("alg")
    if jwk_alg is not None and jwk_alg != alg:
        logger.warning(
            "JWT/JWK algorithm mismatch for kid=%s: header_alg=%s jwk_alg=%s",
            kid,
            alg,
            jwk_alg,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token signing key",
        )

    key = _key_from_jwk(alg=alg, jwk=jwk)

    try:
        claims = jwt.decode(
            access_token,
            key=key,
            algorithms=[alg],
            audience=_expected_audience(),
            issuer=_issuer(),
        )
    except jwt_exceptions.PyJWTError as exc:
        logger.info("JWT verification failed: %s", exc)
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
