"""FIXTURES SINTETICOS M2 -- SOLO PARA PRUEBAS. Replican la ESTRUCTURA del shell autenticado, del menu y de la Ficha RUC
observada en el reconocimiento LIVE (ids, jerarquia nivel1..nivel4, iframe de la aplicacion, marcadores del frame de la
Ficha), con datos FICTICIOS. No copian HTML real de SUNAT.

El arbol del menu se construye con JavaScript porque el DOM real anida `li` dentro de `li` (el parser HTML lo impediria).

Escenarios (`portal.set_scenario("m2_...")`):
  m2_success            shell completo; Ficha RUC navegable; frame de la Ficha valido
  m2_no_marker          shell SIN `ul#ulMenu01` (marcador ausente)
  m2_lookalike          pagina con `#divCabecera` + `#btnSalir` pero sin el resto del shell
  m2_login_like_shell   shell completo + control de contrasena presente (no es el shell)
  m2_no_ficha_entry     el menu no ofrece "Ficha RUC"
  m2_ficha_bad_frame    el frame de la Ficha carece de `#popupcabinas`
  m2_ficha_wrong_ruc    la Ficha corresponde a OTRO RUC
  m2_ficha_partial      secciones opcionales ausentes + una fecha mal formada
  m2_notice_overlay     aviso MODAL (confirmar datos de contacto) que cubre todo el menu hasta que el HUMANO lo cierra;
                        sus botones llaman a /contacto/* (RUTAS PROHIBIDAS: el agente jamas debe pulsarlos)
  m2_notice_popup       ventana emergente informativa (otra Page) que NO cubre el menu
  m2_login_hop*         el login pasa por una pagina de transito del host de aterrizaje (observado en el preflight real)"""

from __future__ import annotations

FICHA_PATH = "/ol-ti-itmoddatruc/fixture-0001"
FICHA_RUC = "20999999990"
OTHER_RUC = "20100070970"

_ROWS = [
    ("Número de RUC:", "{ruc} - EMPRESA FICTICIA FIXTURE S.A.C."),
    ("Tipo Contribuyente:", "SOCIEDAD ANONIMA CERRADA"),
    ("Nombre Comercial:", "-"),
    ("Fecha de Inscripción:", "15/03/2015"),
    ("Fecha de Inicio de Actividades:", "01/04/2015"),
    ("Estado del Contribuyente:", "ACTIVO"),
    ("Condición del Contribuyente:", "HABIDO"),
    ("Domicilio Fiscal:", "AV. FICTICIA 123 - LIMA - LIMA - MIRAFLORES"),
    ("Sistema Emisión de Comprobante:", "MANUAL/COMPUTARIZADO"),
    ("Actividad Comercio Exterior:", "SIN ACTIVIDAD"),
    ("Sistema Contabilidad:", "COMPUTARIZADO"),
    ("Actividad(es) Económica(s):", "Principal - 6201 - ACTIVIDADES DE PROGRAMACION INFORMATICA|Secundaria 1 - 7020 - ACTIVIDADES DE CONSULTORIA DE GESTION"),
    ("Comprobantes de Pago c/aut. de impresión (F. 806 u 816):", "FACTURA|BOLETA DE VENTA"),
    ("Sistema de Emisión Electrónica:", "FACTURA PORTAL DESDE 01/06/2018"),
    ("Emisor electrónico desde:", "01/06/2018"),
    ("Comprobantes Electrónicos:", "FACTURA (desde 01/06/2018)"),
    ("Afiliado al PLE desde:", "-"),
    ("Padrones:", "NINGUNO"),
]


_LIVE_NAME = "EMPRESA FICTICIA FIXTURE S.A.C."
# ESTRUCTURA REAL observada en vivo (sonda saneada): cada fila son 3 celdas rotulo | ":" | valor; la Ficha real NO trae fila "Numero de RUC".
_LIVE_ROWS = [
    ("Apellidos y Nombres o Razón Social:", _LIVE_NAME),
    ("Tipo de Contribuyente:", "SOCIEDAD ANONIMA CERRADA"),
    ("Fecha de Inscripción:", "15/03/2015"),
    ("Fecha de Inicio de Actividades:", "01/04/2015"),
    ("Estado del Contribuyente:", "ACTIVO"),
    ("Condición de Domicilio Fiscal:", "HABIDO"),
    ("Emisor electrónico desde:", "01/06/2018"),
    ("Comprobantes Electrónicos:", "FACTURA (desde 01/06/2018)"),
    ("Nombre Comercial:", "-"),
    ("Actividad Económica Principal:", "6201 - ACTIVIDADES DE PROGRAMACION INFORMATICA"),
    ("Actividad Económica Secundaria 1:", "7020 - ACTIVIDADES DE CONSULTORIA DE GESTION"),
    ("Sistema Emisión Comprobantes de Pago:", "MANUAL/COMPUTARIZADO"),
    ("Sistema de Contabilidad:", "COMPUTARIZADO"),
    ("Actividad de Comercio Exterior:", "SIN ACTIVIDAD"),
]


def live_ficha_html(scenario: str) -> str:
    """Ficha con la estructura REAL observada: la primera tabla trae una celda `td.T1` "FICHA RUC: <RUC>" + (otra linea) la denominacion, y
    cada dato son 3 celdas rotulo | ":" | valor. NO hay fila "Numero de RUC". Variantes:
      m2_ficha_live_nested            el RUC dentro de elementos anidados (b/span) con <br>
      m2_ficha_live_spaced            saltos de linea y espacios multiples dentro del encabezado
      m2_ficha_live_no_ruc_cell       sin encabezado (identificador ausente)
      m2_ficha_live_dup_header        DOS encabezados "FICHA RUC" (identificador duplicado)
      m2_ficha_live_header_not_first  el encabezado NO esta en la primera tabla (no es el oficial)
      m2_ficha_live_incidental_number numeros de 11 digitos incidentales (enlace, valor) sin el prefijo "FICHA RUC"
      m2_ficha_live_wrong_ruc         el encabezado trae OTRO RUC
      m2_ficha_live_prefixed_activities   actividades con prefijo "Principal - " / "Secundaria N - "
      m2_ficha_live_loading / _never_loads   contenido aun cargando / documento parcial que nunca termina"""
    ruc = OTHER_RUC if scenario == "m2_ficha_live_wrong_ruc" else FICHA_RUC
    rows = list(_LIVE_ROWS)
    if scenario == "m2_ficha_live_prefixed_activities":
        rows = [(c, f"Principal - {v}") if c.startswith("Actividad Económica Principal") else (c, f"Secundaria 1 - {v}") if c.startswith("Actividad Económica Secundaria") else (c, v) for c, v in rows]
    if scenario == "m2_ficha_live_incidental_number":
        rows = rows + [("Telefono de referencia:", "20111111119"), ("Enlace:", "<a href='/x'>20222222228</a>")]

    def header_table(extra_class: str = "") -> str:
        if scenario == "m2_ficha_live_nested":
            cell = f"<b>FICHA RUC:</b> <span>{ruc}</span><br><span>{_LIVE_NAME}</span>"
        elif scenario == "m2_ficha_live_spaced":
            cell = f"FICHA   RUC :\n    {ruc}\n   {_LIVE_NAME}  "
        else:
            cell = f"FICHA RUC: {ruc}<br>{_LIVE_NAME}"
        return f"<table class='form-table'><tr><td class='T1{extra_class}'>{cell}</td><td></td><td></td></tr></table>"

    if scenario in {"m2_ficha_live_no_ruc_cell", "m2_ficha_live_incidental_number"}:
        header = ""
    elif scenario == "m2_ficha_live_dup_header":
        header = header_table() + header_table()
    elif scenario == "m2_ficha_live_header_not_first":
        header = "<table class='form-table'><tr><td>Consulta realizada (fixture)</td><td></td><td></td></tr></table>" + header_table()
    else:
        header = header_table()

    def row(caption: str, value: str) -> str:
        return f"<table class='form-table'><tr><td class='bgn'>{caption}</td><td>:</td><td class='bg'>{value}</td></tr></table>"

    markers = "<input type='hidden' id='popupcabinas'><input type='hidden' id='validaCIIUvsCamRegRTAESP'>"
    if scenario in {"m2_ficha_live_loading", "m2_ficha_live_never_loads"}:
        skeleton = "".join("<table class='form-table'><tr><td></td><td></td><td></td></tr></table>" for _ in range(6))
        fill = ""
        if scenario == "m2_ficha_live_loading":
            body = header + "".join(row(c, v) for c, v in rows)
            fill = f"<script>setTimeout(function(){{document.getElementById('contenido').innerHTML = {body!r};}}, 2500);</script>"
        return f"<!doctype html><html><head><title>Ficha RUC (fixture)</title></head><body>{markers}<div id='contenido'>{skeleton}</div>{fill}</body></html>"
    return (
        "<!doctype html><html><head><title>Ficha RUC (fixture)</title></head><body>"
        + markers + header + "".join(row(c, v) for c, v in rows) + sections_html(scenario) + "</body></html>"
    )


_DOMICILIO = [
    ("Departamento", "DEPARTAMENTO FICTICIO"), ("Provincia", "PROVINCIA FICTICIA"), ("Distrito", "DISTRITO FICTICIO"),
    ("Tipo y Nombre Zona", "URB. ZONA FICTICIA"), ("Tipo y Nombre Vía", "AV. VIA FICTICIA"), ("Nro", "123"), ("Km", "-"),
    ("Condición del inmueble declarado como Domicilio Fiscal", "PROPIO"),
]


def sections_html(scenario: str) -> str:
    """Secciones adicionales del Reporte oficial (domicilio estructurado, tributos afectos, representantes, establecimientos) con datos
    SINTETICOS. Solo en `m2_ficha_live_sections`; `m2_ficha_live_sections_headers_unknown` cambia los encabezados para probar el fallo cerrado."""
    if scenario not in {"m2_ficha_live_sections", "m2_ficha_live_sections_headers_unknown", "m2_ficha_live_sections_collapsed", "m2_ficha_live_sections_absent",
                        "m2_ficha_live_sections_no_header"}:
        return ""
    unknown = scenario.endswith("headers_unknown")
    collapsed = scenario.endswith("collapsed")  # contenido presente en el DOM pero plegado (display:none): `innerText` vacio
    absent = scenario.endswith("absent")  # solo los titulos: la seccion no trae contenido
    no_header = scenario.endswith("no_header")  # tributos sin fila de encabezado

    def title(text: str) -> str:
        return f"<table class='form-table'><tr><td class='bgn'>{text}</td><td></td><td></td></tr></table>"

    def wrap(html: str) -> str:
        if absent:
            return ""
        return f"<div class='collapse' style='display:none'>{html}</div>" if collapsed else html

    def grid(header: list[str], data: list[list[str]], with_header: bool = True) -> str:
        head = "".join(f"<th>{'Columna ' + str(i) if unknown else h}</th>" for i, h in enumerate(header)) if with_header else ""
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in data)
        return wrap(f"<table class='grid'>{('<tr>' + head + '</tr>') if head else ''}{body}</table>")

    out = [title("Datos de Domicilio Fiscal")]
    out += [wrap(f"<table class='form-table'><tr><td class='bgn'>{c}</td><td>:</td><td class='bg'>{v}</td></tr></table>") for c, v in _DOMICILIO]
    out.append(title("Registro de Tributos Afectos"))
    out.append(grid(["Tributo", "Afecto desde", "Marca de Exoneración", "Desde", "Hasta"], [
        ["IGV - OPER. INT. - CTA. PROPIA", "01/05/2019", "-", "-", "-"], ["RENTA - REGIMEN MYPE TRIBUTARIO", "01/05/2019", "-", "-", "-"],
        ["SENCICO", "01/05/2019", "-", "-", "-"]], with_header=not no_header))
    out.append(title("Representantes Legales"))
    out.append(grid(["Tipo y Número de Documento", "Apellidos y Nombres", "Cargo", "Fecha de Nacimiento", "Fecha Desde", "Dirección"], [
        ["DOC. NACIONAL DE IDENTIDAD<br>00000001", "PERSONA FICTICIA UNO", "TITULAR-GERENTE", "01/01/1980", "25/02/2019", "CALLE SECRETA 1"]]))
    out.append(title("Establecimientos Anexos"))
    out.append(grid(["Código", "Tipo", "Denominación", "Domicilio", "Ubigeo", "Condición Legal"], [
        ["0001", "SUCURSAL", "ANEXO FICTICIO", "CALLE FICTICIA 1", "LIMA LIMA", "ALQUILADO"]]))
    return "".join(out)


def ficha_html(scenario: str) -> str:
    if scenario.startswith("m2_ficha_live"):
        return live_ficha_html(scenario)
    ruc = OTHER_RUC if scenario == "m2_ficha_wrong_ruc" else FICHA_RUC
    rows = list(_ROWS)
    if scenario == "m2_ficha_partial":
        rows = [r for r in rows if r[0].split(":")[0] not in {"Sistema Contabilidad", "Padrones", "Afiliado al PLE desde"}]
        rows = [(c, "31/31/2015") if c.startswith("Fecha de Inscripci") else (c, v) for c, v in rows]
    tables = []
    for caption, value in rows:
        cell = "<br>".join(value.split("|")).replace("{ruc}", ruc)
        tables.append(f"<table class='form-table'><tr><td class='bgn'>{caption}</td><td class='bg'>{cell}</td></tr></table>")
    markers = "" if scenario == "m2_ficha_bad_frame" else (
        "<input type='hidden' id='popupcabinas'><input type='hidden' id='validaCIIUvsCamRegRTAESP'>"
    )
    return f"""<!doctype html><html><head><title>Ficha RUC (fixture)</title></head><body>
{markers}{''.join(tables)}
<form><input type='button' class='form-button' id='btnImprimirFixture' value='Imprimir'
 onclick="fetch('/ficha/print')"></form>
<form><input type='button' class='form-button' id='btnModificarFixture' value='Modificar'
 onclick="fetch('/ficha/modify')"></form>
</body></html>"""


def shell_html(scenario: str, ficha_url: str) -> str:
    no_marker = scenario == "m2_no_marker"
    lookalike = scenario == "m2_lookalike"
    login_like = scenario == "m2_login_like_shell"
    ficha_entry = "false" if scenario == "m2_no_ficha_entry" else "true"
    header_nav = "" if (no_marker or lookalike) else (
        "<div id='divNavPrinicipal' class='navbar-collapse collapse'><ul id='ulMenu01' class='nav navbar-nav navbar-right'>"
        "<li id='liOpcionFavorito' class='dropdown'><a id='aOpcionFavorito'>Favoritos</a></li></ul></div>"
    )
    app_region = "" if lookalike else (
        "<div id='divContainerAplicacion' class='container' style='display:none'>"
        "<div id='iDivApplication' class='embed-responsive embed-responsive-4by3' style='display:none'></div></div>"
    )
    menu_region = "" if lookalike else (
        "<div id='divContainerMenu' class='container'><div id='divTodasOpciones' class='row'>"
        "<ul id='ulMenuRoot' class='ulMenu'></ul>"
        "<div id='divOpcionesDerecha'><a href='/declaraciones'><div class='divIconosTitulo'>Declaraciones y Pagos</div></a>"
        "<a href='/sire'><div class='divIconosTitulo'>SIRE</div></a><a href='/buzon'>Buzón</a></div></div></div>"
    )
    password = "<input id='txtContrasena' type='password'>" if login_like else ""
    notice = ""
    if scenario == "m2_notice_overlay":
        notice = (
            "<div id='divAvisoContacto' class='modal-aviso' style='position:fixed;top:0;left:0;right:0;bottom:0;z-index:99999;"
            "background:rgba(0,0,0,.6)'><div class='modal-dialog'><p>Confirme su telefono y correo (fixture)</p>"
            "<button id='btnConfirmarContacto' onclick=\"fetch('/contacto/confirmar')\">Confirmar</button>"
            "<button id='btnRecordar' onclick=\"fetch('/contacto/recordar')\">Recordar despues</button></div></div>"
            "<script>window.__dismissNotice=function(){var n=document.getElementById('divAvisoContacto');if(n)n.remove();}</script>"
        )
    elif scenario == "m2_notice_popup":
        notice = "<script>window.open('/aviso.html','aviso','width=320,height=200');</script>"
    elif scenario in {"m2_contact_interstitial", "m2_contact_two_modals", "m2_contact_mandatory", "m2_notice_informative"}:
        # Pantalla subyacente "Valida tus datos de contacto" + modal "Informativo" con "Finalizar": TODOS sus controles llaman a rutas PROHIBIDAS
        # (/contacto/*); el agente jamas debe pulsarlos. El humano cierra el aviso con `window.__dismissNotice()`; `mandatory` no tiene salida.
        informative_only = scenario == "m2_notice_informative"
        heading = "" if informative_only else "<h2 class='titulo'>VALIDA TUS DATOS DE CONTACTO</h2><button id='btnEnviarCodigo' onclick=\"fetch('/contacto/enviar-codigo')\">Enviar código</button>"
        modal = lambda ident, title, button: (  # noqa: E731
            f"<div id='{ident}' class='modal-aviso modal' role='dialog' aria-modal='true' style='position:fixed;top:0;left:0;right:0;bottom:0;z-index:99999;background:rgba(0,0,0,.6)'>"
            f"<div class='modal-dialog'><h4 class='modal-title'>{title}</h4><p>Verifica tu correo electrónico y tu teléfono móvil.</p>"
            f"<button id='btn{ident}' onclick=\"fetch('/contacto/finalizar')\">{button}</button></div></div>"
        )
        second = (
            "<script>window.__second=true;</script>" if scenario == "m2_contact_two_modals" else ""
        )
        dismiss = {
            "m2_contact_mandatory": "window.__dismissNotice=function(){};",
            "m2_contact_two_modals": (
                "window.__dismissNotice=function(){var n=document.querySelector('.modal-aviso');if(!n)return;var wasFirst=n.id==='divAvisoUno';n.remove();"
                "if(wasFirst){var d=document.createElement('div');d.innerHTML=window.__secondHtml;document.body.appendChild(d.firstChild);}"
                "else{var h=document.querySelector('h2.titulo');if(h)h.remove();var b=document.getElementById('btnEnviarCodigo');if(b)b.remove();}};"
            ),
        }.get(scenario, "window.__dismissNotice=function(){var n=document.querySelector('.modal-aviso');if(n)n.remove();var h=document.querySelector('h2.titulo');if(h)h.remove();var b=document.getElementById('btnEnviarCodigo');if(b)b.remove();};")
        first_title = "Informativo"
        second_html = modal("divAvisoDos", "Informativo", "Finalizar").replace("'", "\\'") if scenario == "m2_contact_two_modals" else ""
        notice = (
            heading
            + modal("divAvisoUno" if scenario == "m2_contact_two_modals" else "divAvisoContacto", first_title, "Cerrar" if informative_only else "Finalizar")
            + f"<script>window.__secondHtml='{second_html}';{dismiss}</script>{second}"
        )
    return f"""<!doctype html><html><head><title>SUNAT - Menú SOL</title>
<style>.oculto{{display:none}} li{{list-style:none}}</style></head><body>
<div id="divCabecera" class="navbar navbar-default navbar-fixed-top" role="navigation">
  <button id="btnNavbar" class="navbar-toggle collapsed" type="button">=</button>
  {header_nav}
  <div id="divNavAzul"><button id="btnSalir" class="btn navbar-btn" type="button" style="display:none">Cerrar sesión</button></div>
  <div id="divBreadcrumb" style="display:none"><ol class="breadcrumb"><li id="liBreadNivel4"></li></ol></div>
</div>
{password}{notice}{menu_region}{app_region}
<script>
(function () {{
  const root = document.getElementById('ulMenuRoot');
  if (!root) return;
  const mk = (tag, cls, id) => {{ const e = document.createElement(tag); if (cls) e.className = cls; if (id) e.id = id; return e; }};
  const span = (text) => {{ const s = mk('span', 'spanNivelDescripcion'); s.textContent = text; return s; }};
  const toggle = (li, body) => li.addEventListener('click', () => {{ body.style.display = (body.style.display === 'none') ? '' : 'none'; }});
  const decoy = mk('li', 'nivel1 liOpcion', 'nivel1_20'); decoy.appendChild(span('Otros servicios')); root.appendChild(decoy);
  const l1 = mk('li', 'nivel1 liOpcion', 'nivel1_10'); l1.appendChild(span('Mi RUC y otros registros'));
  const b1 = mk('li', 'nivel1Cuerpo liCuerpo', 'nivel1Cuerpo_10'); b1.style.display = 'none';
  root.appendChild(l1); root.appendChild(b1); toggle(l1, b1);
  const l2 = mk('li', 'nivel2 liOpcion', 'nivel2_10_1'); l2.appendChild(span('Registro'));
  const b2 = mk('li', 'nivel2Cuerpo liCuerpo', 'nivel2Cuerpo_10_1'); b2.style.display = 'none';
  b1.appendChild(l2); b1.appendChild(b2); toggle(l2, b2);
  const l3 = mk('li', 'nivel3 liOpcion', 'nivel3_10_1_1'); l3.appendChild(span('Consultas'));
  const b3 = mk('li', 'nivel3Cuerpo liCuerpo', 'nivel3Cuerpo_10_1_1'); b3.style.display = 'none';
  b2.appendChild(l3); b2.appendChild(b3); toggle(l3, b3);
  const other = mk('li', 'nivel4 liOpcion', 'nivel4_10_1_1_1_2'); other.appendChild(span('Consulta de otro registro'));
  b3.appendChild(other);
  if ({ficha_entry}) {{
    const l4 = mk('li', 'nivel4 liOpcion', 'nivel4_10_1_1_1_1'); l4.appendChild(span('Ficha RUC')); b3.appendChild(l4);
    l4.addEventListener('click', () => {{
      document.getElementById('divContainerMenu').style.display = 'none';
      document.getElementById('divContainerAplicacion').style.display = '';
      const app = document.getElementById('iDivApplication'); app.style.display = '';
      const f = document.createElement('iframe'); f.id = 'iframeApplication'; f.className = 'embed-responsive-item';
      f.style.width = '100%'; f.style.height = '400px'; f.src = {ficha_url!r}; app.appendChild(f);
      const crumb = document.getElementById('liBreadNivel4'); crumb.textContent = 'Ficha RUC';
      document.getElementById('divBreadcrumb').style.display = '';
    }});
  }}
}})();
</script></body></html>"""
