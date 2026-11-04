# Detección de anuncios (Cadena SER) — banco de pruebas

Objetivo: aprender a reconocer los bloques de publicidad de Cadena SER para silenciarlos.
El método actual es un **catálogo de anuncios que se aprende solo**: el audio que se repite
a lo largo del día (cuñas, promos) entra al catálogo; el catálogo se busca en el audio nuevo
y, si dos anuncios conocidos rodean un hueco corto, el hueco también se propone.

Todo está en `modelo/`:

| Archivo | Qué hace |
|---|---|
| `capture.py` | Graba la radio en clips de 10 s (`--minutes 0` = sin límite). |
| `store.py`, `audio.py` | Base SQLite y lectura de audio continuo. |
| `fingerprint.py` | Huella acústica tipo Shazam. |
| `segments_repeat.py` | Encuentra audio que se repite en momentos distintos. |
| `catalogo.py` | Catálogo: construir, escanear, huecos, caducar, calibrar, evaluar. |
| `ciclo_catalogo.py` | Bucle cada 30 min: repetición, catálogo, escaneo, afinado, huecos y limpieza (para dejarlo en un servidor). |
| `revisar.py` | Web mínima: un anuncio recortado, Sí o No (`python revisar.py`, solo en 127.0.0.1). Las respuestas las aplica el ciclo. |

Requisitos: `pip install -r requirements.txt` (más `ffmpeg` en el PATH para grabar).
`docs/PROGRESO.md` es el historial de las primeras pruebas (usaba una app Docker
que ya se retiró; sigue en el historial de git).
