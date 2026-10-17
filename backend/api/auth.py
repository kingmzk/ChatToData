"""Authentication and authorization dependency deriving trusted identity for Talk2Data HTTP API."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
from typing import Optional
from fastapi import Header, HTTPException, Request, status

from backend.config import get_auth_secret, get_env_mode
from backend.domain.models import ApiActor
from backend.security.auth import SecurityRuntime


def create_production_token(
    tenant_id: str,
    subject_id: str,
    secret: str,
    roles: tuple[str, ...] = ("analyst",),
    permissions: tuple[str, ...] = (),
) -> str:
    """Generate cryptographically signed HMAC-SHA256 bearer token for production authentication."""
    payload_dict = {
        "tenant_id": tenant_id,
        "subject_id": subject_id,
        "roles": list(roles),
        "permissions": list(permissions),
    }
    raw_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def get_current_actor(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_tenant_id: Optional[str] = Header(None),
    x_subject_id: Optional[str] = Header(None),
    x_user_roles: Optional[str] = Header(None),
    x_user_permissions: Optional[str] = Header(None),
) -> ApiActor:
    """Derive authenticated actor from trusted authentication context.
    
    In PRODUCTION mode:
    - Requires TALK2DATA_AUTH_SECRET configured; otherwise explicitly disables production mode with 503.
    - Identity is derived strictly from HMAC-SHA256 cryptographically verified Authorization bearer token.
    - Client-supplied identity headers (X-Tenant-Id, X-Subject-Id) are strictly rejected with 400 Bad Request.
    
    In DEVELOPMENT mode (TALK2DATA_ENV='development'):
    - Allows developer convenience headers with explicit security runtime verification.
    """
    env = get_env_mode()
    security: SecurityRuntime = getattr(request.app.state, "security_runtime", SecurityRuntime())

    if env == "production":
        secret = get_auth_secret()
        if not secret or not secret.strip():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Production mode disabled: TALK2DATA_AUTH_SECRET is not configured for cryptographic bearer-token signature verification.",
            )

        # Reject client-supplied header spoofing
        if x_tenant_id or x_subject_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Security violation: Client-supplied identity headers are forbidden in production mode.",
            )

        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or malformed Authorization bearer token.",
            )

        raw_token = authorization[7:].strip()
        parts = raw_token.split(".")
        if len(parts) != 2:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token structure: Expected '<payload_b64>.<signature>'.",
            )

        payload_b64, signature = parts[0], parts[1]
        expected_sig = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Cryptographic verification failed: Token signature is invalid or tampered.",
            )

        try:
            payload_json = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
            data = json.loads(payload_json)
            tenant_id = data["tenant_id"]
            subject_id = data["subject_id"]
            roles = tuple(data.get("roles", ["analyst"]))
            permissions = set(data.get("permissions", []))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Malformed token payload: {exc}",
            )

        return security.authenticate_actor(
            tenant_id=tenant_id,
            subject_id=subject_id,
            roles=roles,
            permissions=permissions,
        )

    else:
        # Development mode
        tenant_id = x_tenant_id or "tenant_a"
        subject_id = x_subject_id or "user_alice"
        roles = tuple([r.strip() for r in x_user_roles.split(",") if r.strip()]) if x_user_roles else ()
        permissions = set([p.strip() for p in x_user_permissions.split(",") if p.strip()]) if x_user_permissions else set()

        if authorization and authorization.startswith("Bearer dev-"):
            parts = authorization[11:].split("-")
            if len(parts) >= 2:
                tenant_id = parts[0]
                subject_id = parts[1]

        return security.authenticate_actor(
            tenant_id=tenant_id,
            subject_id=subject_id,
            roles=roles,
            permissions=permissions,
        )
