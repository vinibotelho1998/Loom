; Instalador do Loom.
;
; Compile por packaging/build.ps1 (ou BUILD.bat): e' de la' que vem a versao,
; lida de loom/__init__.py, para nao existir numero repetido em dois
; lugares. Compilar este arquivo na mao tambem funciona, usando a reserva.

#define MyAppName "Loom"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "Vinicius Botelho"
#define MyAppExeName "Loom.exe"
#define MyAppUrl "https://github.com/vinibotelho1998/Loom"

[Setup]
; AppId fixo e unico: e' por ele que o Windows reconhece uma atualizacao no
; lugar de uma segunda instalacao. Nunca mudar entre versoes.
AppId={{E8B5405F-4792-4DEA-882E-D76965FE85F0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppUrl}
AppSupportURL={#MyAppUrl}/issues
AppUpdatesURL={#MyAppUrl}/releases/latest
AppCopyright=Copyright (C) {#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription=Instalador do {#MyAppName}
AppComments=Terminal com abas para o Claude Code: perfis de conta, sessoes salvas e consumo do plano.
VersionInfoCompany={#MyAppPublisher}

DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto

OutputDir=..\dist
OutputBaseFilename=Loom-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\ui\loom.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

; O terminal nao mexe em nada do sistema: instala para o usuario atual e nao
; pede UAC. Quem rodar o setup como admin instala para a maquina inteira.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Fecha o app antes de sobrescrever os arquivos, em vez de falhar no meio.
CloseApplications=yes
RestartApplications=no
SetupMutex=LoomSetupMutex

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na area de trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "..\dist\Loom\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\prereqs\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; O Qt e' compilado com MSVC: sem o runtime a janela nem abre. A instalacao do
; redistribuivel pede elevacao, entao so' acontece quando falta mesmo.
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Instalando Visual C++ Redistributable..."; Flags: waituntilterminated shellexec; Check: not IsVCRedistInstalled
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir o {#MyAppName} agora"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabel=A instalação do [name] terminou.%n%nAs contas, sessões salvas e o histórico de consumo ficam em %USERPROFILE%\.loom e continuam lá depois de atualizar.

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
function IsVCRedistInstalled: Boolean;
var
  Installed: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed)
    and (Installed = 1);
end;

// As contas do Claude Code, as sessoes salvas e o consumo do plano moram em
// %USERPROFILE%\.loom. Apagar isso sem perguntar seria derrubar o login de
// quem so' queria reinstalar.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dados: string;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  Dados := ExpandConstant('{%USERPROFILE}\.loom');
  if not DirExists(Dados) then
    Exit;
  if MsgBox('Apagar tambem as contas, sessoes salvas e o historico de consumo?'
            + #13#10 + #13#10 + Dados,
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(Dados, True, True, True);
end;
