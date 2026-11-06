<#
Para todo lo de RadioBlock en asuka: captura (y su ffmpeg), ciclo (y su transcripcion) y web de revision.
  powershell -NoProfile -ExecutionPolicy Bypass -File asuka_parar.ps1
Para volver a arrancarlo: asuka_arrancar.ps1
#>
$patron = 'capture\.py|ciclo_catalogo\.py|revisar\.py|ia_transcribir\.py|en_vivo\.py|CADENASER'
$ps = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in 'cmd.exe', 'python.exe', 'ffmpeg.exe' -and $_.CommandLine -match $patron -and $_.ProcessId -ne $PID })
foreach ($p in $ps) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    "parado {0} {1}" -f $p.ProcessId, $p.Name
}
Start-Sleep -Seconds 2
$quedan = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in 'cmd.exe', 'python.exe', 'ffmpeg.exe' -and $_.CommandLine -match $patron })
"quedan corriendo: $($quedan.Count)"
