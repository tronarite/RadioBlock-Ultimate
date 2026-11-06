<#
Arranca en asuka lo que tiene que estar siempre corriendo (si no lo esta ya) y fija su prioridad:
  captura  -> normal  (no debe perder audio)
  revisar  -> por debajo de lo normal (la web responde aunque el ciclo trabaje)
  ciclo    -> baja    (solo usa CPU que nadie mas quiere)

  powershell -NoProfile -ExecutionPolicy Bypass -File asuka_arrancar.ps1              # arranca lo que falte y corrige prioridades
  powershell -NoProfile -ExecutionPolicy Bypass -File asuka_arrancar.ps1 -Reiniciar   # ademas relanza ciclo y web (tras un git pull)

La captura nunca se relanza si ya corre (dos capturas de la misma radio se pisan).
Los logs quedan en esta carpeta: capture2.log, ciclo.log, revisar.log.
#>
param([switch]$Reiniciar)

$modelo = $PSScriptRoot
$py = '..\..\.venv-capture\Scripts\python.exe'
$ffmpeg = 'C:\Otros archivos de programa\ffmpeg\bin'
$tareas = @(
    @{ script = 'capture.py';        args = '--radios cadena_ser --minutes 0';                             log = 'capture2.log'; prio = 'Normal';      reiniciable = $false },
    @{ script = 'revisar.py';        args = '--port 8070 --host 100.72.42.80';                             log = 'revisar.log';  prio = 'BelowNormal'; reiniciable = $true },
    @{ script = 'ciclo_catalogo.py'; args = '--radio cadena_ser --cada 120 --horas 36 --retener-dias 14'; log = 'ciclo.log';    prio = 'Idle';        reiniciable = $true }
)
$codigo = @{ Normal = 32; Idle = 64; BelowNormal = 16384 }   # Win32_ProcessStartup.PriorityClass

function Procesos([string]$script) {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in 'cmd.exe', 'python.exe' -and $_.CommandLine -match [regex]::Escape($script) })
}

foreach ($t in $tareas) {
    $ps = Procesos $t.script
    if ($ps.Count -and $Reiniciar -and $t.reiniciable) {
        $ps | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 3
        "$($t.script): parado para relanzarlo"
        $ps = @()
    }
    if (-not $ps.Count) {
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
