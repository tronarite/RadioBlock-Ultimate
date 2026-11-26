# Instrucciones para agentes de IA

Este proyecto se termina siguiendo **`deteccion-anuncios-test/docs/GUIA_IA.md`**. Léela entera
(secciones 0 a 5) antes de tocar nada y ejecuta sus tareas en orden.

Lo imprescindible, por si no llegas a leerla:

- El código vigente está en `deteccion-anuncios-test/modelo/`. `backend/` era una arquitectura
  antigua y ya se retiró.
- Todo cambio va a la rama `beta` (commit + push). Nunca a `main` sin que el usuario lo confirme.
- El repo es público: nada de audio, bases de datos ni claves. Lo privado vive en
  `deteccion-anuncios-test/modelo/data/` (ignorado por git).
- `deteccion-anuncios-test/modelo/data/archivo-2026-11-06/` es de solo lectura.
- El servidor asuka está parado: pide permiso antes de arrancar nada allí.
- No hay revisión manual y no se añaden paneles ni procesos que la guía no pida.
