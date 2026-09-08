# Baixa o que vai embutido no instalador. Sem acentos de proposito: o
# PowerShell 5.1 le .ps1 como ANSI e um caractere fora do ASCII quebra o parser.
$ErrorActionPreference = "Stop"

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$repoRoot = Split-Path -Parent $scriptDir
$prereqDir = Join-Path $repoRoot "prereqs"
New-Item -ItemType Directory -Path $prereqDir -Force | Out-Null

$files = @(
    @{ Url = "https://aka.ms/vs/17/release/vc_redist.x64.exe"; Out = (Join-Path $prereqDir "vc_redist.x64.exe") }
)

foreach ($f in $files) {
    if (Test-Path $f.Out) {
        Write-Host ("Ja existe: " + $f.Out)
        continue
    }
    Write-Host ("Baixando: " + $f.Url)
    Invoke-WebRequest -Uri $f.Url -OutFile $f.Out -UseBasicParsing
}

Write-Host "Dependencias prontas."
