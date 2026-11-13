# RadioBlock — diseño del producto final (2026-11-13)

Diseño aprobado por el usuario el 2026-11-13: sin revisión manual, sin API de pago, en asuka.

## 1. La idea final

Escuchar una emisora por internet sin anuncios: el bloque publicitario entero sale mudo y el
programa no se toca. El sistema trabaja solo: descubre los anuncios, decide cuáles lo son, los
silencia y se corrige, sin que nadie revise nada.

## 2. Qué está resuelto y qué no (medido con los datos del 4 al 6-nov)

| Pieza | Estado | Dato |
|---|---|---|
| Descubrir audio que se repite | Resuelto | Una campaña entra al catálogo en 1-2 días (cada anuncio suena ~3 veces al día) |
| Reconocerlo en directo y silenciarlo | Resuelto | 100 % de los anuncios conocidos, bordes a ±1 s (`pruebas/test_vivo.py`) |
| Afinar el recorte y partir bloques en cuñas | Resuelto | Consenso de emisiones en `catalogo.py` |
| **Decidir si una entrada es anuncio** | **Sin resolver** | Lo hacía una persona: 280 Sí, 826 No y 1.011 sin revisar |
| Escuchar el resultado fuera del servidor | Sin hacer | El piloto solo suena en la propia máquina |

El único cuello de botella es la revisión. Las entradas del catálogo ya cubrían el **97 %** del
tiempo de los bloques, pero solo el **33 %** estaba confirmado: el otro 64 % esperaba un Sí o un No.
Tres de cada cuatro audios repetidos **no** son anuncios (sintonías, promos, cortes de voz de los
boletines), así que no se puede silenciar todo lo que se repite.

### Prueba hecha hoy: ¿puede decidir solo?

Con las 1.104 entradas ya contestadas (solo lectura, sobre el archivo del 6-nov):

| Juez automático | Precisión en los Sí | Recall | Conclusión |
|---|---|---|---|
| Solo estructura (vecinos, reloj, duración) | 67 % | 90 % | No basta para decidir; sí sirve de veto |
| Solo texto, modelo de palabras (sin textos repetidos entre entrenamiento y prueba) | 62-79 % | 75-86 % | No basta: solo hay 57 textos distintos |
| Texto leído por un modelo de lenguaje gratuito (prueba a ciegas, abajo) | 93 % (100 % corregida) | 87-93 % | Sirve |

Dos señales de estructura separan bien por sí solas y se usan como reglas:

- **Reloj**: una sintonía suena siempre en el mismo minuto de la hora (concentración ≥ 0,99 en el
  25 % de los No); los anuncios no pasan de 0,52 en el 90 % de los casos.
- **Vecinos distintos**: un anuncio sale rodeado cada vez de cuñas diferentes (mediana 2,2 vecinos
  distintos por emisión, frente a 1,1 en los No).

Conclusión: el juez tiene que ser **transcripción + modelo de lenguaje**, con la estructura como
segunda opinión.

### Prueba a ciegas del juez (2026-11-13)

Los 57 textos distintos ya contestados (30 Sí, 27 No), sin enseñar la respuesta, con el prompt de
`juez.py`:

| Modelo (gratis) | Dice anuncio | Aciertos | Encuentra de 30 Sí | Dudosas |
|---|---|---|---|---|
| Gemini 3.8 Flash high (Antigravity) | 30 | 28 | 28 (93 %) | 4 |
| Gemini 3.8 Flash low (Antigravity) | 28 | 26 | 26 (87 %) | 6 |
| Combo gratuito de OmniRoute | 29 | 27 | 27 (90 %) | 0 |

- Los 2 «fallos» son los mismos en los tres modelos y son anuncios de verdad (Daikin y BP) que
  estaban guardados como No: el error era de la etiqueta. Corregido, ningún modelo dio por anuncio
  algo que no lo fuera.
- Los Sí que no encuentra son cuñas cuya transcripción no dice nada comercial («¡Oh, Dios mío!
  ¡No es posible!», «La SER presenta el placer de escuchar»): fallo de la transcripción, no del
  juez. Quedan como `dudosa` o `programa` y solo se silencian si suenan dentro de un bloque.
- Límite de la prueba: son 57 textos de una sola emisora y de tres días.

## 3. El producto

Un servicio por emisora, en el servidor. Se usa así:

1. Se abre `http://<servidor>:8070/ser.mp3` en cualquier reproductor (VLC, móvil, navegador). Suena
   la emisora con ~60 s de retraso y los bloques de anuncios en silencio.
2. `http://<servidor>:8070/` es una sola página: qué está sonando (radio o silencio), minutos
   silenciados hoy, tamaño del catálogo y las últimas decisiones del juez. Solo informa.
3. No hay revisión manual de ningún tipo: ni cola, ni botones.

El primer día de una emisora nueva solo escucha. Empieza a silenciar al segundo día y llega a su
nivel normal en 3-4 días (hace falta un fin de semana para las campañas de sábado y domingo).

## 4. Arquitectura: dos procesos, una base

```
emisora ──ffmpeg──► PCM (una sola decodificación)
                      │
          ┌───────────┴────────────┐
          ▼                        ▼
   clips de 10 s (3 días)   SERVICIO (prioridad normal)
          │                  huella cada 2,5 s → índice de anuncios
          │                  → tramos mudos → salida con 60 s de retraso
          │                  → ffmpeg MP3 → HTTP (audio + página de estado)
          ▼                        ▲ recarga el índice cada 10 min
   CICLO (prioridad baja, cada 2 h)│
   repetición → catálogo → escaneo → afinado
   → transcripción → JUEZ → autocorrección → caducidad
          │
          ▼
     catalogo.db (solo escribe el ciclo)
```

- **Una sola decodificación** para grabar, reconocer y emitir. Elimina el fallo del 6-nov: la
  huella del mismo directo cambia según la versión de ffmpeg (36 % de hashes alineados con la
  misma, 3 % con otra). Con un solo flujo ya da igual qué ffmpeg tenga la máquina.
- **Servicio** = `capture.py` + `en_vivo.py` fusionados, más la salida HTTP. Nunca espera al
  ciclo: si el ciclo se cae, sigue silenciando con el último catálogo.
- **Ciclo** = el `ciclo_catalogo.py` actual, con el juez en el sitio donde hoy esperan las
  respuestas humanas.

## 5. El juez automático (lo nuevo)

Cada entrada nueva de ≥ 6 s, ya afinada y transcrita, pasa por tres filtros en orden:

1. **Herencia** (ya existe): si es el mismo audio que una entrada ya juzgada, copia el veredicto.
2. **Vetos de estructura**: reloj ≥ 0,9 con 6 o más emisiones → `programa`, sin mirar el texto.
3. **Modelo de lenguaje**: recibe el texto, la duración, las emisiones por día, cuántos vecinos
   distintos tiene y 20 ejemplos ya juzgados de esa emisora. Devuelve `anuncio`, `programa` o
   `dudosa` con el motivo en una línea. Las promos de la propia emisora cuentan como `programa`.

Estados de una entrada: `nueva → anuncio | programa | dudosa → caducada`. Cada veredicto guarda
su fuente (`herencia`, `regla`, `ia`).

Sin API de pago. `juez.py` habla con cualquier servicio compatible con la API de OpenAI y se
configura con tres variables (`RB_JUEZ_URL`, `RB_JUEZ_KEY`, `RB_JUEZ_MODELO`). Opciones gratuitas:

- OmniRoute (el gateway local que ya se usa en el PC; salta solo de proveedor). Probado: 27/30.
- La API de Gemini con una clave gratuita de Google AI Studio. La suscripción Gemini Plus no
  incluye API, pero el nivel gratuito existe aparte; sus límites están sin comprobar.

Volumen: unas 50-100 entradas nuevas al día y por emisora, en lotes de 40 textos: 2-3 peticiones
al día. Si el servicio no responde, las entradas esperan a la vuelta siguiente.

## 6. Reglas de silenciado

- Suena un `anuncio` → mudo su tramo (con 1 s antes y 0,8 s después, como ahora).
- Hueco de hasta 20 s entre dos anuncios → mudo (ya existe, `RELLENO_S`).
- Una `dudosa` o `nueva` pegada (≤ 5 s) a un tramo mudo → se silencia con él. Así el primer pase
  de una campaña nueva no se cuela si suena dentro del bloque.
- Suena un `programa` → corta el relleno: nunca se silencia por encima de algo que se sabe que es
  radio.
- Límite de seguridad: 8 min seguidos en silencio → vuelve el sonido y queda anotado.
- Fundido de 50 ms. Opción de atenuar al 10 % en vez de silenciar.

## 7. Cómo aprende y se corrige solo

| Mecanismo | Qué hace |
|---|---|
| Repetición (existe) | Cada vuelta mete en el catálogo lo que se ha repetido en las últimas 36 h |
| Afinado (existe) | Cuantas más emisiones, mejor recorte; al cambiar el recorte se vuelve a juzgar |
| Ejemplos propios | El juez recibe como ejemplos los veredictos firmes de esa emisora |
| Revisión por conducta | Un `anuncio` que suena aislado (sin vecinos) en ≥ 80 % de 6 emisiones, o que resulta ir a reloj fijo, pasa a `dudosa` y se vuelve a juzgar |
| Caducidad | Lo que lleva 30 días sin sonar sale del índice, también lo confirmado (hoy lo confirmado no caduca) |
| Salud diaria | Si los minutos silenciados por hora doblan la mediana de los últimos 7 días, las entradas juzgadas ese día dejan de silenciar hasta la vuelta siguiente y queda anotado |

## 8. Límites que el diseño no resuelve

- **Publicidad leída en directo por el locutor**: no se repite, no se detecta.
- **Primer pase de una campaña en el borde del bloque** (primera o última cuña): se cuela hasta
  que se repite, 1-2 días.
- **Retraso**: se oye ~60 s por detrás del directo.
- **Etiquetas con ruido**: entre los No de 2026 hay al menos un anuncio de una eléctrica. Las
  cifras de la sección 2 tienen ese margen de error.
- **Uso personal**: el audio se sirve solo en la red privada (Tailscale), no en abierto.

Si tras una semana de uso los bordes de bloque se cuelan demasiado, el siguiente paso sería un
clasificador acústico anuncio/programa entrenado con el propio catálogo. No se hace hasta medir
que hace falta.

## 9. Qué se reutiliza, qué es nuevo y qué se retira

| | Archivos |
|---|---|
| Se queda igual | `fingerprint.py`, `segments_repeat.py`, `catalogo.py`, `store.py`, `audio.py`, `ia_transcribir.py`, `cobertura.py` |
| Nuevo | `servicio.py` (≈ 300 líneas: ingesta, silenciador, HTTP), `juez.py` (≈ 150), reglas de la sección 7 dentro del ciclo |
| Cambia | `ciclo_catalogo.py` llama al juez; retención de clips de 14 a 3 días (de 53 GB a ~11 GB) |
| Se retira | `backend/` de la raíz, `capture.py` y `en_vivo.py` (absorbidos), `revisar.py`, `ia_pendientes.py`, `ia_aplicar.py`, `exportar_vivo.py` |

## 10. Plan, con criterio de hecho en cada paso

0. **Prueba a ciegas del juez.** Hecha el 2026-11-13 (sección 2): pasa.
1. **`juez.py` en el ciclo.** Escrito y probado el 2026-11-13 sobre una copia del catálogo
   archivado: juzgó las 183 pendientes que tenían texto (30 anuncio, 152 programa, 1 por la regla
   del reloj) y no dejó ninguna dudosa. La cobertura solo subió del 33 % al 37 % porque otras 664
   pendientes de ≥ 6 s no tienen transcripción (asuka se paró antes de hacerlas). *Hecho cuando*,
   con todo transcrito, `cobertura.py` da ≥ 85 % del tiempo de bloque cubierto.
2. **`servicio.py`**, probado primero reproduciendo la grabación archivada (45 h) como si fuera
   el directo. *Hecho cuando* se oye en el móvil y un bloque real sale mudo entero.
3. **Autocorrección, salud y página de estado.** *Hecho cuando* pasan 3 días sin tocar nada con
   menos de un anuncio colado por hora y ningún corte de programa de más de 5 s.
4. **Cierre**: arranque automático en asuka, retirar lo antiguo, README y merge `beta` → `main`.
5. Opcional: segunda emisora (el catálogo ya va separado por emisora; juez y reglas se comparten).

## 11. Decisiones tomadas (2026-11-13)

1. El juez usa solo modelos gratuitos; no se paga ninguna API.
2. No hay revisión manual: el sistema decide y silencia solo.
3. Corre en asuka y se escucha por Tailscale.
