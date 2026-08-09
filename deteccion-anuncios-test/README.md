# Ad Detector — proyecto de prueba

> ⚠️ **En desarrollo activo, fase intensiva de pruebas.** Este es
> precisamente el proyecto donde se está buscando una manera eficiente y
> fiable de detectar anuncios reales — parámetros como `MIN_SHARED_HASHES`,
> `WINDOW_SECONDS`, `VOTE_FAN_OUT`, etc. se han ido ajustando sobre la marcha
> según lo que muestran los datos reales y seguirán cambiando. Ver
> [`docs/PROGRESO.md`](docs/PROGRESO.md) para el estado actual y qué queda
> por validar antes de darlo por fiable.

Proyecto mínimo con un único propósito: comprobar, dejándolo corriendo mucho
tiempo contra varias emisoras reales, si la detección de repetición por
huella acústica encuentra los anuncios reales sin generar falsos positivos
con la tertulia. No silencia nada, no tiene proxy de audio — solo escucha,
detecta fragmentos que se repiten, y los enseña en una página web para que
los revises tú mismo.

Escucha en paralelo, cada una con sus propios patrones (nunca se comparan
entre sí): **Cadena SER** y **RNE Radio 5** — ver `app/radios.py` para
añadir o quitar emisoras (Los 40 Principales se probó y se desactivó, sus
datos siguen en la BD).

> Para el historial completo de bugs encontrados, arreglados y el porqué de
> cada decisión de diseño, ver [`docs/PROGRESO.md`](docs/PROGRESO.md).

## Cómo funciona

1. Un worker en background por emisora (todas dentro del mismo proceso/
   contenedor) decodifica su stream con `ffmpeg` sin cortarlo en bloques
   independientes: mantiene una **ventana de análisis solapada**
   (`WINDOW_SECONDS`, 50s por defecto) que avanza cada `HOP_SECONDS` (20s
   por defecto) — cada ventana nueva comparte la mayor parte del audio con
   la anterior.

   Esto es importante y no es casualidad: la primera versión troceaba en
   bloques fijos de 20s SIN solape, y con eso, tras 2500+ segmentos reales
   en producción, no detectó ni un solo anuncio. La causa: un anuncio que la
   primera vez cae repartido "40%-60%" entre dos bloques y la segunda vez
   cae "10%-90%" entre otros dos bloques distintos genera conjuntos de picos
   casi completamente distintos cada vez — la huella nunca coincide, aunque
   el audio sea idéntico. Con ventanas solapadas, cualquier fragmento de
   duración razonable queda contenido completo en alguna ventana, sea cual
   sea el instante en que empiece a sonar.

2. De cada ventana calcula una huella acústica tipo Shazam (picos del
   espectrograma + hashes) — la misma técnica que el proyecto principal
   `RadioBlock Ultimate`, aquí aislada para poder validarla sola.

3. Tras cada segmento nuevo se relanza el reagrupado (con control de
   concurrencia — nunca dos a la vez ni más seguido de lo necesario, ver
   `docs/PROGRESO.md` §4.4): dos ventanas se consideran el mismo audio
   repetido si comparten al menos `MIN_SHARED_HASHES` (400 por defecto)
   hashes **en número absoluto**, no en proporción. Se usa un conteo
   absoluto y no un ratio porque las ventanas ya son más grandes que el
   clip que se busca repetir — un ratio se diluye según cuánto audio "de
   alrededor" distinto tenga cada ventana, pero el nº de hashes compartidos
   por un clip repetido se mantiene, caiga donde caiga dentro de la
   ventana. Se descartan además los pares de ventanas que se solapan entre
   sí en el tiempo (comparten audio fuente por construcción, no es una
   repetición real). Además, la agrupación es **consciente de sub-tramos**:
   si dos anuncios distintos suenan pegados, cada comparación se acota al
   tramo exacto que coincide, para no fusionarlos en un solo grupo.

   Calibrado con 5 minutos de audio real de Cadena SER: ventanas sin
   relación entre sí comparten una mediana de ~44 hashes (máximo observado
   ~85); un mismo clip insertado de nuevo en un punto totalmente
   desalineado del original comparte ~1650 — más de 37 veces el ruido de
   fondo. Ver `docs/validacion.md` para el detalle del experimento.

4. Un grupo se enseña en el panel como "sospechoso de anuncio" solo cuando
   ha aparecido 3+ veces en momentos distintos y separados (no cuenta un
   tramo continuo trozeado como varias apariciones). Cuantas más
   apariciones tenga, más se afina el tramo exacto estimado dentro de la
   ventana (marcado en rojo sobre la barra del reproductor).

5. Los audios (`.wav`) de más de `RETENTION_HOURS` horas se borran
   automáticamente (salvo que sean la muestra representativa de un grupo
   vivo), para no llenar el disco si esto se deja corriendo mucho tiempo.

## Arrancar con Docker

```bash
docker compose up --build -d
```

- Panel: http://localhost:8040 (o `http://<ip-del-servidor>:8040` si se
  despliega en un servidor remoto).
- Los datos (SQLite + audios) se guardan en `./data`, montado como volumen —
  sobreviven a reinicios del contenedor.

**Si vienes de una versión anterior** (bloques fijos de 20s, sin solape):
borra `./data` antes de reconstruir — el formato de ventanas cambió y mezclar
datos viejos y nuevos en la misma base de datos daría resultados sin sentido.

```bash
docker compose down
rm -rf data/*
docker compose up --build -d
```

Variables de entorno (en `docker-compose.yml`):

- `WINDOW_SECONDS` — duración de cada ventana de análisis (50s por defecto).
- `HOP_SECONDS` — cada cuánto se genera una ventana nueva (20s por defecto).
- `RETENTION_HOURS` — cuántas horas se conservan los audios (2 por defecto).
- `CLEANUP_INTERVAL_SECONDS` — cada cuánto se limpia el disco (900 por
  defecto).

La mayoría de emisoras están hardcodeadas en `app/radios.py` (nombre + URL
del stream) — añadir una nueva es editar esa lista y reconstruir. La URL de
RNE Radio 5 lleva un token de sesión propio y se lee de la variable de
entorno `RNE5_STREAM_URL` (copiar `.env.example` a `.env` y rellenarla).

## Qué mirar al cabo del tiempo

En el panel verás una lista de "grupos sospechosos de ser anuncio", cada uno
con:

- Cuántas veces se ha visto y cuántos segmentos lo componen en total.
- Un reproductor con una muestra de audio del fragmento.
- Primera y última vez que sonó.
- Un desplegable "ver todas las apariciones" con el audio y la hora exacta
  de cada vez que ese fragmento concreto sonó — útil para confirmar de oído
  si de verdad es el mismo anuncio cada vez, o si es un falso positivo.

Si tras dejarlo un tiempo largo los grupos que aparecen son anuncios reales
(y no trozos de tertulia), la técnica de detección queda validada para
aplicarse tal cual en `RadioBlock Ultimate`.

## Desarrollo local (sin Docker)

Requiere `ffmpeg` instalado en el sistema.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
