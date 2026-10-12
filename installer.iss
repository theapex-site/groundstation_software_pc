; APEX Ground Control Station — Inno Setup Script

[Setup]
AppName=APEX Ground Control Station
AppVersion=1.2.5
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
; No skipifsilent: the in-app updater runs this installer with /VERYSILENT,
; and that silent run still needs to relaunch the app once it's done — a
; manual double-click install still shows this as the usual Finish-page
; "Launch APEX GCS" checkbox either way.
Filename: "{app}\APEX_GCS.exe"; Description: "Launch APEX GCS"; Flags: nowait postinstall
