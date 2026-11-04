#ifndef AppVersion
  #define AppVersion "1.4.0"
#endif
#ifndef BundleDir
  #define BundleDir SourcePath + "..\dist\release\AiwuJiyu-" + AppVersion + "-Windows-x64"
#endif
#ifndef ReleaseDir
  #define ReleaseDir SourcePath + "..\dist\release"
#endif

[Setup]
AppId={{81CC2260-61B3-49BB-9BE4-4F7642A5C129}
AppName=爱屋及鱼
AppVersion={#AppVersion}
AppVerName=爱屋及鱼 {#AppVersion}
AppPublisher=myflww
AppPublisherURL=https://github.com/myflww
AppSupportURL=https://github.com/myflww/aiwu-jiyu/issues
DefaultDirName={localappdata}\Programs\AiwuJiyu
DefaultGroupName=爱屋及鱼
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir={#ReleaseDir}
OutputBaseFilename=AiwuJiyu-Setup-{#AppVersion}-Windows-x64
SetupIconFile={#BundleDir}\app-icon.ico
UninstallDisplayIcon={app}\app-icon.ico
UninstallDisplayName=爱屋及鱼
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
LicenseFile={#BundleDir}\LICENSE
VersionInfoCompany=myflww
VersionInfoVersion={#AppVersion}
VersionInfoDescription=爱屋及鱼桌面安装程序
VersionInfoProductName=爱屋及鱼

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#else
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "在桌面放一只小鱼"; GroupDescription: "快捷方式："

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Excludes: "install.ps1,安装到桌面.cmd"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\爱屋及鱼"; Filename: "{app}\爱屋及鱼.exe"; WorkingDir: "{app}"; IconFilename: "{app}\app-icon.ico"; Tasks: desktopicon
Name: "{group}\爱屋及鱼"; Filename: "{app}\爱屋及鱼.exe"; WorkingDir: "{app}"; IconFilename: "{app}\app-icon.ico"
Name: "{group}\卸载爱屋及鱼"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\爱屋及鱼.exe"; Description: "打开爱屋及鱼"; Flags: nowait postinstall skipifsilent

; The application's data is in the separate Dafeiyu data directory. There is
; deliberately no UninstallDelete directive for runtime/config/memory files.
