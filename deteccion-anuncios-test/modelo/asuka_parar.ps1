<#
Para todo lo de RadioBlock en asuka: servicios de todas las emisoras (y sus ffmpeg), ciclo (y su
transcripcion) y, si quedara, una captura antigua (capture.py).
  powershell -NoProfile -ExecutionPolicy Bypass -File asuka_parar.ps1
Para volver a arrancarlo: asuka_arrancar.ps1
#>
$patron = 'servicio\.py|capture\.py|ciclo_catalogo\.py|ia_transcribir\.py|en_vivo\.py'
$datos = [regex]::Escape((Join-Path $PSScriptRoot 'data'))

# cmd/python de RadioBlock y los ffmpeg que son hijos suyos (o que escriben en data\, como el de capture.py).
# No se mira la URL ni '-f s16le': otros programas (podcast-player) tambien lanzan ffmpeg con esas opciones.
function Objetivos {
    $todos = @(Get-CimInstance Win32_Process)   # una sola foto, antes de matar nada
    $rb = @($todos | Where-Object { $_.Name -in 'cmd.exe', 'python.exe' -and $_.CommandLine -match $patron -and $_.ProcessId -ne $PID })
    $padres = @{}
    foreach ($p in $rb) { $padres[[int]$p.ProcessId] = $p }
    $ff = @($todos | Where-Object {
        $_.Name -eq 'ffmpeg.exe' -and (
            $_.CommandLine -match $datos -or
            ($padres.ContainsKey([int]$_.ParentProcessId) -and $_.CreationDate -ge $padres[[int]$_.ParentProcessId].CreationDate)) })
    $rb + $ff   # el CreationDate evita tomar un ffmpeg ajeno cuyo padre muerto tuviera un pid reutilizado
}

$ps = @(Objetivos)
foreach ($p in $ps) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    "parado {0} {1}" -f $p.ProcessId, $p.Name
}
Start-Sleep -Seconds 2
$vivos = @($ps | Where-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue })
$quedan = @(@($vivos) + @(Objetivos) | Sort-Object ProcessId -Unique)
"quedan corriendo: $($quedan.Count)"
