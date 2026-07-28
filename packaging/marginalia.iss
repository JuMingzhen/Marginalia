; Marginalia 安装程序（Inno Setup 6）
;
; 由 packaging/build.py 调用，不要直接编译——版本号是构建时写进 build/version.iss 的。
;
; 两个刻意的决定：
;
; 1. 默认装到「仅为我安装」的用户目录，不需要管理员权限。装进 Program Files 要提权，
;    而这个程序没有任何需要提权的理由。用户想装到 Program Files 也可以，向导里能选。
;
; 2. 卸载时**不删除数据目录**。笔记存在「文档\Marginalia」（或用户自选位置），
;    跟程序目录完全分开，卸载器碰都不碰。

#include "build\version.iss"

#define AppName "Marginalia"
#define AppPublisher "Marginalia"
#define AppURL "https://github.com/JuMingzhen/Marginalia"
#define AppExe "Marginalia.exe"

[Setup]
; 这个 GUID 决定「同一个程序」的身份，升级时靠它找到已装版本。永远不要改。
AppId={{8F3A1C4B-2E7D-4A96-9B15-C0D6E8A7F241}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}

DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

; 默认按当前用户安装，免 UAC；向导里可以改成为所有人安装
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

LicenseFile=..\LICENSE
OutputDir=output
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
SetupIconFile=..\marginalia\resources\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}

Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; Inno 官方发行版不带简体中文，把第三方的 ChineseSimplified.isl 放进
; Inno Setup 的 Languages 目录即可启用；没有也照常能编译
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#endif

[Types]
Name: "full"; Description: "完整安装（含 OCR，可读扫描版）"
Name: "compact"; Description: "精简安装（不含 OCR）"
Name: "custom"; Description: "自定义"; Flags: iscustom

[Components]
Name: "core"; Description: "Marginalia 主程序"; Types: full compact custom; Flags: fixed
Name: "ocr"; Description: "OCR 组件 —— 扫描版 PDF 的文字识别（约 80 MB）"; Types: full

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式:"
Name: "pdfassoc"; Description: "用 Marginalia 打开 PDF 文件"; \
    GroupDescription: "文件关联:"; Flags: unchecked

[Files]
; 主程序。OCR 相关文件已由 build.py 挪到 ocr-component 目录，这里不会重复
Source: "output\Marginalia\*"; DestDir: "{app}"; Components: core; \
    Flags: ignoreversion recursesubdirs createallsubdirs
; OCR 组件：目录结构与主程序一致，直接铺上去即可
Source: "output\ocr-component\*"; DestDir: "{app}"; Components: ocr; \
    Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; 文件关联。注册到 HKA，per-user 安装时自动落到 HKCU，不需要管理员权限。
; 用 OpenWithProgids 而不是抢占 .pdf 的默认程序——把别人的默认阅读器换掉是很讨厌的事，
; 用户在「打开方式」里能看到 Marginalia，想设为默认自己去设。
Root: HKA; Subkey: "Software\Classes\Marginalia.pdf"; \
    ValueType: string; ValueName: ""; ValueData: "PDF 文档"; \
    Flags: uninsdeletekey; Tasks: pdfassoc
Root: HKA; Subkey: "Software\Classes\Marginalia.pdf\DefaultIcon"; \
    ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: pdfassoc
Root: HKA; Subkey: "Software\Classes\Marginalia.pdf\shell\open\command"; \
    ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: pdfassoc
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; \
    ValueType: string; ValueName: "Marginalia.pdf"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: pdfassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; \
    ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; \
    Flags: uninsdeletekey; Tasks: pdfassoc

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller 运行时可能留下的缓存
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Messages]
english.WelcomeLabel2=这将在你的电脑上安装 [name/ver]。%n%nMarginalia 是一个本地 PDF 阅读与笔记工具。%n%n注意：你的笔记不会存放在程序目录里。首次启动时程序会问你笔记要放哪（默认「文档\Marginalia」），卸载时不会删除它们。

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // 卸载完成后明确告诉用户笔记还在，别让人以为丢了
  if CurUninstallStep = usPostUninstall then
    MsgBox('Marginalia 已卸载。' + #13#10 + #13#10 +
           '你的书库和笔记没有被删除，仍在你当初选定的文件夹里' + #13#10 +
           '（默认是「文档\Marginalia」）。',
           mbInformation, MB_OK);
end;
