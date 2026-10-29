# RESTORE ALL (Waves 2B to 3I) - copies the final backend + frontend files into the project.
# Every existing file is backed up first as <name>.bak-RESTORE. Run inside the project folder.
$ErrorActionPreference = "Stop"
$root = (Get-Location).Path
$src  = Join-Path $root "_restore_pkg"
if (-not (Test-Path (Join-Path $root "app.py"))) { Write-Host "ERROR: run this inside the project folder (app.py not found)" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $src)) { Write-Host "ERROR: _restore_pkg folder not found. Run Expand-Archive first." -ForegroundColor Red; exit 1 }
$files = @("models.py","routes\accounting.py","routes\invoices.py","routes\inventory.py","routes\purchase_orders.py","routes\inventory_adjust.py","routes\reports.py","routes\pdf.py","routes\stock_movements.py","utils\stock_movements.py","utils\accounting_auto.py","frontend\invoicing.html")
foreach ($f in $files) {
  $from = Join-Path $src $f
  $to   = Join-Path $root $f
  if (-not (Test-Path $from)) { Write-Host "ERROR: missing in package: $f" -ForegroundColor Red; exit 1 }
}
$n = 0
foreach ($f in $files) {
  $from = Join-Path $src $f
  $to   = Join-Path $root $f
  $dir  = Split-Path $to -Parent
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
  if (Test-Path $to) { Copy-Item $to "$to.bak-RESTORE" -Force }
  Copy-Item $from $to -Force
  $n++
  Write-Host ("copied  " + $f)
}
Write-Host ""
Write-Host "SUCCESS: $n files restored. Backups end with .bak-RESTORE" -ForegroundColor Green
Write-Host "Flask restarts by itself. Then press Ctrl+F5 in the browser."
