; Inno Setup script for the Lyra installer - compiled by packaging\build.ps1 after PyInstaller.
; Installs per user (no administrator rights) into %LOCALAPPDATA%\Programs\Lyra.

#define AppName "Lyra"
#define AppVersion "1.1.0"
#define AppExe "Lyra.exe"
; the earlier release (installed under another name) is removed first; its data is kept and moved
#define LegacyAppId "{6B0E2F4A-3C1D-4E8B-9A57-4D2C8E1F7A30}"

[Setup]
AppId={{1AF89CA7-B3DC-43D9-BB90-A157C0F81DF5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Lyra
AppPublisherURL=https://github.com/MuhammadMuheb/Ai_Companion_Accessibility
AppSupportURL=https://github.com/MuhammadMuheb/Ai_Companion_Accessibility/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=Lyra-Setup-{#AppVersion}
SetupIconFile=lyra.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} - voice-first AI assistant
WizardStyle=modern
WizardImageFile=wizard-side.bmp
WizardSmallImageFile=wizard-small.bmp
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
AppMutex=Local\LyraAssistant.Instance
CloseApplications=yes
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} installer

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start Lyra when I sign in to Windows (recommended for the wake word)"; GroupDescription: "Startup:"
Name: "models"; Description: "Download the local AI models now (about 4.5 GB, needs Ollama)"; GroupDescription: "AI models:"

[Files]
Source: "dist\Lyra\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "setup-models.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--show"; Comment: "Open Lyra"
Name: "{group}\Lyra - Download AI models"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup-models.ps1"""; IconFilename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--show"; Tasks: desktopicon
; same file name the app's own "Start with Windows" switch uses, so the setting stays in sync
Name: "{userstartup}\Lyra"; Filename: "{app}\{#AppExe}"; Tasks: autostart

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup-models.ps1"""; StatusMsg: "Downloading AI models..."; Tasks: models; Flags: waituntilterminated
Filename: "{app}\{#AppExe}"; Parameters: "--show"; Description: "Launch Lyra now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopLyra"

[UninstallDelete]
Type: files; Name: "{userstartup}\Lyra.lnk"

[Code]
function OllamaInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe'))
            or (FileSearch('ollama.exe', GetEnv('PATH')) <> '');
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpSelectTasks) and not OllamaInstalled() then
    MsgBox('Lyra runs its AI on your PC with Ollama, which is not installed yet.' + #13#10 + #13#10 +
           'You can finish installing Lyra now, then get Ollama from ollama.com/download and run ' +
           '"Lyra - Download AI models" from the Start menu.', mbInformation, MB_OK);
end;

{ The earlier release used another name and AppId. Uninstall it silently (its data folder is kept:
  its "delete my data?" question defaults to No when suppressed) so only Lyra remains. Lyra moves
  that data folder to %LOCALAPPDATA%\Lyra on first start. }
function LegacyUninstaller(): String;
var
  Key: String;
begin
  Result := '';
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#LegacyAppId}_is1';
  if not RegQueryStringValue(HKCU, Key, 'UninstallString', Result) then
    RegQueryStringValue(HKLM, Key, 'UninstallString', Result);
  Result := RemoveQuotes(Result);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Uninstaller: String;
  Code: Integer;
begin
  Result := '';
  Uninstaller := LegacyUninstaller();
  if (Uninstaller <> '') and FileExists(Uninstaller) then
  begin
    Exec('taskkill.exe', '/IM MD.exe /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Exec(Uninstaller, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\Lyra');
    if DirExists(DataDir) then
      if SuppressibleMsgBox('Also delete your Lyra memories, settings, voices and voice prints?' + #13#10 + DataDir,
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
