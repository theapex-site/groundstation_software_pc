"""
temp_test_licence_flow.py  —  throwaway, delete with the other temp_* files.

End-to-end test of APEX GCS licensing against the Firebase Emulator Suite.
Production is never contacted: the script refuses to start unless the emulator
environment variables are set, and it drives widgets/firebase_backend.py — the
real shipping code — rather than a copy of it.

Run (from K:\\APEX\\APEX_Website):
    npx firebase emulators:start --project theapex-88dab

Then (from K:\\APEX\\apex_gcs_src), in a second terminal:
    set FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
    set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
    node ../APEX_Website/firebase/seed-emulator.js
    python temp_test_licence_flow.py

Covers the happy path, every rejection a customer can hit, the one-computer
rule, and four things a tampered build might try.
"""

import os
import sys

if not (os.environ.get("FIREBASE_AUTH_EMULATOR_HOST")
        and os.environ.get("FIRESTORE_EMULATOR_HOST")):
    sys.exit(
        "\nRefusing to run against production.\n\n"
        "Set both of these first, so every call goes to the local emulator:\n"
        "    set FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099\n"
        "    set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080\n"
    )

import time
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import widgets.firebase_backend as fb

AUTH = os.environ["FIREBASE_AUTH_EMULATOR_HOST"]
PROJECT = fb.PROJECT_ID
TEST_EMAIL = os.environ.get("APEX_TEST_EMAIL", "tester@example.com")
OTHER_EMAIL = "someone-else@example.com"
ACTIVE_KEY = "APEX-TEST1-TEST1-TEST1-TEST1"
CANCELLED_KEY = "APEX-TEST2-TEST2-TEST2-TEST2"
PASSWORD = "testpass123"

_passed, _failed = 0, 0


def check(label, condition, detail=""):
    global _passed, _failed
    if condition:
        _passed += 1
        print("  PASS  " + label)
    else:
        _failed += 1
        print("  FAIL  " + label + ("\n          " + detail if detail else ""))


def verify_email_via_emulator(email):
    """The emulator exposes the verification links it would have emailed."""
    r = requests.get(
        "http://" + AUTH + "/emulator/v1/projects/" + PROJECT + "/oobCodes", timeout=10)
    codes = [c for c in r.json().get("oobCodes", [])
             if c.get("email", "").lower() == email.lower()
             and c.get("requestType") == "VERIFY_EMAIL"]
    if not codes:
        return False
    requests.post(fb._IDENTITY + "update?key=" + fb.FIREBASE_API_KEY,
                  json={"oobCode": codes[-1]["oobCode"]}, timeout=10)
    return True


def fresh_user(email):
    """A verified account, as if the customer had clicked the emailed link."""
    try:
        s = fb.sign_up(email, PASSWORD)
    except fb.AuthError:
        s = fb.sign_in(email, PASSWORD)
    fb.send_verification_email(s["idToken"])
    verify_email_via_emulator(email)
    s = fb.refresh_session(s)
    info = fb.account_info(s["idToken"])
    s["uid"] = info["uid"]
    s["email"] = info["email"]
    return s


def raw_patch(session, key, fields, mask):
    """Bypass firebase_backend and hit Firestore directly — what a tampered
    build would do. Rules, not our Python, must be what refuses these."""
    import urllib.parse
    url = (fb._FIRESTORE + "/licences/" + urllib.parse.quote(key, safe="")
           + "?" + "&".join("updateMask.fieldPaths=" + f for f in mask))
    return requests.patch(url, headers={"Authorization": "Bearer " + session["idToken"]},
                          json={"fields": fields}, timeout=10)


print("\nAPEX GCS licensing — emulator end-to-end")
print("=" * 60)
print("auth     : " + AUTH)
print("firestore: " + os.environ["FIRESTORE_EMULATOR_HOST"])
print("emulator mode detected by the app: " + str(fb.USING_EMULATOR))
print()

# ── 1. sign-up rejections ────────────────────────────────────────────────────
print("1. Sign-up checks")
buyer = fresh_user(TEST_EMAIL)
check("account created and email verified",
      fb.account_info(buyer["idToken"])["emailVerified"])

check("a key that does not exist is refused",
      fb.fetch_licence(buyer["idToken"], "APEX-NOPE1-NOPE1-NOPE1-NOPE1") is None)

lic = fb.fetch_licence(buyer["idToken"], ACTIVE_KEY)
check("the buyer can read their own licence", lic is not None)

cancelled = fb.fetch_licence(buyer["idToken"], CANCELLED_KEY)
check("a cancelled licence reads back as revoked",
      cancelled is not None and cancelled.get("status") == "revoked",
      str(cancelled))

ok, msg = fb.check_entitlement(buyer, CANCELLED_KEY)
check("activating a cancelled licence is refused", not ok, msg)

# ── 2. happy path ────────────────────────────────────────────────────────────
print("\n2. Activation")
ok, msg = fb.check_entitlement(buyer, ACTIVE_KEY)
check("matching email + active key activates", ok, msg)

lic = fb.fetch_licence(buyer["idToken"], ACTIVE_KEY)
check("licence is now bound to this account", lic.get("uid") == buyer["uid"])
check("activatedAt recorded", bool(lic.get("activatedAt")))
check("this computer recorded", lic.get("deviceId") == fb.device_id())
check("device label stored for the admin panel", bool(lic.get("deviceLabel")),
      str(lic.get("deviceLabel")))

ok, msg = fb.device_still_ours(buyer, ACTIVE_KEY)
check("relaunch on the same computer still works", ok, msg)

# ── 3. somebody else's licence ───────────────────────────────────────────────
print("\n3. Another customer cannot touch it")
other = fresh_user(OTHER_EMAIL)
check("a different account cannot even read the licence",
      fb.fetch_licence(other["idToken"], ACTIVE_KEY) is None)
ok, msg = fb.check_entitlement(other, ACTIVE_KEY)
check("a different account cannot activate it", not ok, msg)

# ── 4. one computer at a time ────────────────────────────────────────────────
print("\n4. One computer at a time")
real_device = fb.device_id
fb.device_id = lambda: "f" * 32           # pretend to be a second machine
try:
    ok, msg = fb.check_entitlement(buyer, ACTIVE_KEY)
    check("the same buyer can take the licence to a new computer", ok, msg)
    lic = fb.fetch_licence(buyer["idToken"], ACTIVE_KEY)
    check("deviceId moved to the new computer", lic.get("deviceId") == "f" * 32)
finally:
    fb.device_id = real_device

ok, msg = fb.device_still_ours(buyer, ACTIVE_KEY)
check("the first computer is signed out at next launch", not ok, msg)
check("and is told why", "another computer" in msg.lower(), msg)

fb.check_entitlement(buyer, ACTIVE_KEY)   # take it back for the next tests

# ── 5. what a tampered build would try ───────────────────────────────────────
print("\n5. Security rules, bypassing our own code")
# Patching status to the value it already holds changes nothing and is
# correctly allowed — affectedKeys() comes back empty. The two writes that
# would actually matter are the ones below.
r = raw_patch(buyer, CANCELLED_KEY, {"status": fb._to_value("active")}, ["status"])
check("cannot un-revoke a cancelled licence", r.status_code == 403,
      "got HTTP " + str(r.status_code))

r = raw_patch(buyer, ACTIVE_KEY, {"status": fb._to_value("revoked")}, ["status"])
check("cannot change the status of an active licence", r.status_code == 403,
      "got HTTP " + str(r.status_code))

r = raw_patch(buyer, ACTIVE_KEY,
              {"email": fb._to_value("attacker@example.com")}, ["email"])
check("cannot move a licence to another email", r.status_code == 403,
      "got HTTP " + str(r.status_code))

r = raw_patch(buyer, ACTIVE_KEY,
              {"requestId": fb._to_value("SLS-SOMETHING-ELSE")}, ["requestId"])
check("cannot repoint it at another order", r.status_code == 403,
      "got HTTP " + str(r.status_code))

r = raw_patch(other, ACTIVE_KEY,
              {"uid": fb._to_value(other["uid"])}, ["uid"])
check("another account cannot steal it", r.status_code == 403,
      "got HTTP " + str(r.status_code))

r = raw_patch(buyer, CANCELLED_KEY, {"uid": fb._to_value(buyer["uid"])}, ["uid"])
check("cannot claim a cancelled licence", r.status_code == 403,
      "got HTTP " + str(r.status_code))

unverified_email = "unverified@example.com"
try:
    unver = fb.sign_up(unverified_email, PASSWORD)
except fb.AuthError:
    unver = fb.sign_in(unverified_email, PASSWORD)
r = raw_patch(unver, ACTIVE_KEY, {"uid": fb._to_value(unver["uid"])}, ["uid"])
check("an unverified email cannot claim anything", r.status_code == 403,
      "got HTTP " + str(r.status_code))

# ── 6. profile ───────────────────────────────────────────────────────────────
print("\n6. Profile")
fb.set_current_session(buyer)
fb.save_profile(buyer, {"name": "Test Buyer", "institution": "Test College",
                        "city": "Bengaluru", "state": "Karnataka",
                        "country": "India", "pincode": "560001",
                        "licenceKey": ACTIVE_KEY})
prof = fb.fetch_profile(buyer)
check("profile saved and read back", prof is not None and prof.get("name") == "Test Buyer",
      str(prof))
check("licence key stored on the profile for re-login",
      (prof or {}).get("licenceKey") == ACTIVE_KEY)
check("another account cannot read this profile",
      fb.fetch_profile(other) is None or fb.fetch_profile(other).get("name") != "Test Buyer")

print("\n" + "=" * 60)
print("  {} passed, {} failed".format(_passed, _failed))
print("=" * 60 + "\n")
sys.exit(1 if _failed else 0)
