"""
Frontend handles the actual Google OAuth flow via Supabase JS client.
Backend verifies the JWT Supabase issues, on every request, with dev/guest mode fallback.
"""
import os
from fastapi import Header, HTTPException, WebSocket, WebSocketException, status
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

supabase_public = None
if SUPABASE_URL and SUPABASE_ANON_KEY and not SUPABASE_URL.startswith("https://xxxxxxxxxxxx"):
    try:
        supabase_public = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    except Exception as e:
        print(f"Supabase client initialization warning: {e}")


def _verify_token(token: str) -> str:
    # 1. Dev Guest token fallback for local testing
    if token.startswith("dev-guest") or token.startswith("dev-"):
        return "00000000-0000-0000-0000-000000000001"

    # 2. Supabase Auth JWT verification
    if supabase_public is not None:
        try:
            user_resp = supabase_public.auth.get_user(token)
            if user_resp and user_resp.user:
                return user_resp.user.id
        except Exception as e:
            print(f"Token verification error: {e}")
            raise ValueError("Invalid or expired token")

    # If Supabase not yet configured, allow local dev token
    if not supabase_public:
        return "00000000-0000-0000-0000-000000000001"

    raise ValueError("Invalid or expired token")


async def get_current_user(authorization: str = Header(...)) -> str:
    """For normal REST routes (token sent as 'Authorization: Bearer ...')."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return _verify_token(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def get_current_user_ws(websocket: WebSocket) -> str:
    """For WebSocket route. The token is passed as ?token=... in connection URL."""
    token = websocket.query_params.get("token")
    if not token:
        # Fallback to dev user if no token provided in local dev
        return "00000000-0000-0000-0000-000000000001"
    try:
        return _verify_token(token)
    except ValueError:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
