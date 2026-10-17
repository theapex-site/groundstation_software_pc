; APEX Ground Control Station — Inno Setup Script

[Setup]
AppName=APEX Ground Control Station
AppVersion=1.2.8
AppPublisher=APEX
DefaultDirName={autopf}\APEX GCS
DefaultGroupName=APEX GCS
UninstallDisplayIcon={app}\APEX_GCS.exe
OutputDir=installer_output
OutputBaseFilename=APEX_GCS_Setup
SetupIconFile=icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
DisableWelcomePage=no
PrivilegesRequired=admin
; A silent in-app update (/VERYSILENT /SUPPRESSMSGBOXES) was observed
; failing in production (APEX_GCS_install.log, 2026-10-14): DeleteFile on
; signin_pixie.gif retried 4 times over ~4s against "file in use" (the
; previous APEX_GCS.exe process still exiting), then defaulted to Abort
; under SUPPRESSMSGBOXES and rolled the whole update back — silently, no
; error shown, leaving the old version running. dialogs.py's _on_restart
; already pads the handoff with a few seconds before invoking this
; installer, but that is a race against this process's own exit, not a
; guarantee. CloseApplications=force uses Windows Restart Manager to find
; whichever process actually holds each file about to be replaced and
; closes it outright before copying starts — the proper fix for this
; failure mode instead of a longer guess. RestartApplications=no: the
; [Run] step below (with skipifsilent) already relaunches the app itself;
; letting Setup *also* restart whatever it closed would double-launch it.
CloseApplications=force
RestartApplications=no

[Files]
Source: "dist\APEX_GCS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "redist\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\APEX Ground Control Station"; Filename: "{app}\APEX_GCS.exe"; IconFilename: "{app}\APEX_GCS.exe"
Name: "{group}\Uninstall APEX GCS"; Filename: "{uninstallexe}"
Name: "{autodesktop}\APEX GCS"; Filename: "{app}\APEX_GCS.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Installing Visual C++ Runtime..."; Flags: waituntilterminated
Filename: "pnputil"; Parameters: "/add-driver ""{app}\drivers\cp210x\silabser.inf"" /install"; StatusMsg: "Installing CP210x USB driver..."; Flags: runhidden waituntilterminated
; skipifsilent: the in-app updater (widgets/dialogs.py's UpdateDownloadDialog
; _on_restart) runs this installer with /VERYSILENT and separately shell-starts
; APEX_GCS.exe itself once the installer process exits. Without skipifsilent
; both this entry and that explicit start fired, launching two copies of the
; app after every in-app update. A manual double-click install is never
; silent, so the Finish-page "Launch APEX GCS" checkbox still works there.
Filename: "{app}\APEX_GCS.exe"; Description: "Launch APEX GCS"; Flags: nowait postinstall skipifsilent
