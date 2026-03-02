"""Unit tests for JWT verification behavior in auth.py."""
from __future__ import annotations

import base64
import time
import httpx
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from fastapi import HTTPException

import auth


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _set_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_PROJECT_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", "authenticated")
    auth._JWKS_CACHE["expires_at"] = 0.0
    auth._JWKS_CACHE["keys"] = {}


def _claims(*, iss: str, aud: str, sub: str = "user_123", ttl_seconds: int = 300) -> dict:
    now = int(time.time())
    return {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "iat": now,
        "nbf": now,
        "exp": now + ttl_seconds,
    }


def _es256_keypair_and_jwk(
    *,
    kid: str = "kid_es256",
    use: str = "sig",
    jwk_alg: str | None = "ES256",
):
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "EC",
        "kid": kid,
        "use": use,
        "crv": "P-256",
        "x": _b64url(public_numbers.x.to_bytes(32, "big")),
        "y": _b64url(public_numbers.y.to_bytes(32, "big")),
    }
    if jwk_alg is not None:
        jwk["alg"] = jwk_alg
    return private_key, jwk


def _eddsa_keypair_and_jwk(
    *,
    kid: str = "kid_eddsa",
    use: str = "sig",
    jwk_alg: str | None = "EdDSA",
):
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    jwk = {
        "kty": "OKP",
        "kid": kid,
        "use": use,
        "crv": "Ed25519",
        "x": _b64url(public_bytes),
    }
    if jwk_alg is not None:
        jwk["alg"] = jwk_alg
    return private_key, jwk


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_supabase_jwt_accepts_es256(monkeypatch):
    _set_auth_env(monkeypatch)
    private_key, jwk = _es256_keypair_and_jwk()
    token = jwt.encode(
        _claims(iss=auth._issuer(), aud=auth._expected_audience()),
        private_key,
        algorithm="ES256",
        headers={"kid": jwk["kid"]},
    )
    monkeypatch.setattr(auth, "_get_jwks_by_kid", AsyncMock(return_value=jwk))

    user = await auth.verify_supabase_jwt(token)

    assert user.user_id == "user_123"
    assert user.email is None
    assert user.claims["aud"] == "authenticated"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_supabase_jwt_accepts_eddsa(monkeypatch):
    _set_auth_env(monkeypatch)
    private_key, jwk = _eddsa_keypair_and_jwk()
    token = jwt.encode(
        _claims(iss=auth._issuer(), aud=auth._expected_audience()),
        private_key,
        algorithm="EdDSA",
        headers={"kid": jwk["kid"]},
    )
    monkeypatch.setattr(auth, "_get_jwks_by_kid", AsyncMock(return_value=jwk))

    user = await auth.verify_supabase_jwt(token)

    assert user.user_id == "user_123"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_supabase_jwt_rejects_hs256(monkeypatch):
    _set_auth_env(monkeypatch)
    token = jwt.encode(
        _claims(iss=auth._issuer(), aud=auth._expected_audience()),
        "secret",
        algorithm="HS256",
        headers={"kid": "kid_hs256"},
    )

    with pytest.raises(HTTPException) as exc:
        await auth.verify_supabase_jwt(token)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Unsupported JWT algorithm configuration"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_supabase_jwt_rejects_non_allowlisted_algorithm(monkeypatch):
    _set_auth_env(monkeypatch)
    get_jwks_mock = AsyncMock(return_value={})
    monkeypatch.setattr(auth, "_get_jwks_by_kid", get_jwks_mock)
    monkeypatch.setattr(
        auth.jwt,
        "get_unverified_header",
        lambda _token: {"kid": "kid_1", "alg": "RS256"},
    )

    with pytest.raises(HTTPException) as exc:
        await auth.verify_supabase_jwt("ignored")

    assert exc.value.status_code == 401
    assert exc.value.detail == "Unsupported JWT algorithm configuration"
    get_jwks_mock.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        {"alg": "ES256"},
        {"kid": "kid_1"},
    ],
)
async def test_verify_supabase_jwt_rejects_missing_header_fields(monkeypatch, header):
    _set_auth_env(monkeypatch)
    monkeypatch.setattr(auth.jwt, "get_unverified_header", lambda _token: header)

    with pytest.raises(HTTPException) as exc:
        await auth.verify_supabase_jwt("ignored")

    assert exc.value.status_code == 401
    assert exc.value.detail == "Malformed JWT header"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_supabase_jwt_rejects_unknown_kid(monkeypatch):
    _set_auth_env(monkeypatch)
    private_key, jwk = _es256_keypair_and_jwk()
    token = jwt.encode(
        _claims(iss=auth._issuer(), aud=auth._expected_audience()),
        private_key,
        algorithm="ES256",
        headers={"kid": jwk["kid"]},
    )
    monkeypatch.setattr(
        auth,
        "_get_jwks_by_kid",
        AsyncMock(
            side_effect=HTTPException(
                status_code=401,
                detail="Unknown JWT key id",
            )
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await auth.verify_supabase_jwt(token)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Unknown JWT key id"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_supabase_jwt_rejects_non_signature_jwk(monkeypatch):
    _set_auth_env(monkeypatch)
    private_key, jwk = _es256_keypair_and_jwk(use="enc")
    token = jwt.encode(
        _claims(iss=auth._issuer(), aud=auth._expected_audience()),
        private_key,
        algorithm="ES256",
        headers={"kid": jwk["kid"]},
    )
    monkeypatch.setattr(auth, "_get_jwks_by_kid", AsyncMock(return_value=jwk))

    with pytest.raises(HTTPException) as exc:
        await auth.verify_supabase_jwt(token)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token signing key"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_supabase_jwt_rejects_header_jwk_algorithm_mismatch(monkeypatch):
    _set_auth_env(monkeypatch)
    private_key, jwk = _es256_keypair_and_jwk(jwk_alg="EdDSA")
    token = jwt.encode(
        _claims(iss=auth._issuer(), aud=auth._expected_audience()),
        private_key,
        algorithm="ES256",
        headers={"kid": jwk["kid"]},
    )
    monkeypatch.setattr(auth, "_get_jwks_by_kid", AsyncMock(return_value=jwk))

    with pytest.raises(HTTPException) as exc:
        await auth.verify_supabase_jwt(token)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token signing key"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_supabase_jwt_rejects_issuer_mismatch(monkeypatch):
    _set_auth_env(monkeypatch)
    private_key, jwk = _es256_keypair_and_jwk()
    token = jwt.encode(
        _claims(iss="https://other.supabase.co/auth/v1", aud=auth._expected_audience()),
        private_key,
        algorithm="ES256",
        headers={"kid": jwk["kid"]},
    )
    monkeypatch.setattr(auth, "_get_jwks_by_kid", AsyncMock(return_value=jwk))

    with pytest.raises(HTTPException) as exc:
        await auth.verify_supabase_jwt(token)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid or expired access token"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_supabase_jwt_rejects_audience_mismatch(monkeypatch):
    _set_auth_env(monkeypatch)
    private_key, jwk = _es256_keypair_and_jwk()
    token = jwt.encode(
        _claims(iss=auth._issuer(), aud="service_role"),
        private_key,
        algorithm="ES256",
        headers={"kid": jwk["kid"]},
    )
    monkeypatch.setattr(auth, "_get_jwks_by_kid", AsyncMock(return_value=jwk))

    with pytest.raises(HTTPException) as exc:
        await auth.verify_supabase_jwt(token)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid or expired access token"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_expected_auth_failures_log_at_info(monkeypatch, caplog):
    _set_auth_env(monkeypatch)
    private_key, jwk = _es256_keypair_and_jwk()
    token = jwt.encode(
        _claims(iss=auth._issuer(), aud=auth._expected_audience(), ttl_seconds=-1),
        private_key,
        algorithm="ES256",
        headers={"kid": jwk["kid"]},
    )
    monkeypatch.setattr(auth, "_get_jwks_by_kid", AsyncMock(return_value=jwk))
    caplog.set_level("INFO")

    with pytest.raises(HTTPException):
        await auth.verify_supabase_jwt(token)

    assert any(
        record.levelname == "INFO" and "JWT verification failed" in record.message
        for record in caplog.records
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_anomalous_jwk_mismatch_logs_at_warning(monkeypatch, caplog):
    _set_auth_env(monkeypatch)
    private_key, jwk = _es256_keypair_and_jwk(jwk_alg="EdDSA")
    token = jwt.encode(
        _claims(iss=auth._issuer(), aud=auth._expected_audience()),
        private_key,
        algorithm="ES256",
        headers={"kid": jwk["kid"]},
    )
    monkeypatch.setattr(auth, "_get_jwks_by_kid", AsyncMock(return_value=jwk))
    caplog.set_level("WARNING")

    with pytest.raises(HTTPException):
        await auth.verify_supabase_jwt(token)

    assert any(
        record.levelname == "WARNING"
        and "JWT/JWK algorithm mismatch" in record.message
        for record in caplog.records
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_jwks_by_kid_uses_stale_cached_key_when_refresh_returns_http_error(monkeypatch):
    _set_auth_env(monkeypatch)
    _, jwk = _es256_keypair_and_jwk(kid="kid_stale")
    auth._JWKS_CACHE["keys"] = {"kid_stale": jwk}
    auth._JWKS_CACHE["expires_at"] = 0.0

    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url):
            request = httpx.Request("GET", "https://project.supabase.co/auth/v1/.well-known/jwks.json")
            response = httpx.Response(502, request=request)
            raise httpx.HTTPStatusError("bad gateway", request=request, response=response)

    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda **_kwargs: _FailingClient())

    resolved = await auth._get_jwks_by_kid("kid_stale")

    assert resolved == jwk


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_jwks_by_kid_raises_when_no_cached_key_and_refresh_fails(monkeypatch):
    _set_auth_env(monkeypatch)

    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url):
            request = httpx.Request("GET", "https://project.supabase.co/auth/v1/.well-known/jwks.json")
            raise httpx.RequestError("network down", request=request)

    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda **_kwargs: _FailingClient())

    with pytest.raises(HTTPException) as exc:
        await auth._get_jwks_by_kid("kid_missing")

    assert exc.value.status_code == 401
    assert exc.value.detail == "Unable to verify token"
