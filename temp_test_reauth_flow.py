import os, sys, requests
os.environ.setdefault("FIREBASE_AUTH_EMULATOR_HOST","127.0.0.1:9099")
os.environ.setdefault("FIRESTORE_EMULATOR_HOST","127.0.0.1:8080")
sys.path.insert(0, r"K:\APEX\apex_gcs_src")
from PyQt5 import QtCore, QtWidgets
import widgets.right_panel            # before QApplication
app = QtWidgets.QApplication([])
import widgets.dialogs as dlg
import widgets.firebase_backend as fb

EMAIL="tester@example.com"; PWD="testpass123"
OLD="APEX-TEST1-TEST1-TEST1-TEST1"; NEW="APEX-NEWK1-NEWK1-NEWK1-NEWK1"
AUTH=os.environ["FIREBASE_AUTH_EMULATOR_HOST"]
P=fb.PROJECT_ID
ok_n=fail_n=0
def check(label, cond, detail=""):
    global ok_n, fail_n
    if cond: ok_n+=1; print("  PASS  "+label)
    else: fail_n+=1; print("  FAIL  "+label+(("\n          "+str(detail)) if detail else ""))

def verify(email):
    r=requests.get(f"http://{AUTH}/emulator/v1/projects/{P}/oobCodes",timeout=10)
    c=[x for x in r.json().get("oobCodes",[]) if x.get("email","").lower()==email and x.get("requestType")=="VERIFY_EMAIL"]
    if c: requests.post(fb._IDENTITY+"update?key="+fb.FIREBASE_API_KEY,json={"oobCode":c[-1]["oobCode"]},timeout=10)

# a verified, activated customer
try: s=fb.sign_up(EMAIL,PWD)
except fb.AuthError: s=fb.sign_in(EMAIL,PWD)
fb.send_verification_email(s["idToken"]); verify(EMAIL)
s=fb.refresh_session(s); i=fb.account_info(s["idToken"]); s["uid"]=i["uid"]; s["email"]=i["email"]
print("activating with the original key...")
print("  ", fb.check_entitlement(s, OLD))
fb.save_profile(s, {"licenceKey": OLD})

# admin regenerates: old key dies, new one issued
import subprocess, json
NODE = r"""
const admin=require("firebase-admin"); admin.initializeApp({projectId:process.env.GCLOUD_PROJECT});
const db=admin.firestore();
(async()=>{
  const OLD=process.argv[1], NEW=process.argv[2];
  const old=(await db.collection("licences").doc(OLD).get()).data();
  await db.collection("licences").doc(OLD).update({status:"revoked",supersededBy:NEW,revokedReason:"regenerated"});
  await db.collection("licences").doc(NEW).set(Object.assign({},old,{key:NEW,status:"active",supersededBy:null,
    uid:null,activatedAt:null,deviceId:null,deviceLabel:null,revokedReason:null}));
  console.log("regenerated");
})();
"""
env=dict(os.environ, GCLOUD_PROJECT=P)
r=subprocess.run(["node","-e",NODE,OLD,NEW],cwd=r"K:\APEX\APEX_Website",env=env,capture_output=True,text=True)
assert "regenerated" in r.stdout, r.stdout+r.stderr
print("\nadmin regenerated the key.\n")

# relaunch
dlg._save_session(EMAIL, s["refreshToken"])
session=dlg._load_session()
check("saved session survives", bool(session.get("refreshToken")))

win=dlg.LoginWindow()
got={}
win._login_page.needs_reauth.connect(lambda e,ses,why: got.update(email=e,session=ses,why=why))
done={}
win._reauth_page.reauth_success.connect(lambda e: done.update(email=e))

def phase1():
    check("relaunch asks to re-authenticate, not 'signed out'", bool(got), "needs_reauth never fired")
    if not got: app.quit(); return
    check("stack switched to the licence screen", win._stack.currentIndex()==3, win._stack.currentIndex())
    check("email pre-filled", win._reauth_page.email_inp.text()==EMAIL, win._reauth_page.email_inp.text())
    check("reason explains why", "cancel" in got["why"].lower() or "licence" in got["why"].lower(), got["why"])

    # wrong key
    dlg.ThemedMessageBox.critical=staticmethod(lambda *a,**k: None)
    dlg.ThemedMessageBox.warning =staticmethod(lambda *a,**k: None)
    win._reauth_page.licence_inp.setText(OLD)
    win._reauth_page._do_auth()
    check("the dead key is refused", not done, done)

    # new key, typed messily
    win._reauth_page.licence_inp.setText("newk1 newk1 newk1 newk1")
    win._reauth_page._do_auth()
    check("the new key authenticates", done.get("email")==EMAIL, done)
    check("goes straight on to the COM port page", win._stack.currentIndex()==2, win._stack.currentIndex())
    d=fb.fetch_licence(fb.current_session()["idToken"], NEW) or {}
    check("new licence bound to this account", d.get("uid")==s["uid"])
    check("device recorded", d.get("deviceId")==fb.device_id())
    print(f"\n  {ok_n} passed, {fail_n} failed")
    app.quit()

QtCore.QTimer.singleShot(6000, phase1)
app.exec_()
sys.exit(1 if fail_n else 0)
