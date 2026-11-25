# RadioBlock

Escuchar una emisora de radio por internet sin anuncios. Se abre una dirección del servidor de
casa (asuka) y se oye la emisora con unos 60 s de retraso y con los bloques publicitarios enteros
en silencio. Funciona solo: no hay revisión humana. Uso personal, dentro de la red privada
(Tailscale). Hoy se usa con Cadena SER.

## Cómo funciona

1. **Catálogo por repetición**: el audio que se repite a lo largo del día (cuñas, promos,
   sintonías) se descubre con una huella acústica (tipo Shazam) y se guarda en un catálogo.
2. **Juez**: cada entrada del catálogo se transcribe y un modelo de lenguaje gratuito decide si es
   anuncio o programa.
3. **Servicio**: un proceso por emisora graba, reconoce lo que suena contra el catálogo, silencia
   los bloques y emite el resultado por HTTP. Como va 60 s por detrás, silencia desde el primer
   segundo.
4. **Ciclo**: un proceso de prioridad baja repasa cada 2 h lo grabado, amplía el catálogo, llama
   al juez y caduca lo que deja de sonar.

## Cómo se usa

- Página de estado (solo informa, con reproductor): `http://<asuka>:8070/`
- Audio directo para el móvil o cualquier reproductor: `http://<asuka>:8070/directo.mp3`

`<asuka>` es la IP de Tailscale del servidor. 8070 es el puerto de Cadena SER.

## Añadir una emisora

1. Una entrada en `deteccion-anuncios-test/modelo/radios.py` (`key`, `nombre`, `url` y otro
   `puerto`).
2. Otra línea de servicio en `asuka_arrancar.ps1` (`servicio.py --radio <clave>`).
3. Su clave en `--radios` del ciclo, en ese mismo script.

## Operar asuka

Desde `deteccion-anuncios-test\modelo` en asuka:

```
powershell -NoProfile -ExecutionPolicy Bypass -File asuka_arrancar.ps1   # arranca lo que falte
powershell -NoProfile -ExecutionPolicy Bypass -File asuka_estado.ps1     # procesos, disco, logs
powershell -NoProfile -ExecutionPolicy Bypass -File asuka_parar.ps1      # para todo
```

`asuka_arrancar.ps1 -Reiniciar` relanza el ciclo (tras un `git pull`) y `-ReiniciarServicio`
relanza el servicio (corta la emisión unos segundos). Los logs son `servicio.log` y `ciclo.log`.

## Estado

Las pruebas sin conexión están hechas en el PC. Falta desplegarlo en asuka con la clave gratuita
de Gemini y escucharlo unos días: tareas T9 a T11 de la guía.

## Más detalle

[`deteccion-anuncios-test/docs/GUIA_IA.md`](deteccion-anuncios-test/docs/GUIA_IA.md): diseño,
decisiones, estado comprobado, tareas y diagnóstico.
