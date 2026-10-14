"""M3 -- fixtures SINTETICOS de destinos de SOL (SOLO PRUEBAS). NO reproducen ningun destino real de SUNAT: son estructuras inventadas para
verificar el MOTOR generico de contratos (secuencia de menu, presupuesto de clics derivado, identidad positiva y negativa, nueva Page / frame,
avisos, fail-closed). Que estas pruebas pasen NO valida SIRE, Declaraciones y Pagos, Declara Facil ni Tramites y Consultas.

Nombres de escenario: `m3_<variante>[__<escenario m2>]` (la parte tras `__` reutiliza los avisos/fallos del shell de M2)."""

from __future__ import annotations

import json

from windows_agent import destination_contract as dc

SYN_FRAME_LABEL = "Destino Sintetico Frame"
SYN_PAGE_LABEL = "Destino Sintetico Pagina"
SYN_HOME_LABEL = "Destino Sintetico Inicio"
DEST_HOST = "ficha.localhost"

VARIANTS_FRAME = {"frame", "frame_absent", "frame_dup", "frame_depth", "frame_wrongpath", "frame_nomarker", "frame_crumb", "frame_fewtables"}
VARIANTS_PAGE = {"page", "page_extra", "page_wrongpath", "page_nomarker", "page_never"}
VARIANTS_HOME = {"home", "home_absent"}
# Entrada PUBLICA por tarjeta (M3): el login aterriza en la PLATAFORMA `/cl-ti-itmenu2/MenuInternetPlataforma.htm` (no en el Menu SOL).
VARIANTS_PUBLIC = {"public_entryfail", "public", "public_nested", "public_absent", "public_dup", "public_twolinks", "public_wronglanding", "public_nomarker", "public_dupmarker", "public_extra"}
# Menu REAL de SIRE (hojas `nivel4` a 3 niveles, SIN href ni onclick) con los contratos REALES del agente (hosts/esquema locales): RVIE con tilde, RCE y
# Generacion sin tilde; `*_accent` cambia la forma del texto (debe fallar), `*_nomarker`/`*_wrongpath` rompen la identidad de llegada, `gen_dupheader` repite
# la etiqueta en una cabecera `nivel3`.
VARIANTS_SIRE = {"sire_rvie", "sire_rce", "sire_gen", "sire_gen_dupheader", "sire_rce_accent", "sire_rvie_nomarker", "sire_rvie_wrongpath", "sire_gen_fewtables"}
SIRE_REAL = {
    "sire_rvie": ("SIRE_RVIE", "Gestión de Ventas e Ingresos Electrónicos", "/app/contribuyentems/servicio/libroventas/mige/gestionlibroventas/1.0.0/gestionar-rvie/generacion-registro"),
    "sire_rce": ("SIRE_RCE", "Gestion de Compras", "/app/contribuyentems/servicio/librocompras/mige/gestionlibrocompras/1.0.0/"),
    "sire_gen": ("SIRE_GENERACION_REGISTROS", "Generacion de Registros Electronicos", "/app/contribuyentems/servicio/libro/mige/generacionlibro/1.0.0/generacion-registro/generacion-de-registro"),
}
PLATFORM_PATH = "/cl-ti-itmenu2/MenuInternetPlataforma.htm"
PUBLIC_HEADING = "Mis declaraciones y pagos"


def synthetic_contracts() -> dict[str, dc.DestinationContract]:
    frame = dc.ArrivalSpec("FRAME", frozenset({DEST_HOST}), "/destino-frame/", "http", ("synMarker",), 2, SYN_FRAME_LABEL.lower())
    page = dc.ArrivalSpec("NEW_PAGE", frozenset({DEST_HOST}), "/destino-page/", "http", ("synMarker",))
    home = dc.ArrivalSpec("NEW_PAGE", frozenset({DEST_HOST}), "/destino-home/", "http", ("synMarker",))
    return {
        "SYN_FRAME": dc.DestinationContract("SYN_FRAME", "Sintetico Frame", dc.LIVE_VALIDATED,
                                            entry=dc.EntrySpec("MENU_LEAF", SYN_FRAME_LABEL.lower(), leaf_level=3, min_levels=2, max_levels=2), arrival=frame),
        "SYN_PAGE": dc.DestinationContract("SYN_PAGE", "Sintetico Pagina", dc.LIVE_VALIDATED,
                                           entry=dc.EntrySpec("MENU_LEAF", SYN_PAGE_LABEL.lower(), leaf_level=4, min_levels=3, max_levels=3), arrival=page),
        "SYN_HOME": dc.DestinationContract("SYN_HOME", "Sintetico Inicio", dc.LIVE_VALIDATED,
                                           entry=dc.EntrySpec("HOME_LINK", SYN_HOME_LABEL.lower()), arrival=home),
        # Entrada publica: tarjeta + login + plataforma (mismos ids de identidad que la plataforma real; hosts locales y esquema http)
        "SYN_PUBLIC": dc.DestinationContract(
            "SYN_PUBLIC", "Sintetico Publico", dc.LIVE_VALIDATED, entry=dc.EntrySpec("PUBLIC_CARD", PUBLIC_HEADING.lower()),
            arrival=dc.ArrivalSpec("NEW_PAGE", frozenset({"menu.localhost"}), PLATFORM_PATH, "http", dc.PLATFORM_ONCE_IDS)),
    }


def local_acceptance_contracts() -> dict[str, dc.DestinationContract]:
    """Contratos REALES (SIRE y Declaraciones y Pagos) con host/esquema LOCALES pero SIN validar: solo se ejecutan en modo de aceptacion."""
    import dataclasses

    out = {}
    for key in ("SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS", "DECLARACIONES_Y_PAGOS"):
        real = dc.CONTRACTS[key]
        hosts = frozenset({"menu.localhost"}) if key == "DECLARACIONES_Y_PAGOS" else frozenset({DEST_HOST})
        # estado PREVIO a la aceptacion real: sin validar y solo con el reconocimiento (el modo de aceptacion sigue disponible para futuros destinos)
        out[key] = dataclasses.replace(real, validation=dc.NOT_VALIDATED, evidence="RECON_COMPLETE", arrival=dataclasses.replace(real.arrival, hosts=hosts, scheme="http"))
        assert not out[key].enabled
    return out


def local_real_contracts() -> dict[str, dc.DestinationContract]:
    """Los contratos REALES de SIRE (entrada y llegada tal como se reconocieron en SOL) con host/esquema LOCALES y marcados como validados SOLO para esta
    prueba del motor. La estructura (hoja `nivel4`, 3 niveles, texto exacto, ids, tablas, selector de tablas) es la real; el host y el esquema son locales."""
    import dataclasses

    out = {}
    for key in ("SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS", "DECLARACIONES_Y_PAGOS"):
        real = dc.CONTRACTS[key]
        arrival = dataclasses.replace(real.arrival, hosts=frozenset({"menu.localhost"}) if key == "DECLARACIONES_Y_PAGOS" else frozenset({DEST_HOST}), scheme="http")
        out[key] = dataclasses.replace(real, validation=dc.LIVE_VALIDATED, arrival=arrival)
    return out


def variant_of(scenario: str) -> tuple[str | None, str]:
    """(variante M3 o None, escenario M2 heredado)."""
    if not scenario.startswith("m3_"):
        return None, scenario
    head, _, inherited = scenario.partition("__")
    return head[3:], inherited or "m2_success"


def home_link_html(variant: str, origin: str) -> str:
    if variant == "home":
        return f"<a class='syn-home' href='{origin}/destino-home/' target='_blank'><div class='divIconosTitulo'>{SYN_HOME_LABEL}</div></a>"
    return ""


def menu_script(variant: str, origin: str) -> str:
    """JS que agrega las hojas sinteticas al menu existente (b1/b2/b3 ya definidos por el shell de M2)."""
    frame_src = f"{origin}/destino-frame/index"
    wrong = "/otra-ruta/index" if variant == "frame_wrongpath" else "/destino-frame/index"
    frame_src = f"{origin}{wrong}"
    crumb = "Otro destino" if variant == "frame_crumb" else SYN_FRAME_LABEL
    page_path = "/otra-ruta/" if variant == "page_wrongpath" else "/destino-page/"
    extra_popup = f"window.open('{origin}/destino-extra/','extra');" if variant == "page_extra" else ""
    never = variant == "page_never"
    return f"""
<script>
(function () {{
  const mk = (tag, cls, id) => {{ const e = document.createElement(tag); if (cls) e.className = cls; if (id) e.id = id; return e; }};
  const span = (text) => {{ const s = mk('span', 'spanNivelDescripcion'); s.textContent = text; return s; }};
  const b1 = document.getElementById('nivel1Cuerpo_10'), b2 = document.getElementById('nivel2Cuerpo_10_1'), b3 = document.getElementById('nivel3Cuerpo_10_1_1');
  if (!b1 || !b2 || !b3) return;
  const variant = {variant!r};
  const openFrame = () => {{
    document.getElementById('divContainerMenu').style.display = 'none';
    document.getElementById('divContainerAplicacion').style.display = '';
    const app = document.getElementById('iDivApplication'); app.style.display = '';
    const f = document.createElement('iframe'); f.id = 'iframeApplication'; f.className = 'embed-responsive-item';
    f.style.width = '100%'; f.style.height = '400px'; f.src = {frame_src!r}; app.appendChild(f);
    document.getElementById('liBreadNivel4').textContent = {crumb!r};
    document.getElementById('divBreadcrumb').style.display = '';
  }};
  const addFrameLeaf = (parent, level, id) => {{
    const li = mk('li', 'nivel' + level + ' liOpcion', id); li.appendChild(span({SYN_FRAME_LABEL!r})); parent.appendChild(li);
    li.addEventListener('click', openFrame);
  }};
  if (variant === 'frame' || variant === 'frame_wrongpath' || variant === 'frame_nomarker' || variant === 'frame_crumb' || variant === 'frame_fewtables') addFrameLeaf(b2, 3, 'nivel3_syn_frame');
  if (variant.startsWith('sire_')) {{
    const real = {json.dumps(SIRE_REAL)};
    const key = variant.startsWith('sire_gen') ? 'sire_gen' : (variant === 'sire_rce_accent' ? 'sire_rce' : variant.replace(/_(nomarker|wrongpath|fewtables)$/, ''));
    const [, text, path] = real[key];
    const label = variant === 'sire_rce_accent' ? 'Gestión de Compras' : text;
    const appPath = variant === 'sire_rvie_wrongpath' ? real['sire_rce'][2] : path;
    const openSire = () => {{
      document.getElementById('divContainerMenu').style.display = 'none';
      document.getElementById('divContainerAplicacion').style.display = '';
      const app = document.getElementById('iDivApplication'); app.style.display = '';
      const f = document.createElement('iframe'); f.id = 'iframeApplication'; f.className = 'embed-responsive-item'; f.style.width = '100%'; f.style.height = '400px';
      f.src = {origin!r} + appPath + '?v=' + {variant!r}; app.appendChild(f);
    }};
    if (variant === 'sire_gen_dupheader') {{ const h = mk('li', 'nivel3 liOpcion', 'nivel3_syn_gen_header'); h.appendChild(span(label)); b2.appendChild(h); }}
    const leaf = mk('li', 'nivel4 liOpcion', 'nivel4_syn_sire'); leaf.appendChild(span(label)); b3.appendChild(leaf);
    leaf.addEventListener('click', openSire);  // sin href ni onclick: el clic se enlaza por script, como en SOL
  }}
  if (variant === 'frame_dup') {{ addFrameLeaf(b2, 3, 'nivel3_syn_frame'); addFrameLeaf(b2, 3, 'nivel3_syn_frame2'); }}
  if (variant === 'frame_depth') addFrameLeaf(b1, 3, 'nivel3_syn_frame_shallow');  // misma etiqueta pero a OTRA profundidad
  if (variant.startsWith('page') && {str(not never).lower()}) {{
    const li = mk('li', 'nivel4 liOpcion', 'nivel4_syn_page'); li.appendChild(span({SYN_PAGE_LABEL!r})); b3.appendChild(li);
    li.addEventListener('click', () => {{ window.open({origin!r} + {page_path!r} + 'index', 'dest'); {extra_popup} }});
  }}
  if ({str(never).lower()}) {{
    const li = mk('li', 'nivel4 liOpcion', 'nivel4_syn_page'); li.appendChild(span({SYN_PAGE_LABEL!r})); b3.appendChild(li);
    li.addEventListener('click', () => {{ /* no abre nada: la nueva Page nunca llega */ }});
  }}
}})();
</script>"""


def public_entry_html(variant: str, open_login_js: str) -> str:
    """Portada con 5 tarjetas (encabezado + enlace «Ingresar»). SOLO la de «Mis declaraciones y pagos» abre el login en las variantes publicas; las demas
    apuntan a rutas DECOY (jamas deben solicitarse). `public_nested`: el enlace NO esta en el hermano siguiente del encabezado (la estrategia por
    contenedor no depende de esa relacion). Variantes negativas: ausente, encabezado duplicado, DOS enlaces en la tarjeta, solo decoys."""
    decoy = lambda name: f'<a href="/decoy/{name}">Ingresar</a>'  # noqa: E731
    link = "<a href=\"javascript:openLogin()\"><span>Ingresar</span></a>"
    cards = {
        "dyp": ("h2", PUBLIC_HEADING, link),
        "tram": ("h2", "Mis tramites y consultas", decoy("tramites")),
        "per": ("h3", "Personas", decoy("personas")),
        "emp": ("h3", "Empresas", decoy("empresas")),
        "pag": ("h3", "Pagos sin clave SOL", decoy("pagos")),
    }
    if variant == "public_absent":
        cards.pop("dyp")
    if variant == "public_decoy_only":
        cards["dyp"] = ("h2", PUBLIC_HEADING, decoy("declaraciones-sin-login"))
    html = []
    for key, (tag, title, anchor) in cards.items():
        if key == "dyp" and variant == "public_twolinks":
            anchor = anchor + decoy("segundo")
        if key == "dyp" and variant == "public_nested":
            html.append(f"<div class='card'><div class='top'><{tag}>{title}</{tag}></div><div class='mid'><div class='deep'>{anchor}</div></div></div>")
        else:
            html.append(f"<div class='card'><{tag}>{title}</{tag}><div>{anchor}</div></div>")
    if variant == "public_dup":
        html.append(f"<div class='card'><h2>{PUBLIC_HEADING}</h2><div>{decoy('duplicada')}</div></div>")
    # `public_entryfail`: la pagina PUBLICA (anterior al login) tiene un fetch fallido y un error de script, como la real (analitica/terceros). No debe
    # atribuirse jamas a una ventana de autenticacion detenida mas tarde.
    noise = "<script>fetch('http://127.0.0.1:1/x').catch(function(){});setTimeout(function(){throw new TypeError('entry-noise');},0);</script>" if variant == "public_entryfail" else ""
    return (f"<html><head><title>SUNAT SOL Operaciones en Línea (fixture)</title><style>h2 {{ text-transform: uppercase; }}</style>"
            f"<script>function openLogin() {{ {open_login_js} }}</script>{noise}</head><body><section>{''.join(html)}</section></body></html>")


def platform_html(variant: str) -> str:
    """Plataforma autenticada: pasa el marcador del shell (mismos ids/atributos) con la region de APLICACION visible y el menu oculto, mas los ids propios
    de la plataforma. Variantes negativas: sin `aOpcionPlataforma`, `ulSubmenu` duplicado, popup extra, y `public_menu_state` (el menu visible: no es la plataforma)."""
    nomarker = variant == "public_nomarker"
    dup = "<ul id='ulSubmenu'></ul>" if variant == "public_dupmarker" else ""
    popup = "<script>window.open('/aviso.html','aviso','width=300,height=200');</script>" if variant == "public_extra" else ""
    menu_visible = variant == "public_menu_state"
    plataforma = "" if nomarker else "<li><a id='aOpcionPlataforma'>Plataforma</a></li>"
    return f"""<!doctype html><html><head><title>SUNAT - Menú SOL</title></head><body>
<div id="divCabecera" class="navbar navbar-default navbar-fixed-top" role="navigation">
  <button id="btnNavbar" class="navbar-toggle collapsed" type="button">=</button>
  <div id="divNavPrinicipal" class="navbar-collapse collapse"><ul id="ulMenu01" class="nav navbar-nav navbar-right">
    <li id="liOpcionFavorito" class="dropdown"><a id="aOpcionFavorito">Favoritos</a></li>{plataforma}</ul></div>
  <div id="divNavAzul"><button id="btnSalir" class="btn navbar-btn" type="button">Cerrar sesión</button></div>
  <div id="divBreadcrumb"><ol class="breadcrumb"><li id="liBreadNivelHome"></li><li id="liBreadNivel4"></li></ol></div>
</div>
<div id="divContainerAplicacion" class="container"{' style="display:none"' if menu_visible else ''}>
  <div id="iDivApplication" class="embed-responsive embed-responsive-4by3"{' style="display:none"' if menu_visible else ''}><iframe id="iframeApplication" class="embed-responsive-item" src="about:blank"></iframe></div></div>
<div id="divContainerMenu" class="container"{'' if menu_visible else ' style="display:none"'}><div id="divTodasOpciones" class="row"{'' if menu_visible else ' style="display:none"'}></div></div>
<ul id="ulSubmenu"><li class="nivel3" id="nivel3_55_1_1"><span class="spanNivelDescripcion">Declara Fácil</span></li></ul>{dup}
<a href="/declaraciones">Declaraciones (fixture: ruta consecuente, jamas debe pulsarse)</a><a href="/pagos">Pagos (fixture)</a>
{popup}</body></html>"""


def sire_app_html(variant: str, path: str) -> str:
    """Pantallas SIRE con la identidad REAL: RVIE/RCE traen los ids de la plataforma SIRE; Generacion NO trae ids (2 tablas visibles + botones)."""
    if "/generacionlibro/" in path:
        tables = 1 if variant.endswith("fewtables") else 2
        body = "".join("<table class='table table-bordered'><tr><td>fila sintetica</td></tr></table>" for _ in range(tables))
        return f"<!doctype html><html><head><title>SIRE (fixture)</title></head><body><header class='navbar'><span class='navbar-brand'>Generacion (fixture)</span></header>{body}<button class='btn'>a</button></body></html>"
    ids = "" if variant == "sire_rvie_nomarker" else "<button id='btn-aceptar'>Aceptar</button><a id='nav-generacion-registro'>g</a><a id='nav-informacion-gestion'>i</a>"
    return f"<!doctype html><html><head><title>SIRE (fixture)</title></head><body><form>{ids}</form></body></html>"


def destination_html(variant: str, path: str) -> str:
    """Contenido servido en `ficha.localhost/destino-*`."""
    if path.startswith("/app/"):
        return sire_app_html(variant, path)
    marker = "" if variant in {"frame_nomarker", "page_nomarker"} else "<input type='hidden' id='synMarker'>"
    tables = 1 if variant == "frame_fewtables" else 3
    body = "".join("<table class='form-table'><tr><td>fila sintetica</td><td>valor sintetico</td></tr></table>" for _ in range(tables))
    return f"<!doctype html><html><head><title>Destino sintetico</title></head><body>{marker}{body}</body></html>"
