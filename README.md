# Radio Ad Blocker

Escucha streams de radio por internet (música, noticias, tertulia — cualquier
tipo de emisora), detecta fragmentos de audio que se repiten literalmente
(cuñas, anuncios, sintonías) y los silencia automáticamente conforme el
sistema aprende a reconocerlos. La detección de repetición usa huella
acústica (picos de espectrograma + hashes, como Shazam/dejavu) en vez de
clustering por timbre general: solo agrupa segmentos que son el mismo
audio, no locutores que "suenan parecido" — así una tertulia larga nunca se
confunde con una cuña repitiéndose. Solo se pide veredicto al usuario
cuando un patrón se ha repetido varias veces; el habla suelta que nunca se
repite (noticias, tertulia) nunca llega a la cola de revisión. Ver
[`radio-adblocker-spec.md`](radio-adblocker-spec.md) para la especificación
completa (nota: la especificación original describe una cola de revisión
por segmento y un modelo de clustering MFCC/HDBSCAN; esta implementación
sustituye ambas cosas — cola por patrón repetido detectado por huella
acústica en vez de por segmento suelto o por timbre general — ver más abajo
y "Por qué huella acústica y no clustering por timbre").

## Estructura

```
backend/
  app/
    main.py           # FastAPI app: monta la API, el panel y arranca los workers activos
    config.py          # Rutas de datos y parámetros por defecto (vía variables de entorno)
    db/                 # Modelos SQLAlchemy (radios, segmentos, clusters, muteos) y sesión
    analysis/
      fingerprint.py     # Huella acústica tipo Shazam (picos de espectrograma + hashes)
      model.py           # Agrupación por huella acústica por emisora + reentrenamiento
    worker/
      worker.py          # Un worker por emisora activa: lee el stream, segmenta, analiza, silencia
      proxy.py            # Servidor HTTP que retransmite el audio (con anuncios mudos) a los clientes
      manager.py           # Arranca/para workers y asigna puertos de proxy dinámicamente
      cleanup.py            # Rotación automática de los .wav de segmentos antiguos
      tunnel.py              # Cloudflare Quick Tunnel: expone el proxy de una radio a internet
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
Para exponer una radio a internet (botón "Exponer a internet" en el panel) hace falta
además `cloudflared` (`brew install cloudflared` / [instrucciones para Linux](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)) —
es opcional, el resto de la app funciona igual sin él.

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
4. El sistema escucha en continuo y guarda la huella acústica de cada segmento
   (20s por defecto). Cada `RETRAIN_EVERY_N_SEGMENTS` segmentos (por defecto 8,
   ~2,5 min con segmentos de 20s) reagrupa todo lo escuchado por solape de huella,
   sin que hagas nada.
5. Cuando un mismo audio se ha repetido varias veces en momentos distintos (por
   defecto 3+, ver `MIN_APARICIONES` en `analysis/model.py`), aparece en "Patrones
   nuevos detectados" con una muestra de audio. Lo etiquetas una vez —Es anuncio /
   No es anuncio / Ignorar— y esa decisión se aplica a todas las repeticiones
   pasadas y futuras de ese patrón. Lo que nunca se repite (habla suelta, aunque
   sea del mismo locutor) no llega a pedirte nada.
6. Los patrones ya revisados se pueden reetiquetar o eliminar desde la pestaña
   "Patrones".

## Por qué huella acústica y no clustering por timbre

La primera versión de este motor usaba MFCC medio + HDBSCAN: agrupaba segmentos
por similitud de timbre general. Funcionaba para música con anuncios claramente
distintos, pero fallaba en emisoras de habla (tertulia, noticias): el mismo
locutor hablando durante varios minutos suena parecido a sí mismo de un
fragmento a otro, así que el sistema agrupaba trozos de una conversación
continua como si fueran la misma cuña repitiéndose una y otra vez — falsos
patrones con decenas de "apariciones" que en realidad eran una sola charla
trozeada. La huella acústica (picos de espectrograma + hashes de pares
cercanos, igual que Shazam/dejavu) exige coincidencia real de contenido: dos
frases distintas del mismo locutor no comparten los mismos picos, así que no
generan huella común. Solo el mismo clip exacto (una cuña, un anuncio) vuelve
a producir los mismos hashes al repetirse.

## Estado de este scaffold

Implementa la arquitectura completa descrita en la especificación (worker, proxy,
analizador, detección de repetición, API REST, websocket de estado en vivo, panel
web), con la sustitución de modelo de ML descrita arriba. Puntos a tener en cuenta
antes de producción:

- El reentrenamiento reagrupa por huella acústica toda la emisora cada
  `RETRAIN_EVERY_N_SEGMENTS` segmentos nuevos; se ejecuta en un hilo aparte para
  no bloquear el proxy, pero la comparación de huellas puede volverse costosa con
  cientos de miles de segmentos acumulados — en ese punto conviene limitar la
  ventana de entrenamiento a los N segmentos/días más recientes.
- Rotación automática de `data/segments/` ya implementada (`worker/cleanup.py`,
  variable `SEGMENT_RETENTION_DAYS`).
- Sin autenticación en el panel, tal y como especifica el documento.
