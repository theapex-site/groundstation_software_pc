"""
widgets/auth_backend.py
───────────────────────
Backup for APEX GCS — captured images, detection results and mission files,
uploaded to the customer's own folder in Firebase Cloud Storage with an offline
retry queue.

Accounts, OTP, passwords and the Google Sheet they lived in are gone from here;
see widgets/firebase_backend.py and the note at the bottom of this file.
"""

import json
import os
import threading
import time

_APPDATA    = os.environ.get("APPDATA", os.path.expanduser("~"))
_QUEUE_DIR  = os.path.join(_APPDATA, "ApexGCS")
_QUEUE_FILE = os.path.join(_QUEUE_DIR, "upload_queue.json")
os.makedirs(_QUEUE_DIR, exist_ok=True)


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

def _enqueue(local_path: str, subfolder: str):
    q = _load_queue()
    for item in q:
        if item.get("path") == local_path:
            return
    q.append({"path": local_path, "sub": subfolder})
    _save_queue(q)


# ══════════════════════════════════════════════════════════════════════════════
#  File upload  (with offline queue fallback)
# ══════════════════════════════════════════════════════════════════════════════

def upload_file(email: str = "", local_path: str = "", subfolder: str = "files") -> bool:
    """
    Back one file up to the signed-in customer's Storage folder.

    `email` is ignored and kept only so the existing call sites do not all have
    to change; the destination comes from whoever is signed in, which is the
    whole point of the move off a shared Drive folder.

    Never raises. A failure is queued and retried, because the file is already
    safe on this machine in APEX_GCS_Saved and nothing about a mission should
    stop because a backup did.
    """
    if not local_path or not os.path.isfile(local_path):
        return False
    if subfolder == "images":
        subfolder = "received images"

    import widgets.firebase_backend as fb
    session = fb.current_session()
    if not session:
        # Not signed in yet, or the session expired. Worth retrying later.
        _enqueue(local_path, subfolder)
        return False

    try:
        return fb.upload_backup(session, local_path, subfolder)
    except fb.AuthError as exc:
        if getattr(exc, "full", False):
            # Retrying a full allowance just fails again every minute, so this
            # one is reported and dropped rather than queued.
            print("[Backup] allowance full, not uploading '" + local_path + "'")
            return False
        print("[Backup] " + str(exc))
        _enqueue(local_path, subfolder)
        return False
    except Exception as exc:
        print("[Backup] unexpected error for '" + local_path + "': " + str(exc))
        _enqueue(local_path, subfolder)
        return False


# ── Background retry worker ────────────────────────────────────────────────────

def _queue_worker():
    while True:
        time.sleep(60)
        q = _load_queue()
        if not q:
            continue
        # upload_file re-queues its own failures, so the queue is cleared first
        # and rebuilt from what did not get through.
        _save_queue([])
        for item in q:
            path = item.get("path", "")
            if not os.path.isfile(path):
                continue            # deleted since; nothing left to back up
            upload_file("", path, item.get("sub", "files"))

_worker_thread = threading.Thread(target=_queue_worker, daemon=True)
_worker_thread.start()


# ─────────────────────────────────────────────────────────────────────────────
#  What used to be here
# ─────────────────────────────────────────────────────────────────────────────
# Accounts, OTP and passwords moved to widgets/firebase_backend.py:
# _send_welcome_email, send_otp, login, email_exists, signup, get_user_info,
# _get_active_users_sheet, log_user_login, log_user_logout, reset_password.
#
# All of it read and wrote a Google Sheet and sent mail through smtp.gmail.com
# using a sender address and app password compiled into this file — and so into
# every .exe ever shipped, where both were trivially extractable. Firebase Auth
# replaces the accounts, and Firebase sends the verification and password-reset
# mail itself, so no mail credential exists in the build.
#
# Google Drive backup has gone too: _get_drive, DRIVE_ROOT_FOLDER_ID,
# _find_folder, _create_folder, _get_or_create_folder, _share_folder,
# create_user_drive_folder, get_user_drive_url, _guess_mime and the
# APEX_BACKUP mode switch. Every upload landed in one APEX-owned Drive account
# through a token sitting in the build, which gave anyone who unpacked the exe
# full read and write access to every customer's backups. Firebase Cloud
# Storage replaces it: each customer writes only to their own folder, through a
# one-time signed URL the gcsApi function issues against their own login, so no
# credential ships at all. Files already in Drive were left exactly where they
# are; nothing was migrated out.
#
# No Google Sheet is read or written by APEX GCS any more.
