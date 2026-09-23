; Inno Setup script for the MD installer - compiled by packaging\build.ps1 after PyInstaller.
; Installs per user (no administrator rights) into %LOCALAPPDATA%\Programs\MD.

#define AppName "MD"
#define AppVersion "1.0.0"
#define AppExe "MD.exe"

[Setup]
AppId={{6B0E2F4A-3C1D-4E8B-9A57-4D2C8E1F7A30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=MD
AppPublisherURL=https://github.com/MuhammadMuheb/Ai_Companion_Accessibility
AppSupportURL=https://github.com/MuhammadMuheb/Ai_Companion_Accessibility/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=MD-Setup-{#AppVersion}
SetupIconFile=md.ico
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
AppMutex=Local\MDCompanion.Instance
CloseApplications=yes
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} installer

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start MD when I sign in to Windows (recommended for the wake word)"; GroupDescription: "Startup:"
Name: "models"; Description: "Download the local AI models now (about 4.5 GB, needs Ollama)"; GroupDescription: "AI models:"

[Files]
Source: "dist\MD\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "setup-models.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--show"; Comment: "Open MD"
Name: "{group}\MD - Download AI models"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup-models.ps1"""; IconFilename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--show"; Tasks: desktopicon
; same file name the app's own "Start with Windows" switch uses, so the setting stays in sync
Name: "{userstartup}\MD Companion"; Filename: "{app}\{#AppExe}"; Tasks: autostart

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup-models.ps1"""; StatusMsg: "Downloading AI models..."; Tasks: models; Flags: waituntilterminated
Filename: "{app}\{#AppExe}"; Parameters: "--show"; Description: "Launch MD now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopMD"

[UninstallDelete]
Type: files; Name: "{userstartup}\MD Companion.lnk"

[Code]
function OllamaInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe'))
            or (FileSearch('ollama.exe', GetEnv('PATH')) <> '');
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpSelectTasks) and not OllamaInstalled() then
    MsgBox('MD runs its AI on your PC with Ollama, which is not installed yet.' + #13#10 + #13#10 +
           'You can finish installing MD now, then get Ollama from ollama.com/download and run ' +
           '"MD - Download AI models" from the Start menu.', mbInformation, MB_OK);
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\MD');
    if DirExists(DataDir) then
      if SuppressibleMsgBox('Also delete your MD memories, settings and voice prints?' + #13#10 + DataDir,
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
