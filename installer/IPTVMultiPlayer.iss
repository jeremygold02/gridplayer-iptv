#define MyAppName "IPTV Multi Player"
#define MyAppPublisher "jeremygold02"
#define MyAppURL "https://github.com/jeremygold02/iptv-multi-player"
#define MyAppUserModelID "jeremygold02.IPTVMultiPlayer"

#ifndef MyAppVersion
#define MyAppVersion "0.1.0"
#endif

[Setup]
AppId={{B6B2F2A1-4F51-4763-9E23-7C7A89CB64D5}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases/latest
UninstallDisplayName={#MyAppName}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=IPTV Multi Player Setup
SetupIconFile=..\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\IPTV Multi Player.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startmenuicon"; Description: "Create a Start Menu shortcut"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\IPTV Multi Player.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\IPTV Multi Player.exe"; WorkingDir: "{app}"; IconFilename: "{app}\IPTV Multi Player.exe"; AppUserModelID: "{#MyAppUserModelID}"; Tasks: startmenuicon
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\IPTV Multi Player.exe"; WorkingDir: "{app}"; IconFilename: "{app}\IPTV Multi Player.exe"; AppUserModelID: "{#MyAppUserModelID}"; Tasks: desktopicon

[Run]
Filename: "{autoprograms}\{#MyAppName}.lnk"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: shellexec nowait postinstall skipifsilent; Check: WizardIsTaskSelected('startmenuicon')
Filename: "{app}\IPTV Multi Player.exe"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; Check: not WizardIsTaskSelected('startmenuicon')
