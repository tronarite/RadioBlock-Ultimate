# Pruebas sin conexión

Comprobaciones que se ejecutan desde `modelo/` sobre datos ya guardados. Ninguna escribe en las
bases. Uso y criterio de cada una en su cabecera y en `docs/GUIA_IA.md`.

| Script | Qué comprueba |
|---|---|
| `simular_reglas.py` | Aplica las reglas de silenciado (`reglas.py`) a las detecciones de un catálogo: minutos mudos por hora, cuánto de los bloques reales sale mudo y cuánto programa conocido se silencia. `--ocultar F` mide las reglas con parte de las etiquetas escondidas. |
| `prueba_motor.py` | El motor de `servicio.py` con audio real: anuncios y promo intercalados, una entrada larga y, con `--comparar`, lo que silenció el servicio frente a lo que dicen las reglas. |
| `juez_ciegas.py` | El juez (`juez.py`) con textos de entradas ya contestadas, sin enseñarle la respuesta; compara con la verdad del 6-nov. Necesita la clave del modelo de lenguaje. |
