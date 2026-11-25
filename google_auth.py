import os
import time
import json
import secrets
import hashlib
import base64
import datetime
from google.oauth2.credentials import Credentials
from google.auth.exceptions import RefreshError
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# Don't raise if Google grants a subset of requested scopes
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

SCOPES = [
    CALENDAR_SCOPE,
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


def _callback_url():
    port = os.environ.get("PORT", "4567")
    return f"http://localhost:{port}/oauth/callback"


def _client_config():
    return {
        "web": {
            "client_id":     os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri":      "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": [_callback_url()],
        }
    }


def authorization_url():
    verifier = secrets.token_urlsafe(96)[:128]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=_callback_url(),
    )
    url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        code_challenge=challenge,
        code_challenge_method="S256",
    )
    return url, state, verifier


def exchange_code(code, state, code_verifier):
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=_callback_url(),
        state=state,
    )
    flow.fetch_token(code=code, code_verifier=code_verifier)
    return flow.credentials


class AccountAuthError(Exception):
    """The account's refresh token was rejected; the user must re-connect it."""

    def __init__(self, email, message):
        super().__init__(f"{email}: {message}")
        self.email = email


def mark_auth_error(db, account_id, message):
    db.execute(
        "UPDATE google_accounts SET auth_error=? WHERE id=?",
        (str(message)[:300], account_id),
    )
    db.commit()


def credentials_for_account(account_row, db):
    # google-auth expects expiry as a naive UTC datetime. Without it,
    # creds.expired is always False and the refresh below never runs.
    expiry = datetime.datetime.fromtimestamp(
        account_row["expires_at"] or 0, datetime.timezone.utc
    ).replace(tzinfo=None)

    creds = Credentials(
        token=account_row["access_token"],
        refresh_token=account_row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=SCOPES,
        expiry=expiry,
    )

    if creds.expired and creds.refresh_token:
        import google.auth.transport.requests
        try:
            creds.refresh(google.auth.transport.requests.Request())
        except RefreshError as e:
            mark_auth_error(db, account_row["id"], e)
            raise AccountAuthError(account_row["email"], e)
        db.execute(
            "UPDATE google_accounts SET access_token=?, expires_at=?, auth_error='' WHERE id=?",
            (creds.token, int(creds.expiry.timestamp()), account_row["id"]),
        )
        db.commit()

    return creds
