"""Catalogo semilla de obligaciones tributarias/laborales peruanas.

IMPORTANTE: 'source' describe la obligacion de forma general (nombre del
formulario/sistema y la entidad responsable), sin citar numero de resolucion
ni fecha especifica de norma, porque esas citas exactas deben ser verificadas
por el contador antes de aprobarse para uso real (ver docs/TAX_RULES_MODEL.md).
Ninguna fila de este catalogo contiene fechas de vencimiento: esas se generan
unicamente desde el motor de cronogramas versionado (fase posterior).
"""

OBLIGATION_CATALOG_SEED = [
    {
        "code": "621",
        "name": "IGV - Renta mensual (PDT 621 / Formulario Virtual 621)",
        "description": "Declaracion jurada mensual del IGV y pago a cuenta del Impuesto a la Renta.",
        "periodicity": "mensual",
        "applies_to_regime": ["RER", "RMT", "GENERAL"],
        "source": "SUNAT - Formulario Virtual 621 (IGV - Renta mensual)",
        "source_version": "catalogo-v1-2026",
    },
    {
        "code": "PLAME",
        "name": "Planilla Electronica (PLAME)",
        "description": "Declaracion mensual de la planilla electronica de trabajadores y derechohabientes.",
        "periodicity": "mensual",
        "applies_to_regime": ["RER", "RMT", "GENERAL"],
        "source": "SUNAT / Ministerio de Trabajo - Planilla Electronica (PLAME)",
        "source_version": "catalogo-v1-2026",
    },
    {
        "code": "SIRE-RVIE",
        "name": "SIRE - Registro de Ventas e Ingresos Electronico",
        "description": (
            "Registro electronico de ventas e ingresos dentro del Sistema Integrado "
            "de Registros Electronicos."
        ),
        "periodicity": "mensual",
        "applies_to_regime": ["RER", "RMT", "GENERAL"],
        "source": "SUNAT - Sistema Integrado de Registros Electronicos (SIRE), modulo RVIE",
        "source_version": "catalogo-v1-2026",
    },
    {
        "code": "SIRE-RCE",
        "name": "SIRE - Registro de Compras Electronico",
        "description": (
            "Registro electronico de compras dentro del Sistema Integrado "
            "de Registros Electronicos."
        ),
        "periodicity": "mensual",
        "applies_to_regime": ["RER", "RMT", "GENERAL"],
        "source": "SUNAT - Sistema Integrado de Registros Electronicos (SIRE), modulo RCE",
        "source_version": "catalogo-v1-2026",
    },
    {
        "code": "710",
        "name": "Renta Anual (Formulario Virtual 710)",
        "description": "Declaracion jurada anual del Impuesto a la Renta de tercera categoria.",
        "periodicity": "anual",
        "applies_to_regime": ["RMT", "GENERAL"],
        "source": "SUNAT - Formulario Virtual 710 (Declaracion Jurada Anual del Impuesto a la Renta)",
        "source_version": "catalogo-v1-2026",
    },
    {
        "code": "ITAN",
        "name": "Impuesto Temporal a los Activos Netos (ITAN)",
        "description": (
            "Declaracion y pago del ITAN para contribuyentes del Regimen General "
            "sujetos a este impuesto."
        ),
        "periodicity": "anual",
        "applies_to_regime": ["GENERAL"],
        "source": "SUNAT - Impuesto Temporal a los Activos Netos (ITAN)",
        "source_version": "catalogo-v1-2026",
    },
    {
        "code": "ESSALUD",
        "name": "EsSalud - Aportaciones",
        "description": "Aportaciones mensuales al seguro social de salud de los trabajadores.",
        "periodicity": "mensual",
        "applies_to_regime": ["RER", "RMT", "GENERAL"],
        "source": "EsSalud - Aportaciones al Seguro Social de Salud",
        "source_version": "catalogo-v1-2026",
    },
    {
        "code": "AFPNET",
        "name": "AFPnet - Aportes previsionales",
        "description": "Declaracion y pago de aportes previsionales de trabajadores afiliados a una AFP.",
        "periodicity": "mensual",
        "applies_to_regime": ["RER", "RMT", "GENERAL"],
        "source": "SBS / AFPnet - Sistema Privado de Pensiones",
        "source_version": "catalogo-v1-2026",
    },
    {
        "code": "DETRACCIONES",
        "name": "Sistema de Detracciones (SPOT)",
        "description": "Control de depositos de detraccion sobre operaciones sujetas al sistema.",
        "periodicity": "eventual",
        "applies_to_regime": ["RER", "RMT", "GENERAL"],
        "source": "SUNAT - Sistema de Pago de Obligaciones Tributarias (SPOT / Detracciones)",
        "source_version": "catalogo-v1-2026",
    },
    {
        "code": "RETENCIONES",
        "name": "Regimen de Retenciones del IGV",
        "description": "Control de retenciones del IGV efectuadas por agentes de retencion designados.",
        "periodicity": "mensual",
        "applies_to_regime": ["RMT", "GENERAL"],
        "source": "SUNAT - Regimen de Retenciones del IGV",
        "source_version": "catalogo-v1-2026",
    },
    {
        "code": "PERCEPCIONES",
        "name": "Regimen de Percepciones del IGV",
        "description": "Control de percepciones del IGV aplicadas por agentes de percepcion designados.",
        "periodicity": "mensual",
        "applies_to_regime": ["RMT", "GENERAL"],
        "source": "SUNAT - Regimen de Percepciones del IGV",
        "source_version": "catalogo-v1-2026",
    },
]
