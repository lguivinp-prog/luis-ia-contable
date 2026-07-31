"""Imprime key_id + fingerprint de la clave de cifrado de integraciones
activa (Fase 3 / Bloque 3.1, ver ADR-0064). Usado por scripts/backup.ps1
para poblar el manifiesto -- nunca imprime la clave en si. Si la
integracion no esta configurada en este entorno, ambos campos son null
(no es un error).

Uso: python -m app.cli.integration_key_info
"""

import json

from app.core.config import get_settings
from app.core.integration_crypto import compute_key_fingerprint, is_encryption_configured


def main() -> None:
    if is_encryption_configured():
        result = {
            "key_id": get_settings().integration_token_key_id,
            "fingerprint": compute_key_fingerprint(),
        }
    else:
        result = {"key_id": None, "fingerprint": None}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
