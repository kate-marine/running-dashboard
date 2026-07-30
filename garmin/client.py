"""Authenticated Garmin Connect session, shared by every pull script.

Uses garminconnect's built-in tokenstore: after the first login (which may
prompt for MFA), the session token is cached to disk so later runs don't
need to re-authenticate or re-enter an MFA code.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from garminconnect import Garmin

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOKENSTORE_PATH = PROJECT_ROOT / "data" / ".garmin_tokens"


def get_client() -> Garmin:
    load_dotenv()

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            "GARMIN_EMAIL / GARMIN_PASSWORD not set. Copy .env.example to "
            ".env and fill in your Garmin Connect credentials."
        )

    TOKENSTORE_PATH.mkdir(parents=True, exist_ok=True)

    client = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: input("Enter Garmin Connect MFA code: "),
    )
    client.login(tokenstore=str(TOKENSTORE_PATH))
    return client
