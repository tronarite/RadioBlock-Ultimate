# Radio Ad Blocker — Especificación del sistema

## Visión general

Sistema que escucha streams de radio por internet, detecta automáticamente segmentos que suenan diferentes a la música habitual, y los presenta al usuario para que los etiquete. Con el tiempo, el sistema aprende a reconocer esos patrones por sí solo y los silencia automáticamente sin intervención del usuario.

El sistema es **general**: funciona con cualquier emisora de radio que emita por HTTP como stream MP3 o AAC, no está atado a ninguna emisora concreta.

---

## Requisitos funcionales

### Gestión de radios

- El usuario puede registrar emisoras desde el panel web indicando nombre, URL del stream, y descripción opcional.
- Cada emisora se puede activar o desactivar individualmente.
- Una emisora activa tiene un worker corriendo en segundo plano que la escucha en continuo.
- Si el stream se cae, el worker reintenta la conexión automáticamente con backoff exponencial.

### Detección de segmentos

- El worker divide el stream en segmentos de audio de duración fija (configurable, por defecto 10 segundos).
- De cada segmento extrae un vector de características acústicas (ver sección de modelo).
- Compara el segmento con los patrones ya aprendidos para esa emisora.
- Si el segmento encaja con un patrón conocido como "anuncio" → lo silencia automáticamente.
- Si el segmento es de confianza baja (no encaja bien con ningún patrón conocido) → lo deja pasar y lo encola para que el usuario lo etiquete.

### Proxy de audio

- El worker actúa como proxy: el usuario se conecta a la URL local del proxy en lugar de a la emisora directamente.
- El proxy reencoda el stream en tiempo real, sustituyendo los segmentos de anuncio por silencio puro.
- La URL local del proxy se expone por HTTP. El usuario se encarga de exponerla hacia fuera si lo necesita.
- Un reproductor cualquiera (VLC, navegador, etc.) puede conectarse al proxy sin configuración especial.

### Aprendizaje

- Cada emisora tiene su propio modelo independiente — los patrones de SER Ceuta no se mezclan con los de otra emisora.
- El modelo empieza vacío. Las primeras sesiones son 100% manuales: el usuario etiqueta todos los segmentos desconocidos.
- Conforme se acumulan etiquetas, el modelo ajusta sus clusters y aumenta su confianza.
- El objetivo a largo plazo es que el modelo necesite cada vez menos intervención del usuario.
- El reentrenamiento ocurre de forma continua conforme llegan etiquetas nuevas, no en batches programados.

### Panel web

- Accesible por HTTP, sin autenticación.
- Tiene las siguientes secciones:

**Dashboard general**
- Lista de emisoras registradas con estado (activa/inactiva, conectada/caída).
- Indicador en tiempo real de si cada emisora está emitiendo música o anuncio en este momento.
- Número de segmentos pendientes de etiquetar por emisora.

**Detalle de emisora**
- Estado actual del stream (música / anuncio / silencio / caído).
- Gráfica de nivel de audio en tiempo real.
- Cola de segmentos pendientes de etiquetar, cada uno con:
  - Player de audio de ~10s para escucharlo directamente en el panel.
  - Botones: "Anuncio" / "Música" / "Ignorar".
- Historial de los últimos segmentos mutados con timestamp y duración.

**Estadísticas por emisora**
- Total de minutos escuchados.
- Total de minutos mutados (anuncios bloqueados).
- Porcentaje de tiempo en anuncios.
- Número de patrones aprendidos.
- Evolución temporal: gráfica por día/semana de minutos de anuncios detectados.

**Gestión de patrones**
- Lista de clusters aprendidos para esa emisora.
- Para cada cluster: etiqueta asignada, número de segmentos que lo componen, sample de audio representativo.
- Posibilidad de reetiquerar o eliminar un cluster.

---

## Arquitectura

```
[Stream emisora] ──► [Worker] ──► [Proxy HTTP local]
                        │                │
                        ▼                ▼
                   [Analizador]    [Reproductor usuario]
                        │
                        ▼
                   [Base de datos SQLite]
                        │
                        ▼
                   [Panel web]
```

### Componentes

**Worker** (uno por emisora activa)
- Hilo de lectura del stream.
- Hilo de segmentación y extracción de features.
- Hilo del proxy de audio (escribe al cliente con o sin silencio según el estado).
- Hilo de inferencia del modelo.

**Analizador**
- Recibe segmentos de audio en bruto.
- Extrae features y consulta el modelo.
- Devuelve: `{label: "anuncio"|"musica"|"desconocido", confidence: 0.0-1.0}`.

**Base de datos SQLite**
- Tabla `radios`: id, nombre, url, descripcion, activa.
- Tabla `segmentos`: id, radio_id, timestamp, duracion, features (blob), label, confidence, archivo_audio.
- Tabla `clusters`: id, radio_id, label, centroid (blob), n_segmentos.
- Tabla `muteos`: id, radio_id, timestamp_inicio, timestamp_fin, duracion.

**Panel web**
- Backend: FastAPI sirviendo la API REST y los archivos de audio de los segmentos.
- Frontend: HTML + JS sencillo (HTMX o vanilla), sin frameworks pesados.
- Comunicación en tiempo real: WebSockets para el estado del stream y la gráfica de audio.

---

## Modelo de ML

### Extracción de features

De cada segmento de ~10s se extraen:

- **MFCCs** (Mel-frequency cepstral coefficients) — 13 coeficientes, media y desviación. Captura el timbre general del audio.
- **Spectral centroid** — media y desviación. Indica si el audio es "brillante" o "oscuro".
- **Spectral rolloff** — frecuencia por debajo de la cual cae el 85% de la energía espectral.
- **RMS energy** — media y desviación. Nivel de volumen general.
- **Zero crossing rate** — cuántas veces la señal cruza el cero por segundo. Alta en voz hablada, baja en música.
- **Tempo** — BPM estimado. La música tiene tempo estable; los anuncios hablados no.

El vector resultante tiene ~30 dimensiones y se normaliza antes de alimentar al modelo.

### Clustering

- **Algoritmo**: HDBSCAN (Hierarchical DBSCAN).
  - No requiere especificar el número de clusters de antemano.
  - Maneja bien el ruido y los outliers (los marca como "desconocido" en lugar de forzarlos en un cluster).
  - Se adapta bien a clusters de formas irregulares.
- Los segmentos etiquetados por el usuario anclan los clusters: si el usuario dice que un segmento es "anuncio", todos los segmentos futuros similares se clasifican igual.
- **Umbral de confianza**: configurable por emisora (por defecto 0.75). Por debajo → "desconocido" → cola de etiquetado.

### Reentrenamiento

- Cada vez que el usuario etiqueta un segmento, se reajusta el modelo de esa emisora.
- El reentrenamiento es incremental y ocurre en background sin interrumpir el proxy.

---

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| Backend / API | Python + FastAPI |
| Análisis de audio | librosa |
| Modelo ML | scikit-learn (HDBSCAN via hdbscan) |
| Base de datos | SQLite (via SQLAlchemy) |
| Proxy de audio | ffmpeg (subprocess) + streaming chunked HTTP |
| Panel web frontend | HTML + HTMX + Chart.js |
| Tiempo real (gráfica) | WebSockets (FastAPI nativo) |
| Despliegue | Docker Compose |

---

## Despliegue en asuka

### Estructura de contenedores

```yaml
# docker-compose.yml
services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"   # API + Panel web
    volumes:
      - ./data:/data  # SQLite + archivos de audio de segmentos
    environment:
      - DATA_DIR=/data

  # Los proxies de cada radio se gestionan dinámicamente desde el backend.
  # Cada radio activa ocupa un puerto: 8001, 8002, 8003...
```

### Puertos

- `8000` — Panel web y API.
- `8001`, `8002`, ... — Un puerto por radio activa (proxy de audio). El panel muestra la URL de cada proxy.

### Volúmenes

- `/data/radio_adblocker.db` — Base de datos SQLite.
- `/data/segments/` — Archivos de audio de los segmentos (para el player del panel). Se rotan automáticamente, guardando solo los últimos N días.

---

## Flujo de uso típico

1. El usuario abre el panel y añade una emisora (nombre + URL).
2. Activa la emisora. El sistema arranca el worker y el proxy.
3. El usuario abre su reproductor favorito y apunta a `http://asuka:8001` (URL del proxy).
4. Escucha la radio normalmente. El sistema va analizando el audio en segundo plano.
5. Cuando detecta un segmento desconocido, aparece en la cola del panel.
6. El usuario abre el panel, escucha el sample de 10s y pulsa "Anuncio" o "Música".
7. El modelo actualiza sus clusters con esa etiqueta.
8. Conforme pasa el tiempo, el modelo reconoce los patrones sin preguntar y los silencia automáticamente.
9. El usuario puede revisar las estadísticas y ver cuántos minutos de anuncios se han bloqueado.

---

## Limitaciones conocidas

- **Latencia de detección**: el sistema analiza segmentos completos, por lo que los primeros segundos de un anuncio pueden oírse antes de que se aplique el mute. Es una limitación aceptada.
- **Variaciones del mismo anuncio**: si la emisora emite el mismo anuncio con diferente compresión o volumen, el modelo puede no reconocerlo la primera vez. Con suficientes ejemplos lo aprende.
- **Arranque en frío**: las primeras sesiones de una emisora nueva requieren etiquetado manual intensivo hasta que el modelo tiene suficientes ejemplos.
- **Streams sin ICY metadata**: algunos streams no publican metadatos. El sistema funciona igualmente, pero no puede apoyarse en el título del stream como señal adicional.

---

## Posibles mejoras futuras (fuera de scope inicial)

- Autenticación en el panel.
- Compartir clusters entre emisoras de la misma cadena.
- Exportar/importar modelos entrenados.
- App móvil o extensión de navegador como cliente del proxy.
- Detección de cuñas de la propia emisora (no solo anuncios de terceros).
