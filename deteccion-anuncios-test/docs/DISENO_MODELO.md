# Diseño del modelo de anuncios (aprendizaje activo)

Basado en investigación (jun-oct 2026) y en lo que se sabe de proyectos afines:
- Repetición por huella: ~98 % recall / 99 % precisión en radio francesa (Khemiri et al. 2014) — encuentra cuñas que se repiten, no las nuevas.
- AdblockRadio (archivado): clasificador anuncio/voz/música sobre ventanas ~1 s + suavizado + huella de anuncios conocidos.
- Adetector: CNN sobre MFCC de 3 s + media móvil (T=0.85, n=10).
- RADIA (arXiv 2403.03538): ASR + clasificación de texto, F1-macro 87,76, detecta cuñas nuevas.
- No verificado: cifras de F1 de embeddings (YAMNet/PANNs) citadas por un informe; se miden aquí con etiquetas propias.

## Fórmula
1. Ventanas de 3 s cada 1 s (`store.WINDOW_S/HOP_S`) -> embedding (PANNs por defecto; se compara con las features a mano con `bench.py`).
2. Candidatos a tramo: (a) repetición por huella (bordes por alineación), (b) clasificador suavizado en el tiempo.
3. El usuario etiqueta TRAMOS (a/n, opcional ajustar bordes); la etiqueta se propaga a las ventanas del tramo.
4. Modelo ligero (regresión logística sobre embeddings) + aprendizaje activo.
5. Validación: separada por día, y aparte sobre anuncios SIN repetición previa (generalización real).
6. Criterio de fiable (a confirmar con el usuario): precisión >= 95 % con recall >= 80 %.
7. Si el audio no basta: añadir canal de texto (Whisper tiny + palabras de cuña) como característica.
