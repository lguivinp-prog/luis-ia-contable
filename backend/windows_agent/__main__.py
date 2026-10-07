"""Fase 5, Bloque 5.2, Domain E2 -- punto de entrada productivo del Agente
Windows (seccion 8 del encargo):

    python -m windows_agent pair
    python -m windows_agent run
    python -m windows_agent status

Ningun valor secreto se acepta jamas como argumento de linea de comandos
(seccion 9 del encargo, `SOL_CREDENTIAL_ACCEPTED_FROM_CLI=NO`) -- ni
siquiera el codigo de pairing, que se solicita interactivamente
(`pairing.prompt_pairing_code`)."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import uuid
from pathlib import Path

from windows_agent.config import ConfigError, harden_directory_acl, load_config, resolve_data_directory
from windows_agent.identity import (
    IdentityCorruptError,
    IdentityUnprotectError,
    NotPairedError,
    WindowsDpapiIdentityStore,
)
from windows_agent.browser_runtime import check_installed_browser, configure_browsers_path
from windows_agent.install_paths import (
    default_ca_directory,
    installed_browsers_directory,
    read_build_info,
)
from windows_agent.logging_setup import configure_agent_logging, log_event
from windows_agent.pairing import PairingError, pair_agent, prompt_pairing_code
from windows_agent.browser_executor import SupervisedAuthExecutor
from windows_agent.portal_contract import SOL_PORTAL_CONTRACT
from windows_agent.runtime import AgentRevokedError, AgentRuntime
from windows_agent.single_instance import SingleInstanceError, WindowsSingleInstanceGuard


def _load_ca_certificate_pem(ca_dir: str) -> bytes:
    """El agente SOLO necesita el certificado PUBLICO de la CA (para
    verificar al broker) -- nunca su clave privada, que vive
    exclusivamente del lado del broker."""
    from pathlib import Path

    cert_path = Path(ca_dir) / "ca.cert.pem"
    if not cert_path.is_file():
        raise SystemExit(
            f"No se encontro el certificado de la CA del broker en '{cert_path}'. "
            "Este comando espera ejecutarse en la misma maquina que el broker "
            "(v1, solo loopback) con acceso de lectura a ese material publico."
        )
    return cert_path.read_bytes()


_LEGACY_CA_DIR = "/var/lib/luis-ia-contable/sol_broker_pki"


def _data_dir(args: argparse.Namespace) -> Path:
    """Directorio de datos del agente: `--data-dir` explicito (instalacion en una raiz personalizada) o, por
    defecto, `%LOCALAPPDATA%\\LuisIAContable\\SolAgent`. Solo una RUTA (no secreta); la lista blanca de campos de
    configuracion persistente no cambia."""
    explicit = getattr(args, "data_dir", None)
    return Path(explicit) if explicit else resolve_data_directory()


def _resolve_ca_dir(args: argparse.Namespace, data_dir: Path) -> str:
    if args.ca_dir:
        return str(args.ca_dir)
    return str(default_ca_directory(data_dir)) if os.name == "nt" else _LEGACY_CA_DIR


def _agent_process_running() -> bool:
    """Sonda NO destructiva: intenta tomar el mutex nombrado y lo libera de inmediato."""
    probe = WindowsSingleInstanceGuard()
    try:
        probe.acquire()
    except SingleInstanceError:
        return True
    except Exception:  # plataforma sin soporte u otro fallo: no se afirma nada
        return False
    probe.release()
    return False


def _cmd_version(args: argparse.Namespace) -> int:
    info = read_build_info()
    if info is None:
        print("VERSION=development (no es una build empaquetada)")
        return 0
    for key in sorted(info):
        print(f"{key}={info[key]}")
    return 0


def _cmd_browser_check(args: argparse.Namespace) -> int:
    """Diagnostico seguro del navegador instalado (ver `browser_runtime`)."""
    return check_installed_browser()


def _cmd_pair(args: argparse.Namespace) -> int:
    data_dir = _data_dir(args)
    data_dir.mkdir(parents=True, exist_ok=True)
    harden_directory_acl(data_dir)
    logger = configure_agent_logging(data_dir)
    identity_store = WindowsDpapiIdentityStore(data_dir)
    config = load_config(data_dir)

    ca_certificate_pem = _load_ca_certificate_pem(_resolve_ca_dir(args, data_dir))

    pairing_id_raw = input("pairing_id (UUID mostrado por el backend): ").strip()
    try:
        pairing_id = uuid.UUID(pairing_id_raw)
    except ValueError:
        print("pairing_id invalido -- debe ser un UUID.", file=sys.stderr)
        return 2
    pairing_code = prompt_pairing_code()

    try:
        result = pair_agent(
            identity_store=identity_store,
            pairing_base_url=config.pairing_base_url,
            ca_certificate_pem=ca_certificate_pem,
            pairing_id=pairing_id,
            pairing_code=pairing_code,
            protocol_version=config.protocol_version,
            timeout=config.connect_timeout_seconds,
        )
    finally:
        pairing_code = ""  # nunca retenida mas alla de este punto
    del pairing_code

    log_event(logger, "agent_paired", agent_id=str(result.agent_id))
    print(f"Emparejamiento exitoso -- agent_id={result.agent_id}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    data_dir = _data_dir(args)
    identity_store = WindowsDpapiIdentityStore(data_dir)
    if not identity_store.is_paired():
        print("UNPAIRED -- ejecuta 'python -m windows_agent pair' primero.")
        _print_install_status(data_dir, args)
        return 0
    try:
        identity = identity_store.load()
    except IdentityCorruptError:
        print("IDENTITY_CORRUPT -- material local ilegible. No se borra ni re-empareja automaticamente.")
        return 1
    print(f"PAIRED -- agent_id={identity.agent_id} certificate_not_after={identity.certificate_not_after}")
    _print_install_status(data_dir, args)
    return 0


def _print_install_status(data_dir: Path, args: argparse.Namespace) -> None:
    """Estado operativo SEGURO (Domain G): solo hechos no secretos -- nunca claves, tokens ni credenciales."""
    info = read_build_info()
    print(f"VERSION={info.get('version') if info else 'development'}")
    print(f"PROCESS_RUNNING={'YES' if _agent_process_running() else 'NO'}")
    print(f"CA_CERTIFICATE_PRESENT={'YES' if (Path(_resolve_ca_dir(args, data_dir)) / 'ca.cert.pem').is_file() else 'NO'}")
    browsers = installed_browsers_directory()
    if browsers is not None:
        print(f"INSTALLED_BROWSERS_PRESENT={'YES' if browsers.is_dir() and any(browsers.iterdir()) else 'NO'}")


def _cmd_run(args: argparse.Namespace) -> int:
    data_dir = _data_dir(args)
    logger = configure_agent_logging(data_dir)
    identity_store = WindowsDpapiIdentityStore(data_dir)
    config = load_config(data_dir)

    try:
        guard = WindowsSingleInstanceGuard()
        guard.acquire()
    except SingleInstanceError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    try:
        try:
            identity = identity_store.load()
        except NotPairedError:
            print("UNPAIRED -- ejecuta 'python -m windows_agent pair' primero.", file=sys.stderr)
            return 4
        except IdentityCorruptError:
            print("IDENTITY_CORRUPT -- material local ilegible. Falla cerrado.", file=sys.stderr)
            return 5
        except IdentityUnprotectError:
            print(
                "IDENTITY_UNPROTECT_FAILED -- no se pudo descifrar la identidad local "
                "con DPAPI (usuario de Windows distinto del que emparejo). Falla cerrado.",
                file=sys.stderr,
            )
            return 6

        ca_certificate_pem = _load_ca_certificate_pem(_resolve_ca_dir(args, data_dir))

        runtime = AgentRuntime(
            identity=identity,
            config=config,
            ssl_context_factory=lambda: identity_store.build_client_ssl_context(
                ca_certificate_pem=ca_certificate_pem
            ),
            logger=logger,
            # E3: autenticacion supervisada (AUTHENTICATE_TO_LANDING_ONLY) tras reclamar un job.
            # Opt-in explicito (`--enable-sol-auth`): por defecto el agente nunca abre el navegador.
            auth_executor_factory=(
                (lambda **hooks: SupervisedAuthExecutor(SOL_PORTAL_CONTRACT, logger=logger, **hooks))
                if args.enable_sol_auth
                else None
            ),
        )

        stopping = {"flag": False}

        def _handle_signal(signum: int, frame: object) -> None:
            stopping["flag"] = True

        signal.signal(signal.SIGINT, _handle_signal)
        try:
            signal.signal(signal.SIGTERM, _handle_signal)
        except (ValueError, AttributeError):
            pass  # SIGTERM no disponible en todas las plataformas/hilos

        print(f"Agente en ejecucion -- agent_id={identity.agent_id}. Ctrl+C para detener.")
        try:
            # E2.1-B: conexion/reconexion/heartbeat/reclamo viven en
            # `AgentRuntime.run_forever` -- el proceso sobrevive a un broker
            # no disponible (backoff acotado) y reconecta con HELLO nuevo.
            runtime.run_forever(should_stop=lambda: stopping["flag"])
        except AgentRevokedError:
            print(
                "AGENT_REVOKED -- el backend revoco este agente. No se re-empareja automaticamente.",
                file=sys.stderr,
            )
            return 7
        finally:
            runtime.stop()
        return 0
    finally:
        guard.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="windows_agent", description="Agente Windows nativo SOL (Domain E2)"
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Directorio de datos del agente (identidad/config/logs). Por defecto %%LOCALAPPDATA%%\\LuisIAContable\\SolAgent.",
    )
    parser.add_argument(
        "--ca-dir",
        default=None,
        help=(
            "Directorio con el certificado PUBLICO de la CA del broker (ca.cert.pem). Por defecto en Windows: "
            "<datos del agente>\\broker_ca (copia publica provista por la instalacion)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pair", help="Emparejar este agente con el broker local")
    run_parser = sub.add_parser("run", help="Ejecutar el agente (HELLO + heartbeat + descubrimiento/reclamo de jobs)")
    run_parser.add_argument(
        "--enable-sol-auth",
        action="store_true",
        help=(
            "Habilita la autenticacion supervisada (E3, AUTHENTICATE_TO_LANDING_ONLY) al reclamar un job. "
            "APAGADO por defecto: sin este indicador el agente NUNCA abre el navegador ni toca el portal."
        ),
    )
    sub.add_parser("status", help="Mostrar el estado de emparejamiento local")
    sub.add_parser("version", help="Mostrar la version/build del Agente empaquetado")
    sub.add_parser("browser-check", help="Abrir y cerrar el navegador instalado en about:blank (diagnostico)")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_browsers_path()  # solo en build empaquetada: navegador propio de la app
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "version":
            return _cmd_version(args)
        if args.command == "browser-check":
            return _cmd_browser_check(args)
        if args.command == "pair":
            return _cmd_pair(args)
        if args.command == "status":
            return _cmd_status(args)
        if args.command == "run":
            return _cmd_run(args)
    except (ConfigError, PairingError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
