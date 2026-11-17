; Установщик Instant Replay (Inno Setup 6)
; Сборка: ISCC.exe /DAppVersion=1.0.0 installer\InstantReplay.iss

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Instant Replay"
#define AppExe "InstantReplay.exe"

[Setup]
AppId={{6E1B6C3A-6A2F-4C1B-9B7E-1D3A0F2C9A41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Instant Replay
DefaultDirName={autopf}\InstantReplay
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; По умолчанию ставится только для текущего пользователя, без прав администратора
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\Output
OutputBaseFilename=InstantReplay-Setup-{#AppVersion}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
AppMutex=InstantReplayAppMutex
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ru.Autostart=Запускать вместе с Windows (в трее)
en.Autostart=Start with Windows (in tray)
ru.Launch=Запустить Instant Replay
en.Launch=Launch Instant Replay
ru.Extra=Дополнительно:
en.Extra=Additional:

[Tasks]
Name: "autostart"; Description: "{cm:Autostart}"; GroupDescription: "{cm:Extra}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:Extra}"

[Files]
Source: "..\dist\InstantReplay\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "InstantReplay"; ValueData: """{app}\{#AppExe}"" --minimized"; \
  Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:Launch}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\Temp\InstantReplay"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // автозагрузку могли включить из самой программы — убираем в любом случае
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'InstantReplay');
end;
