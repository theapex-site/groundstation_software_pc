"""
widgets/auth_backend.py
───────────────────────
Google Drive backup for APEX GCS — captured images, detection results and
mission files, uploaded to the customer's folder with an offline retry queue.

Accounts, OTP, passwords and the Google Sheet they lived in are gone from
here; see widgets/firebase_backend.py and the note at the bottom of this file.
"""

import json
import os
import threading
import time

_HERE          = os.path.dirname(os.path.abspath(__file__))

_APPDATA    = os.environ.get("APPDATA", os.path.expanduser("~"))
_QUEUE_DIR  = os.path.join(_APPDATA, "ApexGCS")
_QUEUE_FILE = os.path.join(_QUEUE_DIR, "upload_queue.json")
os.makedirs(_QUEUE_DIR, exist_ok=True)

DRIVE_ROOT_FOLDER_ID = "1B-PerIYcBrNHy5rAIDqDObaV8zghoC6g"


# ══════════════════════════════════════════════════════════════════════════════
#  Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _get_drive():
    from googleapiclient.discovery import build
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    import pickle

    SCOPES     = ["https://www.googleapis.com/auth/drive"]
    token_file = os.path.join(_HERE, "drive_token.pickle")
    oauth_file = os.path.join(_HERE, "drive_oauth.json")

    creds = None
    if os.path.exists(token_file):
        with open(token_file, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow  = InstalledAppFlow.from_client_secrets_file(oauth_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, "wb") as f:
            pickle.dump(creds, f)

    return build("drive", "v3", credentials=creds)

# ── Offline queue helpers ──────────────────────────────────────────────────────

def _load_queue() -> list:
    try:
        with open(_QUEUE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def _save_queue(q: list):
    try:
        with open(_QUEUE_FILE, "w") as f:
            json.dump(q, f, indent=2)
    except Exception:
        pass

def _enqueue(email: str, local_path: str, subfolder: str):
    q = _load_queue()
    for item in q:
        if item["path"] == local_path and item["email"] == email:
            return
    q.append({"email": email, "path": local_path, "sub": subfolder})
    _save_queue(q)

# ══════════════════════════════════════════════════════════════════════════════
#  Drive folder management
# ══════════════════════════════════════════════════════════════════════════════

def _find_folder(service, name: str, parent_id: str):
    q = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
        f" and '{parent_id}' in parents and trashed=false"
    )
    try:
        results = service.files().list(q=q, fields="files(id)").execute()
        files   = results.get("files", [])
        if files:
            return files[0]["id"]
    except Exception:
        pass
    return None

def _create_folder(service, name: str, parent_id: str) -> str:
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    f = service.files().create(body=meta, fields="id").execute()
    return f["id"]

def _get_or_create_folder(service, name: str, parent_id: str) -> str:
    fid = _find_folder(service, name, parent_id)
    return fid if fid else _create_folder(service, name, parent_id)

def _share_folder(service, folder_id: str, email: str):
    """Share folder with user as reader — view only, no upload/edit."""
    try:
        service.permissions().create(
            fileId=folder_id,
            body={"type": "user", "role": "reader", "emailAddress": email},
            sendNotificationEmail=False,
            fields="id",
        ).execute()
    except Exception:
        pass

def create_user_drive_folder(email: str):
    try:
        svc         = _get_drive()
        user_folder = _get_or_create_folder(svc, email, DRIVE_ROOT_FOLDER_ID)
        _get_or_create_folder(svc, "received images",  user_folder)
        _get_or_create_folder(svc, "object detection", user_folder)
        _get_or_create_folder(svc, "files",            user_folder)
        _share_folder(svc, user_folder, email)
        return user_folder
    except Exception as e:
        print(f"[Drive] create_user_drive_folder failed: {e}")
        return None

def get_user_drive_url(email: str):
    try:
        svc = _get_drive()
        fid = _get_or_create_folder(svc, email, DRIVE_ROOT_FOLDER_ID)
        _get_or_create_folder(svc, "received images",  fid)
        _get_or_create_folder(svc, "object detection", fid)
        _get_or_create_folder(svc, "files",            fid)
        _share_folder(svc, fid, email)
        return f"https://drive.google.com/drive/folders/{fid}"
    except Exception:
        return None

# ══════════════════════════════════════════════════════════════════════════════
#  File upload  (with offline queue fallback)
# ══════════════════════════════════════════════════════════════════════════════

def _guess_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",  ".gif":  "image/gif",
        ".pdf": "application/pdf",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv":  "text/csv",  ".txt":  "text/plain",
        ".json": "application/json",
    }.get(ext, "application/octet-stream")

def upload_file(email: str, local_path: str, subfolder: str = "files") -> bool:
    if not os.path.isfile(local_path):
        return False
    if subfolder == "images":
        subfolder = "received images"
    try:
        from googleapiclient.http import MediaFileUpload
        svc      = _get_drive()
        user_fid = _get_or_create_folder(svc, email, DRIVE_ROOT_FOLDER_ID)
        sub_fid  = _get_or_create_folder(svc, subfolder, user_fid)
        filename  = os.path.basename(local_path)
        mime_type = _guess_mime(local_path)
        media     = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
        svc.files().create(
            body={"name": filename, "parents": [sub_fid]},
            media_body=media,
            fields="id",
        ).execute()
        return True
    except Exception as e:
        print(f"[Drive] upload failed, queuing '{local_path}': {e}")
        _enqueue(email, local_path, subfolder)
        return False

# ── Background retry worker ────────────────────────────────────────────────────

def _queue_worker():
    while True:
        time.sleep(60)
        q = _load_queue()
        if not q:
            continue
        still_pending = []
        for item in q:
            ok = upload_file(item["email"], item["path"], item.get("sub", "files"))
            if not ok:
                still_pending.append(item)
        _save_queue(still_pending)

_worker_thread = threading.Thread(target=_queue_worker, daemon=True)
_worker_thread.start()

# ══════════════════════════════════════════════════════════════════════════════
#  Welcome email
# ══════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
#  Accounts, OTP and passwords moved to widgets/firebase_backend.py
# ─────────────────────────────────────────────────────────────────────────────
# What used to live below this line: _send_welcome_email, send_otp, login,
# email_exists, signup, get_user_info, _get_active_users_sheet,
# log_user_login, log_user_logout and reset_password.
#
# All of it read and wrote a Google Sheet and sent mail through smtp.gmail.com
# using a sender address and app password compiled into this file — and so
# into every .exe ever shipped, where both were trivially extractable. Firebase
# Auth replaces the accounts, and Firebase sends the verification and
# password-reset mail itself, so no mail credential exists in the build.
#
# No Google Sheet is read or written by APEX GCS any more. Existing sheets were
# left exactly as they are; nothing was migrated out of them.
#
# This module now does one thing: Google Drive backup of captured images,
# detection results and mission files, which is unrelated to accounts.
