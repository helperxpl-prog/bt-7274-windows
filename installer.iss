; Inno Setup Script for BT-7274 (SRS Vanguard Titan AI Assistant)
; Packages standalone Windows executable distribution into a single Setup.exe

#define MyAppName "BT-7274"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "SRS Vanguard"
#define MyAppExeName "BT-7274.exe"
#define MyAppIcon "config\bt-7274.ico"

[Setup]
AppId={{C8E28135-6FD9-4B52-87F1-38A73E477F92}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/helperxpl-prog/bt-7274-Windows
AppSupportURL=https://github.com/helperxpl-prog/bt-7274-Windows
AppUpdatesURL=https://github.com/helperxpl-prog/bt-7274-Windows
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=Output
OutputBaseFilename=BT-7274-Setup
SetupIconFile={#MyAppIcon}
UninstallDisplayIcon={app}\config\bt-7274.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Standalone compiled distribution
Source: "dist\BT-7274\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Seed fresh api_keys.json from clean example template ONLY if user has no existing config
Source: "dist\BT-7274\config\api_keys.example.json"; DestDir: "{app}\config"; DestName: "api_keys.json"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\config\bt-7274.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\config\bt-7274.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up temporary files, logs, and empty directories on uninstall
Type: files; Name: "{app}\*.log"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: dirifempty; Name: "{app}\plugins"
Type: dirifempty; Name: "{app}\actions"
Type: dirifempty; Name: "{app}\config\certs"
Type: dirifempty; Name: "{app}\config"
Type: dirifempty; Name: "{app}\dashboard\static"
Type: dirifempty; Name: "{app}\dashboard"
Type: dirifempty; Name: "{app}\memory"
Type: dirifempty; Name: "{app}\core"
Type: dirifempty; Name: "{app}"
