"""
widgets/auth_backend.py
"""

import json
import os
import random
import smtplib
import threading
import time
from email.mime.text import MIMEText

import gspread
from oauth2client.service_account import ServiceAccountCredentials

_HERE          = os.path.dirname(os.path.abspath(__file__))
_CREDS         = os.path.join(_HERE, "credentials.json")

_APPDATA    = os.environ.get("APPDATA", os.path.expanduser("~"))
_QUEUE_DIR  = os.path.join(_APPDATA, "ApexGCS")
_QUEUE_FILE = os.path.join(_QUEUE_DIR, "upload_queue.json")
os.makedirs(_QUEUE_DIR, exist_ok=True)

SENDER_EMAIL = "apexcubesat@gmail.com"
APP_PASSWORD  = "eesy ncqq hnah xhxx"

SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1y2gFuAA_1V8xS_APGXz7AOeayK0Uq_70G3VBjWXjnrk/edit"
)

DRIVE_ROOT_FOLDER_ID = "1B-PerIYcBrNHy5rAIDqDObaV8zghoC6g"

_scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


# ══════════════════════════════════════════════════════════════════════════════
#  Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _get_creds():
    return ServiceAccountCredentials.from_json_keyfile_name(_CREDS, _scope)

def _get_sheet():
    creds  = _get_creds()
    client = gspread.authorize(creds)
    return client.open_by_url(SHEET_URL).sheet1

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

def _send_welcome_email(name: str, email: str):
    try:
        url = get_user_drive_url(email)

        backup_button = (
            f'<a href="{url}" style="display:inline-block;padding:14px 32px;'
            f'background:#1a73e8;color:#ffffff;text-decoration:none;'
            f'border-radius:6px;font-size:16px;font-weight:bold;'
            f'letter-spacing:0.5px;">&#128193; Open My Backup Folder</a>'
        ) if url else (
            '<p style="color:#888;">Your backup folder is being set up. '
            'Open the app and click <b>Open Backup</b> to access it.</p>'
        )

        plain_url = url if url else "Not available yet — open the app and click Open Backup."

        html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0d0d1a;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0d0d1a;padding:40px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#12122a;border-radius:14px;overflow:hidden;
                  box-shadow:0 4px 32px rgba(0,0,0,0.5);max-width:600px;width:100%;">

      <!-- HEADER -->
      <tr>
        <td style="background:linear-gradient(135deg,#0d1b4b,#0a2a6e);
                   padding:32px 40px;text-align:center;">
          <img src="https://drive.google.com/uc?export=view&id=19Wpa56nDuPn7ctl8BT27VX8pHX5Qrv3w"
               width="160" height="160"
               style="border-radius:50%;border:3px solid #00e5ff;
                      box-shadow:0 0 20px rgba(0,229,255,0.4);margin-bottom:16px;"
               alt="APEX Logo"/>
          <h1 style="margin:0;color:#00e5ff;font-size:26px;letter-spacing:3px;
                     text-transform:uppercase;">APEX GCS</h1>
          <p style="margin:6px 0 0;color:#90caf9;font-size:13px;letter-spacing:2px;">
            GROUND CONTROL STATION
          </p>
        </td>
      </tr>

      <!-- BODY -->
      <tr>
        <td style="padding:40px 40px 32px;">

          <h2 style="color:#ffffff;margin:0 0 6px;font-size:22px;">
            Welcome aboard, {name}! &#127775;
          </h2>
          <p style="color:#90caf9;margin:0 0 24px;font-size:14px;">
            Your APEX GCS account has been created successfully.
          </p>

          <p style="color:#b0bec5;font-size:15px;line-height:1.8;margin:0 0 28px;">
            Your personal backup folder is live on Google Drive.
            Everything you capture — satellite images, object detection results,
            and mission files — will be automatically saved there in real time.
          </p>

          <!-- Folder structure box -->
          <table width="100%" cellpadding="16" cellspacing="0"
                 style="background:#0d1b3e;border-radius:10px;
                        border:1px solid #1e3a6e;margin-bottom:32px;">
            <tr>
              <td style="color:#00e5ff;font-size:13px;font-family:'Courier New',monospace;
                         line-height:2.2;">
                &#128193; Your Backup Folder/<br>
                &nbsp;&nbsp;&nbsp;&nbsp;&#128247; received images/<br>
                &nbsp;&nbsp;&nbsp;&nbsp;&#128269; object detection/<br>
                &nbsp;&nbsp;&nbsp;&nbsp;&#128196; files/
              </td>
            </tr>
          </table>

          <!-- CTA Button -->
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="margin-bottom:28px;">
            <tr><td align="center">{backup_button}</td></tr>
          </table>

          <!-- Plain link fallback -->
          <p style="color:#546e7a;font-size:12px;text-align:center;
                    margin:0 0 28px;word-break:break-all;">
            Or copy this link:<br>
            <a href="{url}" style="color:#90caf9;">{plain_url}</a>
          </p>

          <table width="100%" cellpadding="12" cellspacing="0"
                 style="background:#0a1a0a;border-radius:8px;
                        border-left:4px solid #00e5ff;margin-bottom:28px;">
            <tr>
              <td style="color:#80cbc4;font-size:13px;line-height:1.7;">
                &#128274; <b style="color:#ffffff;">View only access</b> —
                you can see all your uploaded files from any device.<br>
                Files are uploaded automatically by the APEX GCS app.
              </td>
            </tr>
          </table>

          <hr style="border:none;border-top:1px solid #1e2a4a;margin:0 0 24px;">

          <p style="color:#455a64;font-size:12px;text-align:center;margin:0;">
            This email was sent by the APEX CubeSat Team.<br>
            If you did not create this account, please ignore this email.
          </p>

        </td>
      </tr>

      <!-- FOOTER -->
      <tr>
        <td style="background:#080814;padding:18px;text-align:center;
                   border-top:1px solid #1a1a3e;">
          <p style="margin:0;color:#263238;font-size:11px;">
            &copy; 2025 APEX CubeSat &middot; Ground Control Station
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""

        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText as _MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Welcome to APEX GCS — Your Backup Folder is Ready"
        msg["From"]    = f"APEX CubeSat <{SENDER_EMAIL}>"
        msg["To"]      = email
        msg.attach(_MIMEText(html, "html"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.sendmail(SENDER_EMAIL, email, msg.as_string())
        server.quit()
    except Exception as e:
        print(f"[Email] welcome email failed: {e}")

# ══════════════════════════════════════════════════════════════════════════════
#  Public API — Auth
# ══════════════════════════════════════════════════════════════════════════════

def send_otp(receiver_email: str):
    otp = str(random.randint(100000, 999999))
    msg = MIMEText(f"Your APEX GCS OTP is: {otp}")
    msg["Subject"] = "APEX GCS Verification OTP"
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = receiver_email
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.sendmail(SENDER_EMAIL, receiver_email, msg.as_string())
        server.quit()
        return otp
    except Exception:
        return None

def login(email: str, password: str) -> bool:
    try:
        sheet = _get_sheet()
        data  = sheet.get_all_records()
        for row in data:
            if str(row.get("Email", "")).strip() == email and \
               str(row.get("Password", "")).strip() == password:
                return True
        return False
    except Exception:
        return False

def email_exists(email: str) -> bool:
    try:
        sheet = _get_sheet()
        data  = sheet.get_all_records()
        return any(str(row.get("Email", "")).strip() == email for row in data)
    except Exception:
        return False

def signup(
    name: str,
    email: str,
    password: str,
    institution: str,
    city: str,
    state: str,
    country: str,
    pincode: str,
) -> bool:
    try:
        sheet = _get_sheet()
        sheet.append_row([name, email, password, institution,
                          city, state, country, pincode])
        threading.Thread(
            target=_send_welcome_email, args=(name, email), daemon=True
        ).start()
        return True
    except Exception:
        return False

def get_user_info(email: str):
    try:
        sheet = _get_sheet()
        data  = sheet.get_all_records()
        for row in data:
            if str(row.get("Email", "")).strip() == email:
                return dict(row)
        return None
    except Exception:
        return None

def _get_active_users_sheet():
    creds  = _get_creds()
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_url(SHEET_URL)
    try:
        ws = spreadsheet.worksheet("ActiveUsers")
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="ActiveUsers", rows=1000, cols=6)
        ws.append_row(
            ["Email", "Status", "Login Time", "Logout Time",
             "Session Duration (mins)", "Notes"],
            value_input_option="RAW"
        )
    return ws

def log_user_login(email: str) -> bool:
    import datetime
    try:
        ws  = _get_active_users_sheet()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ws.append_row([email, "ACTIVE", now, "", "", "Login"],
                      value_input_option="RAW")
        return True
    except Exception as e:
        print(f"[ActiveUsers] log_user_login failed: {e}")
        return False

def log_user_logout(email: str) -> bool:
    import datetime
    try:
        ws      = _get_active_users_sheet()
        data    = ws.get_all_values()
        now     = datetime.datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        for i in range(len(data) - 1, 0, -1):
            row = data[i]
            if len(row) >= 3 and row[0].strip() == email and row[1].strip() == "ACTIVE":
                try:
                    login_dt = datetime.datetime.strptime(row[2].strip(), "%Y-%m-%d %H:%M:%S")
                    duration = round((now - login_dt).total_seconds() / 60, 2)
                except Exception:
                    duration = ""
                sheet_row = i + 1
                ws.update_cell(sheet_row, 2, "INACTIVE")
                ws.update_cell(sheet_row, 4, now_str)
                ws.update_cell(sheet_row, 5, duration)
                ws.update_cell(sheet_row, 6, "Logout")
                return True

        ws.append_row([email, "INACTIVE", "", now_str, "", "Logout (no open session)"],
                      value_input_option="RAW")
        return True
    except Exception as e:
        print(f"[ActiveUsers] log_user_logout failed: {e}")
        return False

def reset_password(email: str, new_password: str) -> bool:
    try:
        sheet = _get_sheet()
        data  = sheet.get_all_records()
        for i, row in enumerate(data, start=2):
            if str(row.get("Email", "")).strip() == email:
                sheet.update_cell(i, 3, new_password)
                return True
        return False
    except Exception:
        return False