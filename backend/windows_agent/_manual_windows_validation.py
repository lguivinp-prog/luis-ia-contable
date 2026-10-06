"""Fase 5, Bloque 5.2, Domain E2 -- script de validacion MANUAL en Windows
real (seccion 74 del encargo). NUNCA importado por el paquete productivo
ni por la suite de pytest -- se ejecuta directamente:

    python -m windows_agent._manual_windows_validation

Prueba, contra la API real de Windows en este host:
1. DPAPI round-trip (CryptProtectData/CryptUnprotectData reales).
2. El flujo completo de `WindowsDpapiIdentityStore` (guardar/cargar
   identidad, construir un `ssl.SSLContext` real desde el material
   protegido -- confirma que ningun archivo contiene la clave en claro).
3. `WindowsSingleInstanceGuard` -- un segundo proceso con el MISMO mutex
   debe fallar.

Nunca usa credenciales SOL reales -- solo material PKI/DPAPI sintetico de
prueba."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


def _line(msg: str) -> None:
    print(f"[manual-validation] {msg}")


def validate_dpapi_round_trip() -> None:
    from windows_agent.identity import _dpapi_protect, _dpapi_unprotect

    plaintext = f"dpapi-round-trip-sentinel-{uuid.uuid4().hex}".encode("ascii")
    protected = _dpapi_protect(plaintext, description="manual-validation")
    assert protected != plaintext, "DPAPI no transformo los bytes -- no esta cifrando de verdad"
    assert plaintext not in protected, "el texto plano aparece dentro del blob protegido"
    recovered = _dpapi_unprotect(protected)
    assert recovered == plaintext, "el round-trip DPAPI no devolvio el texto original"
    _line(f"DPAPI_ROUND_TRIP=PASS (protected_len={len(protected)}, plaintext_len={len(plaintext)})")


def validate_identity_store_full_flow() -> None:
    from cryptography.hazmat.primitives import serialization

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.broker import pki  # noqa: E402
    from windows_agent.identity import WindowsDpapiIdentityStore  # noqa: E402

    data_dir = Path(tempfile.mkdtemp(prefix="sol_agent_manual_validation_"))
    try:
        ca = pki.generate_ca(common_name="Manual validation CA")
        key, csr_pem = pki.build_agent_csr(common_name="manual-validation-agent")
        cert = pki.sign_agent_csr(ca=ca, csr_pem=csr_pem, agent_common_name="manual-validation-agent")
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        store = WindowsDpapiIdentityStore(data_dir)
        assert not store.is_paired()
        agent_id = uuid.uuid4()
        store.save(agent_id=agent_id, certificate_pem=cert_pem, private_key_pem=key_pem)
        assert store.is_paired()

        # NINGUN archivo del directorio de datos puede contener la clave
        # privada en claro (ni su cuerpo base64 DER).
        der_b64 = b"".join(key_pem.splitlines()[1:-1])
        for f in data_dir.iterdir():
            blob = f.read_bytes()
            assert key_pem not in blob and b"-----BEGIN PRIVATE KEY-----" not in blob, f.name
            assert der_b64[:64] not in blob, f.name
        _line("IDENTITY_KEY_NEVER_PLAINTEXT_AT_REST=PASS")

        identity = store.load()
        assert identity.agent_id == agent_id
        _line("IDENTITY_LOAD_AFTER_SAVE=PASS")

        ca_cert_pem = ca.certificate.public_bytes(serialization.Encoding.PEM)
        ssl_ctx = store.build_client_ssl_context(ca_certificate_pem=ca_cert_pem)
        assert ssl_ctx is not None
        _line("BUILD_CLIENT_SSL_CONTEXT=PASS")

        # tras construir el contexto, el directorio sigue sin archivos con
        # la clave en claro y sin residuos temporales.
        names = sorted(p.name for p in data_dir.iterdir())
        assert not any(n.startswith(".key") or n.endswith(".tmp") for n in names), names
        _line(f"PLAINTEXT_PRIVATE_KEY_FILES_CREATED=0 files={names}")
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


_MUTEX_CHILD_SNIPPET = (
    "from windows_agent.single_instance import WindowsSingleInstanceGuard, SingleInstanceError\n"
    "import sys\n"
    "g = WindowsSingleInstanceGuard()\n"
    "try:\n"
    "    g.acquire()\n"
    "    print('CHILD_ACQUIRED_MUTEX')\n"
    "    sys.exit(0)\n"
    "except SingleInstanceError:\n"
    "    print('CHILD_BLOCKED_BY_EXISTING_MUTEX')\n"
    "    sys.exit(1)\n"
)


def validate_single_instance_guard() -> None:
    from windows_agent.single_instance import WindowsSingleInstanceGuard

    guard = WindowsSingleInstanceGuard()
    guard.acquire()
    try:
        env = {"PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
        import os

        full_env = dict(os.environ)
        full_env.update(env)
        result = subprocess.run(
            [sys.executable, "-c", _MUTEX_CHILD_SNIPPET],
            capture_output=True,
            text=True,
            env=full_env,
            timeout=15,
        )
        assert "CHILD_BLOCKED_BY_EXISTING_MUTEX" in result.stdout, (
            f"un segundo proceso pudo adquirir el MISMO mutex -- stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
        assert result.returncode != 0
        _line("SINGLE_INSTANCE_SECOND_PROCESS_BLOCKED=PASS")
    finally:
        guard.release()

    # tras liberar, un proceso nuevo SI debe poder adquirirlo.
    env2 = dict(__import__("os").environ)
    env2["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    result2 = subprocess.run(
        [sys.executable, "-c", _MUTEX_CHILD_SNIPPET], capture_output=True, text=True, env=env2, timeout=15
    )
    assert "CHILD_ACQUIRED_MUTEX" in result2.stdout, result2.stdout
    _line("SINGLE_INSTANCE_AVAILABLE_AFTER_RELEASE=PASS")


def main() -> int:
    import platform

    if platform.system() != "Windows":
        _line("SKIPPED -- este script requiere Windows real.")
        return 1
    _line(f"Windows version: {platform.platform()}")
    _line(f"Python version: {platform.python_version()}")
    validate_dpapi_round_trip()
    validate_identity_store_full_flow()
    validate_single_instance_guard()
    _line("ALL_MANUAL_WINDOWS_VALIDATIONS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
