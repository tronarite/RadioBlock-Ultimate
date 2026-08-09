# Por qué el diseño original no detectaba nada (y cómo se validó el arreglo)

## El síntoma

Tras dejar corriendo la primera versión (bloques fijos de 20s, sin solape,
comparados por proporción de huella compartida) contra Cadena SER durante
~14 horas (2502 segmentos), no se detectó ni un solo patrón repetido — ni
falsos positivos ni verdaderos positivos. Cero.

## La hipótesis

Un anuncio no suena sincronizado con ningún reloj: la primera vez puede
empezar en el segundo 7 de un bloque de 20s, la segunda vez en el segundo
14 de otro bloque distinto. La huella acústica solo empareja picos
espectrales que caen **dentro de la misma ventana de análisis**. Si el
bloque de análisis corta el anuncio en un punto distinto cada vez que
suena, cada ocurrencia genera un conjunto de picos casi completamente
distinto — la huella nunca coincide, aunque el audio sea idéntico.

## El experimento (con audio real, no sintético)

1. Se capturaron ~7 minutos de audio real de Cadena SER en directo
   (`ffmpeg` grabando el stream tal cual).
2. Se tomó un clip real de 25s de esa grabación (segundos 30-55).
3. Se insertó una copia de ese mismo clip más adelante en el buffer, en un
   punto **deliberadamente desalineado** con la rejilla de 20s original
   (segundo 150.37 — no es múltiplo de 20).
4. Se comparó cómo se comportaban dos métodos sobre ese mismo buffer:
   - **Método viejo**: bloques fijos de 20s sin solape, huella comparada
     por proporción (`hashes compartidos / min(tamaño de cada huella)`).
   - **Método nuevo**: ventanas de 50s que avanzan cada 20s (solapadas),
     huella comparada por **número absoluto** de hashes compartidos.

## Resultado

| | similitud (ratio) | hashes compartidos (absoluto) | ¿detectado? |
|---|---|---|---|
| Método viejo, bloques 20s | 0.530 | — | Sí (por suerte: el corte de la rejilla coincidió en una posición parecida las dos veces) |
| Método nuevo, ventanas 50s, por ratio | 0.336 | 1647 | No con umbral de ratio 0.4 — la ventana más grande diluye la proporción con audio "de alrededor" que no coincide |
| Método nuevo, ventanas 50s, por conteo absoluto | — | 1647 | **Sí**, con margen enorme |

Calibración del "suelo de ruido" (57 pares de ventanas reales sin ninguna
relación entre sí, del mismo audio capturado):

- hashes compartidos por azar: mediana 44, p90 65, máximo observado 85.
- hashes compartidos por el clip repetido (real, desalineado): **1647**.
- eso es **37.4×** la mediana del ruido de fondo.

## Conclusión

El fallo no era el algoritmo de huella en sí (picos + hashes), sino dos
decisiones de diseño alrededor de él:

1. **Trocear en bloques fijos sin solape** rompe la huella cuando el
   contenido repetido no cae alineado — que es el caso normal, no la
   excepción. Arreglado con ventanas solapadas (`WINDOW_SECONDS` /
   `HOP_SECONDS` en `worker.py`).
2. **Medir similitud como proporción** penaliza a las ventanas grandes
   (necesarias para el punto 1) porque diluye el conteo con contenido no
   compartido. Arreglado usando un **conteo absoluto** de hashes
   compartidos (`MIN_SHARED_HASHES` en `grouping.py`), que separa señal de
   ruido de forma mucho más limpia según los datos reales medidos arriba.

Reproducible con los scripts en `docs/experimentos/` (necesitan un
`captura.wav` propio — ver la cabecera de cada script). La lógica final
integrada en el proyecto está en `app/worker.py` y `app/grouping.py`.
