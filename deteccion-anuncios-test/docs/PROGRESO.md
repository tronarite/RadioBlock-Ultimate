# Progreso del proyecto — detector de anuncios por huella acústica

Registro a fondo de todo el trabajo hecho en `deteccion-anuncios-test`: por
qué existe, qué se ha probado, qué falló, qué se arregló y en qué estado
queda. Pensado para poder retomar el proyecto sin tener que releer el
código entero ni recordar de memoria por qué las cosas son como son.

## 1. Por qué existe este proyecto

Es un proyecto de **verificación**, separado de la app principal
`RadioBlock Ultimate` a propósito: antes de confiar en la detección de
anuncios por huella acústica dentro de la app real (que sí silencia audio
en directo), había que comprobar — dejándolo escuchando radio real mucho
tiempo, sin intervención manual — si la técnica encuentra los anuncios de
verdad y no genera falsos positivos con la tertulia o la música.

No silencia nada. No tiene proxy de audio. Solo escucha, detecta
fragmentos que se repiten, y los enseña en un panel web para revisarlos de
oído.

## 2. Cómo funciona (arquitectura actual)

1. **Un worker por emisora**, todos dentro del mismo proceso/contenedor,
   decodifican su stream con `ffmpeg` en tiempo real (`-re`).
2. **Ventanas de análisis solapadas** (`WINDOW_SECONDS=50`,
   `HOP_SECONDS=20`): cada ventana nueva comparte la mayor parte del audio
   con la anterior. Esto sustituyó a la primera versión (bloques fijos de
   20s sin solape), que tras 2500+ segmentos reales en producción no
   detectó ni un solo anuncio — ver §4.1.
3. **Huella acústica tipo Shazam** (`app/fingerprint.py`): picos de energía
   del espectrograma (constelación) + hashes de pares de picos cercanos
   `(f1, f2, dt)`. Cada hash guarda también el frame (tiempo local) del
   pico ancla que lo generó — no solo SI dos ventanas comparten audio,
   sino DÓNDE dentro de cada una.
4. **Alineación por desfase temporal** (`fingerprint.align`): para dos
   huellas con tiempo, el desfase mayoritario entre hashes compartidos
   separa la coincidencia real del ruido de fondo — la misma técnica que
   usa Shazam de verdad para confirmar un match (contrastado con
   documentación pública del algoritmo).
5. **Agrupación por sub-tramos, no por ventana entera**
   (`app/grouping.py`): si dos anuncios distintos suenan pegados (a veces A
   seguido de B, otras veces B seguido de C), una ventana puede contener el
   final de uno y el principio de otro. Agrupar por ventana completa
   fusionaría A y B por transitividad. En su lugar, cada comparación se
   alinea y se acota al tramo exacto que coincide, así una ventana puede
   aportar a DOS grupos distintos.
6. Un grupo se enseña como "sospechoso de anuncio" solo con 3+ apariciones
   en momentos distintos y separados (`MIN_APARICIONES=3`).
7. **Precisión creciente del tramo exacto**: cuantas más apariciones tenga
   un grupo, más se afina el tramo estimado de inicio/fin del anuncio
   dentro de la ventana — se compara la ventana representativa contra cada
   otra aparición y se toma la mediana de los rangos alineados
   (`_estimate_group_boundary`).
8. **Multi-radio en una sola app**: Cadena SER y RNE Radio 5 en el mismo
   proceso/contenedor/base de datos (columna `radio`), nunca se comparan
   patrones entre emisoras distintas. Los 40 Principales se probó y luego
   se desactivó (datos conservados en la BD, sin borrar).

## 3. Cronología resumida

- **Fase 1 — detección no funcionaba**: 2502 segmentos analizados en un
  despliegue remoto, cero anuncios detectados. Causa raíz: bloques de
  análisis fijos y disjuntos — un anuncio que cae repartido de forma
  distinta cada vez entre bloques nunca genera la misma huella. Solución:
  ventanas solapadas + umbral de hashes compartidos en número absoluto
  (calibrado con audio real: ruido de fondo ~44 hashes de mediana, un
  clip repetido de verdad comparte miles).
- **Fase 2 — pruebas movidas a local**: de un servidor remoto a este Mac,
  para poder iterar más rápido y revisar resultados sin depender de SSH.
- **Fase 3 — multi-radio**: se añadieron RNE Radio 5 y Los 40 Principales,
  y se consolidaron las tres emisoras en una sola web/contenedor (antes
  habría sido un despliegue por emisora) para comodidad de uso.
- **Fase 4 — reproductor de audio**: el `<audio controls>` nativo se veía
  "cortado" a los pocos segundos y la barra de progreso era casi
  inmanejable. Diagnóstico en dos pasos:
  - Causa 1: el formato FLAC en el que se guardaban los audios tiene
    soporte de *seek*/duración poco fiable en el elemento `<audio>` de
    Chrome. Se volvió a guardar en WAV (sin comprimir, pero de
    reproducción fiable).
  - Causa 2 (independiente, seguía pasando tras el cambio a WAV): con
    muchos `<audio preload="metadata">` en la misma página, el límite de
    conexiones por origen de Chrome generaba contención, cortando las
    peticiones *range* del audio que sí estaba sonando. Se arregló con
    `preload="none"`.
  - De paso se construyó un reproductor propio (barra de tipo
    `<input type="range">`, más ancha y manejable) con pausa automática
    del audio anterior al reproducir uno nuevo.
- **Fase 5 — precisión del tramo exacto**: se implementó que la ventana
  representativa de un grupo se comparase contra las demás apariciones
  para acotar cada vez mejor dónde empieza y termina el anuncio dentro de
  la ventana de 50s (ver §2.7). Tuvo un bug (§4.3) descubierto y arreglado
  esta sesión.
- **Fase 6 — agrupación por sub-tramos**: para separar dos anuncios
  distintos que a veces suenan pegados, en vez de fundirlos en un solo
  grupo por transitividad de ventana completa (ver §2.5). Validado con
  audio sintético, con 3 bugs encontrados y corregidos en el proceso
  (§4.2).
- **Fase 7 — estabilidad de fondo (esta sesión)**: tras dejarlo corriendo
  toda la noche, la detección se paró silenciosamente a las ~8h. Causa
  raíz encontrada y arreglada: apilamiento de hilos en `regroup_async`
  (§4.4) — el hallazgo más serio de todo el proyecto hasta ahora, porque
  no producía ningún error visible en los logs.

## 4. Bugs encontrados y arreglados (con causa raíz)

### 4.1 — Cero detecciones con bloques fijos sin solape

Bloques de 20s disjuntos: un mismo anuncio cae repartido de forma distinta
entre bloques cada vez que suena, generando huellas casi sin coincidencia
aunque el audio sea idéntico. Arreglado con ventanas solapadas
(`HOP_SECONDS < WINDOW_SECONDS`) — cualquier fragmento razonable queda
contenido entero en alguna ventana.

### 4.2 — Agrupación por sub-tramos: 3 bugs de fusión/fragmentación

Al implementar que un anuncio A y un anuncio B pegados no se fusionaran en
un solo grupo:

1. **Fragmentación**: la misma aparición de un anuncio real (SER, jingle
   de ~13h de vida) acababa repartida en 2-3 grupos separados, porque
   ventanas de la misma aparición nunca se comparan entre sí (comparten
   audio fuente por construcción) y no siempre había una arista transitiva
   que conectase todas las apariciones. Arreglado con una segunda pasada
   (`_merge_similar_groups`) que fusiona grupos cuyo contenido de verdad
   coincide, con un umbral más laxo (ya pasaron el filtro fuerte por
   separado).
2. **Sobre-fusión (primer intento)**: con audio sintético, A y B se
   fusionaban en un único grupo erróneamente. Diagnóstico con
   `shared_hash_count`/`align` reveló que el clip sintético (basado en
   senoides) tiene mucha menos riqueza espectral que audio real — 79
   hashes alineados entre versión completa/parcial del MISMO clip
   sintético, muy por debajo de los ~1647 medidos con audio real. Confirmó
   que era un artefacto de la señal de prueba, no un fallo del algoritmo.
3. **Sobre-fusión (segundo intento)**: `_members_match()`, usada en la
   fusión de la segunda pasada, se olvidaba de comprobar que las dos
   ventanas comparadas no se solapasen en tiempo fuente — dos ventanas
   vecinas que comparten ~30s de audio real por construcción (contienen el
   mismo material por solape) se comparaban directamente y, claro,
   coincidían casi del todo, fusionando por error los grupos de A y B.
   Arreglado añadiendo la comprobación de solape como primera línea de
   `_members_match()`.

Validado tras los 3 arreglos: exactamente 2 grupos puros (A: 9
miembros/3 apariciones, B: 10 miembros/3 apariciones), sin mezcla — ver
`docs/experimentos/` para los scripts.

### 4.3 — `n_estimaciones` clavado en 1 sin importar cuántas apariciones

Descubierto revisando los resultados de una noche entera: un grupo con
27 apariciones reales seguía mostrando "1 comparación" en la estimación
del tramo exacto — la funcionalidad de "más apariciones = más precisión"
prometida al usuario no se estaba cumpliendo.

Causa: `_estimate_group_boundary()` leía las entradas ya existentes en
`members` (la lista que arma el union-find), pero el union-find solo
guarda **una región por ventana la primera vez que se establece** — así
que la ventana representativa casi siempre aparece una única vez ahí
aunque el grupo tenga muchas apariciones reales.

Arreglo: en vez de mirar `members`, comparar la ventana representativa
**directamente contra cada otra ventana única del grupo** (excluyendo las
que se solapan en tiempo fuente con ella), recalculando `align()` fresco
en cada `regroup()`. Verificado con datos reales: el patrón de 26-29
apariciones de Cadena SER pasó de `n_estimaciones=1` a `n_estimaciones=15`
tras el arreglo.

### 4.4 — Apilamiento de hilos en `regroup_async` (el más grave)

**Síntoma**: tras 8h corriendo bien (ambas radios conectadas, segmentos
creciendo con normalidad), la detección se paró en seco durante ~2h sin
ningún error en los logs. `docker top` mostró 54 PIDs (debería haber 3:
uvicorn + 2 ffmpeg) y `docker stats` mostró 102% CPU.

**Causa raíz**: `worker.py` llama a `regroup_async(radio)` después de
**cada segmento nuevo** (cada ~20s por radio). `regroup_async` lanzaba un
hilo **nuevo** en cada llamada, sin ningún control de si ya había uno
corriendo — solo esperaba a un lock global antes de empezar a trabajar.
Mientras el dataset era pequeño, cada `regroup()` tardaba bien menos de
20s y no pasaba nada. Pero con horas de audio acumuladas (ver §4.5), un
`regroup()` completo empezó a tardar **más** que el intervalo entre
segmentos — así que cada nueva llamada añadía un hilo a la cola en vez de
sustituir al anterior. Con el tiempo se apilaron 54 hilos esperando el
lock, saturando la CPU vía contención del GIL de Python hasta el punto de
bloquear el propio bucle de lectura del stream de audio (ffmpeg se quedó
escribiendo a una tubería que nadie vaciaba — sin ningún error, solo se
quedó esperando).

**Arreglo** (`app/grouping.py`, `regroup_async`): guard con dos
condiciones — (a) nunca lanzar un hilo nuevo para una radio si ya hay uno
corriendo, (b) nunca relanzar antes de `MIN_REGROUP_INTERVAL_SECONDS=45`
desde el último lanzamiento. Verificado tras el redespliegue: recuento de
hilos vuelve a la normalidad tras cada regroup, sin crecimiento.

### 4.5 — Coste de `_build_edges` disparado con muchas horas acumuladas

Encontrado mientras se diagnosticaba §4.4: con 2687 segmentos reales
acumulados (24h de `REGROUP_LOOKBACK_HOURS`), un solo `regroup()` tardaba
**163 segundos** — muy por encima del intervalo de 20s entre segmentos, la
causa directa del apilamiento de §4.4.

**Causa raíz**: el conteo de "votos" por hash (paso previo barato antes
de calcular la alineación exacta) emparejaba **todas las ventanas dentro
de cada bucket de hash entre sí** (O(k²) por bucket). Con horas de audio
real acumulado, algunos hashes (de cuñas/jingles que suenan
constantemente) aparecen en 100+ ventanas — medido: ~5568 buckets de
tamaño 50-150, sumando ~106 millones de operaciones de emparejamiento
solo para una radio.

**Arreglo**: emparejar cada aparición de un hash solo con sus
`VOTE_FAN_OUT=6` vecinas más próximas en el tiempo (la misma idea que ya
usa `fingerprint.py` al generar los hashes: no hace falta comparar cada
pico con todos los demás, solo con los cercanos) — una repetición real
comparte miles de hashes, así que sobran votos para superar el umbral
aunque cada hash individual solo "vote" entre vecinos. Además: los datos
ahora se piden ya ordenados por tiempo (`ORDER BY timestamp`) para no
tener que ordenar cada bucket de hash por separado, y el chequeo de
solape usa timestamps en epoch precalculados en vez de recalcular
aritmética de `datetime` en cada comparación.

Resultado medido sobre datos reales (24h, 2687 segmentos, Cadena SER):
163s → 71.8s solo en `_build_edges` (~2.3×); el pipeline completo (con la
segunda pasada de fusión) queda en ~127s para el caso más cargado
(el patrón de 26-29 apariciones). Sigue sin ser instantáneo, pero el
guard de §4.4 hace que esto ya no importe: nunca se apilan hilos
esperando, como mucho el sistema tarda un poco más en reflejar el último
segmento.

**Aviso para el futuro**: si esto se deja corriendo muchos días seguidos
y `REGROUP_LOOKBACK_HOURS` se aumenta bastante, este coste puede volver a
crecer. Antes de subir ese valor, medir de nuevo con
`docs/experimentos/` o el mismo método usado aquí (copiar `detector.db`,
cronometrar `_build_edges`/`_group_by_region` con datos reales).

### 4.6 — Casi-incidente: OOM al probar el arreglo a mano

Al verificar el arreglo de §4.4 con `docker exec ... python3 -c "regroup(...)"`
mientras la app principal seguía corriendo, el contenedor entero murió por
falta de memoria (`exit code 137`, contenedor reiniciado solo). Medido
después en aislamiento: el pipeline completo de un `regroup()` a 24h
consume un pico de ~1.26GB de RSS (sobre todo por mantener en memoria las
~2700 huellas decodificadas, cada una con ~5000 hashes). Ejecutar esto en
un **proceso separado** (el `docker exec`) mientras el proceso principal
ya tenía su propia copia en memoria dobló la presión y superó el límite
del contenedor (3.827GiB). No es un fallo del arreglo en sí — es una
lección de metodología: para probar `regroup()` contra datos reales sin
arriesgar el contenedor en marcha, copiar `data/detector.db` a un sitio
aparte y probar con un intérprete de Python en el host, nunca con
`docker exec` mientras la app principal sigue corriendo.

## 5. Estado al cierre de esta sesión

- Ambas emisoras (Cadena SER, RNE Radio 5) llevaban corriendo con
  normalidad, con patrones reales detectados:
  - Cadena SER: patrón con **26-29 apariciones** en ~13-14h (casi
    seguro la cuña/hora en punto de la emisora), más un segundo patrón de
    4 apariciones.
  - RNE Radio 5: varios patrones de 3-15 apariciones.
- Los 3 arreglos de esta sesión (guard de concurrencia, optimización de
  `_build_edges`, `n_estimaciones` real) están **desplegados y verificados
  contra datos reales**, no solo en teoría.
- El contenedor se apaga al final de esta sesión (ver README raíz del
  repo) tras subir todo esto a GitHub — los datos (`data/detector.db` +
  audios) se quedan en el Mac, no se han borrado ni subido al repo.

## 6. Cómo retomarlo

```bash
cd deteccion-anuncios-test
cp .env.example .env   # rellenar RNE5_STREAM_URL con un token válido
docker compose up --build -d
```

Panel en `http://localhost:8040`. Ver `README.md` de esta carpeta para
más detalle de arranque, y `docs/validacion.md` +
`docs/experimentos/` para los experimentos de calibración del umbral de
huella.

Si en algún momento la detección lleva mucho tiempo sin actualizarse:
comprobar primero `docker stats <contenedor>` (PIDs y CPU deberían estar
estables, no creciendo) antes de asumir que es un problema de red — la
lección de §4.4 es precisamente que "parece que no llegan datos" puede
ser en realidad la CPU saturada por el propio proceso de reagrupado.
