; Inno Setup 6 脚本：把 PyInstaller 的产物打成一个 Windows 安装包。
; 由 scripts\build-installer.ps1 调用，用 /DAppVersion=x.y.z 传入版本号。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Shotwright"
#define AppPublisher "Shotwright"
#define AppExeName "Shotwright.exe"

[Setup]
AppId={{6F1B2C74-2A4E-4B57-9C1D-8E3A5D2F7B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=Shotwright-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 数据目录（项目、素材、渲染结果）在用户目录里，不随卸载删除。
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; PyInstaller 的单目录产物。
Source: "..\dist\Shotwright\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\config.example.toml"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "serve --open"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清理程序目录里的缓存，用户数据在 %LOCALAPPDATA% 下保留。
Type: filesandordirs; Name: "{app}\web"
