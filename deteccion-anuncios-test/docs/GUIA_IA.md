# RadioBlock — guía para la IA que termine el proyecto

Escrita el 2026-11-15 tras revisar todo el repositorio, los datos archivados del 4 al 6-nov y los
logs de asuka. Sustituye como plan de trabajo al «Lo que falta» de `../README.md` y a la sección 10
de `DISENO_PRODUCTO.md` (ese documento sigue valiendo como explicación del producto; donde
contradiga a esta guía, manda esta guía).

Está pensada para que un modelo menos capaz que el que la escribió pueda terminar el proyecto sin
tomar decisiones de diseño: las decisiones ya están tomadas aquí, con el dato que las justifica.

---

## 0. Cómo trabajar con esta guía

1. Lee enteras las secciones 0 a 5 antes de tocar nada. Después ejecuta las tareas de la sección 7
   **en orden**. No te saltes ninguna ni las mezcles.
2. Cada tarea tiene: *Objetivo*, *Archivos*, *Pasos*, *Comprobación* y *No tocar*. Una tarea está
   hecha solo cuando su comprobación pasa. Apunta el resultado (la salida real del comando) en la
   sección 9 y haz commit.
3. Si una comprobación no pasa tras dos intentos razonados, **para**: escribe en la sección 9 qué
   has probado y qué sale, y pregunta al usuario. No cambies un umbral ni una constante para que
   la comprobación pase.
4. Antes de programar una tarea, di en dos líneas qué vas a hacer y qué supones. Después, el
   mínimo código que la resuelva. No refactorices ni reformatees lo que la tarea no pide.
5. Informa al usuario en lenguaje llano: qué corre, dónde y qué ha cambiado. Quiere el proyecto
   **simple y ligero**: no añadas paneles, servidores, bases de datos, dependencias ni procesos
   que esta guía no pida. Si crees que hace falta algo más, propónlo y espera su respuesta.

### Reglas fijas

| Tema | Regla |
|---|---|
| Git | Todo cambio se sube a la rama `beta` (commit + push) sin que nadie lo pida. **Nunca** `merge` ni `push` a `main` sin que el usuario lo confirme en ese momento. |
| Repo público | El repo del código es público. No subas audio, bases de datos, logs con texto de anuncios ni claves. `deteccion-anuncios-test/modelo/data/` está en `.gitignore`: todo lo privado va ahí. |
| Datos | El archivo `modelo/data/archivo-2026-11-06/` es la única copia local completa. **Solo lectura**: nunca ejecutes nada que escriba o borre dentro. Trabaja sobre copias (tarea T0). |
| asuka | Está **parado** por decisión del usuario. Pide permiso antes de arrancar nada allí. Nunca dos capturas de la misma emisora. No toques `podcast-player` ni nada ajeno a RadioBlock. |
| Sistema | Crear tareas programadas, abrir puertos o cambiar ajustes de asuka lo hace el usuario o lo autoriza él de forma expresa en ese momento. |
| Claves | La clave de Gemini la crea el usuario y solo vive en `data/juez.env` de asuka. No la escribas en el chat, en un commit ni en un log. |
| Revisión manual | No existe. No añadas colas, botones ni páginas de revisión (decisión del usuario, dos veces). |
| Constantes | Las de `fingerprint.py`, `segments_repeat.py`, `catalogo.py` y `reglas.py` salen de mediciones. No las cambies sin repetir la medición que se indica junto a cada una. |

### Cuándo preguntar al usuario

Pregunta solo en estos casos; en todo lo demás, sigue la guía: (a) una comprobación no pasa tras
dos intentos; (b) hay que hacer algo en asuka; (c) hace falta la clave de Gemini; (d) el paso de
escuchar (T10) y el merge a `main` (T11); (e) algo de la guía contradice lo que ves en el código.

---

## 1. Qué es el producto

Escuchar una emisora de radio por internet sin anuncios. El usuario abre en su móvil o su PC una
dirección de su servidor (`http://<asuka>:8070/directo.mp3`) y oye la emisora con unos 60 s de
retraso y con los **bloques publicitarios enteros en silencio**. El sistema trabaja solo:

1. **Descubre** qué audio se repite a lo largo del día (cuñas, promos, sintonías) y lo guarda en
   un catálogo de huellas acústicas.
2. **Juzga** cada entrada del catálogo: transcribe su audio y un modelo de lenguaje gratuito
   decide si es un anuncio o es programa.
3. **Silencia** en directo: reconoce los anuncios del catálogo en lo que está sonando y, como va
   60 s por detrás del directo, le da tiempo a silenciarlos desde su primer segundo.
4. **Se corrige**: caduca lo que deja de sonar, degrada lo que se comporta como programa y tiene
   límites de seguridad.

No hay revisión humana. Uso personal: solo se sirve en la red privada del usuario (Tailscale).

---

## 2. Decisiones cerradas

No las reabras. Si una resulta imposible, para y pregunta.

| # | Decisión | Fecha |
|---|---|---|
| 1 | Se silencia el **bloque** entero; separar cuñas es solo una ayuda interna para aprender. | 22-oct |
| 2 | Método: catálogo por repetición con huella acústica (tipo Shazam). No clasificador acústico, no clustering por timbre. | 5-nov |
| 3 | El juez usa solo modelos gratuitos. No se paga ninguna API. | 13-nov |
| 4 | No hay revisión manual: ni cola, ni botones, ni válvula de corrección. | 13-nov y 15-nov |
| 5 | Corre en asuka y se escucha por Tailscale. | 13-nov |
| 6 | Retraso de ~60 s. Bloque en silencio total (queda la opción `--atenuar`). | 13-nov |
| 7 | **Varias emisoras por archivo**: se termina con Cadena SER, pero añadir otra debe ser una entrada en `radios.py` y otro proceso de servicio. Sin panel de gestión. | 15-nov |
| 8 | **Juez en producción: clave gratuita de Gemini** (Google AI Studio), guardada en asuka. | 15-nov |
| 9 | **Promos e indicativos de la propia emisora**: son «programa», pero si quedan **entre dos anuncios** salen mudos con el bloque. Fuera de un bloque suenan. | 15-nov |
| 10 | Se retira `backend/` (arquitectura antigua) y las herramientas de revisión manual. | 13-nov |
| 11 | Audio de salida: MP3 mono a 22 050 Hz (lo mismo que se analiza). Mejorar la calidad es una tarea opcional posterior (T12) y la decide el usuario. | 15-nov |

---

## 3. Estado verificado el 2026-11-15

Todo lo de esta sección se comprobó ejecutándolo, no leyendo documentos.

### 3.1 Qué funciona

| Pieza | Archivo | Estado |
|---|---|---|
| Grabar la emisora en clips de 10 s | `capture.py` | Funciona (45 h grabadas sin cortes). Será absorbido por `servicio.py`. |
| Huella acústica y alineación | `fingerprint.py` | Funciona. No tocar. |
| Encontrar audio repetido | `segments_repeat.py` | Funciona. No tocar. |
| Catálogo: alta, escaneo, afinado, división de bloques, herencia | `catalogo.py` | Funciona, con los defectos de 3.3. |
| Bucle del servidor | `ciclo_catalogo.py` | Funciona, con los defectos de 3.3. |
| Transcripción | `ia_transcribir.py` | Funciona (faster-whisper `small`, CPU). |
| Juez automático | `juez.py` | Funciona contra OmniRoute en el PC. Prueba a ciegas: 27-28 de 30 anuncios, ningún falso anuncio. |
| Silenciador (piloto) | `en_vivo.py` | Funciona en la propia máquina (`ffplay`). Sin conexión tapó el 100 % de los anuncios conocidos, bordes a ±1 s. |
| **Reglas de silenciado** | `reglas.py` | **Nuevo (15-nov), hecho y probado.** Funciones puras; ver 6.3. |
| Simulador de reglas | `pruebas/simular_reglas.py` | **Nuevo (15-nov).** Da las cifras de 3.2. |

### 3.2 Cifras de referencia (archivo del 6-nov, 43,8 h de Cadena SER)

Sirven para saber si has roto algo. Comandos desde `modelo/`:

```
python reglas.py
  -> reglas: autoprueba correcta

python cobertura.py --bd data/archivo-2026-11-06/bd/catalogo.db
  -> entradas: activo+confirmado 280, activo sin confirmar 1011, lista_negra 826, caducado 53
  -> bloques con algun confirmado: 123; tiempo en bloques reales 225.3 min:
     confirmados 33%, sin revisar 64%, sin cubrir 3%

python pruebas/simular_reglas.py --bd data/archivo-2026-11-06/bd/catalogo.db --ocultar 0.5
  -> 14915 detecciones en 43.8 h: A=1807 P=9218 N=3890
  -> solo anuncios + relleno: mudo 1.80 min/h; bloques reales mudos 35%; programa conocido mudo 0.03 min/h
  -> reglas completas:        mudo 3.59 min/h; bloques reales mudos 70%; programa conocido mudo 0.05 min/h;
     tramo mas largo 3.7 min
  -> con el 50% de las etiquetas escondidas: anuncios escondidos que salen mudos 86%;
     programa escondido silenciado 9% (0.17 min/h)
```

Lectura: con solo un tercio del catálogo juzgado, las reglas nuevas ya dejan mudo el 70 % del
tiempo de bloque (el piloto dejaba el 35 %). El coste medido es de unos 10 s por hora de sintonías
o promos silenciadas de más. Lo que falta hasta el objetivo lo tiene que poner el juez (T3 y T4).

Otras medidas del 15-nov:

- Índice en memoria con todo el catálogo (2.117 entradas, 3,3 M de hashes): **383 MB** y 12 s de
  carga. Solo anuncios: 47 MB.
- `store.clips_for_range` tarda **28 ms por llamada** con 16.257 clips, porque lee y convierte
  todas las filas cada vez. Una vuelta del ciclo la llama unas 20.000 veces.
- Regla de conducta (6.6): sobre 152 anuncios confirmados con 6 o más emisiones, degradaría a 1.

### 3.3 Defectos encontrados en esta revisión

Cada uno tiene su tarea en la sección 7.

| # | Defecto | Prueba | Tarea |
|---|---|---|---|
| D1 | **657 de las 847 pendientes de ≥ 6 s no tienen audio** (`catalog.wav` vacío), así que nunca se transcriben ni se juzgan. El audio solo lo escribe `afinar()` cuando consigue afinar: 357 tienen menos de 3 emisiones y otras 300 tienen 3 o más pero el afinado falla. `DISENO_PRODUCTO.md` dice que «asuka se paró antes de hacerlas»: es incorrecto, no se habrían hecho nunca. Es la causa de que el 64 % del tiempo de bloque siga «sin revisar». | Consulta sobre `catalogo.db` | T3 |
| D2 | `clips_for_range` recorre todos los clips en cada llamada (coste que crece con los días grabados). | 28 ms/llamada | T2 |
| D3 | `afinar_pendientes` reintenta **en cada vuelta** las ~300 entradas cuyo afinado falla, cargando hasta 40 emisiones de audio de cada una. | Lectura del código + D1 | T2 |
| D4 | `agrupar()` compara todas las pendientes entre sí en cada vuelta (≈ 500.000 alineaciones con 1.011 pendientes) y solo servía a la web de revisión manual. | Lectura del código | T2 |
| D5 | `construir()` vuelve a sacar la huella de **todos** los tramos de `segments` en cada vuelta, también los antiguos; `segments` y `detections` crecen sin límite. | Lectura del código | T2 |
| D6 | Un veredicto `dudosa` no se vuelve a mirar nunca, y la transcripción no se rehace cuando el afinado cambia el audio. | `juez.py`, `ia_transcribir.py` | T4 |
| D7 | El catálogo no guarda quién decidió cada entrada ni cuándo (hace falta para la autocorrección y la página de estado). | Esquema de `catalog` | T4 |
| D8 | `caducar()` no caduca lo confirmado ni la lista negra, y calcula la referencia mezclando emisoras. `last_seen` solo se actualiza en entradas activas. | `catalogo.py` | T5 |
| D9 | **Umbral en directo**: `catalogo.umbral` pide el 10 % de los hashes de la entrada. En una ventana de 15 s solo cabe un trozo de una entrada larga, así que una entrada de más de ~45 s **no puede reconocerse en directo**. Hay 84 pendientes y 2 anuncios por encima de 45 s. | Cálculo + consulta | T6 |
| D10 | `juez.py` apunta por defecto al OmniRoute del PC (`127.0.0.1:20128`), que en asuka no existe. | `juez.py` | T4 |
| D11 | `requirements.txt` no incluye `faster-whisper` (lo usa `ia_transcribir.py`). | `requirements.txt` | T0 |
| D12 | El ciclo borra los clips de más de N días **contando desde hoy**: si se ejecuta sobre datos antiguos (el archivo), borra todo el audio. | `limpiar_clips` | T1 |
| D13 | `ia_transcribir.py`, `cobertura.py` y `caducar()` no distinguen emisora. | Lectura del código | T5 |

---

## 4. Cómo funciona lo que ya existe

Todo el código vigente está en `deteccion-anuncios-test/modelo/`. Lo que hay en `backend/`,
`docker-compose.yml` y `radio-adblocker-spec.md` de la raíz es una arquitectura anterior: no se
usa y se retira en T11. `docs/PROGRESO.md` y `docs/validacion.md` describen una app Docker ya
retirada; `docs/DISENO_MODELO.md` describe un clasificador que se descartó. Solo sirven de historia.

### 4.1 Mapa de archivos y destino

| Archivo | Qué hace | Destino |
|---|---|---|
| `fingerprint.py` | `fingerprint(pcm, sr) -> {hash: frame}`; `align(a, b) -> (inliers, rango_a_s, rango_b_s)`. | Se queda igual |
| `store.py` | SQLite: tablas `clips`, `segments` (y otras antiguas sin uso). `SAMPLE_RATE=22050`, `CLIP_SECONDS=10`. | Cambia (T2) |
| `audio.py` | `load_range(c, radio, t0, t1)`: PCM continuo a partir de los clips; huecos = ceros. | Se queda igual |
| `segments_repeat.py` | `find_segments`: ventanas de 40 s cada 20 s; pares de ventanas con ≥ 60 hashes alineados y separadas ≥ 120 s → tramos en `segments` con `group_key`. | Se queda igual |
| `catalogo.py` | `construir`, `escanear`, `guardar_detecciones`, `afinar`, `dividir`, `heredar`, `caducar`, `Indice`, `votar`. | Cambia (T2, T3, T5, T8) |
| `ciclo_catalogo.py` | Bucle del servidor; único proceso que escribe `catalogo.db`. | Cambia (T1-T5, T8) |
| `ia_transcribir.py` | Transcribe `data/entradas/<id>.wav` a `transcripciones.db`. | Cambia (T3) |
| `juez.py` | Regla del reloj + modelo de lenguaje → `respuestas.db`. | Cambia (T4) |
| `reglas.py` | Reglas de silenciado (puras). | Hecho; no tocar sin simular |
| `cobertura.py` | Informe: cuánto de cada bloque tapan los confirmados. | Cambia (T5: `--radio`) |
| `radios.py` | Lista de emisoras (clave, nombre, URL). | Cambia (T5: `puerto`) |
| `capture.py` | Graba con `ffmpeg -f segment`. | Se retira en T11 (lo absorbe `servicio.py`); `find_ffmpeg` se muda |
| `en_vivo.py`, `exportar_vivo.py` | Piloto del silenciador. | Se retiran en T11; su lógica pasa a `servicio.py` |
| `revisar.py`, `ia_pendientes.py`, `ia_aplicar.py` | Revisión manual y por IA a mano. | Se retiran en T11 |
| `asuka_*.ps1` | Arranque, estado y parada en asuka. | Cambian (T9) |
| `pruebas/` | Comprobaciones sueltas del 6-nov y `simular_reglas.py`. | Se queda `simular_reglas.py`; lo demás se retira en T11 |

### 4.2 Datos (todo bajo `modelo/data/`, que git ignora)

| Ruta | Quién escribe | Contenido |
|---|---|---|
| `clips/<radio>/AAAAMMDD_HHMMSS.wav` | la captura | Audio en trozos de 10 s, mono, 22.050 Hz, 16 bits |
| `modelo.db` → tabla `clips` | la captura | `radio`, `timestamp` (ISO, hora local, inicio del clip), `path` (relativo a `clips/`) |
| `catalogo.db` | **solo el ciclo** | Copia de `clips`, más `segments`, `catalog` y `detections` |
| `respuestas.db` → `respuestas` | el juez (y el ciclo marca `aplicada`) | `catalog_id`, `resp` (`si`/`no`/`dudosa`), `ts`, `aplicada`, `fuente` |
| `transcripciones.db` → `t` | `ia_transcribir.py` | `clave` (`e<id>`), `texto` |
| `entradas/<id>.wav` | el ciclo | Audio recortado de cada entrada del catálogo |

Hay dos bases separadas (`modelo.db` y `catalogo.db`) para que la captura y el ciclo no se
bloqueen entre sí. El ciclo copia los clips nuevos de una a otra al empezar cada vuelta
(`sincronizar_clips`).

**Tabla `catalog`** (una fila = un audio que se repite):

| Columna | Significado |
|---|---|
| `huella` | Huella acústica (blob). `fingerprint.bytes_to_fingerprint` la convierte en `{hash: frame}` |
| `n_hashes`, `dur` | Tamaño de la huella y duración en segundos |
| `estado` | `activo`, `lista_negra` o `caducado` |
| `confirmado` | 1 = tiene veredicto firme |
| `t_origen` | Epoch del audio del que salió |
| `last_seen`, `n_matches` | Última vez que sonó y cuántas emisiones se han detectado |
| `refinado` | Emisiones usadas en el último afinado (0 = sin afinar) |
| `wav` | No vacío = existe `entradas/<id>.wav` |
| `grupo`, `dispersion` | Restos de la revisión manual; no usar |

**Las tres clases** (función `reglas.clase(estado, confirmado)`): todo el diseño habla de ellas.

| Clase | Nombre | En la tabla | Qué hace el silenciador |
|---|---|---|---|
| **A** | anuncio | `estado='activo'`, `confirmado=1` | Mudo siempre |
| **P** | programa | `estado='lista_negra'` | Suena, salvo si queda entre dos anuncios |
| **N** | pendiente (nueva o dudosa) | `estado='activo'`, `confirmado=0` | Mudo solo si toca un tramo mudo |
| — | caducada | `estado='caducado'` | No existe para nadie |

**Tabla `detections`**: cada vez que una entrada suena (`catalog_id`, `t_start`, `t_end` en epoch,
`inliers`). Hay detecciones de entradas A, P y N.

### 4.3 Una vuelta del ciclo, tal como está hoy

`ciclo_catalogo.vuelta()`:

1. `sincronizar_clips` — copia a `catalogo.db` los clips nuevos de `modelo.db`.
2. `aplicar_respuestas` — pasa a `catalog` las filas de `respuestas.db` con `aplicada=0`:
   `si` → `confirmado=1`; `no` → `estado='lista_negra', confirmado=1` y además
   `propagar_negra` (marca como No las pendientes que son ese mismo audio); `dudosa` → nada.
3. `limpiar_clips` — borra clips viejos (archivo y filas).
4. `segments_repeat.find_segments` — busca repeticiones en las últimas 36 h.
5. `catalogo.construir` — da de alta como entrada cada tramo de un grupo con 3 o más
   repeticiones, de entre 6 y 90 s, si no es el mismo audio que una entrada existente.
6. `catalogo.caducar`.
7. `catalogo.escanear` + `guardar_detecciones` — busca todas las entradas en las últimas 36 h.
8. `catalogo.afinar_pendientes` — con 3 o más emisiones recorta la entrada a lo que coincide
   en el 65 % de ellas; si varias emisiones empiezan o acaban en un punto interior con pausa,
   **divide** la entrada en varias (la original pasa a `caducado`). Da de baja las pendientes
   que quedan por debajo de 6 s.
9. `catalogo.heredar` — una pendiente cuyo audio es (≥ 60 %) el de una entrada ya juzgada
   copia su veredicto.
10. `ia_transcribir.py` (subproceso) y `juez.py` (subproceso).
11. `catalogo.agrupar` (se retira en T2).

### 4.4 El piloto del silenciador (`en_vivo.py`)

Tres hilos: `leer` (ffmpeg → PCM al búfer `Directo`), `analizar` (cada 2,5 s saca la huella de los
últimos 15 s, `catalogo.votar` contra el índice, `fingerprint.align` para saber qué trozo coincide,
lo apunta en `Silencios`) y el hilo principal (saca el audio con 45 s de retraso, multiplicado por
una ganancia 0/1 con un fundido de 50 ms, hacia `ffplay`). Solo conoce los anuncios confirmados y
solo aplica la regla del relleno. Su lógica es correcta y se reutiliza en `servicio.py` (T6).

---

## 5. Trampas conocidas

Léelas todas. Cada una costó horas descubrirla.

1. **La huella depende de la decodificación.** El mismo directo decodificado por dos versiones de
   ffmpeg da huellas que casi no coinciden (3 % de hashes alineados con ffmpeg 9 en el PC frente a
   36 % en asuka con ffmpeg 7.1; el umbral es 10 %). Consecuencias: (a) el catálogo de asuka **no
   reconoce nada en un directo capturado en el PC**; (b) en producción, lo que se graba y lo que se
   reconoce en vivo tiene que salir de **un solo proceso ffmpeg**; (c) si se actualiza el ffmpeg de
   asuka, el catálogo deja de valer y se rehace solo en 1-2 días. No actualices ffmpeg en asuka.
2. **36 % es lo normal, no un fallo.** Dos huellas del mismo audio nunca coinciden al 100 %: el
   umbral de picos de `fingerprint.py` es global a la ventana analizada, así que cambia según qué
   más haya en ella. Por eso los umbrales son bajos (10 % de la entrada, mínimo 25).
3. **`RB_DB`.** `store.py` abre `data/modelo.db` salvo que la variable `RB_DB` apunte a otra base.
   `ciclo_catalogo.py` la fija a `catalogo.db` al importarse. Si ejecutas a mano algo que importe
   `store` o `catalogo` sin esa variable, crearás tablas en `modelo.db`. Para pruebas a mano:
   `set RB_DB=...\data\catalogo.db` (cmd) o `$env:RB_DB = "..."` (PowerShell).
4. **Solo el ciclo escribe `catalogo.db`.** El juez y el servicio la abren en solo lectura
   (`file:...?mode=ro`). Todo lo que otro proceso quiera cambiar va por `respuestas.db`.
5. **El archivo es de solo lectura y el ciclo borra audio** (D12). Mientras no hagas T1, no
   ejecutes el ciclo sobre datos antiguos. Después de T1, en pruebas, siempre `--retener-dias 0`.
6. **Las horas.** `clips.timestamp` es hora local sin zona. Todo lo demás (`t_origen`,
   `detections`) es epoch. La conversión es `datetime.fromisoformat(ts).timestamp()`. En el cambio
   de hora de otoño una hora se repite: ese rato el audio se solapa. Limitación aceptada; no la
   arregles.
7. **Los márgenes.** La huella «llega tarde» al principio de un audio y «acaba antes» del final.
   Por eso `PAD_INI_S=1.0` y `PAD_FIN_S=0.8` en `catalogo.py` (y 2,5/1,5 en `segments_repeat.py`).
   No los quites: sin ellos se oye el primer segundo de cada anuncio.
8. **`votar` tolera ±1 frame y `align` no.** `catalogo.votar` suma los desfases vecinos;
   `fingerprint.align` exige el mismo desfase exacto, así que da menos coincidencias que `votar`
   para el mismo par. Es una de las razones por las que el afinado falla en entradas que sí se
   detectan (D1). No lo «arregles»: T3 hace que el juicio no dependa del afinado.
9. **Windows.** asuka es Windows Server con shell `cmd`. Los procesos se lanzan con
   `Invoke-CimMethod` para que sobrevivan al cierre del ssh. Al buscar procesos por su línea de
   comandos, excluye el tuyo (`$_.ProcessId -ne $PID`): una vez un script se mató a sí mismo.
10. **Consola en cp1252.** Un `print` con un carácter fuera de cp1252 (por ejemplo `∩` o `→`)
    rompe el script en Windows. En el código, mensajes en ASCII, como el resto de `modelo/`.
11. **`catalog.wav` guarda rutas absolutas de asuka** en los datos antiguos. Nadie lee el contenido
    de la columna, solo si está vacía; el audio se busca siempre como `data/entradas/<id>.wav`.
    Desde T3 se guarda solo `<id>.wav`.
12. **`aplicar_respuestas` con `fuente='usuario'`** además propaga por `grupo`. Ya no habrá
    respuestas de usuario nuevas; no uses esa fuente para nada.
13. **El juez tarda una vuelta en hacer efecto**: escribe en `respuestas.db` y el ciclo lo aplica.
    T4 añade una segunda llamada a `aplicar_respuestas` justo después del juez.
14. **Memoria del índice.** Cargar todo el catálogo cuesta ~380 MB hoy y crecerá. El servicio
    carga solo lo que ha sonado en los últimos 7 días (6.4) y, al recargar, tiene dos índices a
    la vez durante unos segundos.
15. **SQLite ocupado.** Mientras el ciclo confirma una escritura, un lector puede recibir
    `database is locked`. Abre siempre con `timeout=30` y, en el servicio, si la recarga del
    índice falla, conserva el índice anterior y reinténtalo en la siguiente.
16. **`pruebas/` con rutas de asuka.** Varios scripts de `pruebas/` llevan rutas fijas de asuka.
    No son parte del producto.

---

## 6. Diseño final

### 6.1 Arquitectura

Por emisora, un proceso **servicio**. Para todas, un proceso **ciclo**. Una carpeta de datos.

```
emisora --ffmpeg (una sola decodificacion)--> PCM mono 22050 Hz
                                                |
                          SERVICIO (prioridad normal, uno por emisora)
                          |- graba clips de 10 s  -> data/clips/<radio>/ + modelo.db
                          |- Motor: huella cada 2,5 s -> detecciones A/P/N -> reglas.planificar
                          |         -> audio con 60 s de retraso y los tramos mudos
                          |- ffmpeg codifica MP3 -> HTTP  /directo.mp3  /  /estado.json  /
                          '- recarga el indice de catalogo.db (solo lectura) cada 10 min

                          CICLO (prioridad baja, cada 2 h, una emisora tras otra)
                          repeticion -> catalogo -> escaneo -> afinado -> audio de entradas
                          -> herencia -> conducta -> transcripcion -> JUEZ -> caducidad -> poda
                                                |
                                          catalogo.db (solo escribe el ciclo)
```

- El servicio **nunca espera al ciclo**. Si el ciclo se cae, el servicio sigue silenciando con el
  último catálogo que cargó.
- El servicio **sustituye a `capture.py`**: nunca corren los dos para la misma emisora.
- Procesos finales en asuka con una emisora: `servicio.py --radio cadena_ser` y
  `ciclo_catalogo.py --radios cadena_ser`. Nada más (ni `capture.py`, ni `revisar.py`).

### 6.2 Emisoras (`radios.py`)

Cada emisora es un diccionario con `key`, `nombre`, `url` y **`puerto`** (nuevo). Cadena SER: 8070.
Para añadir una emisora: una entrada con otro puerto, otra línea de servicio en
`asuka_arrancar.ps1` y su clave en `--radios` del ciclo. Nada más debe hacer falta. Todas las
tablas llevan ya la columna `radio` y los identificadores de `catalog` son únicos entre emisoras.

### 6.3 Reglas de silenciado (`reglas.py`, ya escrito)

`reglas.planificar(dets)` recibe detecciones `(ini, fin, clase)` y devuelve los tramos mudos:

1. Todo tramo **A** sale mudo.
2. **Relleno**: el hueco entre dos tramos A de hasta `RELLENO_S = 20` s sale mudo, suene lo que
   suene (una cuña que aún no está en el catálogo, o una promo entre dos anuncios).
3. **Puente**: un hueco mayor, de hasta `PUENTE_MAX_S = 40` s, sale mudo solo si está ocupado por
   entradas conocidas (P o N) sin dejar más de `PEGADO_S = 5` s sin explicar. Es la decisión 9.
4. **Borde**: una **N** a `PEGADO_S` o menos de un tramo mudo sale muda con él. Un solo salto (no
   se encadena) y nunca por encima de una P.
5. `reglas.limitar`: ningún tramo mudo dura más de `MAX_MUDO_S = 480` s.

Por qué estos valores (simulación del 15-nov sobre 43,8 h, escondiendo la mitad de las etiquetas):
sin la regla 4, el 67 % de los anuncios escondidos sale mudo; con ella, el 86 %, a cambio de
silenciar el 9 % del programa escondido (0,17 min/h, sintonías y promos). Encadenar saltos, o
cambiar `PEGADO_S` entre 2 y 10 s, `RELLENO_S` entre 10 y 30 s o `PUENTE_MAX_S` entre 40 y 60 s,
no cambia el resultado de forma apreciable: no merece la pena afinarlos.

Restricción: `PUENTE_MAX_S + ventana (15 s) + salto (2,5 s) <= retraso (60 s)`. Si se baja el
retraso, hay que bajar `PUENTE_MAX_S`.

### 6.4 El servicio (`servicio.py`, nuevo)

Dos partes. La difícil es la primera, y no lleva hilos.

**A. `Motor`: núcleo síncrono.** Recibe PCM y devuelve PCM retrasado y silenciado. Sin hilos, sin
red, sin reloj, sin base de datos: se puede probar con un archivo.

```python
SR = store.SAMPLE_RATE
WIN_S, HOP_S = 15.0, 2.5        # ventana de reconocimiento y cada cuanto se analiza (como en_vivo.py)
BLOQUE_S, FUNDIDO_S = 0.5, 0.05
RETRASO_S = 60.0
FUSIBLE_S = 25 * 60             # maximo de silencio en cualquier hora seguida

def umbral_vivo(indice, eid):
    """Como catalogo.umbral, pero contando solo lo que cabe de la entrada en la ventana (defecto D9)."""
    huella, dur, _clase = indice.entradas[eid]
    return max(catalogo.MIN_INLIERS_ABS,
               int(catalogo.FRAC_ENTRADA * len(huella) * min(1.0, WIN_S / max(dur, 1e-6))))

class Motor:
    def __init__(self, indice, retraso_s=RETRASO_S, atenuar=0.0, al_cambiar=None):
        self.indice = indice        # catalogo.Indice; el tercer dato de cada entrada es su clase 'A'|'P'|'N'
        self.buf = np.zeros(0, np.int16)
        self.base = 0               # muestra absoluta de buf[0]
        self.fin_ventana = int(WIN_S * SR)   # muestra en la que acaba la proxima ventana a analizar
        self.pos = 0                # proxima muestra que sale
        self.dets = []              # [ini_s, fin_s, eid, clase], en segundos desde el arranque
        self.plan = []              # tramos mudos vigentes (salida de reglas)
        ...
    def procesar(self, datos: bytes) -> bytes:
        """Anade PCM int16 y devuelve el PCM que ya puede salir (puede ser b'')."""
        # 1. anadir al bufer
        # 2. mientras haya una ventana completa: self._analizar(); self.fin_ventana += salto
        # 3. mientras self.pos + bloque + retraso <= muestras recibidas: salida += self._bloque()
        # 4. recortar el bufer hasta min(self.pos, self.fin_ventana - ventana)
    def cerrar(self) -> bytes:
        """Fin de la fuente (solo en pruebas): saca lo que queda, sin esperar al retraso."""
```

- `_analizar` es el cuerpo del bucle de `en_vivo.analizar`, con tres cambios: usa `umbral_vivo`;
  guarda la clase de la entrada en la detección; y después de añadir o alargar una detección
  recalcula `self.plan = reglas.limitar(reglas.planificar((a, b, cl) for a, b, _e, cl in self.dets))`.
  Dos detecciones de la misma entrada que se tocan (a 2 s o menos) son la misma: se funden (como
  `Silencios.anadir`). Borra las detecciones que acabaron hace más de 15 minutos de `self.pos`.
- `_bloque` es el cuerpo del bucle principal de `en_vivo.main` (ganancia por muestra, fundido con
  la convolución, tal cual). Cuando el estado pasa de sonar a mudo o al revés, llama a
  `al_cambiar(mudo, t_s, eids)`.
- **Fusible**: guarda los bloques mudos de la última hora; si suman más de `FUSIBLE_S`, ese bloque
  sale con sonido y se avisa una vez con `al_cambiar`. Es un seguro contra un catálogo estropeado.
- Los tiempos del `Motor` son **segundos desde el arranque** (muestra / SR). La hora real solo
  hace falta para los logs: la pone quien usa el `Motor`.

**B. Lo que rodea al `Motor`.** Todo es entrada/salida sencilla.

| Pieza | Qué hace |
|---|---|
| Ingesta | Un hilo. Lanza `ffmpeg ... -i <url> -vn -ac 1 -ar 22050 -f s16le -` (los mismos argumentos que `en_vivo.leer`), lee de 22.050 en 22.050 bytes (0,5 s) y por cada trozo llama, en este orden, a `grabador.anadir(trozo)` y `salida = motor.procesar(trozo)`, y manda `salida` al codificador. Si ffmpeg muere: espera (2, 4, 8... hasta 60 s) y lo relanza; **no** termina el proceso. Apunta en el log cada caída. |
| Hora real | `ancla = (muestras_recibidas, time.time())`, fijada cada vez que arranca ffmpeg. `epoch(m) = ancla_epoch + (m - ancla_muestra) / SR`. |
| Grabador | Acumula el PCM; cada 220.500 muestras escribe `data/clips/<radio>/AAAAMMDD_HHMMSS.wav` (16 bits) e inserta en `modelo.db` la fila de `clips` con `timestamp = datetime.fromtimestamp(epoch_del_primer_sample).isoformat(timespec="milliseconds")`. Si el nombre de archivo ya existe, suma 1 s al nombre (no al `timestamp`). Al reconectar descarta el trozo incompleto. |
| Índice | `cargar_indice(ruta_catalogo, radio, ahora)`: abre en solo lectura y carga las entradas A, P y N de esa emisora con `COALESCE(last_seen, t_origen) >= ahora - 7 días`, y de las N solo las de `dur >= 6`. Devuelve `catalogo.Indice({id: (huella, dur, clase)})`. Un hilo lo repite cada 10 minutos y hace `motor.indice = nuevo`. Si falla, deja el anterior. Si el catálogo aún no existe, índice vacío: el servicio suena sin silenciar nada. |
| Codificador | `ffmpeg -loglevel error -f s16le -ar 22050 -ac 1 -i - -c:a libmp3lame -b:a 64k -f mp3 -`. Un hilo lee su salida de 4.096 en 4.096 bytes y la reparte. |
| HTTP | `ThreadingHTTPServer` en `--host` (por defecto `127.0.0.1`) y el puerto de la emisora. Reparto con la clase `AudioBroadcaster` de `backend/app/worker/proxy.py`: **cópiala** a `servicio.py` antes de retirar `backend/`. Rutas: `/directo.mp3` (`Content-Type: audio/mpeg`), `/estado.json` y `/`. |
| Registro | Cada tramo mudo terminado se añade a `data/silencios_<radio>.csv`: `epoch_ini,epoch_fin,segundos,ids`. |
| Página `/` | Una sola página HTML, sin dependencias, que solo informa: un `<audio controls src="/directo.mp3">`, qué suena ahora (radio o silencio), minutos mudos hoy, entradas en el índice por clase, hora de la última recarga, los 20 últimos silencios y los 20 últimos veredictos del juez (id, veredicto, fuente y los primeros 80 caracteres del texto, leídos de `respuestas.db` y `transcripciones.db` en solo lectura). Sin botones. Se refresca sola cada 30 s leyendo `/estado.json`. |

Argumentos: `--radio`, `--host`, `--retraso`, `--atenuar`, y para pruebas `--fuente <carpeta de
clips>` (lee esos WAV en orden en vez de ffmpeg; con `--ritmo-real` espera 0,5 s por trozo para
poder escucharlo), `--desde`/`--hasta` (`AAAAMMDD_HHMMSS`), `--sin-grabar` y `--sin-http`.

### 6.5 El ciclo, orden final de una vuelta

Por cada emisora de `--radios`, en este orden:

| # | Paso | Cambio respecto a hoy |
|---|---|---|
| 1 | `sincronizar_clips` | — |
| 2 | `aplicar_respuestas` + `completar_fuente` | Guarda `fuente` y `t_veredicto` (T4) |
| 3 | `limpiar_clips` | `--retener-dias 0` = no borrar (T1); por defecto 3 días (T9) |
| 4 | `find_segments` | — |
| 5 | `construir` | — |
| 6 | `escanear` + `guardar_detecciones` | `last_seen` también para P (T5) |
| 7 | `afinar_pendientes` | No reintenta lo que ya falló (T2) |
| 8 | `completar_wavs` | **Nuevo** (T3) |
| 9 | `heredar` | Marca `fuente='herencia'`; salta las degradadas (T4, T8) |
| 10 | `revisar_conducta` | **Nuevo** (T8) |
| 11 | transcripción (subproceso) | Rehace si el audio cambió (T3) |
| 12 | juez (subproceso) y otra vez `aplicar_respuestas` | T4 |
| 13 | `caducar` | 30 días, todas las clases, por emisora (T5) |
| 14 | `podar` | **Nuevo** (T2) |
| 15 | `salud` | **Nuevo** (T8) |

Cada paso imprime cuánto ha tardado (T1). `agrupar`, los huecos y `--extras` desaparecen.

### 6.6 El juez y la autocorrección

**Juez** (`juez.py`). Para cada pendiente de ≥ 6 s con transcripción:

1. **Reloj**: con 6 o más emisiones y concentración del minuto de la hora ≥ 0,9 → `no`
   (`fuente='regla'`), sin mirar el texto. Ya existe.
2. **Modelo de lenguaje** con el prompt actual → `si`, `no` o `dudosa` (`fuente='ia'`). Ya existe.
   **No cambies el prompt** sin repetir la prueba a ciegas (T4): es el que dio 27-28 de 30.
3. Las `dudosa` siguen siendo clase N: solo se silencian si tocan un bloque.

Qué se juzga y cuándo: una entrada con último veredicto `si` o `no` no se vuelve a juzgar. Una
con último veredicto `dudosa` se vuelve a juzgar solo si su transcripción es posterior a ese
veredicto (porque el afinado cambió el audio).

Conexión: tres variables, que se leen del entorno o, si no están, del archivo `data/juez.env`
(líneas `CLAVE=valor`): `RB_JUEZ_URL`, `RB_JUEZ_KEY`, `RB_JUEZ_MODELO`. En asuka apuntan a la API de
Gemini compatible con OpenAI. Al escribir esta guía la URL base era
`https://generativelanguage.googleapis.com/v1beta/openai` (el juez le añade `/chat/completions`);
**compruébala en la documentación de Google** («OpenAI compatibility» de la API de Gemini) y elige
allí el modelo Flash gratuito vigente. En el PC, sin ese archivo, sigue usando OmniRoute.

*Comprobado el 2026-11-26 en la documentación de Google* (las páginas de ai.google.dev redirigen a
aistudio.google.com/docs): URL base `https://generativelanguage.googleapis.com/v1beta/openai/`
(docs/openai). Modelos Flash de texto gratuitos en la tabla de precios (docs/pricing): `gemini-3.8-flash`,
`gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-2.5-flash`, entre otros;
para el juez basta uno Flash o Flash-Lite. Los límites por minuto y por día del nivel gratuito no se
publican para estos modelos: se ven en AI Studio (docs/rate-limits); el juez ya espera 5 s entre lotes
de 40. Una suscripción de consumo (Google AI Plus/Pro) **no** da acceso a la API: la clave gratuita se
crea aparte en AI Studio (docs/google-ai-plans). Privacidad: en el nivel gratuito Google puede usar lo
enviado para mejorar sus productos, salvo en el EEE, Suiza y Reino Unido, donde se aplican las
condiciones de pago también a la cuota gratuita (docs/terms). Lo que se envía son transcripciones de
anuncios y cortes de radio, no datos personales. `juez.env` de asuka quedaría así (la clave la pone el
usuario):

```
RB_JUEZ_URL=https://generativelanguage.googleapis.com/v1beta/openai
RB_JUEZ_KEY=
RB_JUEZ_MODELO=gemini-3.8-flash
```

Antes de dar el juez por bueno con Gemini, repetir `pruebas/juez_ciegas.py` (T9, paso 5).

**Autocorrección** (dentro del ciclo):

| Mecanismo | Regla exacta |
|---|---|
| Conducta | Entrada A cuya `fuente` no es `usuario`, con 6 o más detecciones. De sus 20 últimas, una es *aislada* si ninguna detección de **otra** entrada activa (A o N) está a 15 s o menos de su inicio o su final; no cuentan como vecinas las que se solapan con ella en más de la mitad de la más corta (son copias del mismo audio). Si las aisladas son ≥ 80 % → vuelve a pendiente: `confirmado=0`, `fuente='conducta'`, y una fila `dudosa` en `respuestas.db` para que el juez no la repita. Medido: degradaría a 1 de 152 anuncios buenos. |
| Reloj tardío | Entrada A o N con 6 o más detecciones y reloj ≥ 0,9 → `estado='lista_negra'`, `confirmado=1`, `fuente='regla'`. |
| Caducidad | 30 días sin sonar → `caducado`, sea A, P o N. |
| Salud | Con al menos 3 días completos en `silencios_<radio>.csv`: si los minutos mudos de las últimas 24 h superan el doble de la mediana diaria de los 7 días anteriores **y** pasan de 30 min, las entradas A con `fuente='ia'` y `t_veredicto` en las últimas 24 h vuelven a pendiente (`fuente='salud'`, fila `dudosa`). Imprime una línea que empiece por `SALUD:`. |
| Fusible | En el servicio: nunca más de 8 min seguidos ni más de 25 min por hora en silencio. |

### 6.7 Límites que el diseño no resuelve

Díselo al usuario si pregunta; no intentes arreglarlos.

- Publicidad leída en directo por el locutor: no se repite, no se detecta.
- La primera o la última cuña de un bloque, las primeras veces que suena una campaña nueva: se
  cuela hasta que se ha repetido tres veces y el juez la ha visto (1-2 días).
- El primer día de una emisora nueva solo escucha. Nivel normal en 3-4 días (hace falta un fin de
  semana).
- Se oye unos 60 s por detrás del directo, en mono.
- Cada tanto se silenciará una sintonía o una promo pegada a un bloque (unos 10 s por hora).

---

## 7. Plan de tareas

Fase A (T0-T8): en el PC, sin conexión, con los datos archivados. Fase B (T9-T10): en asuka, con
permiso del usuario. Fase C (T11-T12): cierre.

Todos los comandos se ejecutan desde `deteccion-anuncios-test/modelo/` con el Python del entorno
virtual de T0, salvo que se diga otra cosa.

---

### T0 — Banco de pruebas en el PC

*Objetivo*: poder ejecutar todo sobre una copia de los datos del 6-nov.

*Pasos*:

1. Añade `faster-whisper` a `deteccion-anuncios-test/requirements.txt` (D11).
2. Crea el entorno: `python -m venv ..\.venv` y `..\.venv\Scripts\pip install -r ..\requirements.txt`.
   Si una versión fijada no existe para el Python instalado, instala la más cercana y anótalo en
   la sección 9; no cambies el archivo sin avisar.
3. Aparta lo antiguo que hay en `data/` (son restos de octubre): crea `data/antiguo-pc/` y mueve
   dentro `data/modelo.db`, `data/vivo.db`, `data/vivo_log.csv`, `data/clips/` y `data/models/`.
   No borres nada.
4. Copia (no enlaces ni muevas) desde `data/archivo-2026-11-06/`: `bd/catalogo.db`,
   `bd/respuestas.db`, `bd/transcripciones.db` y `bd/modelo.db` a `data/`; `entradas/` a
   `data/entradas/`; `grabacion/clips/` a `data/clips/` (6,7 GB; `robocopy <origen> <destino> /E`).
5. Guarda aparte una segunda copia de las tres bases en `data/base-t0/`: será el punto de partida
   para repetir pruebas (`copy data\base-t0\*.db data\`).

*Comprobación*: los tres comandos de 3.2, con `--bd data/catalogo.db`, dan exactamente las mismas
cifras. Además:

```
python -c "import os; os.environ['RB_DB']='data/catalogo.db'; import store, audio; c=store.conn2(); s=audio.radio_span(c,'cadena_ser'); print(round((s[1]-s[0])/3600,1)); print(float(abs(audio.load_range(c,'cadena_ser',s[0]+3600,s[0]+3610)).max())>0)"
  -> 45.2
  -> True
```

*No tocar*: nada dentro de `data/archivo-2026-11-06/`.

---

### T1 — Medir una vuelta del ciclo

*Objetivo*: saber cuánto tarda cada paso antes de optimizar, y poder ejecutar el ciclo sobre datos
antiguos sin que borre audio (D12).

*Archivos*: `ciclo_catalogo.py`.

*Pasos*:

1. En `limpiar_clips`, si `dias <= 0` devuelve 0 sin hacer nada. Actualiza la ayuda de
   `--retener-dias` («0 = no borrar»).
2. En `vuelta`, mide cada paso con `time.perf_counter()` e imprime una línea por paso con este
   formato exacto: `fase <nombre>: <segundos con un decimal> s`. Hazlo con una función auxiliar
   pequeña, no repitiendo el código.
3. Restaura las bases (`copy data\base-t0\*.db data\`) y ejecuta en segundo plano, guardando la
   salida: `python -u ciclo_catalogo.py --radio cadena_ser --una-vez --retener-dias 0 > data\vuelta-t1.log 2>&1`.
   Puede tardar más de una hora. No tienes configurado el juez aún: no importa, fallará solo ese
   paso o usará OmniRoute si está encendido.

*Comprobación*: `data\vuelta-t1.log` acaba en `vuelta terminada`, contiene una línea `fase` por
paso y **no** contiene `retencion:`. `dir data\clips\cadena_ser | find /c ".wav"` sigue dando
16.357 archivos. Copia la tabla de tiempos a la sección 9.

*No tocar*: la lógica de ningún paso.

---

### T2 — Quitar el trabajo inútil del ciclo

*Objetivo*: arreglar D2, D3, D4 y D5. Ninguno cambia el resultado, solo el tiempo.

*Archivos*: `store.py`, `catalogo.py`, `ciclo_catalogo.py`.

*Pasos*:

1. **D2**, en `store.clips_for_range`: pide a SQLite solo los clips del rango. Las fechas ISO se
   ordenan bien como texto y ya existe el índice `ix_clips_radio_ts`.

   ```python
   iso = lambda t: _dt.datetime.fromtimestamp(t).isoformat(timespec="seconds")
   rows = c.execute("SELECT * FROM clips WHERE radio=? AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
                    (radio, iso(t0 - CLIP_SECONDS - 1), iso(t1 + 1))).fetchall()
   ```

   El bucle que filtra por solape se queda igual.
2. **D3**, en `catalogo.py`: añade a `COLUMNAS_EXTRA` la columna `"intento": "INTEGER NOT NULL DEFAULT 0"`
   (emisiones que había la última vez que se intentó afinar). En `afinar_pendientes`, la condición
   pasa a `n_matches >= ? AND n_matches >= 2 * MAX(refinado, intento)`, y después de cada llamada
   a `afinar` (haya cambiado o no) haz `UPDATE catalog SET intento=<n_matches leído> WHERE id=?`.
   Así cada entrada se reintenta solo cuando sus emisiones se duplican.
3. **D4**: quita de `vuelta` la llamada a `catalogo.agrupar` y su `print`. No borres la función
   todavía (la usa la revisión manual; se va en T11).
4. **D5**, función nueva `catalogo.podar(c, radio, horas)`:
   `ref` = final del audio disponible (`audio.radio_span`). Borra de `segments` las filas de esa
   emisora con `source='repeat'`, `label IS NULL` y `t_end < ref - (horas + 12) * 3600`; borra
   todas las de `source='hueco'`; borra de `detections` las de esa emisora con
   `t_end < ref - 30 * 86400`. Llámala al final de la vuelta. Quita de `vuelta` y de `main` todo
   lo de `--extras` (huecos y evaluación).

*Comprobación*:

1. Antes y después del cambio 1, este valor debe ser idéntico:
   `python -c "import os; os.environ['RB_DB']='data/catalogo.db'; import store, audio, hashlib; c=store.conn2(); s=audio.radio_span(c,'cadena_ser'); print(hashlib.md5(audio.load_range(c,'cadena_ser',s[0]+5000.3,s[0]+5047.9).tobytes()).hexdigest())"`
2. Restaura las bases y repite la vuelta de T1 (`data\vuelta-t2.log`). Las líneas
   `[cadena_ser] ... ventanas, ... grupos repetidos` y `detecciones=... nuevas=...` deben coincidir
   con las de `vuelta-t1.log`. El tiempo total debe bajar. Anota la tabla nueva en la sección 9.
3. Ejecuta una **segunda** vuelta sin restaurar: en el log, las líneas `  entrada N:` (afinados)
   deben ser muchas menos que en la primera.

*No tocar*: `find_segments`, `escanear`, `afinar`, `construir`. Si tras esta tarea una vuelta
tarda más de 2 horas en el PC, anótalo; la optimización siguiente está descrita en T12 y no se
hace ahora.

---

### T3 — Audio y transcripción para todas las pendientes

*Objetivo*: arreglar D1. Es el cambio que más acerca el proyecto a su meta.

*Archivos*: `catalogo.py`, `ciclo_catalogo.py`, `ia_transcribir.py`.

*Pasos*:

1. Función nueva en `catalogo.py`:

   ```python
   def completar_wavs(c, radio: str) -> int:
       """Da audio (data/entradas/<id>.wav) a las entradas activas que no lo tienen, sin esperar
       al afinado: de su ultima emision detectada o, si no hay, del audio del que salieron."""
       carpeta = store.DATA_DIR / "entradas"
       carpeta.mkdir(parents=True, exist_ok=True)
       n = 0
       for e in c.execute("SELECT id, dur, t_origen FROM catalog WHERE radio=? AND estado='activo' "
                          "AND wav IS NULL AND dur >= ?", (radio, MIN_DUR_ENTRADA_S)).fetchall():
           d = c.execute("SELECT t_start, t_end FROM detections WHERE radio=? AND catalog_id=? "
                         "ORDER BY t_start DESC LIMIT 1", (radio, e["id"])).fetchone()
           tramos = [(d["t_start"], d["t_end"])] if d else []
           if e["t_origen"]:
               tramos.append((e["t_origen"], e["t_origen"] + e["dur"]))
           for a, b in tramos:
               pcm = audio.load_range(c, radio, a, b)
               if pcm.size and float(np.abs(pcm).max()) > 0:
                   audio.write_wav(str(carpeta / f"{e['id']}.wav"), pcm)
                   c.execute("UPDATE catalog SET wav=? WHERE id=?", (f"{e['id']}.wav", e["id"]))
                   n += 1
                   break
       c.commit()
       return n
   ```

   En `afinar`, guarda también solo el nombre (`f"{eid}.wav"`) en la columna `wav`; el archivo
   sigue escribiéndose en `data/entradas/`.
2. En `vuelta`, llámala después de `afinar_pendientes` e imprime cuántos audios ha creado.
3. En `ia_transcribir.py`: la tabla `t` gana dos columnas, `mtime REAL` y `ts TEXT` (añádelas con
   `ALTER TABLE` si faltan). Al transcribir, guarda `mtime` = fecha de modificación del WAV y
   `ts` = `datetime('now')`. Una entrada se transcribe si no tiene fila **o** si el `mtime` del
   archivo difiere en más de 1 s del guardado. Las filas antiguas (sin `mtime`) no se rehacen.

*Comprobación* (sobre las bases restauradas más una vuelta completa, o llamando a las funciones a
mano con `RB_DB` puesta):

```
python -c "import sqlite3; c=sqlite3.connect('data/catalogo.db'); print(c.execute(\"select count(*) from catalog where estado='activo' and confirmado=0 and dur>=6 and wav is null\").fetchone())"
  -> (0,)   o un numero menor de 20; antes eran 657
python ia_transcribir.py
python -c "import sqlite3; print(sqlite3.connect('data/transcripciones.db').execute('select count(*) from t').fetchone())"
  -> mas de 1300 (antes 769)
```

La transcripción de ~650 audios tarda entre 30 y 90 minutos en CPU. Ábre cinco de los WAV nuevos
al azar y comprueba que su texto en `transcripciones.db` tiene sentido.

*No tocar*: `afinar` más allá del nombre del archivo; `recortar_por_consenso`; `dividir`.

---

### T4 — Juez completo

*Objetivo*: que el juez decida sobre todo lo pendiente, guardando quién decidió (D6, D7, D10), y
medir si con eso se alcanza la meta.

*Archivos*: `juez.py`, `ciclo_catalogo.py`, `catalogo.py`, y uno nuevo: `pruebas/juez_ciegas.py`.

*Pasos*:

1. `catalogo.py`: añade a `COLUMNAS_EXTRA` `"fuente": "TEXT"` y `"t_veredicto": "REAL"`.
2. `ciclo_catalogo.aplicar_respuestas`: al aplicar un `si` o un `no`, guarda en esa fila
   `fuente=<la de la respuesta>` y `t_veredicto=time.time()`. Al final de la función llama a una
   nueva `completar_fuente(c)`: toda fila con `confirmado=1` y `fuente IS NULL` recibe la fuente
   de su última fila en `respuestas.db` si la tiene, o `'herencia'` si no, y `t_veredicto` = ahora
   si estaba vacío. (Así quedan marcadas también las copias que propaga `propagar_negra` y las que
   confirma `heredar`; llama a `completar_fuente` también después de `heredar`.)
3. `juez.py`, configuración: al arrancar, si existe `<data>/juez.env`, léelo (líneas `CLAVE=valor`,
   ignora vacías y las que empiecen por `#`) y haz `os.environ.setdefault` con cada una. No
   imprimas nunca el valor de `RB_JUEZ_KEY`.
4. `juez.py`, qué se juzga: sustituye el conjunto `ya` por el **último** veredicto de cada entrada
   (`SELECT catalog_id, resp, ts FROM respuestas ORDER BY id`, quedándote con el último). Se salta
   la entrada si ese veredicto es `si` o `no`. Si es `dudosa`, se juzga solo si
   `transcripciones.t.ts` de `e<id>` existe y es posterior (comparación de texto) a ese `ts`.
5. `juez.py`: entre lote y lote, `time.sleep(5)` (los planes gratuitos limitan por minuto). Si la
   respuesta del modelo llega vacía o sin ningún par válido, cuenta como fallo.
6. `ciclo_catalogo.vuelta`: después del subproceso del juez, llama otra vez a `aplicar_respuestas`.
7. `pruebas/juez_ciegas.py` (nuevo, solo lectura): toma las entradas ya juzgadas con
   `fuente='usuario'` o `'ia'` en `respuestas.db` (las del 2026) que tengan texto, las agrupa por
   texto normalizado igual que `juez.py`, llama a `juez.preguntar` **sin enseñar la respuesta** y
   compara. Imprime: textos distintos, cuántos dice anuncio, cuántos de esos estaban como Sí,
   cuántos Sí encuentra, y la lista de discrepancias con su texto.
8. Ejecuta la prueba a ciegas y después una vuelta completa (o `python juez.py --radio cadena_ser`
   seguido de una vuelta) con el modelo que tengas: en el PC, OmniRoute (arráncalo si no responde)
   o, si el usuario ya ha dado la clave de Gemini, `data/juez.env`.

*Comprobación*:

1. Prueba a ciegas: de los textos que estaban como Sí, encuentra al menos el 85 %; y los «dice
   anuncio y estaba como No» son 3 o menos. Lee esos últimos: en la prueba del 13-nov eran Daikin y
   BP, anuncios de verdad mal etiquetados. Si alguno es claramente programa, para y avisa.
2. Tras aplicar los veredictos:
   `python pruebas/simular_reglas.py --bd data/catalogo.db` → en «reglas completas»,
   **bloques reales mudos ≥ 85 %** y programa conocido mudo ≤ 0,15 min/h.
   `python cobertura.py --bd data/catalogo.db` → anota «confirmados %».
3. `select fuente, count(*) from catalog where confirmado=1 group by 1` no tiene ninguna fila con
   `fuente` vacía.

*Si «bloques reales mudos» queda entre el 70 y el 85 %*: no es un fallo tuyo. Anota la cifra, mira
con una consulta cuántas pendientes de ≥ 6 s quedan sin texto o como `dudosa`, y sigue con T5: la
medida que decide es la escucha real de T10. *Si queda por debajo del 70 %* (peor que antes de
empezar), has roto algo: revisa T3 y T4.

*No tocar*: el texto de `PROMPT`, `RELOJ_FIJO`, `RELOJ_MIN_EMISIONES`.

---

### T5 — Caducidad y varias emisoras

*Objetivo*: D8 y D13, y la decisión 7.

*Archivos*: `catalogo.py`, `ciclo_catalogo.py`, `cobertura.py`, `ia_transcribir.py`, `radios.py`.

*Pasos*:

1. `guardar_detecciones`: actualiza `n_matches` y `last_seen` en todas las detecciones nuevas, no
   solo en las de entradas activas (quita el `if d["estado"] == "activo"`).
2. `caducar(c, radio, dias)`: referencia = `MAX(COALESCE(last_seen, t_origen))` **de esa emisora**;
   pasan a `caducado` las entradas de esa emisora con `estado IN ('activo','lista_negra')` y
   `COALESCE(last_seen, t_origen) < ref - dias * 86400`, confirmadas o no. Cambia el mensaje.
   Actualiza las dos llamadas (`vuelta` y `main` de `catalogo.py`). El valor por defecto de
   `--caducar-dias` pasa a 30 en los dos sitios.
3. `ciclo_catalogo.py`: `--radio` pasa a ser `--radios` (una o varias claves; por defecto
   `cadena_ser`); cada vuelta recorre las emisoras una tras otra. `sincronizar_clips` y
   `limpiar_clips` se hacen una vez por vuelta, no por emisora. El juez se llama con `--radio` de
   cada una; la transcripción, una vez.
4. `cobertura.py`: argumento `--radio` (por defecto `cadena_ser`) aplicado a sus tres consultas.
5. `radios.py`: añade `"puerto": 8070` a Cadena SER y `8071` a RNE5. Quita el comentario sobre Los
   40. (RNE5 no tiene publicidad: sirve de ejemplo de configuración, no como segunda emisora real.)

*Comprobación*: una vuelta con `--radios cadena_ser --una-vez --retener-dias 0` termina; las cifras
de `simular_reglas.py` no empeoran respecto a T4; `python cobertura.py --radio cadena_ser` funciona
y `--radio rne5` no falla (dice que no hay datos). Con `--caducar-dias 1` sobre una **copia** de
`catalogo.db`, caducan entradas A, P y N (compruébalo con un `GROUP BY estado, confirmado` antes y
después).

---

### T6 — El motor del silenciador

*Objetivo*: la clase `Motor` de 6.4 A, probada sin conexión. Es la pieza más delicada: hazla sola,
sin HTTP ni ffmpeg.

*Archivos*: `servicio.py` (nuevo) y `pruebas/prueba_motor.py` (nuevo).

*Pasos*:

1. En `servicio.py`, importa `find_ffmpeg` desde `capture.py` (`from capture import find_ffmpeg`),
   como hace `en_vivo.py`. Se moverá a `servicio.py` en T11, al borrar `capture.py`.
2. Escribe `umbral_vivo`, `cargar_indice` y `Motor` como en 6.4. Copia de `en_vivo.py`, sin
   reescribirlas, la lógica de `analizar` y la del bucle de salida (ganancia y fundido).
3. Añade a `servicio.py` el modo `--fuente <carpeta> --desde --hasta --sin-http --sin-grabar`:
   lee los WAV en orden de nombre, los convierte a int16 y se los pasa al `Motor` en trozos de 0,5 s;
   al acabar llama a `cerrar()`. Escribe `data/silencios_<radio>.csv` usando como hora real la del
   nombre del primer clip. Con `--salida-wav <archivo>` guarda además lo que saldría.
4. `pruebas/prueba_motor.py`: dos pruebas.
   - **Sintética**: construye a mano un índice con dos entradas A y una P sacadas de tres WAV de
     `data/entradas/` (huella con `fingerprint.fingerprint`), monta un audio «radio, A1, 8 s de
     radio, A2, radio» con trozos de clips reales (como hace `pruebas/test_vivo.py`), pásalo por el
     `Motor` y comprueba: cada anuncio queda mudo en ≥ 95 % de su duración; los 8 s de en medio
     quedan mudos; los primeros 15 s y los últimos 15 s de radio suenan (salida = entrada
     retrasada, muestra a muestra); la longitud total de la salida es igual a la de la entrada.
   - **Entrada larga** (D9): una entrada A de más de 50 s (concatena cuatro anuncios y saca la
     huella del conjunto) se reconoce y queda muda en ≥ 90 %.
5. Prueba con datos reales: `python servicio.py --radio cadena_ser --fuente data/clips/cadena_ser --desde 20261106_080000 --hasta 20261106_110000 --sin-http --sin-grabar`.

*Comprobación*:

1. `python pruebas/prueba_motor.py` pasa las dos pruebas.
2. Para las 3 h reales, compara `data/silencios_cadena_ser.csv` con lo que dicen las reglas sobre
   las detecciones guardadas del mismo tramo (escribe la comparación dentro de `prueba_motor.py`
   con `reglas.unir` y `reglas.restar`): el servicio debe dejar mudo **≥ 90 %** del tiempo que
   `reglas.planificar` deja mudo con las detecciones de `detections` de esas 3 h, y el tiempo mudo
   del servicio que cae fuera de ese plan debe ser **≤ 15 %** de su total. No serán iguales: el
   escaneo sin conexión usa ventanas de 30 s y el directo de 15 s.
3. Velocidad: las 3 h se procesan en menos de 1 h en el PC (hace falta ir bastante más rápido que
   el tiempo real). Anota el tiempo.

*No tocar*: `reglas.py`, `catalogo.votar`, `fingerprint.py`.

---

### T7 — El servicio completo

*Objetivo*: ingesta, grabación de clips, MP3 y HTTP alrededor del `Motor` (6.4 B).

*Archivos*: `servicio.py`.

*Pasos*: implementa las piezas de la tabla de 6.4 B, una a una y en ese orden. Después de cada
una, arranca y comprueba que lo anterior sigue funcionando.

*Comprobación* (en el PC; aquí el catálogo no reconocerá el directo, ver trampa 1: solo se prueba
la fontanería):

1. `python servicio.py --radio cadena_ser --fuente data/clips/cadena_ser --desde 20261106_080000 --hasta 20261106_090000 --ritmo-real --sin-grabar`
   y abrir `http://127.0.0.1:8070/` en un navegador: a los 60 s empieza a sonar la SER del 6-nov; en
   la página se ve cuándo pasa a silencio, y los bloques de anuncios **salen mudos**. Pide al
   usuario que lo escuche diez minutos.
2. `python servicio.py --radio cadena_ser` (directo real, 5 minutos): suena por
   `http://127.0.0.1:8070/directo.mp3` con ~60 s de retraso; en `data/clips/cadena_ser/` aparece un
   WAV nuevo cada 10 s y su fila en `modelo.db`; dos clips seguidos tienen `timestamp` separados
   exactamente 10,000 s.
3. Corta la red 30 s (o mata el ffmpeg de entrada): el servicio no muere, lo apunta y se recupera.
4. Con dos reproductores conectados a la vez suenan los dos; al cerrar uno, el otro sigue.
5. Déjalo 2 horas: la memoria del proceso no crece de forma continua.

Al terminar, borra los clips de prueba de hoy de `data/clips/cadena_ser/` y sus filas en
`modelo.db` (son de ffmpeg 9 y ensucian el banco de pruebas).

---

### T8 — Autocorrección

*Objetivo*: conducta, reloj tardío y salud (6.6).

*Archivos*: `catalogo.py` (`revisar_conducta`), `ciclo_catalogo.py` (`salud`, llamadas).

*Pasos*:

1. `catalogo.revisar_conducta(c, radio) -> list[tuple[int, str]]` con las reglas exactas de la
   tabla de 6.6; devuelve `(id, 'conducta' | 'regla')` de lo que ha cambiado. La función `reloj`
   está en `juez.py`: muévela a `reglas.py` e impórtala desde los dos sitios.
2. En el ciclo, por cada `(id, 'conducta')` inserta en `respuestas.db` una fila
   `resp='dudosa', fuente='conducta', aplicada=1`.
3. `catalogo.heredar`: no tocar las entradas con `fuente IN ('conducta','salud')` (si no, volverían
   a confirmarse por ser copia de otro anuncio).
4. `salud(c, radio)` en el ciclo, con la regla de 6.6. Si no existe el CSV o hay menos de 3 días,
   no hace nada.

*Comprobación*: sobre una **copia** de `catalogo.db` tal como quedó en T4, `revisar_conducta`
degrada como mucho a 5 entradas; imprímelas con su texto y comprueba leyendo que no son anuncios
evidentes (si lo son, anótalo, no cambies la regla). Para `salud`, fabrica un
`silencios_cadena_ser.csv` de 5 días con 40 min mudos al día y un último día con 120: imprime
`SALUD:` y degrada solo entradas con `fuente='ia'` de las últimas 24 h. Con el último día a 50 min,
no hace nada.

---

### T9 — Despliegue en asuka

**Pide permiso al usuario antes de empezar.** Necesitas de él: (a) el visto bueno para arrancar
asuka; (b) que cree una clave gratuita en Google AI Studio y la ponga él mismo en
`data\juez.env` de asuka (dale las tres líneas del archivo con la clave en blanco).

*Pasos*:

1. En asuka (`ssh asuka`; pregunta al usuario la carpeta del repo si no la conoces): `git pull` en
   `beta`; `.venv-capture\Scripts\pip install -r deteccion-anuncios-test\requirements.txt` (el
   entorno `.venv-capture` está en la raíz del repo).
2. Comprueba la memoria y el disco y anótalos
   (`powershell -c "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB"` y
   `asuka_estado.ps1`). El servicio necesita ~1 GB libre; los clips de 3 días, ~11 GB.
3. Actualiza `asuka_arrancar.ps1`: las tareas pasan a ser dos —
   `servicio.py --radio cadena_ser --host <la IP de Tailscale que ya usa ese script>` (prioridad `Normal`, log `servicio_cadena_ser.log`,
   no reiniciable salvo con un interruptor nuevo `-ReiniciarServicio`) y
   `ciclo_catalogo.py --radios cadena_ser --cada 120 --horas 36 --retener-dias 3 --caducar-dias 30`
   (prioridad `Idle`, log `ciclo.log`, reiniciable). Quita `capture.py` y `revisar.py`.
   Actualiza los patrones de `asuka_estado.ps1` y `asuka_parar.ps1` (añade `servicio`, quita
   `revisar`).
4. El catálogo que hay en asuka es el del 6-nov: **déjalo**. Sus huellas salen del mismo ffmpeg y
   la caducidad irá limpiando. Los clips antiguos (unos 53 GB; `data\clips` es un enlace a otro
   disco, cuidado al medir o borrar) los borrará la retención de 3 días en la primera vuelta:
   avisa al usuario antes, porque es irreversible (tiene copia de 2 días en el PC).
5. Prueba del juez contra Gemini: `python pruebas\juez_ciegas.py`. Mismo criterio que en T4.
6. Arranca con `asuka_arrancar.ps1` y comprueba con `asuka_estado.ps1`.

*Comprobación*:

1. Desde el PC, `http://100.72.42.80:8070/` carga y `directo.mp3` suena.
2. En asuka, `pruebas\pipe_vs_clips.py` ya no aplica (hay una sola decodificación). En su lugar: a
   la hora de arrancar, `servicio_cadena_ser.log` muestra anuncios reconocidos (`anuncio N reconocido`) si
   alguna campaña del 6-nov sigue en antena; si no, espera a la tarde siguiente.
3. Tras la primera vuelta del ciclo: `ciclo.log` acaba en `vuelta terminada`, con veredictos del
   juez (`juez: ... veredictos`).
4. A las 24 h: el usuario oye un bloque real mudo desde el móvil.

---

### T10 — Escuchar y ajustar (con el usuario)

Tres días de uso normal sin tocar nada. Cada día mira `asuka_estado.ps1`, la página de estado y
`silencios_cadena_ser.csv`, y pregunta al usuario qué ha oído.

| Lo que cuenta el usuario | Qué mirar | Qué hacer |
|---|---|---|
| «Se cuelan anuncios en mitad de un bloque» | ¿Están en el catálogo como N o `dudosa`? | Si son N sin texto: revisar T3. Si el juez dijo `no`: leer el texto y repetir la prueba a ciegas. |
| «Se cuela la primera/última cuña» | Es el límite conocido (6.7) | Nada los dos primeros días. Si sigue al cuarto, mirar si esa entrada existe. |
| «Corta el programa» | El CSV: qué ids provocaron ese silencio | Leer su texto. Si es una entrada A que no es anuncio, comprobar si `revisar_conducta` debería haberla cogido. No añadir excepciones a mano. |
| «Silencia la sintonía de vuelta» | Regla del borde | Coste esperado (~10 s/h). Si molesta mucho: `borde=False` en la llamada del servicio y volver a simular. |
| «Se oye el principio del anuncio» | Márgenes `PAD_INI_S` | Subir 0,5 s y probar un día. |

*Hecho cuando*: pasan 3 días seguidos sin tocar nada con menos de un anuncio colado por hora y
ningún corte de programa de más de 5 s, según el usuario.

---

### T11 — Cierre

1. **Arranque automático**: tarea programada de Windows al iniciar el sistema que ejecute
   `asuka_arrancar.ps1`. Da al usuario el comando `schtasks` exacto y que lo ejecute él o te
   autorice. El servicio debe reintentar durante 5 minutos si la IP de Tailscale aún no existe al
   arrancar. Prueba: reiniciar asuka (lo hace el usuario) y que todo vuelva solo.
2. **Retirar lo antiguo** (un commit por grupo, comprobando que todo arranca después de cada uno):
   `backend/`, `docker-compose.yml`, `data/segments/` y `radio-adblocker-spec.md` de la raíz;
   `capture.py` (antes mueve `find_ffmpeg` a `servicio.py`), `en_vivo.py`, `exportar_vivo.py`,
   `revisar.py`, `ia_pendientes.py`, `ia_aplicar.py`; de `pruebas/` todo menos `simular_reglas.py`, `prueba_motor.py` y
   `juez_ciegas.py`; `docs/PROGRESO.md`, `docs/validacion.md`, `docs/DISENO_MODELO.md`,
   `docs/presentacion.html`; de `catalogo.py`: `agrupar`, `propagar_grupo`, `huecos`,
   `proponer_huecos`, `exportar_huecos`, `evaluar`, `calibrar` y sus órdenes; de `store.py`: las
   tablas `windows`, `cuts`, `marks` y lo que ya no use nadie. Antes de borrar cada función,
   busca con grep que nadie la llama.
3. **Documentación**: un `README.md` raíz corto (qué es, cómo se usa, cómo se añade una emisora,
   cómo se opera asuka) y este archivo reducido a lo que siga siendo cierto. Propón al usuario
   subir la carpeta `modelo/` a la raíz del repo; no lo hagas sin que diga que sí.
4. **Merge `beta` → `main`**: solo cuando el usuario lo confirme en ese momento.

---

### T12 — Opcionales (solo si el usuario los pide)

- **Segunda emisora**: entrada en `radios.py`, servicio en otro puerto, clave en `--radios`.
  Primer día solo escucha. Comprobar que `simular_reglas.py --radio <clave>` da cifras razonables
  a los 4 días.
- **Calidad de audio**: hoy sale mono a 22 kHz. Para estéreo a 44,1 kHz habría que decodificar
  una sola vez a esa calidad y derivar de ahí la señal de análisis; cambia la huella (el catálogo
  se rehace) y hay que diseñarlo con cuidado. No lo improvises.
- **Vueltas más rápidas**: si una vuelta pasa de 2 h, guardar en una tabla la huella de cada
  ventana (inicios alineados a múltiplos de `HOP_S` en epoch) para no recalcularla en cada vuelta.
  Solo con la medida de T1/T2 en la mano.

---

## 8. Operación y diagnóstico

| Síntoma | Causa probable | Comprobación |
|---|---|---|
| El servicio suena pero nunca silencia | Índice vacío; o el ffmpeg cambió (trampa 1) | Página de estado: entradas por clase. `servicio_<radio>.log`: ¿hay «reconocido»? |
| No suena nada | ffmpeg de entrada caído; URL de la emisora cambiada | `servicio_<radio>.log`; probar la URL con `ffmpeg -i <url> -t 5 -f null -` |
| El catálogo no crece | El ciclo no corre o falla | `asuka_estado.ps1`: últimas vueltas y errores |
| Muchas pendientes sin juzgar | Juez sin servicio (clave, límite) o sin transcripción | `ciclo.log`: líneas `juez:` y `transcrita` |
| Silencia de más de golpe | Veredictos malos recientes | Página de estado: últimos veredictos; ¿línea `SALUD:` en el log? |
| Disco lleno | Retención sin aplicar | `asuka_estado.ps1`; `--retener-dias` |
| `database is locked` | Dos escritores | Solo el ciclo escribe `catalogo.db`; ¿hay un script a mano abierto? |

Arrancar, estado y parada en asuka, desde `modelo\`:
`powershell -NoProfile -ExecutionPolicy Bypass -File asuka_arrancar.ps1` (`asuka_estado.ps1`,
`asuka_parar.ps1`).

---

## 9. Registro de avance

Añade una entrada por tarea: fecha, qué se hizo, salida de la comprobación y desviaciones.

| Fecha | Tarea | Resultado |
|---|---|---|
| 2026-11-15 | Revisión completa y esta guía | `reglas.py` y `pruebas/simular_reglas.py` escritos y probados; cifras en 3.2. Nada más cambiado. |
| 2026-11-16 / 26 | T0 | Entorno `deteccion-anuncios-test/.venv` (Python 3.12.10). Desviación: además de `faster-whisper` hace falta `av<19` (faster-whisper 1.2 falla con PyAV 19). Copias en `data/` y `data/base-t0/`; lo de octubre en `data/antiguo-pc/`. Comprobación (26-nov, sobre `data/base-t0/`): `reglas: autoprueba correcta`; cobertura 280 / 1011 / 826 / 53, 123 bloques, 225.3 min, 33 % / 64 % / 3 %; simulación `14915 detecciones en 43.8 h: A=1807 P=9218 N=3890`, 1.80 min/h y 35 %, 3.59 min/h y 70 %, programa 0.03 y 0.05 min/h, tramo más largo 3.7 min, escondidas 86 % y 9 % (0.17 min/h); `45.2` / `True`. Todo exacto. |
| 2026-11-16 | T1 | `data/vuelta-t1.log` acaba en `vuelta terminada`; 16.357 WAV intactos; la única línea con «retencion» es la de la fase (no se borró ningún clip). Fases: sincronizar 0.0, respuestas 0.6, retencion 0.0, repeticion 828.8, construir 120.1, escaneo 1249.9, afinado 640.0, herencia 49.6, transcripcion 81.8, juez 7.0, agrupar 25.8 s. Total ≈ 3.004 s. |
| 2026-11-16 / 26 | T2 | Comprobación 1: md5 de `load_range` igual con la consulta antigua de `store.py` (commit 67f3d88) y la nueva: `d62360b800ffc00f4f23202aa3e50c63`. Comprobación 2: `6478 ventanas, 56 grupos repetidos, 5 tramos nuevos` y `detecciones=12253 nuevas=2406` iguales en t1 y t2. Fases t1 → t2: repeticion 828.8 → 672.7, construir 120.1 → 95.9, escaneo 1249.9 → 638.4, afinado 640.0 → 287.3 s. El total de t2 (≈ 3.468 s) **no es comparable**: esa vuelta ya incluía T3 y T4 (audios 14.2 s, transcripción cortada por el límite de 1.500 s, juez 217 s). Comprobación 3 (segunda vuelta sin restaurar): pendiente, va con la vuelta de T5. |
| 2026-11-16 / 26 | T3 | Código hecho (`completar_wavs`, `wav` solo con el nombre, `mtime`/`ts` en `transcripciones.db`). Tras la vuelta de t2: `pend>=6 sin wav (0,)` (antes 657). La transcripción de esa vuelta se cortó a 1.500 s (1.110 textos, 341 pendientes sin texto); se completa a mano el 26-nov. Nota: en producción entran 50-100 entradas al día y el límite de 1.500 s basta; solo la tanda inicial necesita varias vueltas. No se cambia. |
| 2026-11-26 | T4 (prueba a ciegas) | `pruebas/juez_ciegas.py` corregido: la verdad sale de `data/base-t0/respuestas.db` (las respuestas del 6-nov); `data/respuestas.db` ya contiene los 400 veredictos que dio el propio juez el 16-nov y la prueba se habría comparado consigo misma. Con OmniRoute (`combo/chat`): `textos distintos: 91 (48 Si, 43 No)`; `dice anuncio: 44; de ellos estaban como Si: 42`; `Si encontrados: 42 de 48 (88%)`; `dudosas: 8`. Dice anuncio y estaba como No: «www.mooji.org» (5 s) y «restaurante del mundo» (9 s); ninguno es claramente programa. Pasa (≥ 85 % y ≤ 3). Fallo a anotar: «…el rebozado de estos calamares… Harina y Holanda» es un anuncio evidente y lo dio por programa. |
| 2026-11-26 | T3 (cierre) | Transcripción completada a mano (`ia_transcribir.py --hilos 6`, 341 entradas): `textos 1451` (> 1.300), `pendientes >=6 s sin texto 0 de 416`. Cinco textos nuevos al azar, con sentido (sorteo de la ONCE, crónica de Omán, Patrimonio Nacional...). |
| 2026-11-26 | T4 | `juez.py` dos pasadas (la primera perdió un lote de 40 por tiempo de espera de OmniRoute; la segunda lo recogió): 400 + 48 veredictos (ia: 340 no, 63 si, 5 dudosa). `aplicar_respuestas`: 408. Después: `fuente` herencia 919, ia 950, regla 1, usuario 142, **ninguna vacía**; quedan 12 pendientes de ≥ 6 s. `simular_reglas`: `15002 detecciones en 45.2 h: A=2546 P=12421 N=35`; reglas completas: mudo 5.44 min/h en 208 tramos, el más largo 4.4 min, **bloques reales mudos 100 % de 244 min**, programa conocido mudo **0.04 min/h**. `cobertura`: confirmados **98 %**, sin revisar 1 %, sin cubrir 2 %. Pasa. Aviso: con casi todo juzgado, los «bloques reales» se definen con esas mismas detecciones, así que el 100 % es optimista; la medida que decide es T10. Copia en `data/despues-t4/`. |
| 2026-11-26 | T8 (comprobación) | **Salud: pasa.** CSV fabricado de 5 días a 40 min: con el último día a 50 min no hace nada; a 120 min imprime `SALUD: ... 120 min mudos en 24 h frente a una mediana de 40 min/dia` y degrada solo los 62 anuncios con `fuente='ia'` de las últimas 24 h. **Conducta: no pasa.** Sobre una copia de `despues-t4`, `revisar_conducta` degrada **79** anuncios (77 con `fuente='herencia'`, 2 `ia`); el máximo era 5. Intento 2, para descartar un fallo de implementación: sobre `base-t0` degrada 1 por conducta (la medida «1 de 152» de 3.2) y 8 por reloj, así que el código es fiel a 6.6. Causa: la regla solo cuenta como vecinos las entradas A y N; con el juez ya pasado casi no quedan N y los vecinos de esos anuncios son P (promos, sintonías) o copias de sí mismos. Medido sin cambiar el repo: contando también P como vecino, degrada **0** en `despues-t4` y **0** en `base-t0`. **No se ha cambiado la regla y `revisar_conducta` no se llama desde la vuelta**: decisión pendiente del usuario. |
| 2026-11-26 | T5 (vuelta) y T2 (comprobación 3) | Vuelta sin restaurar sobre el catálogo de después de T4: `python ciclo_catalogo.py --radios cadena_ser --una-vez --retener-dias 0` → `data/vuelta-t5.log` acaba en `vuelta terminada`. `6478 ventanas, 56 grupos repetidos, 0 tramos nuevos`; `0 entradas caducadas (sin sonar en 30 dias)`; `detecciones=11221 nuevas=1355`; juez `10 veredictos`. Fases: repeticion 568.1, construir 75.6, escaneo 549.9, **afinado 33.7** (antes 640), audios 0.1, herencia 6.5, transcripcion 38.3, juez 1.4 s; total ≈ 1.274 s. T2-3: **72** líneas `  entrada N:` frente a 378 en la primera vuelta. Pasa. Cifras después (no empeoran): `16022 detecciones ... A=3021 P=12981 N=20`; reglas completas 5.46 min/h, bloques reales mudos 100 % de 245 min, programa conocido 0.03 min/h; cobertura: confirmados 98 %, sin revisar 0 %, sin cubrir 2 %. |
| 2026-11-26 | T8 (enganche) | La vuelta llama a `salud()` al final y anota las degradadas como `dudosa` (`anotar_dudosas`). `revisar_conducta` **no** se llama (ver la entrada de la comprobación de T8). |
| 2026-11-26 | T9 (preparación) | `asuka_arrancar.ps1` (servicio Normal + ciclo Idle; no arranca el servicio si queda un `capture.py`), `asuka_estado.ps1` y `asuka_parar.ps1` actualizados. Sintaxis comprobada con el parser de PowerShell 5.1 (0 errores); no ejecutados: asuka sigue parado. |
| 2026-11-26 | T6 | `servicio.py` (`Motor`, `umbral_vivo`, `cargar_indice`, modo `--fuente`) y `pruebas/prueba_motor.py`. Desviación: `np.rint(...).clip()` antes de `astype(int16)` (con ganancia 0,9999 la radio no salía idéntica). **Comprobación 1: pasa** (anuncios 100 % mudos, 8 s intermedios 100 %, P lejos de anuncios 0 %, 15 s iniciales y finales idénticos, misma longitud; entrada de 79 s 100 % muda). Nota: con el umbral antiguo la entrada larga también sale 96 % muda (el audio sintético coincide casi perfecto), así que esta prueba no reproduce D9; solo muestra que el arreglo no rompe nada. **Comprobación 3: pasa**: 3 h en 2,4 min (74×). **Comprobación 2: no pasa en sentido estricto.** Intento 1: cubre 70,1 % del plan (fuera 13,3 %). Fallo encontrado: el silencio en curso al acabar la fuente no se escribía en el CSV (`Registro.cerrar`, arreglado). Intento 2: **75,7 %** del plan (pide ≥ 90 %), fuera del plan **12,5 %** (≤ 15 %, pasa). Causa (medida): el escaneo sin conexión guarda cada detección con la duración entera de la entrada (`fin = inicio + dur`) aunque solo suene un trozo. Entradas largas (40-91 s, bloques) en esas 3 h: detecciones 29,2 min, audio que coincide de verdad 11,8 min, y el servicio deja mudo el **94 %** de ese audio real. 4,7 de los 6,1 min «que faltan» están solo dentro de esas entradas largas; de 438 emisiones de anuncio solo 2 quedan sin ningún silencio (una es el borde de las 8:00). Análisis aparte con el plan recortado a lo que coincide (121 detecciones recortadas): cubre **96,1 %** pero el mudo fuera del plan sube a **17,8 %**. No se ha cambiado ningún umbral ni la prueba; queda para decidir al escuchar (T7-1). Posible mejora futura (no hecha): que `escanear` guarde el tramo que coincide en vez de extrapolar la duración. |
| 2026-11-26 | T11 (adelantado, parte) | Hecho antes de T9-T10 por indicación del usuario (trabajo autónomo), solo lo que no afecta al producto: fuera `backend/`, `docker-compose.yml`, `data/segments/`, `radio-adblocker-spec.md`, `docs/PROGRESO.md`, `docs/validacion.md`, `docs/DISENO_MODELO.md`, `docs/presentacion.html`; fuera `en_vivo.py`, `exportar_vivo.py`, `revisar.py`, `ia_pendientes.py`, `ia_aplicar.py` y las pruebas del 6-nov; `catalogo.py` sin `agrupar`, `propagar_grupo`, huecos, `calibrar`, `evaluar`; `store.py` sin `windows`/`cuts`/`marks`. README raíz nuevo. Mismas cifras de 3.2 después (cobertura 33/64/3 %, simulación A=1807 P=9218 N=3890 y 70 %, `prueba_motor: correcta`). Queda: `capture.py` (mover `find_ffmpeg` a `servicio.py`), arranque automático, README de esta carpeta y merge. Scripts de asuka preparados para varias emisoras (`servicio_<radio>.log`, la parada ya no depende de `CADENASER`); `-ReiniciarServicio` relanza todas las emisoras a la vez. |
| 2026-11-26 | T5 (código) | `guardar_detecciones` actualiza todas las clases; `caducar(c, radio, dias)` por emisora y para A, P y N (30 días por defecto); ciclo con `--radios`; `cobertura --radio`; `puerto` en `radios.py`. Con `--dias 1` sobre una copia: activo+confirmado 375 → 372, lista_negra 1228 → 761, activo sin confirmar 416 → 331. `cobertura --radio rne5`: sin datos, sin error. La vuelta completa queda pendiente. |
