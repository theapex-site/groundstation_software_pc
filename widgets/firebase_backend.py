"""
widgets/firebase_backend.py
───────────────────────────
Accounts and licensing for APEX GCS, against the same Firebase project that
backs theapex.site and the admin app (`theapex-88dab`).

Replaces the Google Sheets + Gmail-SMTP half of widgets/auth_backend.py. No
Google Sheet is read or written by this module, and nothing here needs a
service-account key:

  * Auth goes through the Identity Toolkit REST API using the project's **web**
    API key, which is public by design — it already ships inside the admin
    app's JavaScript bundle. It identifies the project, it does not authorise
    anything; every request still carries the signed-in user's own ID token.
  * Firestore goes through the REST API with that ID token, so what a customer
    may read or write is decided by firestore.rules on Google's side, not by
    this file. A patched build gains nothing.
  * Email verification is sent by Firebase itself. That is what let the
    hardcoded Gmail address and app password come out of the build — they were
    extractable from every .exe ever shipped.

Licence model (see firebase/firestore-schema.md in the APEX_Website repo):
a licence doc is keyed by the licence key, carries the buyer's email from the
order, and is minted by the admin app when an order containing a CubeSat is
fulfilled. Sign-up requires the key and the email to match. One computer at a
time: `deviceId` is last-writer-wins, so signing in elsewhere signs this
machine out at its next launch.
"""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import time
import urllib.parse

import requests

# Public web config, same values as admin/src/firebase.js. The API key is not a
# secret — see the module docstring.
FIREBASE_API_KEY = "AIzaSyA4BE0kpcF9o2D9PHbcD3DAvlJJBZ7UCk4"
PROJECT_ID = "theapex-88dab"

# Point at the Firebase Emulator Suite instead of production by exporting the
# same two variables Google's own SDKs honour, so firebase-admin on the API
# side picks up the identical setting without a second switch:
#
#   set FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
#   set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
#
# Unset, everything goes to the live project. Nothing else in the app changes,
# so what gets tested locally is the same code path that ships.
_AUTH_EMU = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST", "").strip()
_FS_EMU = os.environ.get("FIRESTORE_EMULATOR_HOST", "").strip()
USING_EMULATOR = bool(_AUTH_EMU or _FS_EMU)

if _AUTH_EMU:
    _IDENTITY = "http://" + _AUTH_EMU + "/identitytoolkit.googleapis.com/v1/accounts:"
    _SECURETOKEN = "http://" + _AUTH_EMU + "/securetoken.googleapis.com/v1/token"
else:
    _IDENTITY = "https://identitytoolkit.googleapis.com/v1/accounts:"
    _SECURETOKEN = "https://securetoken.googleapis.com/v1/token"

if _FS_EMU:
    _FIRESTORE = (
        "http://" + _FS_EMU + "/v1/projects/" + PROJECT_ID
        + "/databases/(default)/documents"
    )
else:
    _FIRESTORE = (
        "https://firestore.googleapis.com/v1/projects/"
        + PROJECT_ID
        + "/databases/(default)/documents"
    )

_TIMEOUT = 20


class AuthError(Exception):
    """Anything the user should be shown, already phrased for them."""


# Identity Toolkit error codes are shouty and unhelpful in a dialog box.
_MESSAGES = {
    "EMAIL_EXISTS": "An account already exists for this email address. Try signing in instead.",
    "EMAIL_NOT_FOUND": "No account found for this email address.",
    "INVALID_PASSWORD": "Incorrect password.",
    "INVALID_LOGIN_CREDENTIALS": "Incorrect email or password.",
    "USER_DISABLED": "This account has been disabled. Please contact APEX.",
    "WEAK_PASSWORD : Password should be at least 6 characters":
        "Password must be at least 6 characters.",
    "TOO_MANY_ATTEMPTS_TRY_LATER":
        "Too many attempts. Please wait a few minutes and try again.",
    "INVALID_EMAIL": "That email address does not look valid.",
    "MISSING_PASSWORD": "Please enter a password.",
}


def _friendly(code: str) -> str:
    if code in _MESSAGES:
        return _MESSAGES[code]
    for key, text in _MESSAGES.items():
        if code.startswith(key):
            return text
    return "Sign-in failed (" + code + ")."


def _post_identity(method: str, payload: dict) -> dict:
    url = _IDENTITY + method + "?key=" + FIREBASE_API_KEY
    try:
        r = requests.post(url, json=payload, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise AuthError(
            "Could not reach the APEX servers. Check your internet connection.\n"
            + str(exc)
        ) from exc
    data = r.json() if r.content else {}
    if r.status_code >= 400:
        raise AuthError(_friendly(str(data.get("error", {}).get("message", "UNKNOWN"))))
    return data


# ─────────────────────────────────────────────────────────────────────────────
#  Firestore REST value encoding
# ─────────────────────────────────────────────────────────────────────────────

def _to_value(v):
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def _from_value(v):
    if not isinstance(v, dict):
        return v
    if "nullValue" in v:
        return None
    for k in ("stringValue", "booleanValue", "doubleValue", "timestampValue"):
        if k in v:
            return v[k]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "mapValue" in v:
        return {k: _from_value(x) for k, x in v["mapValue"].get("fields", {}).items()}
    if "arrayValue" in v:
        return [_from_value(x) for x in v["arrayValue"].get("values", [])]
    return None


def _doc_to_dict(doc: dict) -> dict:
    return {k: _from_value(v) for k, v in (doc.get("fields") or {}).items()}


def _firestore(method: str, path: str, id_token: str, **kw):
    url = _FIRESTORE + path
    headers = {"Authorization": "Bearer " + id_token}
    try:
        r = requests.request(method, url, headers=headers, timeout=_TIMEOUT, **kw)
    except requests.RequestException as exc:
        raise AuthError(
            "Could not reach the APEX servers. Check your internet connection.\n"
            + str(exc)
        ) from exc
    return r


# ─────────────────────────────────────────────────────────────────────────────
#  This machine
# ─────────────────────────────────────────────────────────────────────────────

def device_id() -> str:
    """
    Stable per-computer id, used for the one-computer-at-a-time rule.

    Prefers Windows' MachineGuid, which survives hardware changes and network
    adapters coming and going; falls back to the MAC-derived uuid.getnode().
    Hashed so nothing identifying leaves the machine.
    """
    raw = ""
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            )
            raw = winreg.QueryValueEx(key, "MachineGuid")[0]
            winreg.CloseKey(key)
        except Exception:
            raw = ""
    if not raw:
        import uuid

        raw = str(uuid.getnode())
    return hashlib.sha256(("apex-gcs:" + raw).encode("utf-8")).hexdigest()[:32]


def device_label() -> str:
    """Human-readable machine name, shown to the admin in the licence panel."""
    try:
        return "{} ({} {})".format(platform.node() or "Unknown PC",
                                   platform.system(), platform.release())
    except Exception:
        return "Unknown PC"


# ─────────────────────────────────────────────────────────────────────────────
#  Auth
# ─────────────────────────────────────────────────────────────────────────────

def _session(data: dict) -> dict:
    return {
        "idToken": data["idToken"],
        "refreshToken": data.get("refreshToken", ""),
        "uid": data.get("localId", ""),
        "email": (data.get("email") or "").lower(),
        "expiresAt": time.time() + int(data.get("expiresIn", 3600)) - 60,
    }


def sign_up(email: str, password: str) -> dict:
    return _session(_post_identity("signUp", {
        "email": email.strip(),
        "password": password,
        "returnSecureToken": True,
    }))


def sign_in(email: str, password: str) -> dict:
    return _session(_post_identity("signInWithPassword", {
        "email": email.strip(),
        "password": password,
        "returnSecureToken": True,
    }))


def send_verification_email(id_token: str) -> None:
    _post_identity("sendOobCode", {"requestType": "VERIFY_EMAIL", "idToken": id_token})


def send_password_reset(email: str) -> None:
    _post_identity("sendOobCode", {"requestType": "PASSWORD_RESET", "email": email.strip()})


def account_info(id_token: str) -> dict:
    data = _post_identity("lookup", {"idToken": id_token})
    users = data.get("users") or [{}]
    u = users[0]
    return {
        "uid": u.get("localId", ""),
        "email": (u.get("email") or "").lower(),
        "emailVerified": bool(u.get("emailVerified")),
    }


def refresh_session(session: dict) -> dict:
    """
    Swap a refresh token for a fresh ID token.

    Needed because the ID token carries the email_verified claim, and that
    claim does not change in an already-issued token when the user clicks the
    verification link — so after verifying we must mint a new one or
    firestore.rules will still see an unverified caller.
    """
    try:
        r = requests.post(
            _SECURETOKEN + "?key=" + FIREBASE_API_KEY,
            data={"grant_type": "refresh_token", "refresh_token": session["refreshToken"]},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise AuthError("Could not reach the APEX servers.\n" + str(exc)) from exc
    if r.status_code >= 400:
        raise AuthError("Your session expired. Please sign in again.")
    d = r.json()
    return {
        "idToken": d["id_token"],
        "refreshToken": d["refresh_token"],
        "uid": d.get("user_id", session.get("uid", "")),
        "email": session.get("email", ""),
        "expiresAt": time.time() + int(d.get("expires_in", 3600)) - 60,
    }


def ensure_fresh(session: dict) -> dict:
    if session.get("expiresAt", 0) <= time.time():
        return refresh_session(session)
    return session


# ─────────────────────────────────────────────────────────────────────────────
#  Licences
# ─────────────────────────────────────────────────────────────────────────────

def normalise_key(key: str) -> str:
    """Accept what people actually type: spaces, lower case, missing dashes."""
    cleaned = "".join(ch for ch in (key or "").upper() if ch.isalnum())
    if cleaned.startswith("APEX"):
        cleaned = cleaned[4:]
    if len(cleaned) != 20:
        return "APEX-" + "-".join(
            [cleaned[i:i + 5] for i in range(0, len(cleaned), 5)]
        ) if cleaned else ""
    return "APEX-" + "-".join([cleaned[i:i + 5] for i in range(0, 20, 5)])


def fetch_licence(id_token: str, key: str) -> dict | None:
    """
    The licence doc, or None if there is no such key / it is not this email's.

    firestore.rules refuses the read unless the doc's email matches the signed-in
    address, so "denied" and "no such key" are deliberately not distinguished
    here — telling them apart would let someone probe for valid keys.
    """
    path = "/licences/" + urllib.parse.quote(key, safe="")
    r = _firestore("GET", path, id_token)
    if r.status_code == 200:
        return _doc_to_dict(r.json())
    if r.status_code in (403, 404):
        return None
    raise AuthError("Could not check the licence key (error " + str(r.status_code) + ").")


def claim_licence(session: dict, key: str, first_time: bool) -> None:
    """
    Bind this licence to this account and this computer.

    Only the five fields firestore.rules permits are sent; everything that
    decides entitlement stays server-side.
    """
    fields = {
        "uid": _to_value(session["uid"]),
        "deviceId": _to_value(device_id()),
        "deviceLabel": _to_value(device_label()),
        "lastLoginAt": _to_value(_now_iso()),
    }
    mask = ["uid", "deviceId", "deviceLabel", "lastLoginAt"]
    if first_time:
        fields["activatedAt"] = _to_value(_now_iso())
        mask.append("activatedAt")

    path = "/licences/" + urllib.parse.quote(key, safe="")
    query = "?" + "&".join("updateMask.fieldPaths=" + f for f in mask)
    r = _firestore("PATCH", path + query, session["idToken"], json={"fields": fields})
    if r.status_code == 403:
        raise AuthError(
            "This licence could not be activated. It may have been cancelled, "
            "or it may already belong to another account."
        )
    if r.status_code >= 400:
        raise AuthError("Could not activate the licence (error " + str(r.status_code) + ").")


# The signed-in session, set once at login so the profile dialog and the main
# window can use it without threading a session object through every widget.
_CURRENT: dict = {}


def set_current_session(session: dict) -> None:
    _CURRENT.clear()
    _CURRENT.update(session or {})


def current_session() -> dict | None:
    return dict(_CURRENT) if _CURRENT.get("idToken") else None


def log_logout() -> None:
    """
    Stamp the sign-out time on the account. Best effort, never raises — this
    runs on the way out of the app and must not be able to block a quit.

    Replaces auth_backend.log_user_logout, which appended to an ActiveUsers
    Google Sheet. No sheet is touched any more.
    """
    session = current_session()
    if not session:
        return
    try:
        session = ensure_fresh(session)
        path = "/gcsUsers/" + urllib.parse.quote(session["uid"], safe="")
        _firestore("PATCH", path + "?updateMask.fieldPaths=lastLogoutAt",
                   session["idToken"],
                   json={"fields": {"lastLogoutAt": _to_value(_now_iso())}})
    except Exception:
        pass


def fetch_profile(session: dict) -> dict | None:
    """The gcsUsers doc for this account, or None if it has none yet."""
    path = "/gcsUsers/" + urllib.parse.quote(session["uid"], safe="")
    r = _firestore("GET", path, session["idToken"])
    if r.status_code == 200:
        return _doc_to_dict(r.json())
    if r.status_code in (403, 404):
        return None
    raise AuthError("Could not load your account (error " + str(r.status_code) + ").")


def save_profile(session: dict, profile: dict) -> None:
    """Store the sign-up details against the account. Best effort — never blocks sign-in."""
    fields = {k: _to_value(v) for k, v in profile.items()}
    fields["uid"] = _to_value(session["uid"])
    fields["email"] = _to_value(session["email"])
    fields["lastLoginAt"] = _to_value(_now_iso())
    path = "/gcsUsers/" + urllib.parse.quote(session["uid"], safe="")
    query = "?" + "&".join("updateMask.fieldPaths=" + f for f in fields)
    try:
        _firestore("PATCH", path + query, session["idToken"], json={"fields": fields})
    except AuthError:
        pass


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────────────────────────────────────────────────────────────────
#  The one call the UI actually needs
# ─────────────────────────────────────────────────────────────────────────────

def check_entitlement(session: dict, key: str) -> tuple[bool, str]:
    """
    Decide whether APEX GCS may open for this account, and bind the machine.

    Returns (ok, message). The message is shown to the user when ok is False.
    """
    licence = fetch_licence(session["idToken"], key)
    if licence is None:
        return False, ("That licence key does not match this email address.\n\n"
                       "Use the same address your order was placed with.")
    if licence.get("status") != "active":
        return False, ("This licence has been cancelled.\n\n"
                       "Please contact APEX to have it reactivated.")

    owner = licence.get("uid")
    if owner and owner != session["uid"]:
        return False, ("This licence is already registered to a different "
                       "account.\n\nContact APEX if you need it moved.")

    this_device = device_id()
    bound = licence.get("deviceId")
    if bound and bound != this_device:
        # Last writer wins, by design: taking the licence over here is what
        # signs the other computer out at ITS next launch.
        pass

    claim_licence(session, key, first_time=not licence.get("activatedAt"))
    return True, ""


def device_still_ours(session: dict, key: str) -> tuple[bool, str]:
    """
    Launch-time check. Someone signing in elsewhere moved `deviceId`, and this
    is where this machine finds out and bows out.
    """
    licence = fetch_licence(session["idToken"], key)
    if licence is None:
        return False, "Your licence could not be found. Please sign in again."
    if licence.get("status") != "active":
        return False, ("This licence has been cancelled.\n\n"
                       "Please contact APEX to have it reactivated.")
    if licence.get("deviceId") and licence["deviceId"] != device_id():
        return False, ("You signed in to APEX GCS on another computer.\n\n"
                       "Only one computer can use a licence at a time.")
    return True, ""


# ─────────────────────────────────────────────────────────────────────────────
#  Backup storage (Firebase Cloud Storage, via Cloud Functions)
# ─────────────────────────────────────────────────────────────────────────────
#
# Uploads are ticketed. The app asks gcsApi for a one-time signed URL, which it
# only gets if the file looks like APEX GCS output and the allowance has room,
# then PUTs the bytes straight to the bucket. Storage rules refuse client writes
# outright, so there is no way round the ticket, and a Cloud Function inspects
# what landed and removes anything that is not genuinely ours.
#
# Nothing here is a secret: the ID token is the customer's own, and the function
# URL is public. No credential ships in the build for this, which is the whole
# point of moving off the Drive token.

# The predictable alias Firebase publishes alongside the Cloud Run URL. Override
# with APEX_GCS_API if the project is ever deployed to another region.
GCS_API_URL = os.environ.get(
    "APEX_GCS_API",
    "https://us-central1-" + PROJECT_ID + ".cloudfunctions.net/gcsApi",
)

STORAGE_TIMEOUT = 120          # a slow link still has to finish a 2 MB image


def _gcs_api(session: dict, action: str, **payload):
    """One call to the backup API, as the signed-in customer."""
    session = ensure_fresh(session)
    body = dict(payload)
    body["action"] = action
    try:
        r = requests.post(
            GCS_API_URL,
            json=body,
            headers={"Authorization": "Bearer " + session["idToken"]},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise AuthError("Could not reach the APEX backup service.\n" + str(exc)) from exc
    data = r.json() if r.content else {}
    if r.status_code >= 400:
        err = AuthError(data.get("message") or ("Backup error " + str(r.status_code)))
        err.full = bool(data.get("full"))
        err.quota = data.get("quota")
        raise err
    return data


def backup_quota(session: dict) -> dict:
    """Used / allowed / free, in bytes, plus the file count."""
    return _gcs_api(session, "quota").get("quota") or {}


def list_backups(session: dict, limit: int = 200) -> list:
    """The customer's backed-up files, newest first, each with a 1-hour link."""
    return _gcs_api(session, "listBackups", limit=limit).get("files") or []


def delete_backups(session: dict, paths: list) -> dict:
    """
    Delete the given backed-up files and give the allowance back.

    `paths` are the object paths returned by list_backups. The server checks
    each one belongs to the caller, so a wrong path is refused rather than
    acted on. Returns {deleted, freedBytes, failed, quota}.
    """
    paths = [str(p) for p in (paths or []) if p]
    if not paths:
        return {"deleted": 0, "freedBytes": 0, "failed": 0}
    return _gcs_api(session, "deleteBackups", paths=paths)


def download_backup(session: dict, url: str, dest_path: str) -> bool:
    """
    Fetch one backed-up file to `dest_path`.

    `url` is the signed read link list_backups already handed out, so this
    needs no token of its own. Streams, because an xlsx can be 10 MB.
    """
    try:
        with requests.get(url, stream=True, timeout=STORAGE_TIMEOUT) as r:
            if r.status_code >= 400:
                print("[Backup] download refused: " + str(r.status_code))
                return False
            os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    if chunk:
                        f.write(chunk)
    except (requests.RequestException, OSError) as exc:
        print("[Backup] download failed for '" + dest_path + "': " + str(exc))
        return False
    return True


def upload_backup(session: dict, local_path: str, subfolder: str = "files") -> bool:
    """
    Back one file up. Returns True on success.

    Raises AuthError only for problems worth telling the user about, chiefly a
    full allowance; everything else is logged and returns False, because a
    backup failing must never interrupt a mission.
    """
    if not os.path.isfile(local_path):
        return False
    size = os.path.getsize(local_path)
    name = os.path.basename(local_path)
    ext = os.path.splitext(name)[1].lower()
    content_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".txt": "text/plain",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(ext, "application/octet-stream")

    ticket = _gcs_api(
        session, "uploadTicket",
        filename=name, subfolder=subfolder,
        sizeBytes=size, contentType=content_type,
    )

    try:
        with open(local_path, "rb") as f:
            r = requests.put(
                ticket["uploadUrl"], data=f,
                headers={"Content-Type": ticket["contentType"]},
                timeout=STORAGE_TIMEOUT,
            )
    except requests.RequestException as exc:
        print("[Backup] upload failed for '" + local_path + "': " + str(exc))
        return False
    if r.status_code >= 400:
        print("[Backup] bucket refused '" + local_path + "': " + str(r.status_code))
        return False
    return True


def human_bytes(n) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return ("{:.0f} {}" if unit in ("B", "KB") else "{:.2f} {}").format(v, unit)
        v /= 1024.0
    return "0 B"
