# pipeline.ps1 — SquaredVoxGameReady full pipeline
# Uso:
#   .\pipeline.ps1 -vox E:\path\modelo.vox
#   .\pipeline.ps1 -vox E:\path\modelo.vox -resolution 2048
#   .\pipeline.ps1 -vox E:\path\modelo.vox -resolution 512 -no_fbx
#   .\pipeline.ps1 -vox E:\path\modelo.vox -no_glb

param(
    [Parameter(Mandatory=$true)]
    [string]$vox,

    [int]$resolution = 1024,

    [string]$output = "E:\Repository\vox_models\Test",

    [string]$project = "E:\Repository\vox_game_ready",

    [string]$blender = "C:\Program Files\Blender Foundation\Blender\blender.exe",

    [switch]$no_fbx,
    [switch]$no_glb
)

$ErrorActionPreference = "Stop"

# ── Etapa 1: Greedy meshing → .obj ───────────────────────────────────────────
Write-Host "`n[1/2] Greedy meshing..." -ForegroundColor Cyan
python "$project\main_greedy.py" $vox .obj

if ($LASTEXITCODE -ne 0) {
    Write-Host "Erro no greedy meshing. Pipeline abortado." -ForegroundColor Red
    exit 1
}

# Caminho do .obj gerado (mesmo diretório do .vox)
$base = [System.IO.Path]::GetFileNameWithoutExtension($vox)
$vox_dir = [System.IO.Path]::GetDirectoryName($vox)
$obj_path = Join-Path $vox_dir "${base}_Greedy.obj"

if (-not (Test-Path $obj_path)) {
    Write-Host "Arquivo .obj não encontrado: $obj_path" -ForegroundColor Red
    exit 1
}

# ── Etapa 2: Blender bake → .fbx / .glb ─────────────────────────────────────
Write-Host "`n[2/2] Bake + Export (Blender headless)..." -ForegroundColor Cyan

$bake_args = @(
    "--background",
    "--python", "$project\bake.py",
    "--",
    $obj_path,
    "--resolution", $resolution,
    "--output", $output
)
if ($no_fbx) { $bake_args += "--no-fbx" }
if ($no_glb)  { $bake_args += "--no-glb" }

& $blender @bake_args

if ($LASTEXITCODE -ne 0) {
    Write-Host "Erro no bake. Verifique o log do Blender acima." -ForegroundColor Red
    exit 1
}

Write-Host "`nPipeline concluído! Arquivos em: $output" -ForegroundColor Green
