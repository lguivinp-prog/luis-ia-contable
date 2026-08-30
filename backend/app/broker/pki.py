"""PKI productiva del broker (Fase 5, Bloque 5.2, Domain D -- ver
docs/phase5/block5_2/). Reimplementacion PRODUCTIVA inspirada en la
evidencia de Spike B (`spikes/sol_agent_ipc/scripts/pki_lib.py`, rama
`spike/phase5-sol-agent-ipc`, tag `spike-phase5-sol-agent-ipc-v1`) -- nunca
mergeada/cherry-picked, solo inspeccionada y reescrita aqui con las
precondiciones productivas de Domain D (fail-closed en arranque, ruta de
clave configurable, TTL documentado, validacion de CSR mas estricta).

Ningun protocolo criptografico propio: unicamente TLS/X.509 estandar via
`cryptography` (x509 + hazmat) sobre RSA/SHA-256 -- misma familia de
primitivas ya validada en el spike (seccion 4 del SPIKE_REPORT.md).

Alcance deliberadamente ACOTADO: esta CA firma UNICAMENTE certificados de
agente Windows emparejado (`sol_agents`) -- nunca una PKI general, nunca
certificados de servidor de otros componentes, nunca CA intermedia."""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.x509.oid import NameOID

# CA autofirmada, vida acotada (no eterna) -- ver `generate_ca`. Vida mas
# larga que la de un certificado de agente individual (`AGENT_CERT_TTL_DAYS`
# abajo) porque re-emitir la CA implicaria re-emitir todos los certificados
# de agente vigentes -- sin mecanismo de renovacion automatica en este
# dominio (seccion 25 del encargo: "sin auto-renew si requiere protocolo
# nuevo -- diferir").
CA_TTL_DAYS = 3650

# TTL acotado del certificado de agente -- ninguno "eterno" (seccion 25).
# 90 dias es un valor documentado, ajustable sin migracion (no toca BD): un
# estudio contable tipico no rota agentes Windows con mas frecuencia que
# esto, y evita la complejidad de renovacion automatica que este dominio
# explicitamente difiere. Un agente cuyo certificado vence simplemente
# vuelve a emparejarse (mismo flujo de pairing, nunca automatico).
AGENT_CERT_TTL_DAYS = 90

_RSA_KEY_SIZE = 2048
_PUBLIC_EXPONENT = 65537
_MIN_ALLOWED_RSA_KEY_SIZE = 2048


class PkiError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CaMaterialMissingError(PkiError):
    """Fail-closed en arranque: ningun fallback inseguro, ningun valor de
    relleno (mismo principio ya productivo de
    `PairingVerifierKeyMisconfiguredError`, Domain A.3)."""

    def __init__(self) -> None:
        super().__init__("SOL_BROKER_CA_MATERIAL_MISSING")


class CaMaterialInvalidError(PkiError):
    def __init__(self) -> None:
        super().__init__("SOL_BROKER_CA_MATERIAL_INVALID")


class CsrInvalidError(PkiError):
    """Generico -- nunca revela ante el agente CUAL validacion especifica
    fallo (parseo, firma, tamano de clave, extensiones peligrosas): mismo
    principio de no-oracle ya aplicado en Domain C."""

    def __init__(self) -> None:
        super().__init__("SOL_BROKER_CSR_INVALID")


@dataclass(frozen=True)
class LocalCa:
    key: RSAPrivateKey
    certificate: x509.Certificate


def _generate_key(key_size: int = _RSA_KEY_SIZE) -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=_PUBLIC_EXPONENT, key_size=key_size)


def generate_ca(*, common_name: str = "luis-ia-contable SOL Agent CA") -> LocalCa:
    """Genera una CA nueva en memoria -- el llamador decide donde
    persistirla (`save_ca`). Nunca se invoca implicitamente al leer
    configuracion: `load_or_fail` NUNCA genera una CA nueva por si sola
    (fail-closed, seccion 17 del encargo -- una CA faltante es un error de
    arranque, no una oportunidad de auto-generar silenciosamente)."""
    key = _generate_key()
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=CA_TTL_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return LocalCa(key=key, certificate=cert)


def save_ca(ca: LocalCa, *, ca_dir: Path) -> None:
    """Escribe `ca.key.pem`/`ca.cert.pem` en `ca_dir` -- SIEMPRE fuera del
    repositorio (el llamador es responsable de que `ca_dir` no sea una ruta
    versionada; ver `get_settings().sol_broker_ca_dir`, mismo patron de
    `document_storage_root`). Nunca escribe en PostgreSQL, logs, ni ninguna
    ruta accesible por el agente o el frontend."""
    ca_dir.mkdir(parents=True, exist_ok=True)
    key_path = ca_dir / "ca.key.pem"
    cert_path = ca_dir / "ca.cert.pem"
    key_path.write_bytes(
        ca.key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(ca.certificate.public_bytes(serialization.Encoding.PEM))
    try:
        key_path.chmod(0o600)
    except OSError:
        pass  # best-effort en plataformas sin permisos POSIX (ej. algunas rutas Windows de prueba)


def load_ca(*, ca_dir: Path) -> LocalCa:
    """Fail-closed: material ausente -> `CaMaterialMissingError`; presente
    pero corrupto/imposible de parsear -> `CaMaterialInvalidError`. Nunca
    genera una CA nueva implicitamente (seccion 17)."""
    key_path = ca_dir / "ca.key.pem"
    cert_path = ca_dir / "ca.cert.pem"
    if not key_path.is_file() or not cert_path.is_file():
        raise CaMaterialMissingError()
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (ValueError, TypeError) as exc:
        raise CaMaterialInvalidError() from exc
    if not isinstance(key, RSAPrivateKey):
        raise CaMaterialInvalidError()
    return LocalCa(key=key, certificate=cert)


def load_or_generate_ca(*, ca_dir: Path) -> LocalCa:
    """Conveniencia SOLO para entornos de desarrollo/prueba locales (nunca
    para produccion real -- el arranque productivo debe usar `load_ca` y
    fallar cerrado si falta material, aprovisionado por un paso operativo
    explicito fuera de este modulo). Genera y persiste una CA nueva
    unicamente si `ca_dir` esta completamente vacio de material CA."""
    try:
        return load_ca(ca_dir=ca_dir)
    except CaMaterialMissingError:
        ca = generate_ca()
        save_ca(ca, ca_dir=ca_dir)
        return ca


def fingerprint_sha256_hex(cert: x509.Certificate) -> str:
    return cert.fingerprint(hashes.SHA256()).hex()


def build_agent_csr(*, common_name: str) -> tuple[RSAPrivateKey, bytes]:
    """SOLO para el arnes de pruebas (`tests/`) -- simula lo que hara el
    futuro Agente Windows real: genera su propia clave privada (que NUNCA
    sale de este proceso) y construye un CSR real. Nunca invocado por
    codigo de producto del broker."""
    key = _generate_key()
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    return key, csr.public_bytes(serialization.Encoding.PEM)


def _validate_csr(csr: x509.CertificateSigningRequest) -> None:
    """Validaciones (seccion 18 del encargo): firma propia valida (prueba de
    posesion de la clave privada), tipo/tamano de clave en lista permitida
    (RSA >= 2048, misma familia que el spike), sin extensiones solicitadas
    en absoluto (ninguna extension peligrosa NI inofensiva -- un CSR de
    agente no necesita pedir ninguna; aceptar CUALQUIER extension abriria la
    puerta a que un agente intente pedir `BasicConstraints(ca=True)` u otra
    extension que este codigo simplemente ignora, pero que una integracion
    futura menos cuidadosa podria copiar por error). La identidad final
    (tenant/agent_id) NUNCA se toma de `csr.subject` -- ver
    `sign_agent_csr`, que ignora el subject del CSR y fija el suyo propio."""
    if not csr.is_signature_valid:
        raise CsrInvalidError()

    public_key = csr.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise CsrInvalidError()
    if public_key.key_size < _MIN_ALLOWED_RSA_KEY_SIZE:
        raise CsrInvalidError()

    if len(list(csr.extensions)) > 0:
        raise CsrInvalidError()


def parse_and_validate_csr(csr_pem: bytes) -> x509.CertificateSigningRequest:
    try:
        csr = x509.load_pem_x509_csr(csr_pem)
    except ValueError as exc:
        raise CsrInvalidError() from exc
    _validate_csr(csr)
    return csr


def _perform_ca_signature(builder: x509.CertificateBuilder, ca: LocalCa) -> x509.Certificate:
    """Costura deliberada (Domain D.1, Gate 1, seccion 4 del encargo): el
    UNICO punto de este modulo que ejecuta la primitiva criptografica real
    de firma con la clave privada de la CA. Aislada en su propia funcion
    para que las pruebas puedan espiar/contar invocaciones REALES de firma
    (via `unittest.mock.patch("app.broker.pki._perform_ca_signature", ...)`)
    sin depender de parchear un atributo de solo-lectura del objeto nativo
    `RSAPrivateKey` (respaldado por Rust, inmutable a nivel de instancia)."""
    return builder.sign(ca.key, hashes.SHA256())


def sign_agent_csr(*, ca: LocalCa, csr_pem: bytes, agent_common_name: str) -> x509.Certificate:
    """Emite un certificado hoja de AGENTE (nunca de servidor, nunca CA
    intermedia) firmado por la CA local. `agent_common_name` es fijado por
    el BROKER (nunca copiado de `csr.subject`, ver seccion 19: "la identidad
    la controla el backend/broker, no el CSR"). Solo se invoca DESPUES de
    validar el CSR -- `parse_and_validate_csr` debe haberse llamado antes
    (este metodo vuelve a parsear/validar por defensividad, nunca confia en
    que el llamador ya lo hizo)."""
    csr = parse_and_validate_csr(csr_pem)

    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, agent_common_name)]))
        .issuer_name(ca.certificate.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=AGENT_CERT_TTL_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.key.public_key()), critical=False,
        )
    )
    return _perform_ca_signature(builder, ca)


def issue_server_leaf_cert(
    *, ca: LocalCa, common_name: str, dns_sans: list[str], ip_sans: list[str] | None = None, valid_days: int = 825
) -> LocalCa:
    """Certificado de SERVIDOR TLS para los listeners del broker mismo
    (`server.py`) -- distinto de `sign_agent_csr` (certificados de
    CLIENTE/agente). Solo para uso local/pruebas: en un despliegue real, la
    ruta operativa de aprovisionar el certificado de servidor del broker es
    un paso de infraestructura fuera de este modulo; aqui se ofrece para que
    los listeners locales de desarrollo/prueba tengan un certificado de
    servidor valido emitido por la misma CA local."""
    key = _generate_key()
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=valid_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.key.public_key()), critical=False,
        )
    )
    san_entries: list[x509.GeneralName] = [x509.DNSName(d) for d in dns_sans]
    if ip_sans:
        import ipaddress

        san_entries += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ip_sans]
    if san_entries:
        builder = builder.add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
    cert = builder.sign(ca.key, hashes.SHA256())
    return LocalCa(key=key, certificate=cert)


def write_leaf_pem(leaf: LocalCa, *, out_dir: Path, name: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    key_path = out_dir / f"{name}.key.pem"
    cert_path = out_dir / f"{name}.cert.pem"
    key_path.write_bytes(
        leaf.key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(leaf.certificate.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


def new_agent_common_name() -> str:
    """CN de marcador de posicion, sin PII -- la identidad real vive en
    `sol_agents` (fingerprint + fila), nunca en el subject del certificado
    (seccion 19)."""
    return f"sol-agent-{uuid.uuid4().hex[:16]}"
