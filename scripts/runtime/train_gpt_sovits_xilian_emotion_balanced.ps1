param(
    [string]$GptSovitsRoot = "D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50",
    [string]$SourceProject = "Natsume_Ai_Xilian_zh_clean_lively",
    [string]$Project = "Natsume_Ai_Xilian_zh_emotion_balanced",
    [string]$Version = "v2ProPlus",
    [string]$GpuNumbers = "0",
    [int]$S2Epochs = 8,
    [int]$S2BatchSize = 2,
    [int]$S2SegmentSize = 20480,
    [int]$S2NumWorkers = 0,
    [int]$S1Epochs = 10,
    [int]$S1BatchSize = 4,
    [int]$S1NumWorkers = 1,
    [switch]$PrepareOnly,
    [switch]$SkipS2,
    [switch]$SkipS1
)

$ErrorActionPreference = "Stop"
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    [System.IO.File]::WriteAllText($Path, $Content, $script:Utf8NoBom)
}

function Write-StatusJson([string]$Path, [object]$Status) {
    Write-Utf8NoBom -Path $Path -Content ($Status | ConvertTo-Json -Depth 10)
}

function Convert-ToForwardSlash([string]$Path) {
    return $Path.Replace("\", "/")
}

function Get-WavNameFromSourceLine([string]$Line) {
    $parts = $Line.Split("|", 4)
    if ($parts.Count -ne 4) {
        throw "Bad GPT-SoVITS source line: $Line"
    }
    return [System.IO.Path]::GetFileName($parts[0])
}

function Copy-SelectedFiles([string]$SourceDir, [string]$TargetDir, [string[]]$Names, [string]$Suffix) {
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    $missing = New-Object System.Collections.Generic.List[string]
    foreach ($name in $Names) {
        $src = Join-Path $SourceDir "$name$Suffix"
        $dst = Join-Path $TargetDir "$name$Suffix"
        if (-not (Test-Path -LiteralPath $src)) {
            $missing.Add($src)
            continue
        }
        Copy-Item -LiteralPath $src -Destination $dst -Force
    }
    if ($missing.Count -gt 0) {
        throw "Missing selected feature files:`n$($missing -join "`n")"
    }
}

function Copy-SelectedTabRows([string]$SourcePath, [string]$TargetPath, [System.Collections.Generic.HashSet[string]]$NameSet) {
    $rows = New-Object System.Collections.Generic.List[string]
    foreach ($line in Get-Content -LiteralPath $SourcePath -Encoding UTF8) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        $name = $line.Split("`t", 2)[0]
        if ($NameSet.Contains($name)) {
            $rows.Add($line)
        }
    }
    if ($rows.Count -ne $NameSet.Count) {
        throw "Filtered row count mismatch for $SourcePath. expected=$($NameSet.Count), actual=$($rows.Count)"
    }
    Write-Utf8NoBom -Path $TargetPath -Content (($rows -join "`n") + "`n")
}

function Invoke-NativeWithRedirect([string]$Exe, [string[]]$Arguments, [string]$StdOutPath, [string]$StdErrPath) {
    $quotedArgs = @($Arguments | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' })
    $cmdLine = '"' + $Exe + '" ' + ($quotedArgs -join " ") + ' > "' + $StdOutPath + '" 2> "' + $StdErrPath + '"'
    cmd.exe /c $cmdLine
    return $LASTEXITCODE
}

$Root = (Resolve-Path -LiteralPath $GptSovitsRoot).Path
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $Root "runtime\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "GPT-SoVITS runtime python not found: $Python"
}

$SourceLogRoot = Join-Path $Root "logs\$SourceProject"
$ProjectLogRoot = Join-Path $Root "logs\$Project"
$SourceList = Join-Path $ProjectLogRoot "0-source.list"
if (-not (Test-Path -LiteralPath $SourceList)) {
    throw "Balanced source list not found: $SourceList"
}
$sourceAuthorizationPath = Join-Path $SourceLogRoot "source_authorization.json"
$projectAuthorizationPath = Join-Path $ProjectLogRoot "source_authorization.json"
if (Test-Path -LiteralPath $sourceAuthorizationPath) {
    Copy-Item -LiteralPath $sourceAuthorizationPath -Destination $projectAuthorizationPath -Force
}
$s2OutputDir = Join-Path $ProjectLogRoot "logs_s2_$Version"
New-Item -ItemType Directory -Force -Path $s2OutputDir | Out-Null

$selectedLines = @(Get-Content -LiteralPath $SourceList -Encoding UTF8 | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
$names = @($selectedLines | ForEach-Object { Get-WavNameFromSourceLine $_ })
$nameSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($name in $names) {
    [void]$nameSet.Add($name)
}
if ($nameSet.Count -ne $selectedLines.Count) {
    throw "Balanced source list has duplicate wav names. selectedLines=$($selectedLines.Count), uniqueNames=$($nameSet.Count)"
}

Copy-SelectedTabRows `
    -SourcePath (Join-Path $SourceLogRoot "2-name2text.txt") `
    -TargetPath (Join-Path $ProjectLogRoot "2-name2text.txt") `
    -NameSet $nameSet
Copy-SelectedTabRows `
    -SourcePath (Join-Path $SourceLogRoot "6-name2semantic.tsv") `
    -TargetPath (Join-Path $ProjectLogRoot "6-name2semantic.tsv") `
    -NameSet $nameSet
Copy-SelectedFiles `
    -SourceDir (Join-Path $SourceLogRoot "3-bert") `
    -TargetDir (Join-Path $ProjectLogRoot "3-bert") `
    -Names $names `
    -Suffix ".pt"
Copy-SelectedFiles `
    -SourceDir (Join-Path $SourceLogRoot "4-cnhubert") `
    -TargetDir (Join-Path $ProjectLogRoot "4-cnhubert") `
    -Names $names `
    -Suffix ".pt"
Copy-SelectedFiles `
    -SourceDir (Join-Path $SourceLogRoot "5-wav32k") `
    -TargetDir (Join-Path $ProjectLogRoot "5-wav32k") `
    -Names $names `
    -Suffix ""
Copy-SelectedFiles `
    -SourceDir (Join-Path $SourceLogRoot "7-sv_cn") `
    -TargetDir (Join-Path $ProjectLogRoot "7-sv_cn") `
    -Names $names `
    -Suffix ".pt"

$sourceConfigPath = Join-Path $SourceLogRoot "config.json"
$s2Config = Get-Content -LiteralPath $sourceConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$projectLogForward = Convert-ToForwardSlash $ProjectLogRoot
$s2Config.train.epochs = $S2Epochs
$s2Config.train.batch_size = $S2BatchSize
$s2Config.train.segment_size = $S2SegmentSize
$s2Config.train.gpu_numbers = $GpuNumbers
$s2Config.train.grad_ckpt = $true
$s2Config.train.if_save_latest = $true
$s2Config.train.if_save_every_weights = $true
$s2Config.train.save_every_epoch = 1
$s2Config.data.exp_dir = $projectLogForward
$s2Config.s2_ckpt_dir = $projectLogForward
$s2Config.name = $Project
$s2Config.version = $Version
$s2Config.model.version = $Version
$s2Config.save_weight_dir = "SoVITS_weights_$Version"
$s2ConfigPath = Join-Path $ProjectLogRoot "tmp_s2_$Version.json"
Write-Utf8NoBom -Path $s2ConfigPath -Content ($s2Config | ConvertTo-Json -Depth 50)
Copy-Item -LiteralPath $s2ConfigPath -Destination (Join-Path $ProjectLogRoot "config.json") -Force

$s1OutputDir = Join-Path $ProjectLogRoot "logs_s1_$Version"
New-Item -ItemType Directory -Force -Path $s1OutputDir | Out-Null
$pretrainedS1 = Convert-ToForwardSlash (Join-Path $Root "GPT_SoVITS\pretrained_models\s1v3.ckpt")
$trainSemanticPath = Convert-ToForwardSlash (Join-Path $ProjectLogRoot "6-name2semantic.tsv")
$trainPhonemePath = Convert-ToForwardSlash (Join-Path $ProjectLogRoot "2-name2text.txt")
$s1OutputForward = Convert-ToForwardSlash $s1OutputDir
$s1ConfigPath = Join-Path $ProjectLogRoot "tmp_s1_$Version.yaml"
$s1Yaml = @"
train:
  seed: 1234
  epochs: $S1Epochs
  batch_size: $S1BatchSize
  save_every_n_epoch: 5
  precision: 16-mixed
  gradient_clip: 1.0
  if_save_every_weights: true
  if_save_latest: true
  if_dpo: false
  half_weights_save_dir: GPT_weights_$Version
  exp_name: $Project
optimizer:
  lr: 0.01
  lr_init: 0.00001
  lr_end: 0.0001
  warmup_steps: 2000
  decay_steps: 40000
data:
  max_eval_sample: 8
  max_sec: 54
  num_workers: $S1NumWorkers
  pad_val: 1024
model:
  vocab_size: 1025
  phoneme_vocab_size: 732
  embedding_dim: 512
  hidden_dim: 512
  head: 16
  linear_units: 2048
  n_layer: 24
  dropout: 0
  EOS: 1024
  random_bert: 0
inference:
  top_k: 5
pretrained_s1: $pretrainedS1
train_semantic_path: $trainSemanticPath
train_phoneme_path: $trainPhonemePath
output_dir: $s1OutputForward
"@
Write-Utf8NoBom -Path $s1ConfigPath -Content $s1Yaml

$runRoot = Join-Path $RepoRoot "artifacts\gpt_sovits_training\$Project"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
$statusPath = Join-Path $runRoot "status.json"
$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$s2LogPath = Join-Path $runRoot "s2_train_$runId.log"
$s2ErrPath = Join-Path $runRoot "s2_train_$runId.err.log"
$s1LogPath = Join-Path $runRoot "s1_train_$runId.log"
$s1ErrPath = Join-Path $runRoot "s1_train_$runId.err.log"
$status = [ordered]@{
    project = $Project
    source_project = $SourceProject
    version = $Version
    run_id = $runId
    selected_count = $selectedLines.Count
    prepared_at = (Get-Date).ToString("o")
    gpt_sovits_root = $Root
    project_log_root = $ProjectLogRoot
    source_authorization_path = if (Test-Path -LiteralPath $projectAuthorizationPath) {
        $projectAuthorizationPath
    } else {
        $null
    }
    s2_config_path = $s2ConfigPath
    s1_config_path = $s1ConfigPath
    s2_log_path = $s2LogPath
    s2_err_path = $s2ErrPath
    s1_log_path = $s1LogPath
    s1_err_path = $s1ErrPath
    state = if ($PrepareOnly) { "prepared" } else { "starting" }
}
Write-StatusJson -Path $statusPath -Status $status

Write-Host "Prepared GPT-SoVITS balanced project assets."
Write-Host "Project: $Project"
Write-Host "Selected rows: $($selectedLines.Count)"
Write-Host "S2 config: $s2ConfigPath"
Write-Host "S1 config: $s1ConfigPath"
Write-Host "Status: $statusPath"

if ($PrepareOnly) {
    exit 0
}

Set-Location -LiteralPath $Root
$env:PATH = "$(Join-Path $Root "runtime");$env:PATH"
$env:_CUDA_VISIBLE_DEVICES = $GpuNumbers
$env:CUDA_VISIBLE_DEVICES = $GpuNumbers
$env:hz = "25hz"
$env:GSV_S2_NUM_WORKERS = [string]$S2NumWorkers
$env:PYTORCH_CUDA_ALLOC_CONF = "expandable_segments:True,max_split_size_mb:128"

if (-not $SkipS2) {
    $status["state"] = "running_s2"
    $status["s2_started_at"] = (Get-Date).ToString("o")
    Write-StatusJson -Path $statusPath -Status $status
    $exitCode = Invoke-NativeWithRedirect `
        -Exe $Python `
        -Arguments @("-s", "GPT_SoVITS/s2_train.py", "--config", $s2ConfigPath) `
        -StdOutPath $s2LogPath `
        -StdErrPath $s2ErrPath
    if ($exitCode -ne 0) {
        $status["state"] = "failed_s2"
        $status["s2_exit_code"] = $exitCode
        $status["failed_at"] = (Get-Date).ToString("o")
        Write-StatusJson -Path $statusPath -Status $status
        exit $exitCode
    }
    $status["s2_finished_at"] = (Get-Date).ToString("o")
}

if (-not $SkipS1) {
    $status["state"] = "running_s1"
    $status["s1_started_at"] = (Get-Date).ToString("o")
    Write-StatusJson -Path $statusPath -Status $status
    $exitCode = Invoke-NativeWithRedirect `
        -Exe $Python `
        -Arguments @("-s", "GPT_SoVITS/s1_train.py", "--config_file", $s1ConfigPath) `
        -StdOutPath $s1LogPath `
        -StdErrPath $s1ErrPath
    if ($exitCode -ne 0) {
        $status["state"] = "failed_s1"
        $status["s1_exit_code"] = $exitCode
        $status["failed_at"] = (Get-Date).ToString("o")
        Write-StatusJson -Path $statusPath -Status $status
        exit $exitCode
    }
    $status["s1_finished_at"] = (Get-Date).ToString("o")
}

$status["state"] = "completed"
$status["completed_at"] = (Get-Date).ToString("o")
Write-StatusJson -Path $statusPath -Status $status
Write-Host "Completed GPT-SoVITS balanced training."
