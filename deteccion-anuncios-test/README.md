# Detección de anuncios (Cadena SER) — banco de pruebas

Objetivo: reconocer los bloques de publicidad de Cadena SER y silenciarlos enteros.
El método es un **catálogo de anuncios que se aprende solo**: el audio que se repite a lo
largo del día (cuñas, promos) entra al catálogo, el catálogo se busca en el audio nuevo y
cada entrada se revisa una vez (Sí = anuncio, No = radio o promo de la SER).

## Estado (2026-11-06)

- **asuka** graba la SER sin parar y el ciclo aprende solo, en prioridad baja, con una vuelta
  cada ~3 h. Web de revisión: http://100.72.42.80:8070/ (Tailscale).
- **Catálogo**: 199 anuncios confirmados, más 79 Sí y 76 No de la IA (6-nov) que el ciclo aplica
  en su primera vuelta. Cobertura medida antes de aplicarlos: los confirmados tapan el **23 %**
  del tiempo de los bloques; casi todo lo demás son entradas sin revisar.
- **Reglas que ya funcionan**: nada de menos de 6 s entra al catálogo (sintonías, la hora); un
  "No" vale para todas las copias de ese audio; las respuestas nuevas heredan las anteriores.
- **Silenciador en vivo** (`en_vivo.py`): con audio de asuka tapa al 100 % los anuncios conocidos
  y rellena el hueco entre dos seguidos (±1 s). **En el PC no reconoce nada**: el mismo directo
  da otra huella con el ffmpeg 9 del PC (3 % de hashes alineados, umbral 10 %); en asuka, con
  ffmpeg 7.1, da 36 %. Por eso tiene que ir en asuka.

## Lo que falta: 4 pasos

1. **Revisar lo acumulado.** En asuka `python ia_pendientes.py --n 150 > p.json`; Claude
   clasifica por el texto, tú apruebas la tabla y se guarda con `python ia_aplicar.py v.json`.
   Los dudosos, de oído en la web. Medir con `python cobertura.py`.
   *Hecho cuando* los confirmados tapan ≥ 85 % del tiempo de los bloques.
2. **Silenciador en asuka.** `en_vivo.py` corriendo en asuka, con los confirmados leídos de
   `catalogo.db` (sin exportar) y el audio ya silenciado servido por HTTP en Tailscale, para
   escucharlo en el móvil o el PC. *Hecho cuando* se oye desde el móvil y un bloque real sale
   mudo entero.
3. **Ajustar escuchando.** Uno o dos días de uso: lo que se cuela se confirma; si corta radio,
   se ajustan `RELLENO_S` y los márgenes. *Hecho cuando* pasa un día sin anuncios audibles ni
   cortes de programa.
4. **Cerrar.** Arranque automático en asuka (tarea programada que llama a
   `asuka_arrancar.ps1`), retirar lo antiguo (`backend/` de la raíz, scripts de prueba), README
   raíz con el método final y merge `beta` → `main`.

## Operar asuka

Desde `deteccion-anuncios-test\modelo` en asuka:

- Estado: `powershell -NoProfile -ExecutionPolicy Bypass -File asuka_estado.ps1`
- Arrancar lo que falte / relanzar tras `git pull`: `... -File asuka_arrancar.ps1 [-Reiniciar]`
  (captura en prioridad normal, web por debajo de lo normal, ciclo en baja; la captura nunca se
  relanza si ya corre).

## Archivos (`modelo/`)

| Archivo | Qué hace |
|---|---|
| `capture.py` | Graba la radio en clips de 10 s (`--minutes 0` = sin límite). |
| `store.py`, `audio.py` | Base SQLite y lectura de audio continuo. |
| `fingerprint.py` | Huella acústica tipo Shazam. |
| `segments_repeat.py` | Encuentra audio que se repite en momentos distintos. |
| `catalogo.py` | Catálogo: construir, escanear, afinar, heredar, caducar, calibrar, evaluar. |
| `ciclo_catalogo.py` | Bucle del servidor: repetición, catálogo, escaneo, afinado, respuestas, transcripción (`--extras`: huecos y evaluación). |
| `revisar.py` | Web mínima: un audio, Sí o No. Las respuestas las aplica el ciclo. |
| `ia_transcribir.py` | Transcribe las entradas pendientes (faster-whisper); lo llama el ciclo. |
| `ia_pendientes.py` | Saca los textos pendientes para clasificarlos (paso 1). |
| `ia_aplicar.py` | Guarda veredictos de la IA (fuente `ia`); el ciclo los aplica sin extenderlos al grupo. |
| `cobertura.py` | Mide cuánto de cada bloque tapan los confirmados. |
| `en_vivo.py`, `exportar_vivo.py` | Piloto del silenciador en vivo (retraso de 45 s). |
| `asuka_arrancar.ps1`, `asuka_estado.ps1` | Arranque con prioridades y parte de estado en asuka. |

Requisitos: `pip install -r requirements.txt` (más `ffmpeg` en el PATH para grabar).
`docs/PROGRESO.md` es el historial de las primeras pruebas (una app Docker ya retirada).
