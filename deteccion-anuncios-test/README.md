# Detección de anuncios (Cadena SER) — banco de pruebas

Objetivo: reconocer los bloques de publicidad de Cadena SER y silenciarlos enteros.
El método es un **catálogo de anuncios que se aprende solo**: el audio que se repite a lo
largo del día (cuñas, promos) entra al catálogo, el catálogo se busca en el audio nuevo y
cada entrada se revisa una vez (Sí = anuncio, No = radio o promo de la SER).

## Estado: en pausa desde el 2026-11-06

- **Todo parado.** asuka ya no graba ni aprende (se paró con `asuka_parar.ps1`) y en el PC no
  corre nada.
- **Datos guardados** en el repo privado `tronarite/RadioBlock-datos` (bases, audio de las 1.390
  entradas, logs y veredictos), y copia completa en el PC en `modelo/data/archivo-2026-11-06/`
  (git la ignora), que además tiene la grabación de la SER del 4 al 6-nov (7,7 GB). Su README
  explica cómo restaurarlo.
- **Catálogo al parar**: 280 anuncios confirmados, 826 en lista negra y 1.011 sin revisar. Los
  confirmados tapaban el **33 %** del tiempo de los bloques; casi todo lo demás eran entradas
  sin revisar (solo un 3 % sin ninguna entrada).
- **Ritmo medido**: cada anuncio suena ~3 veces al día, así que una campaña entra al catálogo en
  1-2 días; los anuncios de entre semana estaban casi todos en las primeras 24 h.
- **Si se retoma dentro de años**, los anuncios serán otros: el catálogo de 2026 solo sirve de
  referencia. Lo práctico es volver a grabar (paso 0) y seguir.
- **Reglas que ya funcionan**: nada de menos de 6 s entra al catálogo (sintonías, la hora); un
  "No" vale para todas las copias de ese audio; las respuestas nuevas heredan las anteriores.
- **Silenciador en vivo** (`en_vivo.py`): con audio de asuka tapa al 100 % los anuncios conocidos
  y rellena el hueco entre dos seguidos (±1 s). **En el PC no reconoce nada**: el mismo directo
  da otra huella con el ffmpeg 9 del PC (3 % de hashes alineados, umbral 10 %); en asuka, con
  ffmpeg 7.1, da 36 %. Por eso tiene que ir en asuka.

## Lo que falta: 4 pasos (y uno previo)

0. **Volver a ponerlo en marcha.** En asuka (u otra máquina Windows con Python, el venv de
   `requirements.txt` y ffmpeg 7.1; ojo: con otro ffmpeg la huella cambia) arrancar con
   `asuka_arrancar.ps1` (lleva rutas de asuka: adaptarlas si es otra máquina) y dejarlo 3-4 días
   que incluyan un fin de semana. Para seguir con los datos de 2026, restaurar el archivo.
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
- Parar todo: `... -File asuka_parar.ps1`.

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
| `asuka_arrancar.ps1`, `asuka_estado.ps1`, `asuka_parar.ps1` | Arranque con prioridades, parte de estado y parada en asuka. |
| `pruebas/` | Comprobaciones sueltas del 6-nov (silenciador sin conexión, huella PC frente a asuka, ritmo de los anuncios). |

Requisitos: `pip install -r requirements.txt` (más `ffmpeg` en el PATH para grabar).
`docs/PROGRESO.md` es el historial de las primeras pruebas (una app Docker ya retirada).
