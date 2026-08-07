# Radio Ad Blocker

Escucha streams de radio por internet (música, noticias, tertulia — cualquier
tipo de emisora), detecta fragmentos de audio que se repiten (cuñas,
anuncios, sintonías) y los silencia automáticamente conforme el sistema
aprende a reconocerlos. El clustering es no supervisado: agrupa por
similitud acústica solo con escuchar, y únicamente pide veredicto al
usuario cuando un patrón se ha repetido varias veces — el habla suelta que
nunca se repite (noticias, tertulia) nunca llega a la cola de revisión. Ver
[`radio-adblocker-spec.md`](radio-adblocker-spec.md) para la especificación
completa (nota: la especificación original describe una cola de revisión
por segmento; esta implementación la sustituye por una cola por patrón
repetido, ver más abajo).

## Estructura

```
backend/
  app/
    main.py           # FastAPI app: monta la API, el panel y arranca los workers activos
    config.py          # Rutas de datos y parámetros por defecto (vía variables de entorno)
    db/                 # Modelos SQLAlchemy (radios, segmentos, clusters, muteos) y sesión
    analysis/
      features.py       # Extracción de features acústicos (librosa)
      model.py           # Clustering HDBSCAN por emisora + reentrenamiento incremental
    worker/
      worker.py          # Un worker por emisora activa: lee el stream, segmenta, analiza, silencia
      proxy.py            # Servidor HTTP que retransmite el audio (con anuncios mudos) a los clientes
      manager.py           # Arranca/para workers y asigna puertos de proxy dinámicamente
    api/                  # Routers REST (radios, segmentos, clusters, stats) + websocket de estado
    static/                # Panel web (HTML + JS vanilla + Chart.js, sin build step)
data/                       # Montado como volumen: SQLite + archivos de audio de segmentos
docker-compose.yml
```

## Arrancar con Docker

```bash
docker compose up --build
```

- Panel web y API: http://localhost:8000
- Cada radio activa expone su proxy de audio en un puerto propio (8001, 8002, ...),
  visible desde el panel y desde `GET /api/radios`.

## Desarrollo local (sin Docker)

Requiere `ffmpeg` instalado en el sistema (`brew install ffmpeg` / `apt install ffmpeg`).

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
DATA_DIR=../data uvicorn app.main:app --reload
```

## Flujo de uso

1. Abre el panel (`/`) y añade una emisora (nombre + URL del stream).
2. Actívala: el sistema arranca su worker y su proxy en un puerto (p.ej. `:8001`).
3. Apunta tu reproductor a `http://<host>:8001` en lugar de a la emisora directamente.
4. El sistema escucha en continuo y guarda las características acústicas de cada
   segmento de 10s. Cada `RETRAIN_EVERY_N_SEGMENTS` segmentos (por defecto 15,
   ~2,5 min) reagrupa todo lo escuchado con HDBSCAN, sin que hagas nada.
5. Cuando un grupo de sonidos parecidos se ha repetido varias veces (por defecto
   3+, ver `MIN_CLUSTER_SIZE` en `analysis/model.py`), aparece en "Patrones nuevos
   detectados" con una muestra de audio. Lo etiquetas una vez —Es anuncio / No es
   anuncio / Ignorar— y esa decisión se aplica a todas las repeticiones pasadas y
   futuras de ese patrón. Lo que nunca se repite (habla suelta) no llega a pedirte nada.
6. Los patrones ya revisados se pueden reetiquetar o eliminar desde la pestaña
   "Patrones".

## Estado de este scaffold

Implementa la arquitectura completa descrita en la especificación (worker, proxy,
analizador, clustering HDBSCAN, API REST, websocket de estado en vivo, panel web).
Puntos a tener en cuenta antes de producción:

- El reentrenamiento recalcula el clustering completo de la emisora (HDBSCAN no
  soporta actualización incremental real) cada `RETRAIN_EVERY_N_SEGMENTS`
  segmentos nuevos; se ejecuta en un hilo aparte para no bloquear el proxy, pero
  puede volverse costoso con miles de segmentos acumulados — en ese punto conviene
  limitar la ventana de entrenamiento a los N segmentos más recientes.
- No hay rotación automática todavía de `data/segments/` (la especificación pide
  conservar solo los últimos N días).
- Sin autenticación en el panel, tal y como especifica el documento.
