# Pruebas sueltas (6-nov-2026)

Scripts de comprobación que se usaron para llegar a las conclusiones del README. Reciben las rutas
por argumentos; algunos llevan rutas de asuka dentro.

| Script | Qué comprueba |
|---|---|
| `test_vivo.py` | Silenciador sin conexión: mete anuncios conocidos entre radio normal y mide cuánto tapa (salió 100 %, ±1 s). |
| `pipe_vs_clips.py` | En asuka: el directo leído como `en_vivo.py` frente a la captura, misma huella (36 % de hashes alineados). |
| `grabar_pc.py`, `diag_vivo.py` | En el PC: graba el directo y mira coincidencias con los confirmados (3 %, por debajo del umbral: con otro ffmpeg la huella cambia). |
| `ritmo.py` | Emisiones por día de cada anuncio confirmado (mediana 3,3) y cuándo apareció por primera vez. |
| `crecimiento.py` | Entradas nuevas por día, memoria que ocuparían y espacio de los audios. |
| `duraciones.py` | Duración de los Sí frente a los No (ningún Sí del usuario bajo 8,6 s: de ahí el mínimo de 6 s). |
