# Copies AI_CONTEXT.txt + PHASE1_PROMPT.txt to clipboard, ready to paste into any AI chat
$base = 'C:\Users\farha\OneDrive\Pictures\invoicing_project\backups'
$ctx = Get-Content "$base\AI_CONTEXT.txt" -Raw
$task = Get-Content "$base\PHASE1_PROMPT.txt" -Raw
$combined = $ctx + "`n`n`n" + $task
Set-Clipboard -Value $combined
Write-Host "SUCCESS: both files copied to clipboard" -ForegroundColor Green
Write-Host "Length: $($combined.Length) characters" -ForegroundColor Cyan
Write-Host ""
Write-Host "Now paste into Claude, LongCat, or any AI chat." -ForegroundColor Yellow
