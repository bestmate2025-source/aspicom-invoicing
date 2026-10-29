# Wave 3J - splash card before login (frontend\invoicing.html)
$ErrorActionPreference = "Stop"
$path = Join-Path (Get-Location) "frontend\invoicing.html"
if (-not (Test-Path $path)) { Write-Host "ERROR: run this inside the project folder (frontend\invoicing.html not found)" -ForegroundColor Red; exit 1 }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$text = [System.IO.File]::ReadAllText($path, $utf8)
if ($text.Contains("SPLASH CARD (3J)")) { Write-Host "Already applied - nothing to do." -ForegroundColor Yellow; exit 0 }
$h = [regex]::Matches($text, "</head>").Count
$b = [regex]::Matches($text, "<body>").Count
if ($h -ne 1 -or $b -ne 1) { Write-Host "ERROR: expected exactly one </head> and one <body> (found $h and $b). Nothing changed." -ForegroundColor Red; exit 1 }
$bak = "$path.bak-SPLASH3J"
Copy-Item $path $bak -Force
$css = @'
<style id="splash-3j-css">
/* SPLASH CARD (3J) */
#splash-3j{position:fixed; inset:0; z-index:2147483000; display:flex; align-items:center; justify-content:center; background:linear-gradient(160deg,#1A2E05 0%,#365314 55%,#4D7C0F 100%); opacity:1; transition:opacity .6s ease; cursor:pointer; padding:16px;}
#splash-3j.hide{opacity:0; pointer-events:none;}
#splash-3j .sp-card{background:#FFFFFF; color:#1A2E05; border-radius:20px; padding:34px 40px; max-width:460px; width:100%; text-align:center; box-shadow:0 24px 70px rgba(0,0,0,.35); animation:sp-in .7s ease both; font-family:'Inter',Arial,sans-serif;}
#splash-3j .sp-logo{font-family:'Fraunces',Georgia,serif; font-size:34px; font-weight:600; letter-spacing:-0.02em; margin-bottom:2px;}
#splash-3j .sp-logo span{color:#65A30D;}
#splash-3j .sp-tag{font-size:12px; letter-spacing:.14em; text-transform:uppercase; color:#4D7C0F; font-weight:700; margin-bottom:20px;}
#splash-3j .sp-line{height:3px; width:64px; background:#A3E635; border-radius:3px; margin:0 auto 20px;}
#splash-3j .sp-dev{font-size:13px; color:#5B6B4A; margin-bottom:4px;}
#splash-3j .sp-name{font-size:19px; font-weight:700; margin-bottom:16px;}
#splash-3j .sp-row{font-size:15px; font-weight:600; margin:6px 0;}
#splash-3j .sp-row a{color:#3F6212; text-decoration:none;}
#splash-3j .sp-skip{margin-top:20px; font-size:11px; color:#8A9A78;}
@keyframes sp-in{from{opacity:0; transform:translateY(14px) scale(.97);} to{opacity:1; transform:none;}}
@media print{#splash-3j{display:none !important;}}
</style>
'@
$body = @'
<div id="splash-3j"><div class="sp-card"><div class="sp-logo">Ledger<span>.</span></div><div class="sp-tag">Invoicing &amp; Accounting</div><div class="sp-line"></div><div class="sp-dev">Developed by</div><div class="sp-name">SoftSolution Cheptech ERP SYSTEM</div><div class="sp-row"><a href="https://www.fixyoursitproblem.online" target="_blank" rel="noopener">WWW.FIXYOURSITPROBLEM.ONLINE</a></div><div class="sp-row"><a href="tel:+971508160834">+971-508160834</a></div><div class="sp-skip">Click anywhere to continue</div></div></div>
<script>
(function () {
  var d = document.getElementById('splash-3j'); if (!d) return;
  var gone = false;
  function close() { if (gone) return; gone = true; d.classList.add('hide'); setTimeout(function () { if (d.parentNode) d.parentNode.removeChild(d); }, 700); }
  d.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('a')) return; close(); });
  setTimeout(close, 2800);
})();
</script>
'@
$text = $text.Replace("</head>", $css + "`n</head>")
$text = $text.Replace("<body>", "<body>`n" + $body)
[System.IO.File]::WriteAllText($path, $text, $utf8)
Write-Host "SUCCESS: splash card added. Backup: $bak" -ForegroundColor Green
Write-Host "Now press Ctrl+F5 in the browser."
