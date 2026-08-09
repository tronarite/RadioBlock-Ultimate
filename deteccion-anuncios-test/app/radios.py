"""Emisoras a escuchar. Añadir una nueva es tan simple como añadir una
entrada aquí y reconstruir — no hace falta gestión dinámica para un
proyecto de verificación como este.

La URL de RNE5 lleva un token de sesión propio (cid/sid/token) — no va
hardcodeada en el código para no dejarla en el historial de git; se lee
de la variable de entorno RNE5_STREAM_URL (ver .env.example)."""

import os

_RNE5_URL_DEFAULT = (
    "https://d131.rndfnk.com/star/crtve/rne5/cad/mp3/128/stream.mp3"
    "?cid=TU_CID&sid=TU_SID&token=TU_TOKEN&tvf=TU_TVF"
)

RADIOS = [
    {
        "key": "cadena_ser",
        "nombre": "Cadena SER",
        "url": "http://playerservices.streamtheworld.com/api/livestream-redirect/CADENASER.mp3",
    },
    {
        "key": "rne5",
        "nombre": "RNE Radio 5",
        "url": os.environ.get("RNE5_STREAM_URL", _RNE5_URL_DEFAULT),
    },
    # Los 40 Principales: desactivada, nos quedamos solo con SER y RNE5.
    # Sus datos/patrones detectados siguen en la BD (no se han borrado),
    # solo no se sigue escuchando ni se muestra en el panel.
]

RADIOS_BY_KEY = {r["key"]: r for r in RADIOS}
