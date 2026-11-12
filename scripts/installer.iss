; Inno Setup 6 脚本：把 PyInstaller 的单目录产物（dist\Shotwright，含自带的 FFmpeg）打成 Windows 安装包。
; 由 scripts\build-installer.ps1 调用，用 /DAppVersion=x.y.z 传入版本号。
;
; 安装到用户目录、不需要管理员权限。配置、项目和日志放在 %LOCALAPPDATA%\Shotwright
; （第一次启动时由程序创建，见 shotwright\desktop.py），卸载和升级都不会删掉。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Shotwright"
#define AppPublisher "Shotwright"
#define AppExeName "Shotwright.exe"
#define AppURL "https://github.com/rushansishui/Shotwright"

[Setup]
AppId={{6F1B2C74-2A4E-4B57-9C1D-8E3A5D2F7B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
; 显示在「应用和功能」里的项目主页、支持和更新链接。
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases/latest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=Shotwright-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
; 系统语言能匹配到安装界面的语言时直接用，不再弹出选择语言的对话框。
ShowLanguageDialog=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=lowest
; 升级时如果程序还开着，先提示关闭，避免文件被占用。
CloseApplications=yes
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
#if FileExists(SourcePath + "..\resources\icon.ico")
SetupIconFile=..\resources\icon.ico
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; Inno Setup 6 不自带简体中文（只在非官方翻译里），所以把语言文件放在仓库里：
; scripts\ChineseSimplified.isl，来自 jrsoftware/issrc 的 is-6_7_0 标签
; （Files/Languages/Unofficial，适用于 Inno Setup 6.5.0+），已加 UTF-8 BOM。
#if Ver >= EncodeVer(6, 5, 0)
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
#else
  #pragma warning "Inno Setup 低于 6.5，安装界面不带简体中文；请升级到最新的 Inno Setup 6"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; 旧版本把配置模板放在程序目录里，现在打包在 _internal 中，清掉旧文件。
Type: files; Name: "{app}\config.example.toml"
; 升级前清空上一版的运行库，避免新旧版本的 DLL 混在一起。
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Shotwright\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{localappdata}\{#AppName}"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\{#AppName} Data"; Filename: "{localappdata}\{#AppName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; 不带参数启动：程序会自己启动服务并打开浏览器。
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
