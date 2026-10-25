"""Verify a Google Sign-In ID token (the credential the Google button hands the browser) using Google's tokeninfo endpoint."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleAuthError(Exception):
    pass


def _fetch(token, timeout=10):
    url = TOKENINFO + "?" + urllib.parse.urlencode({"id_token": token})
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:            # noqa: S310 - fixed https host
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise GoogleAuthError("Google did not accept this sign-in. Please try again.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GoogleAuthError("Could not reach Google. Check your connection and try again.") from exc


def verify_id_token(token, client_id, fetch=None, now=None):
    """Returns {'sub','email','name','picture'} for a valid token issued to OUR client id, else raises GoogleAuthError."""
    if not client_id:
        raise GoogleAuthError("Google sign-in is not set up yet.")
    if not token:
        raise GoogleAuthError("No Google credential received.")
    info = (fetch or _fetch)(token)
    if info.get("aud") != client_id:
        raise GoogleAuthError("This Google sign-in was issued for a different app.")
    if info.get("iss") not in ISSUERS:
        raise GoogleAuthError("Unexpected Google token issuer.")
    if int(info.get("exp", 0)) < (now or time.time()):
        raise GoogleAuthError("This Google sign-in has expired. Please try again.")
    if str(info.get("email_verified", "")).lower() != "true":
        raise GoogleAuthError("Your Google email address is not verified.")
    return {"sub": info["sub"], "email": info.get("email", "").lower(), "name": info.get("name", ""), "picture": info.get("picture", "")}
