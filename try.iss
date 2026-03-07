#define MyAppName "Cassettify"
#define MyAppVersion "1.1"
#define MyAppPublisher "Juan Martin Moreno Ratto"
#define MyAppExeName "Cassettify.exe"

[Setup]
AppId={{64239108-35DB-4E57-A3A2-9D338A524A8D}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppVerName={#MyAppName} {#MyAppVersion}

DefaultDirName={autopf}\{#MyAppName}
DisableDirPage=yes

UninstallDisplayIcon={app}\{#MyAppExeName}

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir={#SourcePath}\dist_installer
OutputBaseFilename=CassettifySetup_new

SetupIconFile="{#SourcePath}\icono.ico"

SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourcePath}\dist_new\Cassettify\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autoprograms}\{#MyAppName}\Actualizar dependencias"; Filename: "{cmd}"; Parameters: "/C pwsh -NoProfile -ExecutionPolicy Bypass -File ""{app}\_internal\update_deps.ps1"""; WorkingDir: "{app}\_internal"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent


