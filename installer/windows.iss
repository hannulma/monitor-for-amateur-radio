; Inno Setup -skripti: Monitor for BrandMeister -asennusohjelma Windowsille.
; Käännetään GitHub Actionsissa:  iscc /DAppVersion=0.8.0b1 installer\windows.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Monitor for BrandMeister"
#define AppExe "MonitorForBrandMeister.exe"

[Setup]
AppId={{6B1C8E2A-4F7D-4C1E-9B3A-2D5E8F0A7C41}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=hannulma
AppPublisherURL=https://github.com/hannulma/monitor-for-brandmeister
AppSupportURL=https://github.com/hannulma/monitor-for-brandmeister/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=MonitorForBrandMeister-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\{#AppExe}
CloseApplications=force
RestartApplications=no

[Languages]
Name: "finnish"; MessagesFile: "compiler:Languages\Finnish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Käynnistä Windowsin käynnistyessä / Start with Windows"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--no-browser"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Ohjelman sisäinen päivitys ajaa asennuksen hiljaisena -> käynnistä ohjelma uudelleen (selainvälilehti yhdistää itse)
Filename: "{app}\{#AppExe}"; Parameters: "--no-browser --wait-for-exit"; Flags: nowait skipifnotsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopApp"
