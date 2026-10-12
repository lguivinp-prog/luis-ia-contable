"use client";

// «Información SUNAT»: lo que SUNAT publica en la Ficha RUC según el ÚLTIMO snapshot aceptado, con su fecha y procedencia. Se muestra
// sin abrir SUNAT. Está separada de los datos INTERNOS (contactos, honorarios, etiquetas, notas): aquí nada es editable y nada se
// presenta como oficial si no viene del snapshot. Los representantes legales (datos personales) solo los entrega el backend a quien
// puede editar el cliente.
import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchFichaOverview } from "@/lib/fichaRuc";
import { describeInternalRegime, describeOfficialRegime, formatDateTime, formatDiffValue } from "@/lib/fichaRucModel";
import type { FichaOverview } from "@/lib/types/fichaRuc";

type Row = [label: string, value: string];

function text(value: unknown): string {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

function dateText(iso: unknown): string {
  return typeof iso === "string" && iso ? formatDiffValue("activities_start_date", iso) : "—";
}

function Facts({ rows }: { rows: Row[] }) {
  return (
    <dl style={{ display: "grid", gridTemplateColumns: "minmax(170px, 230px) 1fr", gap: "0.3rem 1rem", margin: 0 }}>
      {rows.map(([label, value]) => (
        <div key={label} style={{ display: "contents" }}>
          <dt style={{ color: "var(--muted)" }}>{label}</dt>
          <dd style={{ margin: 0 }}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SunatInformation({ clientId, canOpenCenter }: { clientId: string; canOpenCenter: boolean }) {
  const [overview, setOverview] = useState<FichaOverview | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchFichaOverview(clientId)
      .then((value) => {
        if (alive) setOverview(value);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [clientId]);

  if (failed) {
    return (
      <section className="surface-card" style={{ padding: "1.25rem" }} aria-labelledby="info-sunat">
        <h2 id="info-sunat" style={{ fontSize: "1.05rem" }}>Información SUNAT</h2>
        <p style={{ color: "var(--muted)" }}>No se pudo cargar la información SUNAT de este contribuyente.</p>
      </section>
    );
  }
  if (!overview) return null;

  const snapshot = overview.latest_accepted;
  const fields = (snapshot?.fields ?? {}) as Record<string, unknown>;
  const regime = describeOfficialRegime(overview);
  const dom = (fields.domicilio ?? null) as Record<string, string> | null;
  const tributos = (fields.tributos_afectos ?? []) as Array<{ tributo: string; afecto_desde?: string | null; marca_exoneracion?: string | null }>;
  const establecimientos = (fields.establecimientos_anexos ?? []) as Array<Record<string, string>>;
  const representantes = (fields.representantes_legales ?? []) as Array<Record<string, string>>;
  const actividades = (fields.actividades_economicas ?? []) as Array<{ tipo: string; ciiu?: string | null; descripcion: string }>;

  return (
    <section className="surface-card" style={{ padding: "1.25rem", display: "flex", flexDirection: "column", gap: "1rem" }} aria-labelledby="info-sunat">
      <div>
        <h2 id="info-sunat" style={{ fontSize: "1.05rem" }}>Información SUNAT</h2>
        {snapshot ? (
          <p style={{ color: "var(--muted)", marginTop: "0.25rem" }}>
            Fuente: Ficha RUC de SUNAT · consultada el {formatDateTime(snapshot.retrieved_at)} · revisada el {formatDateTime(snapshot.reviewed_at)}.
            {overview.last_unchanged_verification_at && <> Última verificación sin cambios: {formatDateTime(overview.last_unchanged_verification_at)}.</>}
          </p>
        ) : (
          <p style={{ color: "var(--muted)", marginTop: "0.25rem" }}>
            Todavía no hay una Ficha RUC aceptada para este contribuyente. Ningún dato tributario de esta sección es oficial hasta
            entonces.
          </p>
        )}
        {overview.pending_snapshot_id && canOpenCenter && (
          <p style={{ marginTop: "0.4rem" }}>
            <Link href={`/clients/${clientId}/ficha-ruc/${overview.pending_snapshot_id}`}>Hay una propuesta de la Ficha RUC pendiente de revisión</Link>
          </p>
        )}
      </div>

      <div>
        <h3 style={{ fontSize: "0.95rem", marginBottom: "0.4rem" }}>Régimen del impuesto a la renta</h3>
        <Facts
          rows={[
            ["Según SUNAT", regime.confirmed ? `${regime.text} · al ${formatDateTime(regime.asOf)}` : regime.text],
            ["Dato interno del cliente", describeInternalRegime(overview)],
          ]}
        />
        <p style={{ color: "var(--muted)", fontSize: "0.82rem", marginTop: "0.4rem" }}>
          Los tributos afectos se muestran como información. Las obligaciones mensuales no se asignan automáticamente: las decide un
          profesional.
        </p>
      </div>

      {snapshot && (
        <>
          <div>
            <h3 style={{ fontSize: "0.95rem", marginBottom: "0.4rem" }}>Identificación y estado</h3>
            <Facts
              rows={[
                ["Razón social", text(fields.razon_social)],
                ["Tipo de contribuyente", text(fields.tipo_contribuyente)],
                ["Estado", text(fields.estado_contribuyente)],
                ["Condición del domicilio", text(fields.condicion_contribuyente)],
                ["Dependencia SUNAT", text(fields.dependencia_sunat)],
                ["Fecha de inscripción", dateText(fields.fecha_inscripcion)],
                ["Inicio de actividades", dateText(fields.fecha_inicio_actividades)],
                ["Sistema de contabilidad", text(fields.sistema_contabilidad)],
                ["Sistema de emisión", text(fields.sistema_emision_comprobante)],
                ["Emisor electrónico desde", dateText(fields.emisor_electronico_desde)],
                ["Comprobantes electrónicos", text(fields.comprobantes_electronicos)],
              ]}
            />
          </div>

          <div>
            <h3 style={{ fontSize: "0.95rem", marginBottom: "0.4rem" }}>Domicilio fiscal</h3>
            {dom ? (
              <Facts
                rows={[
                  ["Departamento / provincia / distrito", [dom.departamento, dom.provincia, dom.distrito].filter(Boolean).join(" / ") || "—"],
                  ["Vía y número", [dom.via, dom.numero].filter(Boolean).join(" ") || "—"],
                  ["Zona", text(dom.zona)],
                  ["Condición del inmueble", text(dom.condicion_inmueble)],
                ]}
              />
            ) : (
              <p style={{ color: "var(--muted)" }}>{text(fields.domicilio_fiscal)}</p>
            )}
          </div>

          <div>
            <h3 style={{ fontSize: "0.95rem", marginBottom: "0.4rem" }}>Actividades económicas</h3>
            {actividades.length ? (
              <ul style={{ margin: 0, paddingLeft: "1.2rem" }}>
                {actividades.map((a, index) => (
                  <li key={`${a.ciiu ?? ""}-${index}`}>
                    {a.tipo === "PRINCIPAL" ? "Principal" : "Secundaria"}: {a.ciiu ? `${a.ciiu} - ` : ""}
                    {a.descripcion}
                  </li>
                ))}
              </ul>
            ) : (
              <p style={{ color: "var(--muted)" }}>—</p>
            )}
          </div>

          <div>
            <h3 style={{ fontSize: "0.95rem", marginBottom: "0.4rem" }}>Registro de tributos afectos</h3>
            {tributos.length ? (
              <table className="data-table" aria-label="Tributos afectos según SUNAT">
                <thead>
                  <tr>
                    <th>Tributo</th>
                    <th>Afecto desde</th>
                    <th>Exoneración</th>
                  </tr>
                </thead>
                <tbody>
                  {tributos.map((row, index) => (
                    <tr key={`${row.tributo}-${index}`}>
                      <td>{row.tributo}</td>
                      <td>{dateText(row.afecto_desde)}</td>
                      <td>{text(row.marca_exoneracion)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p style={{ color: "var(--muted)" }}>
                No disponible en la Ficha consultada.
                {Array.isArray(fields.secciones_no_reconocidas) && (fields.secciones_no_reconocidas as string[]).includes("tributos_afectos") && " (La sección no pudo interpretarse.)"}
              </p>
            )}
          </div>

          <div>
            <h3 style={{ fontSize: "0.95rem", marginBottom: "0.4rem" }}>Establecimientos anexos</h3>
            {establecimientos.length ? (
              <ul style={{ margin: 0, paddingLeft: "1.2rem" }}>
                {establecimientos.map((e, index) => (
                  <li key={`${e.codigo ?? ""}-${index}`}>
                    {[e.codigo, e.tipo, e.denominacion, e.direccion, e.condicion_legal].filter(Boolean).join(" · ")}
                  </li>
                ))}
              </ul>
            ) : (
              <p style={{ color: "var(--muted)" }}>—</p>
            )}
          </div>

          {overview.sensitive_sections_visible ? (
            <div>
              <h3 style={{ fontSize: "0.95rem", marginBottom: "0.4rem" }}>Representantes legales</h3>
              {representantes.length ? (
                <ul style={{ margin: 0, paddingLeft: "1.2rem" }}>
                  {representantes.map((r, index) => (
                    <li key={`${r.numero_documento ?? ""}-${index}`}>
                      {[r.nombre, r.cargo, r.tipo_documento && r.numero_documento ? `${r.tipo_documento} ${r.numero_documento}` : null, r.fecha_desde ? `desde ${dateText(r.fecha_desde)}` : null]
                        .filter(Boolean)
                        .join(" · ")}
                    </li>
                  ))}
                </ul>
              ) : (
                <p style={{ color: "var(--muted)" }}>—</p>
              )}
              <p style={{ color: "var(--muted)", fontSize: "0.82rem", marginTop: "0.4rem" }}>
                Datos personales de terceros: solo visibles para quien puede editar el contribuyente.
              </p>
            </div>
          ) : (
            <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
              Los representantes legales (datos personales) no se muestran con tu rol.
            </p>
          )}
        </>
      )}
    </section>
  );
}
