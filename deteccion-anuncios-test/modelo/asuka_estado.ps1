<#
Parte de estado de RadioBlock en asuka (solo lectura): procesos y prioridad, disco, datos y log del ciclo.
  powershell -NoProfile -ExecutionPolicy Bypass -File asuka_estado.ps1
#>
$modelo = $PSScriptRoot
$d = Join-Path $modelo 'data'
'hora ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '   encendido desde ' + (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Get-PSDrive C | ForEach-Object { 'disco C: libre {0:N1} GB de {1:N1} GB' -f ($_.Free / 1GB), (($_.Used + $_.Free) / 1GB) }
foreach ($s in 'clips', 'entradas', 'huecos') {
    $f = Get-ChildItem (Join-Path $d $s) -Recurse -File -ErrorAction SilentlyContinue
    '{0}: {1} archivos, {2:N2} GB' -f $s, $f.Count, (($f | Measure-Object Length -Sum).Sum / 1GB)
}
$c = Get-ChildItem (Join-Path $d 'clips\cadena_ser') -File -ErrorAction SilentlyContinue | Sort-Object Name
if ($c) { 'audio desde ' + $c[0].BaseName + ' hasta ' + $c[-1].BaseName }
'--- procesos'
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'capture|ciclo_catalogo|revisar|ia_transcribir|en_vivo' } | ForEach-Object {
    $q = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
    '{0,6} prio={1,-11} cpu={2,8:N0}s mem={3,6:N0}MB  {4}' -f $_.ProcessId, $q.PriorityClass, $q.CPU, ($q.WorkingSet64 / 1MB),
        ($_.CommandLine -replace '^.*?python\.exe"?\s+', '')
}
'--- vueltas del ciclo'
Get-Content (Join-Path $modelo 'ciclo.log') | Select-String '^=== |^vuelta terminada|^error en la vuelta' | Select-Object -Last 6
'--- ultimas lineas del ciclo'
Get-Content (Join-Path $modelo 'ciclo.log') -Tail 800 | Select-String -NotMatch '^transcrita|^  entrada [0-9]+:' | Select-Object -Last 8
