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
AppPublisherURL=https://github.com/hannulma/monitor-for-amateur-radio
AppSupportURL=https://github.com/hannulma/monitor-for-amateur-radio/issues
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

[InstallDelete]
; vanhat kirjastot pois ennen uusia (kansiomuotoinen paketti, versiosta 0.10.0b2 alkaen)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\MonitorForBrandMeister\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--no-browser"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent; Check: not IsUpdate
; Ohjelman sisäinen päivitys (/UPDATE): käynnistä uudelleen ilman uutta selainvälilehteä (vanha yhdistää itse)
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Parameters: "--no-browser --wait-for-exit"; Flags: nowait postinstall skipifsilent; Check: IsUpdate
; Vanhemmat versiot (0.9–0.10.0b1) ajoivat päivityksen hiljaisena
Filename: "{app}\{#AppExe}"; Parameters: "--no-browser --wait-for-exit"; Flags: nowait skipifnotsilent

[Code]
function IsUpdate: Boolean;
begin
  Result := Pos('/UPDATE', Uppercase(GetCmdTail)) > 0;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { Päivityksessä näytetään vain "Valmis asentamaan" ja lopetussivu }
  Result := IsUpdate and ((PageID = wpLicense) or (PageID = wpSelectDir) or (PageID = wpSelectTasks));
end;

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopApp"
