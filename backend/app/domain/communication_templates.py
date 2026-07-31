"""Catalogo de plantillas de comunicacion asistida por WhatsApp (Fase 3 /
Bloque 3.2, ver ADR-0067, Seccion 8 del pedido). Versionado en codigo, no en
una tabla editable generica todavia -- cada plantilla es un dataclass
inmutable con un renderizador determinista y una allowlist explicita de
variables. Nunca inserta automaticamente contraseñas, credenciales SOL,
tokens, numeros de cuenta, datos bancarios, ni el contenido integro de un
documento -- solo las variables seguras listadas abajo, y siempre queda
editable por el usuario antes de guardarse."""

from __future__ import annotations

from dataclasses import dataclass

# Variables seguras que CUALQUIER plantilla puede declarar como admitidas
# (Seccion 8 del pedido). Nunca se añade aqui un campo que pueda contener
# datos tributarios sensibles (montos de deuda, numeros de cuenta, claves).
ALLOWED_VARIABLES = frozenset(
    {
        "client_business_name",
        "client_ruc",
        "task_title",
        "due_date",
        "document_type",
        "period_label",
        "professional_name",
    }
)


class TemplateNotFoundError(Exception):
    code = "TEMPLATE_NOT_FOUND"


class UnknownTemplateVariableError(Exception):
    code = "UNKNOWN_TEMPLATE_VARIABLE"

    def __init__(self, unknown: set[str]):
        super().__init__(f"Variables no admitidas: {sorted(unknown)}")
        self.unknown = unknown


@dataclass(frozen=True)
class CommunicationTemplate:
    code: str
    version: int
    display_name: str
    description: str
    variables: tuple[str, ...]
    body_template: str

    def render(self, variables: dict[str, str]) -> str:
        """Renderizado determinista: mismo `code`+`version`+variables
        siempre produce el mismo texto. Una variable no declarada por esta
        plantilla es un error explicito (`UnknownTemplateVariableError`),
        nunca se ignora silenciosamente. Una variable admitida pero no
        proporcionada se sustituye por una cadena vacia -- el texto queda
        de todas formas editable antes de guardar."""
        unknown = set(variables) - set(self.variables)
        if unknown:
            raise UnknownTemplateVariableError(unknown)
        safe_values = {name: str(variables.get(name, "") or "") for name in self.variables}
        return self.body_template.format(**safe_values)


_TEMPLATES: dict[str, CommunicationTemplate] = {
    t.code: t
    for t in (
        CommunicationTemplate(
            code="GENERAL_MESSAGE",
            version=1,
            display_name="Mensaje general",
            description="Saludo neutro de punto de partida, para un mensaje libre editado por el usuario.",
            variables=("client_business_name", "professional_name"),
            body_template=(
                "Estimado(a) {client_business_name}, le escribe {professional_name}. "
                "Quedo atento(a) a su respuesta."
            ),
        ),
        CommunicationTemplate(
            code="OBLIGATION_DUE_REMINDER",
            version=1,
            display_name="Recordatorio de vencimiento de obligación",
            description="Recuerda la fecha de vencimiento de una obligación tributaria del periodo indicado.",
            variables=("client_business_name", "period_label", "due_date", "professional_name"),
            body_template=(
                "Estimado(a) {client_business_name}, le recordamos que la obligación tributaria del "
                "periodo {period_label} vence el {due_date}. Quedamos atentos. {professional_name}."
            ),
        ),
        CommunicationTemplate(
            code="DOCUMENT_REQUEST",
            version=1,
            display_name="Solicitud de documento",
            description="Solicita el envío de un tipo de documento para un periodo determinado.",
            variables=(
                "client_business_name", "client_ruc", "document_type", "period_label", "professional_name",
            ),
            body_template=(
                "Estimado(a) {client_business_name} (RUC {client_ruc}), necesitamos que nos envíe "
                "{document_type} correspondiente al periodo {period_label} a la brevedad posible. "
                "Gracias. {professional_name}."
            ),
        ),
        CommunicationTemplate(
            code="PAYMENT_PENDING_REMINDER",
            version=1,
            display_name="Recordatorio de pago pendiente",
            description=(
                "Recuerda que existe un pago pendiente para el periodo indicado -- nunca incluye montos "
                "ni datos bancarios."
            ),
            variables=("client_business_name", "period_label", "professional_name"),
            body_template=(
                "Estimado(a) {client_business_name}, le recordamos que tiene un pago pendiente "
                "correspondiente al periodo {period_label}. Por favor coordinar a la brevedad. "
                "{professional_name}."
            ),
        ),
        CommunicationTemplate(
            code="TASK_FOLLOW_UP",
            version=1,
            display_name="Seguimiento de tarea",
            description="Da seguimiento a una tarea operativa concreta.",
            variables=("client_business_name", "task_title", "professional_name"),
            body_template=(
                "Estimado(a) {client_business_name}, le escribimos para dar seguimiento a: "
                "{task_title}. Quedamos atentos a su respuesta. {professional_name}."
            ),
        ),
    )
}


def get_template(code: str) -> CommunicationTemplate:
    template = _TEMPLATES.get(code)
    if template is None:
        raise TemplateNotFoundError(code)
    return template


def list_templates() -> list[CommunicationTemplate]:
    """Orden estable (por `code`) -- nunca depende del orden de un dict."""
    return sorted(_TEMPLATES.values(), key=lambda t: t.code)
