"use client";

// Cuerpos de las pestanas del workspace cliente/periodo. Reutilizan los paneles existentes (documentos, impuestos y
// pagos, presentacion, cierre, honorarios, SOL supervisado, borradores de WhatsApp); no agregan logica de negocio.
import { SireControlPanel } from "@/components/sire/SireControlPanel";
import { SunatDestinationsPanel } from "@/components/sunat/SunatDestinationsPanel";
import Link from "next/link";
import { type ReactNode, useEffect, useState } from "react";

import { OperationsControls } from "@/components/cockpit/OperationsControls";
import { DeclarationSectionLoader, type DeclarationSection } from "@/components/declarations/DeclarationSections";
import { ClientFeesPanel } from "@/components/fees/ClientFeesPanel";
import {
  DOCUMENT_STATUS_SEMAPHORE,
  FILING_STATUS_SEMAPHORE,
  StatusSemaphore,
  TAX_PAYMENT_STATUS_SEMAPHORE,
  WORKFLOW_STATUS_SEMAPHORE,
} from "@/components/ui/StatusSemaphore";
import { fetchOperationsHistory } from "@/lib/cockpit";
import {
  ACCOUNTING_STATUS_SEMAPHORE,
  BUZON_STATUS_SEMAPHORE,
  COCKPIT_RISK_SEMAPHORE,
  COMMUNICATION_STATUS_SEMAPHORE,
  MONTHLY_PHASE_SEMAPHORE,
  SIRE_STATUS_SEMAPHORE,
  declarationsLabel,
  feeLabel,
  formatDate,
  taxLabel,
} from "@/lib/cockpitPresentation";
import {
  ACCOUNTING_DETAIL_NOTICE,
  BUZON_SYNC_NOTICE,
  COMMUNICATION_NOTICE,
  HISTORY_FIELD_LABEL,
  MANUAL_STATE_NOTICE,
  historyValueLabel,
  isSireDeclaration,
  sireDeclarations,
} from "@/lib/cockpitWorkspace";
import type { OperationsHistoryEntry, OperationsUpdate, Workspace, WorkspaceDeclaration } from "@/lib/types/cockpit";

const MUTED = { color: "var(--muted)", fontSize: "0.85rem" } as const;

export interface SectionProps {
  workspace: Workspace;
  clientId: string;
  periodId: string;
  busy: boolean;
  canCommunicate: boolean;
  onSubmitOperations: (update: OperationsUpdate) => Promise<void>;
  onChanged: () => void;
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="surface-card" style={{ padding: "1.1rem", display: "flex", flexDirection: "column", gap: "0.7rem" }}>
      <h2 style={{ fontSize: "1.05rem" }}>{title}</h2>
      {children}
    </section>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ minWidth: "150px" }}>
      <div style={MUTED}>{label}</div>
      <div>{children}</div>
    </div>
  );
}

// Monta el contenido solo cuando se abre (cada declaracion carga sus propios datos).
function Collapsible({ title, defaultOpen, children }: { title: ReactNode; defaultOpen: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "0.75rem 1rem" }}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{ background: "none", border: "none", cursor: "pointer", fontWeight: 600, padding: 0, textAlign: "left" }}
      >
        {open ? "▾" : "▸"} {title}
      </button>
      {open && <div style={{ marginTop: "0.9rem" }}>{children}</div>}
    </div>
  );
}

function DeclarationSections({
  declarations,
  section,
  onChanged,
  emptyMessage,
}: {
  declarations: WorkspaceDeclaration[];
  section: DeclarationSection;
  onChanged: () => void;
  emptyMessage: string;
}) {
  if (declarations.length === 0) return <p style={MUTED}>{emptyMessage}</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
      {declarations.map((d) => (
        <Collapsible
          key={d.declaration_id}
          defaultOpen={declarations.length === 1}
          title={
            <>
              {d.obligation_name} <span style={MUTED}>· vence {formatDate(d.due_date)}</span>
            </>
          }
        >
          <DeclarationSectionLoader declarationId={d.declaration_id} section={section} onChanged={onChanged} />
        </Collapsible>
      ))}
    </div>
  );
}

function NotGeneratedNotice({ workspace }: { workspace: Workspace }) {
  if (workspace.summary.period_generated) return null;
  return (
    <p>
      Este periodo aún no tiene declaraciones generadas para este cliente.{" "}
      <Link href="/declarations/generate">Generar declaraciones</Link>
    </p>
  );
}

export function SummarySection({ workspace, clientId, periodId }: SectionProps) {
  const row = workspace.summary;
  const ops = workspace.operations;
  const tax = taxLabel(row);
  const fee = feeLabel(row);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <NotGeneratedNotice workspace={workspace} />
      <SunatDestinationsPanel clientId={clientId} originTaxPeriodId={periodId || undefined} />
      <Card title="Estado del periodo">
        <div style={{ display: "flex", gap: "1.25rem", flexWrap: "wrap" }}>
          <Fact label="Fase">
            <StatusSemaphore status={row.monthly_phase} map={MONTHLY_PHASE_SEMAPHORE} />
          </Fact>
          <Fact label="Riesgo">
            <StatusSemaphore status={row.overall_risk} map={COCKPIT_RISK_SEMAPHORE} />
          </Fact>
          <Fact label="Próximo vencimiento">{formatDate(row.nearest_due_date)}</Fact>
          <Fact label="Declaraciones">{declarationsLabel(row)}</Fact>
          <Fact label="Documentos">
            {row.document_status ? (
              <>
                <StatusSemaphore status={row.document_status} map={DOCUMENT_STATUS_SEMAPHORE} />
                {row.missing_documents_count > 0 && <div style={MUTED}>{row.missing_documents_count} faltantes</div>}
              </>
            ) : (
              "—"
            )}
          </Fact>
          <Fact label="SIRE">
            {row.sire_status ? <StatusSemaphore status={row.sire_status} map={SIRE_STATUS_SEMAPHORE} /> : "—"}
          </Fact>
          <Fact label="Impuesto">
            {row.tax_payment_status ? (
              <>
                <StatusSemaphore status={row.tax_payment_status} map={TAX_PAYMENT_STATUS_SEMAPHORE} />
                {tax && <div style={MUTED}>{tax}</div>}
              </>
            ) : (
              "—"
            )}
          </Fact>
          <Fact label="Honorarios">{fee ?? "—"}</Fact>
        </div>
      </Card>
      <Card title="Estados manuales">
        <div style={{ display: "flex", gap: "1.25rem", flexWrap: "wrap" }}>
          <Fact label="Buzón">
            <StatusSemaphore status={ops?.buzon_status ?? row.buzon_status} map={BUZON_STATUS_SEMAPHORE} />
          </Fact>
          <Fact label="Contabilidad">
            <StatusSemaphore status={ops?.accounting_status ?? row.accounting_status} map={ACCOUNTING_STATUS_SEMAPHORE} />
          </Fact>
          <Fact label="Comunicación">
            <StatusSemaphore
              status={ops?.client_communication_status ?? row.client_communication_status}
              map={COMMUNICATION_STATUS_SEMAPHORE}
            />
          </Fact>
        </div>
        <p style={MUTED}>{MANUAL_STATE_NOTICE}</p>
      </Card>
      <Card title="Tareas del cliente">
        <p>
          {workspace.open_tasks_count} abiertas · {workspace.overdue_tasks_count} vencidas
        </p>
        {workspace.tasks.length > 0 && (
          <ul style={{ paddingLeft: "1.1rem" }}>
            {workspace.tasks.map((task) => (
              <li key={task.id}>
                <Link href={`/tasks/${task.id}`}>{task.title}</Link> <span style={MUTED}>· {task.status}</span>
              </li>
            ))}
          </ul>
        )}
        <p style={MUTED}>
          Las tareas se muestran por cliente (no están ligadas a un periodo). <Link href={`/clients/${clientId}`}>Ver ficha del cliente</Link>
        </p>
      </Card>
    </div>
  );
}

export function BuzonSection({ workspace, clientId, busy, onSubmitOperations }: SectionProps) {
  const ops = workspace.operations;
  const phase = workspace.summary.monthly_phase;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <Card title="Apertura del periodo">
        <p>
          Fase actual: <StatusSemaphore status={phase} map={MONTHLY_PHASE_SEMAPHORE} />
        </p>
        <NotGeneratedNotice workspace={workspace} />
        <p style={MUTED}>
          La fase se calcula por calendario (Lima) a partir del periodo; no se edita.
        </p>
      </Card>
      <Card title="Buzón SUNAT">
        <p style={MUTED}>{BUZON_SYNC_NOTICE}</p>
        {ops && (
          <OperationsControls
            operations={ops}
            canUpdate={workspace.can_update_operations}
            fields={[{ field: "buzon_status", label: "Estado de revisión del Buzón", map: BUZON_STATUS_SEMAPHORE }]}
            showNotes
            busy={busy}
            onSubmit={onSubmitOperations}
          />
        )}
      </Card>
      {workspace.sol_access && (
        <Card title="Acceso SOL supervisado">
          <p style={MUTED}>
            Abre una sesión SOL supervisada hasta la página de aterrizaje. No consulta el Buzón ni extrae datos.
          </p>
          <div>
            <Link href={`/clients/${clientId}/sol-access`}>Acceso SOL supervisado</Link>
          </div>
        </Card>
      )}
    </div>
  );
}

export function DocumentsSection({ workspace, onChanged }: SectionProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
      <NotGeneratedNotice workspace={workspace} />
      <DeclarationSections
        declarations={workspace.declarations}
        section="documentos"
        onChanged={onChanged}
        emptyMessage="No hay declaraciones generadas, por lo tanto no hay documentos por revisar."
      />
    </div>
  );
}

export function SireSection({ workspace, onChanged, clientId, periodId }: SectionProps) {
  const row = workspace.summary;
  const sire = sireDeclarations(workspace.declarations);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <SunatDestinationsPanel clientId={clientId} originTaxPeriodId={periodId || undefined} title="Acceso a SIRE y otras herramientas de SUNAT" />
      <SireControlPanel clientId={clientId} periodId={periodId} />
      <Card title="Estado SIRE">
        <div style={{ display: "flex", gap: "1.25rem", flexWrap: "wrap" }}>
          <Fact label="SIRE (global)">
            {row.sire_status ? <StatusSemaphore status={row.sire_status} map={SIRE_STATUS_SEMAPHORE} /> : "—"}
          </Fact>
          <Fact label="RVIE">
            {row.sire_rvie_status ? <StatusSemaphore status={row.sire_rvie_status} map={SIRE_STATUS_SEMAPHORE} /> : "—"}
          </Fact>
          <Fact label="RCE">
            {row.sire_rce_status ? <StatusSemaphore status={row.sire_rce_status} map={SIRE_STATUS_SEMAPHORE} /> : "—"}
          </Fact>
        </div>
        {row.sire_status === "NOT_CONFIGURED" && (
          <p style={MUTED}>El cliente no tiene obligaciones SIRE configuradas.</p>
        )}
        {!row.period_generated && row.sire_status === "NOT_STARTED" && (
          <p style={MUTED}>SIRE está configurado pero el periodo aún no se generó: se considera sin iniciar.</p>
        )}
      </Card>
      <DeclarationSections
        declarations={sire}
        section="presentacion"
        onChanged={onChanged}
        emptyMessage="No hay declaraciones SIRE generadas en este periodo."
      />
    </div>
  );
}

export function DeclarationsSection({ workspace, onChanged }: SectionProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <NotGeneratedNotice workspace={workspace} />
      {workspace.declarations.length > 0 && (
        <table className="data-table" aria-label="Declaraciones del periodo">
          <thead>
            <tr>
              {["Obligación", "Vence", "Flujo", "Presentación", "Documentos", "Impuesto", ""].map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {workspace.declarations.map((d) => (
              <tr key={d.declaration_id}>
                <td>{d.obligation_name}</td>
                <td>{formatDate(d.due_date)}</td>
                <td>
                  <StatusSemaphore status={d.workflow_status} map={WORKFLOW_STATUS_SEMAPHORE} />
                </td>
                <td>
                  <StatusSemaphore status={d.filing_status} map={FILING_STATUS_SEMAPHORE} />
                </td>
                <td>
                  <StatusSemaphore status={d.document_status} map={DOCUMENT_STATUS_SEMAPHORE} />
                </td>
                <td>
                  <StatusSemaphore status={d.tax_payment_status} map={TAX_PAYMENT_STATUS_SEMAPHORE} />
                </td>
                <td>
                  <Link href={`/declarations/${d.declaration_id}`}>Abrir declaración</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2 style={{ fontSize: "1.05rem" }}>Presentación</h2>
      <DeclarationSections
        declarations={workspace.declarations.filter((d) => !isSireDeclaration(d))}
        section="presentacion"
        onChanged={onChanged}
        emptyMessage="No hay declaraciones para presentar. Las SIRE se gestionan en su pestaña."
      />
    </div>
  );
}

export function AccountingSection({ workspace, busy, onSubmitOperations }: SectionProps) {
  const ops = workspace.operations;
  return (
    <Card title="Contabilidad">
      <p>{ACCOUNTING_DETAIL_NOTICE}</p>
      <p style={MUTED}>Aquí solo se registra el estado operativo del trabajo del periodo; no hay asientos, libros ni balances.</p>
      {ops && (
        <OperationsControls
          operations={ops}
          canUpdate={workspace.can_update_operations}
          fields={[{ field: "accounting_status", label: "Estado de contabilidad", map: ACCOUNTING_STATUS_SEMAPHORE }]}
          showNotes
          busy={busy}
          onSubmit={onSubmitOperations}
        />
      )}
    </Card>
  );
}

export function PaymentsSection({ workspace, onChanged }: SectionProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
      <NotGeneratedNotice workspace={workspace} />
      <DeclarationSections
        declarations={workspace.declarations}
        section="impuestos"
        onChanged={onChanged}
        emptyMessage="No hay declaraciones generadas, por lo tanto no hay determinación ni pagos de impuesto."
      />
    </div>
  );
}

export function FeesSection({ clientId }: SectionProps) {
  return <ClientFeesPanel clientId={clientId} showHeader={false} />;
}

export function CommunicationSection({ workspace, clientId, busy, canCommunicate, onSubmitOperations }: SectionProps) {
  const ops = workspace.operations;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <Card title="Comunicación al cliente">
        <p style={MUTED}>{COMMUNICATION_NOTICE}</p>
        {ops && (
          <OperationsControls
            operations={ops}
            canUpdate={workspace.can_update_operations}
            fields={[
              { field: "client_communication_status", label: "Estado de comunicación", map: COMMUNICATION_STATUS_SEMAPHORE },
            ]}
            busy={busy}
            onSubmit={onSubmitOperations}
          />
        )}
      </Card>
      {canCommunicate && (
        <Card title="Borradores asistidos de WhatsApp">
          <p style={MUTED}>
            Los borradores son por cliente (no están ligados a un periodo). El sistema nunca envía mensajes: tú los abres y los envías.
          </p>
          {workspace.communication_drafts.length === 0 ? (
            <p style={MUTED}>Este cliente no tiene borradores todavía.</p>
          ) : (
            <ul style={{ paddingLeft: "1.1rem" }}>
              {workspace.communication_drafts.map((draft) => (
                <li key={draft.id}>
                  <Link href={`/communications/${draft.id}`}>{draft.template_code ?? "Mensaje libre"}</Link>{" "}
                  <span style={MUTED}>
                    · {draft.status} · {formatDate(draft.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p style={MUTED}>{workspace.communication_drafts_total === 1 ? "1 borrador en total." : `${workspace.communication_drafts_total} borradores en total.`}</p>
          <div>
            <Link href={`/communications/new?client_id=${clientId}`}>Crear borrador asistido</Link>
          </div>
        </Card>
      )}
    </div>
  );
}

export function HistorySection({ clientId, periodId, workspace }: SectionProps) {
  const [entries, setEntries] = useState<OperationsHistoryEntry[] | null>(null);
  const [error, setError] = useState(false);
  const version = workspace.operations?.row_version ?? 0;

  useEffect(() => {
    let cancelled = false;
    fetchOperationsHistory(clientId, periodId)
      .then((data) => !cancelled && setEntries(data))
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, [clientId, periodId, version]);

  if (error) return <p role="alert" style={{ color: "var(--danger)" }}>No se pudo cargar el historial.</p>;
  if (!entries) return <p>Cargando…</p>;
  if (entries.length === 0) return <p style={MUTED}>Aún no hay cambios manuales registrados para este periodo.</p>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="data-table" aria-label="Historial de cambios manuales">
        <thead>
          <tr>
            {["Fecha", "Quién", "Campo", "De", "A"].map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.created_at).toLocaleString("es-PE")}</td>
              <td>{e.actor_name ?? "—"}</td>
              <td>{HISTORY_FIELD_LABEL[e.field_name] ?? e.field_name}</td>
              <td>{historyValueLabel(e.field_name, e.from_value)}</td>
              <td>{historyValueLabel(e.field_name, e.to_value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
