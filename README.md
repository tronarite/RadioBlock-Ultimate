# Radio Ad Blocker

Escucha streams de radio por internet, detecta segmentos que no son la
música habitual de la emisora, y los silencia automáticamente conforme el
sistema aprende a reconocerlos. Ver [`radio-adblocker-spec.md`](radio-adblocker-spec.md)
para la especificación completa.

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
4. Cuando aparezcan segmentos "pendientes" en el detalle de la emisora, escúchalos
   y etiquétalos como Anuncio / Música / Ignorar.
5. El modelo se reentrena en background tras cada etiqueta. Con el tiempo, reconoce
   los patrones sin preguntar y los silencia automáticamente en el proxy.

## Estado de este scaffold

Implementa la arquitectura completa descrita en la especificación (worker, proxy,
analizador, clustering HDBSCAN, API REST, websocket de estado en vivo, panel web).
Puntos a tener en cuenta antes de producción:

- El "reentrenamiento incremental" recalcula el clustering completo de la emisora
  en cada etiqueta nueva (HDBSCAN no soporta actualización incremental real); se
  ejecuta en un hilo aparte para no bloquear el proxy, pero puede volverse costoso
  con miles de segmentos acumulados — en ese punto conviene limitar la ventana de
  entrenamiento a los N segmentos más recientes.
- No hay rotación automática todavía de `data/segments/` (la especificación pide
  conservar solo los últimos N días).
- Sin autenticación en el panel, tal y como especifica el documento.
