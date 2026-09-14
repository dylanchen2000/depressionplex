; DEPRESSION-PLEX Inno Setup 安装脚本
; 版本号从命令行 /DAppVersion= 传入，不许手写（守卫 4 验证）

#ifndef AppVersion
  #error "AppVersion 未定义。用法: iscc /DAppVersion=x.y.z installer.iss"
#endif

[Setup]
AppName=DEPRESSION-PLEX
AppVersion={#AppVersion}
AppPublisher=Gene&I Scientific
AppPublisherURL=https://genei.ai
DefaultDirName={autopf}\DEPRESSION-PLEX
DefaultGroupName=DEPRESSION-PLEX
OutputDir=.
OutputBaseFilename=DEPRESSION-PLEX-Setup-{#AppVersion}
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
UninstallDisplayIcon={app}\DEPRESSION-PLEX.exe
DisableProgramGroupPage=yes

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; GUI 文件夹（PyInstaller one-folder 输出）
Source: "dist\DEPRESSION-PLEX\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 后端文件夹（整个装进 backend\）
Source: "dist\depression-analyzer\*"; DestDir: "{app}\backend"; Flags: ignoreversion recursesubdirs createallsubdirs

; ffmpeg 及其许可文件（随包分发，装进 backend\ffmpeg\）
Source: "vendor\ffmpeg\ffmpeg.exe"; DestDir: "{app}\backend\ffmpeg"; Flags: ignoreversion
Source: "vendor\ffmpeg\ffprobe.exe"; DestDir: "{app}\backend\ffmpeg"; Flags: ignoreversion
Source: "vendor\ffmpeg\LICENSE.txt"; DestDir: "{app}\backend\ffmpeg"; Flags: ignoreversion
Source: "vendor\ffmpeg\COPYING.LGPLv2.1.txt"; DestDir: "{app}\backend\ffmpeg"; Flags: ignoreversion
Source: "vendor\ffmpeg\COPYING.LGPLv3.txt"; DestDir: "{app}\backend\ffmpeg"; Flags: ignoreversion

[Icons]
; 开始菜单快捷方式
Name: "{group}\DEPRESSION-PLEX"; Filename: "{app}\DEPRESSION-PLEX.exe"
Name: "{group}\{cm:UninstallProgram,DEPRESSION-PLEX}"; Filename: "{uninstallexe}"

[Run]
; 安装后询问是否启动
Filename: "{app}\DEPRESSION-PLEX.exe"; Description: "{cm:LaunchProgram,DEPRESSION-PLEX}"; Flags: nowait postinstall skipifsilent
