"""M2: parser puro de la Ficha RUC -- estructura completa, secciones opcionales ausentes, valores mal formados,
estructura inesperada, normalizacion. Nada se inventa."""

from windows_agent import ficha_parser as fp

FULL = [
    ("Número de RUC:", ["20999999990 - EMPRESA FICTICIA S.A.C."]),
    ("Tipo Contribuyente:", ["SOCIEDAD ANONIMA CERRADA"]),
    ("Nombre Comercial:", ["-"]),
    ("Fecha de Inscripción:", ["15/03/2015"]),
    ("Fecha de Inicio de Actividades:", ["01/04/2015"]),
    ("Estado del Contribuyente:", ["ACTIVO"]),
    ("Condición del Contribuyente:", ["HABIDO"]),
    ("Domicilio Fiscal:", ["AV. FICTICIA 123 - LIMA"]),
    ("Sistema Emisión de Comprobante:", ["MANUAL/COMPUTARIZADO"]),
    ("Actividad Comercio Exterior:", ["SIN ACTIVIDAD"]),
    ("Sistema Contabilidad:", ["COMPUTARIZADO"]),
    ("Actividad(es) Económica(s):", ["Principal - 6201 - PROGRAMACION", "Secundaria 1 - 7020 - CONSULTORIA"]),
    ("Comprobantes de Pago c/aut. de impresión (F. 806 u 816):", ["FACTURA", "BOLETA DE VENTA"]),
    ("Sistema de Emisión Electrónica:", ["FACTURA PORTAL"]),
    ("Emisor electrónico desde:", ["01/06/2018"]),
    ("Comprobantes Electrónicos:", ["FACTURA (desde 01/06/2018)"]),
    ("Afiliado al PLE desde:", ["-"]),
    ("Padrones:", ["NINGUNO"]),
]


def test_complete_ficha_is_parsed_and_normalized():
    out = fp.parse_ficha(FULL)
    f = out.fields
    assert f["ruc"] == "20999999990" and f["razon_social"] == "EMPRESA FICTICIA S.A.C."
    assert f["fecha_inscripcion"] == "2015-03-15" and f["fecha_inicio_actividades"] == "2015-04-01"
    assert f["emisor_electronico_desde"] == "2018-06-01"
    assert f["actividades_economicas"] == [
        {"tipo": "PRINCIPAL", "ciiu": "6201", "descripcion": "PROGRAMACION"},
        {"tipo": "SECUNDARIA", "ciiu": "7020", "descripcion": "CONSULTORIA"},
    ]
    assert f["comprobantes_pago"] == ["FACTURA", "BOLETA DE VENTA"]
    assert out.malformed == [] and out.schema_version == fp.SCHEMA_VERSION == 1


def test_dash_and_none_markers_mean_absent_never_a_value():
    f = fp.parse_ficha(FULL).fields
    assert "nombre_comercial" not in f and "afiliado_ple_desde" not in f and "padrones" not in f


def test_optional_sections_missing_are_simply_absent():
    out = fp.parse_ficha([FULL[0], FULL[5]])
    assert set(out.fields) == {"ruc", "razon_social", "estado_contribuyente"}


def test_malformed_values_are_omitted_and_reported_not_approximated():
    rows = [FULL[0], ("Fecha de Inscripción:", ["31/31/2015"]), ("Fecha de Inicio de Actividades:", ["2015-04-01"]),
            ("Actividad(es) Económica(s):", ["algo sin formato"])]
    out = fp.parse_ficha(rows)
    assert "fecha_inscripcion" not in out.fields and "fecha_inicio_actividades" not in out.fields
    assert set(out.malformed) == {"fecha_inscripcion", "fecha_inicio_actividades", "actividades_economicas"}


def test_ruc_must_be_eleven_digits():
    for bad in ("2099999999", "ABC", "20999999990X - NOMBRE"):
        out = fp.parse_ficha([("Número de RUC:", [bad])])
        assert "ruc" not in out.fields and out.malformed == ["ruc_y_razon_social"]
    assert fp.parse_ficha([("Número de RUC:", ["20999999990"])]).fields == {"ruc": "20999999990"}


def test_unexpected_portal_shape_yields_no_fields():
    assert fp.parse_ficha([]).fields == {}
    assert fp.parse_ficha([("Texto cualquiera:", ["x"]), ("Otro dato", ["y"])]).fields == {}


def test_caption_matching_is_exact_after_normalization_only():
    assert fp.normalize_caption("  Número   de RUC : ") == "numero de ruc"
    out = fp.parse_ficha([("NUMERO DE RUC", ["20999999990 - X"]), ("Número de RUC adicional:", ["20111111119 - Y"])])
    assert out.fields["ruc"] == "20999999990"  # la variante con texto extra NO coincide
    assert out.captions_found == ["ruc_y_razon_social"]


def test_repeated_caption_keeps_the_first_and_ignores_the_rest():
    out = fp.parse_ficha([("Número de RUC:", ["20999999990 - A"]), ("Número de RUC:", ["20111111119 - B"])])
    assert out.fields["ruc"] == "20999999990"


def test_values_are_bounded():
    long = "X" * 1000
    f = fp.parse_ficha([FULL[0], ("Domicilio Fiscal:", [long]), ("Tipo Contribuyente:", [long])]).fields
    assert len(f["domicilio_fiscal"]) <= 300 and len(f["tipo_contribuyente"]) <= 300


def test_caption_list_is_the_only_extraction_surface():
    assert len(fp.CAPTION_FORMS) == len(set(fp.CAPTION_FORMS)) == 40  # 26 anteriores + dependencia + 13 rotulos del domicilio fiscal del Reporte oficial
    assert fp.CAPTIONS_LIVE_VALIDATED is False  # honestidad: los rotulos aun no se confirmaron en vivo


# --------------------------------------------------------------- estructura REAL observada en vivo (sonda saneada de la aceptacion M2)

LIVE_ROWS = [  # cada fila real son 3 celdas: rotulo | ":" | valor  -> las "lineas de valor" son [":", valor]
    ("Apellidos y Nombres o Razón Social:", [":", "EMPRESA FICTICIA ZETA S.A.C."]),
    ("Tipo de Contribuyente:", [":", "SOCIEDAD ANONIMA CERRADA"]),
    ("Fecha de Inscripción:", [":", "15/03/2015"]),
    ("Fecha de Inicio de Actividades:", [":", "01/04/2015"]),
    ("Estado del Contribuyente:", [":", "ACTIVO"]),
    ("Condición de Domicilio Fiscal:", [":", "HABIDO"]),
    ("Emisor electrónico desde:", [":", "01/06/2018"]),
    ("Comprobantes Electrónicos:", [":", "FACTURA (desde 01/06/2018)"]),
    ("Nombre Comercial:", [":", "-"]),
    ("Actividad Económica Principal:", [":", "6201 - ACTIVIDADES DE PROGRAMACION INFORMATICA"]),
    ("Actividad Económica Secundaria 1:", [":", "7020 - ACTIVIDADES DE CONSULTORIA DE GESTION"]),
    ("Actividad Económica Secundaria 2:", [":", "8299 - OTRAS ACTIVIDADES DE SERVICIO"]),
    ("Sistema Emisión Comprobantes de Pago:", [":", "MANUAL/COMPUTARIZADO"]),
    ("Sistema de Contabilidad:", [":", "COMPUTARIZADO"]),
    ("Actividad de Comercio Exterior:", [":", "SIN ACTIVIDAD"]),
]


def test_live_three_cell_rows_do_not_leak_the_separator_into_values_or_dates():
    result = fp.parse_ficha(LIVE_ROWS)
    f = result.fields
    assert result.malformed == []  # antes: 3 fechas "malformed" por el ":" de la celda separadora
    assert f["fecha_inscripcion"] == "2015-03-15" and f["fecha_inicio_actividades"] == "2015-04-01" and f["emisor_electronico_desde"] == "2018-06-01"
    assert f["razon_social"] == "EMPRESA FICTICIA ZETA S.A.C." and f["estado_contribuyente"] == "ACTIVO"
    assert f["condicion_contribuyente"] == "HABIDO" and "nombre_comercial" not in f  # "-" => ausente
    assert [a["ciiu"] for a in f["actividades_economicas"]] == ["6201", "7020", "8299"]
    assert [a["tipo"] for a in f["actividades_economicas"]] == ["PRINCIPAL", "SECUNDARIA", "SECUNDARIA"]
    assert f["sistema_contabilidad"] == "COMPUTARIZADO" and f["actividad_comercio_exterior"] == "SIN ACTIVIDAD"
    assert "ruc" not in f  # sin encabezado oficial no hay identidad


def test_live_validated_captions_are_a_subset_of_what_the_parser_understands():
    for caption in fp.LIVE_VALIDATED_CAPTIONS:
        assert fp.is_known_caption(caption), caption
    assert fp.is_known_caption("actividad economica secundaria 7") and not fp.is_known_caption("actividad economica secundaria abc")
    assert fp.CAPTIONS_LIVE_VALIDATED is False  # la ubicacion del RUC aun no esta validada en vivo


def test_content_is_ready_only_with_state_type_and_name():
    assert not fp.content_ready(fp.parse_ficha([]))
    assert not fp.content_ready(fp.parse_ficha(LIVE_ROWS[:2]))  # solo denominacion y tipo: aun cargando
    assert not fp.content_ready(fp.parse_ficha([("Tipo de Contribuyente:", [":", "X"]), ("Estado del Contribuyente:", [":", "ACTIVO"])]))  # sin denominacion
    assert fp.content_ready(fp.parse_ficha(LIVE_ROWS))


def _h(ruc="20123456786", table=0, row=0, cell=0):
    return {"ruc": ruc, "table": table, "row": row, "cell": cell}


def test_ruc_comes_only_from_the_unique_official_header_at_its_observed_position():
    result = fp.parse_ficha(LIVE_ROWS, [_h()])
    assert result.fields["ruc"] == "20123456786" and result.ruc_source == "OFFICIAL_HEADER"
    assert "ruc" not in fp.parse_ficha(LIVE_ROWS, []).fields
    assert "ruc" not in fp.parse_ficha(LIVE_ROWS, None).fields
    assert "ruc" not in fp.parse_ficha(LIVE_ROWS, [_h(), _h("20999999990", row=1)]).fields
    assert "ruc" not in fp.parse_ficha(LIVE_ROWS, [_h(), _h()]).fields
    assert "ruc" not in fp.parse_ficha(LIVE_ROWS, [_h(table=1)]).fields
    assert "ruc" not in fp.parse_ficha(LIVE_ROWS, [_h(cell=1)]).fields
    assert "ruc" not in fp.parse_ficha(LIVE_ROWS, [_h("2012345678")]).fields
    assert "ruc" not in fp.parse_ficha(LIVE_ROWS, [_h("2012345678a")]).fields


def test_header_conflicting_with_a_caption_row_ruc_is_rejected_and_reported():
    row = ("Número de RUC:", [":", "20555555559 - EMPRESA FICTICIA ZETA S.A.C."])
    conflict = fp.parse_ficha([row] + LIVE_ROWS, [_h()])
    assert "ruc" not in conflict.fields and "ruc_conflict" in conflict.malformed and conflict.ruc_source is None
    agree = fp.parse_ficha([("Número de RUC:", [":", "20123456786 - EMPRESA FICTICIA ZETA S.A.C."])] + LIVE_ROWS, [_h()])
    assert agree.fields["ruc"] == "20123456786"


def test_activity_lines_with_and_without_kind_prefix_are_parsed_without_inventing_codes():
    rows = [
        ("Actividad Económica Principal:", [":", "Principal - 6201 - ACTIVIDADES DE PROGRAMACION INFORMATICA"]),
        ("Actividad Económica Secundaria 1:", [":", "Secundaria 1 - 7020 - ACTIVIDADES DE CONSULTORIA DE GESTION"]),
        ("Actividad Económica Secundaria 2:", [":", "8299 - OTRAS ACTIVIDADES DE SERVICIO"]),
    ]
    acts = fp.parse_ficha(rows).fields["actividades_economicas"]
    assert [(a["tipo"], a["ciiu"]) for a in acts] == [("PRINCIPAL", "6201"), ("SECUNDARIA", "7020"), ("SECUNDARIA", "8299")]

def test_live_structure_output_validates_against_the_backend_schema_and_proposal_fields():
    from app.schemas.ficha_ruc import FichaRucFieldsV1

    result = fp.parse_ficha(LIVE_ROWS, [_h()])
    assert result.malformed == []
    model = FichaRucFieldsV1.model_validate(result.fields)
    assert model.ruc == "20123456786" and model.razon_social == "EMPRESA FICTICIA ZETA S.A.C."


# --- secciones adicionales (domicilio estructurado, tributos afectos, representantes, establecimientos): datos SINTETICOS ---

DOMICILIO_ROWS = [
    ("Departamento:", [":", "DEPARTAMENTO FICTICIO"]), ("Provincia:", [":", "PROVINCIA FICTICIA"]), ("Distrito:", [":", "DISTRITO FICTICIO"]),
    ("Tipo y Nombre Zona:", [":", "URB. ZONA FICTICIA"]), ("Tipo y Nombre Vía:", [":", "AV. VIA FICTICIA"]), ("Nro:", [":", "123"]),
    ("Km:", [":", "-"]), ("Mz:", [":", "-"]), ("Condición del inmueble declarado como Domicilio Fiscal:", [":", "PROPIO"]),
    ("Dependencia SUNAT:", [":", "0000 - DEPENDENCIA FICTICIA"]),
]
TITLES = [{"table": 3, "title": "registro de tributos afectos"}, {"table": 5, "title": "representantes legales"}, {"table": 7, "title": "establecimientos anexos"}]
GRIDS = [
    {"table": 3, "cells": ["Tributo", "Afecto desde", "Marca de Exoneración", "Desde", "Hasta"]},
    {"table": 3, "cells": ["IGV - OPER. INT. - CTA. PROPIA", "01/05/2019", "-", "-", "-"]},
    {"table": 3, "cells": ["RENTA - REGIMEN MYPE TRIBUTARIO", "01/05/2019", "-", "-", "-"]},
    {"table": 5, "cells": ["Tipo y Número de Documento", "Apellidos y Nombres", "Cargo", "Fecha de Nacimiento", "Fecha Desde", "Dirección", "Teléfono", "Correo"]},
    {"table": 5, "cells": ["DOC. NACIONAL DE\nIDENTIDAD\n00000001", "PERSONA FICTICIA UNO", "TITULAR-GERENTE", "01/01/1980", "25/02/2019", "CALLE SECRETA 1", "999000111", "x@y.z"]},
    {"table": 7, "cells": ["Código", "Tipo", "Denominación", "Domicilio", "Ubigeo", "Condición Legal", "Licencia"]},
    {"table": 7, "cells": ["0001", "SUCURSAL", "ANEXO FICTICIO", "CALLE FICTICIA 1", "LIMA LIMA SAN MARTIN", "ALQUILADO", "-"]},
]


def test_structured_domicilio_dependencia_and_sections_are_extracted_from_the_official_report_structure():
    result = fp.parse_ficha(LIVE_ROWS + DOMICILIO_ROWS, [_h()], GRIDS, TITLES)
    f = result.fields
    assert f["domicilio"] == {"departamento": "DEPARTAMENTO FICTICIO", "provincia": "PROVINCIA FICTICIA", "distrito": "DISTRITO FICTICIO",
                              "zona": "URB. ZONA FICTICIA", "via": "AV. VIA FICTICIA", "numero": "123", "condicion_inmueble": "PROPIO"}
    assert f["dependencia_sunat"].startswith("0000")
    assert [t["tributo"] for t in f["tributos_afectos"]] == ["IGV - OPER. INT. - CTA. PROPIA", "RENTA - REGIMEN MYPE TRIBUTARIO"]
    assert f["tributos_afectos"][1]["afecto_desde"] == "2019-05-01" and "marca_exoneracion" not in f["tributos_afectos"][1]
    assert f["representantes_legales"] == [{"nombre": "PERSONA FICTICIA UNO", "numero_documento": "00000001", "tipo_documento": "DOC. NACIONAL DE IDENTIDAD",
                                            "cargo": "TITULAR-GERENTE", "fecha_desde": "2019-02-25"}]
    assert f["establecimientos_anexos"][0]["codigo"] == "0001" and f["establecimientos_anexos"][0]["condicion_legal"] == "ALQUILADO"
    assert "secciones_no_reconocidas" not in f and result.malformed == []


def test_representative_personal_data_beyond_the_minimum_is_discarded_in_the_agent():
    blob = repr(fp.parse_ficha(LIVE_ROWS, [_h()], GRIDS, TITLES).fields)
    for discarded in ("01/01/1980", "1980-01-01", "CALLE SECRETA", "999000111", "x@y.z"):
        assert discarded not in blob


def test_unrecognised_headers_fail_closed_and_are_reported_not_guessed():
    grids = [{"table": 5, "cells": ["Columna A", "Columna B", "Columna C", "Columna D"]}, {"table": 5, "cells": ["a", "b", "c", "d"]},
             {"table": 7, "cells": ["X", "Y", "Z", "W"]}, {"table": 7, "cells": ["1", "2", "3", "4"]}]
    result = fp.parse_ficha(LIVE_ROWS, [_h()], grids, TITLES)
    assert "representantes_legales" not in result.fields and "establecimientos_anexos" not in result.fields
    assert result.fields["secciones_no_reconocidas"] == ["establecimientos_anexos", "representantes_legales"]
    # sin tablas ni titulos: simplemente nada
    assert "tributos_afectos" not in fp.parse_ficha(LIVE_ROWS, [_h()], [], []).fields


def test_malformed_tribute_dates_are_omitted_and_reported():
    grids = [{"table": 3, "cells": ["Tributo", "Afecto desde", "Marca", "Desde", "Hasta"]}, {"table": 3, "cells": ["RENTA - X", "31/31/2019", "-", "-", "-"]},
             {"table": 3, "cells": ["SENCICO", "01/05/2019", "-", "-", "-"]}]
    result = fp.parse_ficha(LIVE_ROWS, [_h()], grids, TITLES[:1])
    assert [t["tributo"] for t in result.fields["tributos_afectos"]] == ["SENCICO"] and "tributo_fecha" in result.malformed


def test_output_validates_against_the_backend_schema_including_the_new_sections():
    from app.schemas.ficha_ruc import FichaRucFieldsV1

    fields = fp.parse_ficha(LIVE_ROWS + DOMICILIO_ROWS, [_h()], GRIDS, TITLES).fields
    model = FichaRucFieldsV1.model_validate(fields)
    assert model.domicilio.distrito == "DISTRITO FICTICIO" and len(model.tributos_afectos) == 2 and model.representantes_legales[0].cargo == "TITULAR-GERENTE"
