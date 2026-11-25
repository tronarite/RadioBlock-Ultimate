<#
Arranca en asuka lo que tiene que estar siempre corriendo (si no lo esta ya) y fija su prioridad:
  servicio -> normal  (graba, silencia y emite; no debe perder audio)
  ciclo    -> baja    (solo usa CPU que nadie mas quiere)

  powershell -NoProfile -ExecutionPolicy Bypass -File asuka_arrancar.ps1                     # arranca lo que falte y corrige prioridades
  powershell -NoProfile -ExecutionPolicy Bypass -File asuka_arrancar.ps1 -Reiniciar          # ademas relanza el ciclo (tras un git pull)
  powershell -NoProfile -ExecutionPolicy Bypass -File asuka_arrancar.ps1 -ReiniciarServicio  # ademas relanza el servicio (corta la emision unos segundos)

El servicio sustituye a capture.py: si queda un capture.py corriendo, el servicio no se arranca
(se avisa; parar el antiguo con asuka_parar.ps1).
Los logs quedan en esta carpeta: servicio.log y ciclo.log.
#>
param([switch]$Reiniciar, [switch]$ReiniciarServicio)

$modelo = $PSScriptRoot
$py = '..\..\.venv-capture\Scripts\python.exe'
$ffmpeg = 'C:\Otros archivos de programa\ffmpeg\bin'
$tareas = @(
    @{ script = 'servicio.py';       args = '--radio cadena_ser --host 100.72.42.80';                                         log = 'servicio.log'; prio = 'Normal'; reiniciar = [bool]$ReiniciarServicio; excluye = 'capture.py' },
    @{ script = 'ciclo_catalogo.py'; args = '--radios cadena_ser --cada 120 --horas 36 --retener-dias 3 --caducar-dias 30'; log = 'ciclo.log';    prio = 'Idle';   reiniciar = [bool]$Reiniciar;         excluye = $null }
)
$codigo = @{ Normal = 32; Idle = 64; BelowNormal = 16384 }   # Win32_ProcessStartup.PriorityClass

function Procesos([string]$script) {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in 'cmd.exe', 'python.exe' -and $_.CommandLine -match [regex]::Escape($script) -and $_.ProcessId -ne $PID })
}

foreach ($t in $tareas) {
    $ps = Procesos $t.script
    $bloqueado = $false
    if ($t.excluye -and (Procesos $t.excluye).Count) {   # no pueden correr los dos sobre la misma emisora
        $bloqueado = $true
        "AVISO: $($t.excluye) esta corriendo; $($t.script) no se arranca ni se relanza. Paralo antes con asuka_parar.ps1."
    }
    if ($ps.Count -and $t.reiniciar -and -not $bloqueado) {
        $ps | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 3
        "$($t.script): parado para relanzarlo"
        $ps = @()
    }
    if (-not $ps.Count -and -not $bloqueado) {
        $cmd = "cmd /c cd /d `"$modelo`" && set `"PATH=$ffmpeg;%PATH%`" && $py -u $($t.script) $($t.args) >> $($t.log) 2>&1"
        $si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ PriorityClass = [uint32]$codigo[$t.prio] }
        $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine = $cmd; CurrentDirectory = $modelo; ProcessStartupInformation = $si }
        "$($t.script): arrancado (pid $($r.ProcessId), resultado $($r.ReturnValue))"
        Start-Sleep -Seconds 5
    }
    foreach ($p in Procesos $t.script) {   # corrige tambien lo que ya corria
        $g = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
        if ($g -and "$($g.PriorityClass)" -ne $t.prio) {
            try { $g.PriorityClass = $t.prio } catch { "  no se pudo cambiar la prioridad de $($p.ProcessId): $_" }
        }
    }
    $ahora = Procesos $t.script | ForEach-Object { "$((Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue).PriorityClass)" }
    "{0}: {1} procesos ({2})" -f $t.script, $ahora.Count, (($ahora | Sort-Object -Unique) -join ', ')
}
