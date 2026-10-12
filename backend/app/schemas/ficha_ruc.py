"""M2: esquemas de la Ficha RUC (estructurada) y de su revision humana. NUNCA contienen HTML crudo, DOM, cookies ni
credenciales. `extra="forbid"` en todo lo que llega del Agente: un campo no declarado rechaza el mensaje completo."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Address = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]

FICHA_SCHEMA_VERSION = 1


class ActividadEconomica(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["PRINCIPAL", "SECUNDARIA"]
    ciiu: Annotated[str, StringConstraints(pattern=r"^\d{4,6}$")] | None = None
    descripcion: Short


Token = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class DomicilioFiscal(BaseModel):
    """Domicilio fiscal ESTRUCTURADO tal como lo publica la Ficha (cada parte opcional; nada se infiere ni se completa)."""

    model_config = ConfigDict(extra="forbid")
    departamento: Token | None = None
    provincia: Token | None = None
    distrito: Token | None = None
    zona: Token | None = None
    via: Token | None = None
    numero: Token | None = None
    km: Token | None = None
    manzana: Token | None = None
    lote: Token | None = None
    dpto: Token | None = None
    interior: Token | None = None
    referencias: Short | None = None
    condicion_inmueble: Token | None = None


class TributoAfecto(BaseModel):
    """Una fila del Registro de Tributos Afectos. Presentarlo NO implica asignar obligaciones: eso lo decide un profesional."""

    model_config = ConfigDict(extra="forbid")
    tributo: Short
    afecto_desde: date | None = None
    marca_exoneracion: Token | None = None
    exoneracion_desde: date | None = None
    exoneracion_hasta: date | None = None


class RepresentanteLegal(BaseModel):
    """DATO PERSONAL protegido por RBAC (solo `client:write`). Minimizado: sin fecha de nacimiento, direccion, telefono ni correo."""

    model_config = ConfigDict(extra="forbid")
    tipo_documento: Token | None = None
    numero_documento: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)] | None = None
    nombre: Short
    cargo: Token | None = None
    fecha_desde: date | None = None


class EstablecimientoAnexo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codigo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12)] | None = None
    tipo: Token | None = None
    denominacion: Short | None = None
    direccion: Address | None = None
    ubicacion: Short | None = None  # departamento / provincia / distrito tal como se muestran
    condicion_legal: Token | None = None


# Secciones de la Ficha que solo ven quienes pueden editar el cliente (datos personales de terceros).
SENSITIVE_SECTIONS = ("representantes_legales",)


class FichaRucFieldsV1(BaseModel):
    """Campos OFICIALES realmente observados en la Ficha. Todo es opcional salvo el RUC (identidad del contribuyente):
    un campo ausente queda ausente, nunca un valor por defecto."""

    model_config = ConfigDict(extra="forbid")
    ruc: Annotated[str, StringConstraints(pattern=r"^\d{11}$")]
    razon_social: Short | None = None
    tipo_contribuyente: Short | None = None
    nombre_comercial: Short | None = None
    fecha_inscripcion: date | None = None
    fecha_inicio_actividades: date | None = None
    estado_contribuyente: Short | None = None
    condicion_contribuyente: Short | None = None
    domicilio_fiscal: Address | None = None
    sistema_emision_comprobante: Short | None = None
    actividad_comercio_exterior: Short | None = None
    sistema_contabilidad: Short | None = None
    actividades_economicas: list[ActividadEconomica] = Field(default_factory=list, max_length=20)
    comprobantes_pago: list[Line] = Field(default_factory=list, max_length=30)
    sistema_emision_electronica: list[Line] = Field(default_factory=list, max_length=30)
    emisor_electronico_desde: date | None = None
    comprobantes_electronicos: Short | None = None
    afiliado_ple_desde: date | None = None
    padrones: list[Line] = Field(default_factory=list, max_length=30)
    # --- secciones adicionales (aditivas; schema_version sigue en 1) ---
    dependencia_sunat: Short | None = None
    domicilio: DomicilioFiscal | None = None
    tributos_afectos: list[TributoAfecto] = Field(default_factory=list, max_length=40)
    representantes_legales: list[RepresentanteLegal] = Field(default_factory=list, max_length=20)
    establecimientos_anexos: list[EstablecimientoAnexo] = Field(default_factory=list, max_length=60)
    secciones_no_reconocidas: list[Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,40}$")]] = Field(default_factory=list, max_length=10)


class DiffEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    label: str
    current: object | None
    proposed: object | None
    changed: bool


class FichaSnapshotOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    client_id: UUID
    session_id: UUID
    purpose: str
    source: str
    schema_version: int
    retrieved_at: datetime
    review_status: str
    fields: dict
    malformed: list[str]
    diff: list[DiffEntry]
    accepted_fields: list[str] | None
    reviewed_at: datetime | None
    version: int


class AcceptFichaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted_fields: list[str] = Field(min_length=1, max_length=30)
    expected_version: int = Field(ge=1)


class RejectFichaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class StartOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ruc: Annotated[str, StringConstraints(pattern=r"^\d{11}$")]


class OnboardingOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    client_id: UUID
    ruc: str
    status: str
    snapshot_id: UUID | None
    created_at: datetime
    completed_at: datetime | None
    version: int


class CompleteOnboardingRequest(BaseModel):
    """Confirmacion humana unica: acepta los campos OFICIALES elegidos (con correcciones permitidas) y finaliza."""

    model_config = ConfigDict(extra="forbid")
    accepted_fields: list[str] = Field(min_length=1, max_length=30)
    overrides: dict[str, Short] = Field(default_factory=dict, max_length=8)
    activate: bool = False
    expected_version: int = Field(ge=1)


class OfficialRegimeOut(BaseModel):
    """Regimen del impuesto a la renta segun el registro de tributos afectos del ULTIMO snapshot aceptado. `value=None` => no
    confirmado por SUNAT (jamas un valor por defecto)."""

    model_config = ConfigDict(extra="forbid")
    value: str | None
    label: str | None
    source: str | None
    as_of: datetime | None
    snapshot_id: UUID | None


class FichaOverviewOut(BaseModel):
    """Estado de la informacion SUNAT del cliente SIN abrir SUNAT: ultimo snapshot aceptado (con su fecha), propuesta pendiente y
    ultima verificacion sin cambios."""

    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    official_regime: OfficialRegimeOut
    internal_tax_regime: str
    internal_regime_source: str  # MANUAL | SUNAT_FICHA_RUC | NO_REGISTRADO
    internal_regime_changed_at: datetime | None
    latest_accepted: FichaSnapshotOut | None
    pending_snapshot_id: UUID | None
    last_verified_at: datetime | None
    last_unchanged_verification_at: datetime | None
    sensitive_sections_visible: bool
