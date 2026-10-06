"""Fase 5, Bloque 5.2, Domain E2 / E2.1-A -- identidad local del Agente
Windows: clave privada + certificado mTLS emitidos durante el pairing
(Domain D).

INVARIANTE DURO: `AGENT_PRIVATE_KEY_PLAINTEXT_EVER_WRITTEN_TO_FILESYSTEM=NO`.
La clave privada en claro vive UNICAMENTE en memoria del proceso del
agente; jamas se escribe a ningun archivo (ni durable, ni temporal, ni
"efimero").

Diseno final (reemplaza al "puente efimero" de disco de la version
anterior de E2, RECHAZADO -- ver ADR-0072):

1. Al emparejar, la clave privada (generada localmente, nunca transmitida)
   se cifra EN MEMORIA como PKCS#8 cifrado (`ENCRYPTED PRIVATE KEY`) con
   una frase de paso aleatoria de 256 bits generada localmente.
2. A disco van SOLO: el PKCS#8 cifrado y la frase de paso protegida con
   DPAPI (`CryptProtectData`, alcance de USUARIO -- nunca LOCAL_MACHINE).
3. Para el handshake, la frase de paso se descifra con DPAPI en memoria y
   se entrega a OpenSSL mediante el callback `password=` de
   `ssl.SSLContext.load_cert_chain()`; OpenSSL descifra la clave en su
   propia memoria. Ningun archivo con la clave en claro existe en ningun
   momento -- la garantia es ARQUITECTONICA, no un borrado posterior.

Alternativa preferida y NO elegida para v1 (Windows Certificate Store /
CNG con clave no exportable + SChannel): el modulo estandar `ssl` (unica
biblioteca TLS del repositorio, Domain D/E1-B) no puede presentar una
clave residente en CNG como identidad de cliente; exigiria reescribir el
transporte con una biblioteca TLS distinta, no validable en este dominio.
Consecuencia honesta: la clave NO es no-exportable -- es recuperable por
el mismo usuario de Windows (quien puede invocar DPAPI), igual que
cualquier secreto protegido por DPAPI de usuario.

Alcance DPAPI: protege UNICAMENTE la identidad TLS del agente; nunca
sustituye ni toca la jerarquia criptografica de la boveda SOL (Bloque
5.1)."""

from __future__ import annotations

import json
import platform
import secrets
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class IdentityError(Exception):
    """Base de errores de identidad -- nunca incluye material de clave en
    el mensaje."""


class NotPairedError(IdentityError):
    """No existe identidad local -- el agente debe emparejarse primero."""


class IdentityCorruptError(IdentityError):
    """Material presente pero corrupto/no interpretable -- falla cerrado
    (seccion 70 del encargo: nunca se borra/re-empareja en silencio)."""


class IdentityUnprotectError(IdentityError):
    """DPAPI no pudo descifrar el material -- el caso mas comun es que el
    usuario de Windows actual no es el mismo que protegio el material
    originalmente (seccion 71: falla cerrado, nunca debilita la
    proteccion ni migra en silencio). Nunca expone detalle de Windows que
    pudiera ayudar a diagnosticar de mas."""


class PlatformNotSupportedError(IdentityError):
    """Este almacen de identidad es una implementacion EXPLICITA de
    Windows (seccion 7 del encargo: nunca una abstraccion multiplataforma
    falsa) -- se invoco fuera de Windows."""


@dataclass(frozen=True)
class AgentIdentity:
    """Metadata de identidad NO secreta -- la clave privada nunca vive en
    este objeto; se mantiene protegida en disco y solo se descifra
    efimeramente dentro de `build_client_ssl_context`."""

    agent_id: uuid.UUID
    certificate_pem: bytes
    certificate_not_after: datetime


def _is_windows() -> bool:
    return platform.system() == "Windows"


_CERT_FILENAME = "agent.cert.pem"
_ENCRYPTED_KEY_FILENAME = "agent.key.enc.pem"
_PROTECTED_PASSPHRASE_FILENAME = "agent.keypass.dpapi"
_IDENTITY_METADATA_FILENAME = "identity.json"


def encrypt_private_key_pem(private_key_pem: bytes) -> tuple[bytes, bytes]:
    """Cifra EN MEMORIA una clave privada PEM. Devuelve `(encrypted_pem,
    passphrase)`. La frase de paso es hex ASCII (nunca bytes arbitrarios:
    OpenSSL la trata como cadena C y un NUL la truncaria). Independiente de
    plataforma -- el arnes de pruebas Linux ejercita este mismo codigo."""
    from cryptography.hazmat.primitives import serialization

    try:
        key = serialization.load_pem_private_key(private_key_pem, password=None)
    except (ValueError, TypeError) as exc:
        raise IdentityCorruptError() from exc
    passphrase = secrets.token_hex(32).encode("ascii")
    encrypted_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
    )
    return encrypted_pem, passphrase


def build_mtls_client_context(
    *, ca_certificate_pem: bytes, certificate_path: Path, encrypted_key_path: Path, passphrase: bytes
) -> ssl.SSLContext:
    """Contexto mTLS del agente -- mismo patron validado en Domain D (CA +
    cadena de cliente en el MISMO objeto, `verify=<contexto>`). La clave
    llega cifrada; OpenSSL la descifra en memoria via callback."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=ca_certificate_pem.decode("ascii"))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(
        certfile=str(certificate_path), keyfile=str(encrypted_key_path), password=lambda: passphrase
    )
    return ctx


class WindowsDpapiIdentityStore:
    """Unica implementacion productiva. `data_dir` debe ser el directorio
    de datos del agente restringido por ACL (`config.resolve_data_directory`),
    nunca un directorio temporal compartido ni el arbol del repositorio."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    def is_paired(self) -> bool:
        return all(
            (self._data_dir / name).is_file()
            for name in (
                _CERT_FILENAME,
                _ENCRYPTED_KEY_FILENAME,
                _PROTECTED_PASSPHRASE_FILENAME,
                _IDENTITY_METADATA_FILENAME,
            )
        )

    def save(self, *, agent_id: uuid.UUID, certificate_pem: bytes, private_key_pem: bytes) -> None:
        """Persiste la identidad. `private_key_pem` es el PEM en claro SOLO
        en memoria: se cifra aqui y unicamente el PKCS#8 cifrado y la frase
        de paso protegida con DPAPI tocan disco."""
        if not _is_windows():
            raise PlatformNotSupportedError(
                "WindowsDpapiIdentityStore requiere Windows -- ninguna "
                "implementacion multiplataforma existe deliberadamente."
            )
        encrypted_pem, passphrase = encrypt_private_key_pem(private_key_pem)
        protected_passphrase = _dpapi_protect(passphrase, description="luis-ia-contable-sol-agent-identity")
        passphrase = b""

        self._data_dir.mkdir(parents=True, exist_ok=True)
        (self._data_dir / _CERT_FILENAME).write_bytes(certificate_pem)
        (self._data_dir / _ENCRYPTED_KEY_FILENAME).write_bytes(encrypted_pem)
        (self._data_dir / _PROTECTED_PASSPHRASE_FILENAME).write_bytes(protected_passphrase)
        (self._data_dir / _IDENTITY_METADATA_FILENAME).write_text(
            json.dumps({"agent_id": str(agent_id)}), encoding="ascii"
        )

    def load(self) -> AgentIdentity:
        if not _is_windows():
            raise PlatformNotSupportedError(
                "WindowsDpapiIdentityStore requiere Windows -- ninguna "
                "implementacion multiplataforma existe deliberadamente."
            )
        if not self.is_paired():
            raise NotPairedError()

        from cryptography import x509

        try:
            meta = json.loads((self._data_dir / _IDENTITY_METADATA_FILENAME).read_text(encoding="ascii"))
            agent_id = uuid.UUID(meta["agent_id"])
            certificate_pem = (self._data_dir / _CERT_FILENAME).read_bytes()
            cert = x509.load_pem_x509_certificate(certificate_pem)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise IdentityCorruptError() from exc

        return AgentIdentity(
            agent_id=agent_id,
            certificate_pem=certificate_pem,
            certificate_not_after=cert.not_valid_after_utc,
        )

    def build_client_ssl_context(self, *, ca_certificate_pem: bytes) -> ssl.SSLContext:
        """Descifra la frase de paso con DPAPI (memoria) y la entrega a
        OpenSSL por callback; la clave privada en claro nunca toca disco."""
        if not _is_windows():
            raise PlatformNotSupportedError(
                "WindowsDpapiIdentityStore requiere Windows -- ninguna "
                "implementacion multiplataforma existe deliberadamente."
            )
        if not self.is_paired():
            raise NotPairedError()

        try:
            passphrase = _dpapi_unprotect((self._data_dir / _PROTECTED_PASSPHRASE_FILENAME).read_bytes())
        except OSError as exc:
            raise IdentityUnprotectError() from exc

        try:
            return build_mtls_client_context(
                ca_certificate_pem=ca_certificate_pem,
                certificate_path=self._data_dir / _CERT_FILENAME,
                encrypted_key_path=self._data_dir / _ENCRYPTED_KEY_FILENAME,
                passphrase=passphrase,
            )
        except ssl.SSLError as exc:
            raise IdentityCorruptError() from exc
        finally:
            passphrase = b""


# --- DPAPI (ctypes, sin pywin32 -- el acceso a `ctypes.windll` vive
# EXCLUSIVAMENTE dentro de estas funciones, nunca a nivel de modulo, para
# que este archivo siga siendo IMPORTABLE (aunque no usable) fuera de
# Windows -- permite que el resto del paquete se pruebe con componentes
# neutrales a la plataforma segun la seccion 7 del encargo) ---


def _make_data_blob_type():
    import ctypes
    import ctypes.wintypes as wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    return _DATA_BLOB


def _dpapi_bindings():
    """Construye (una sola vez por llamada -- deliberadamente sin cache a
    nivel de modulo, ver el docstring del modulo) los prototipos EXPLICITOS
    de `CryptProtectData`/`CryptUnprotectData`/`LocalFree`. HALLAZGO REAL
    (confirmado ejecutando en Windows real, seccion 74 del encargo): sin
    `argtypes`/`restype` explicitos, `ctypes.windll` en Windows de 64 bits
    intenta empaquetar el puntero devuelto (`LocalFree`) en un entero por
    defecto mas pequeno que un puntero de 64 bits, produciendo
    `ctypes.ArgumentError: int too long to convert` -- un bug real que solo
    se manifiesta con punteros grandes (direcciones altas de heap), nunca
    visible en pruebas superficiales. Declarar los prototipos exactos lo
    corrige de raiz."""
    import ctypes
    import ctypes.wintypes as wintypes

    DATA_BLOB = _make_data_blob_type()

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL

    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL

    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    return DATA_BLOB, crypt32, kernel32


def _dpapi_protect(data: bytes, *, description: str) -> bytes:
    import ctypes

    DATA_BLOB, crypt32, kernel32 = _dpapi_bindings()

    buf_in = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf_in, ctypes.c_void_p))
    blob_out = DATA_BLOB()

    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in), description, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        if blob_out.pbData:
            kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(protected: bytes) -> bytes:
    import ctypes

    DATA_BLOB, crypt32, kernel32 = _dpapi_bindings()

    buf_in = ctypes.create_string_buffer(protected, len(protected))
    blob_in = DATA_BLOB(len(protected), ctypes.cast(buf_in, ctypes.c_void_p))
    blob_out = DATA_BLOB()

    ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        if blob_out.pbData:
            kernel32.LocalFree(blob_out.pbData)
