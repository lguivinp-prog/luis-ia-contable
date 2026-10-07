"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import {
  AccountingSection,
  BuzonSection,
  CommunicationSection,
  DeclarationsSection,
  DocumentsSection,
  FeesSection,
  HistorySection,
  PaymentsSection,
  type SectionProps,
  SireSection,
  SummarySection,
} from "@/components/cockpit/WorkspaceSections";
import { Select } from "@/components/ui/Select";
import { StatusSemaphore } from "@/components/ui/StatusSemaphore";
import { ApiError, apiClient } from "@/lib/apiClient";
import { fetchWorkspace, updateOperations } from "@/lib/cockpit";
import {
  COCKPIT_RISK_SEMAPHORE,
  MONTHLY_PHASE_SEMAPHORE,
  defaultPeriodId,
  formatDate,
  monthlyPeriodsNewestFirst,
} from "@/lib/cockpitPresentation";
import {
  type WorkspaceTabKey,
  cockpitBackUrl,
  describeOperationsError,
  parseWorkspaceTab,
  visibleWorkspaceTabs,
  workspaceUrl,
} from "@/lib/cockpitWorkspace";
import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import type { OperationsUpdate, Workspace } from "@/lib/types/cockpit";
import type { TaxPeriod } from "@/lib/types/declaration";

const SECTIONS: Record<WorkspaceTabKey, (props: SectionProps) => React.ReactNode> = {
  resumen: SummarySection,
  buzon: BuzonSection,
  documentos: DocumentsSection,
  sire: SireSection,
  declaraciones: DeclarationsSection,
  contabilidad: AccountingSection,
  pagos: PaymentsSection,
  honorarios: FeesSection,
  comunicacion: CommunicationSection,
  historial: HistorySection,
};

export default function ClientWorkspacePage() {
  return (
    <Suspense fallback={<p>Cargando…</p>}>
      <ClientWorkspace />
    </Suspense>
  );
}

function ClientWorkspace() {
  const params = useParams<{ clientId: string }>();
  const clientId = params.clientId;
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user } = useCurrentUser();

  const urlPeriod = searchParams.get("period") ?? "";
  const tab = parseWorkspaceTab(searchParams.get("tab"));

  const [periods, setPeriods] = useState<TaxPeriod[]>([]);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Periodos: sin `period` en la URL se elige el predeterminado y se reemplaza la URL (sin ensuciar el historial).
  useEffect(() => {
    void apiClient
      .get<TaxPeriod[]>("/tax-periods")
      .then((list) => {
        setPeriods(list);
        if (!urlPeriod) {
          const initial = defaultPeriodId(list);
          if (initial) router.replace(workspaceUrl(clientId, initial, tab));
          else setLoading(false);
        }
      })
      .catch(() => {
        setError("No se pudieron cargar los periodos tributarios.");
        setLoading(false);
      });
    // Solo al montar: el resto de cambios llegan por la URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = useCallback(async () => {
    if (!urlPeriod) return;
    setError(null);
    try {
      setWorkspace(await fetchWorkspace(clientId, urlPeriod));
    } catch (err) {
      setWorkspace(null);
      setError(
        err instanceof ApiError && err.status === 404
          ? "No se encontró el cliente o el periodo."
          : "No se pudo cargar el workspace.",
      );
    } finally {
      setLoading(false);
    }
  }, [clientId, urlPeriod]);

  useEffect(() => {
    void Promise.resolve().then(() => load());
  }, [load]);

  const submitOperations = useCallback(
    async (update: OperationsUpdate) => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const operations = await updateOperations(clientId, urlPeriod, update);
        setWorkspace((current) => (current ? { ...current, operations } : current));
        setNotice("Cambio guardado.");
        await load(); // refresca fila resumen (estados manuales) desde el backend
      } catch (err) {
        const info = describeOperationsError(err);
        if (info.reload) await load(); // load() limpia el error: el mensaje se muestra DESPUES de recargar
        setError(info.message);
      } finally {
        setBusy(false);
      }
    },
    [clientId, urlPeriod, load],
  );

  const monthly = useMemo(() => monthlyPeriodsNewestFirst(periods), [periods]);
  const tabs = useMemo(() => visibleWorkspaceTabs(user), [user]);
  const activeTab = tabs.some((t) => t.key === tab) ? tab : "resumen";

  const navigate = (nextPeriod: string, nextTab: WorkspaceTabKey) => {
    setNotice(null);
    router.push(workspaceUrl(clientId, nextPeriod, nextTab));
  };

  const Section = SECTIONS[activeTab];
  const summary = workspace?.summary;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.1rem", minWidth: 0 }}>
      <div>
        <Link href={cockpitBackUrl()} style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          ← Volver al cockpit mensual
        </Link>
        <h1 style={{ fontSize: "1.4rem", marginTop: "0.3rem" }}>{summary ? summary.business_name : "Workspace del cliente"}</h1>
        {summary && (
          <p style={{ color: "var(--muted)" }}>
            RUC {summary.ruc} · dígito {summary.ruc_last_digit}
          </p>
        )}
      </div>

      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "flex-end" }}>
        <Select
          id="workspace-period"
          label="Periodo tributario"
          options={monthly.map((p) => ({ value: p.id, label: p.display_name ?? p.canonical_code }))}
          value={urlPeriod}
          onChange={(e) => navigate(e.target.value, activeTab)}
        />
        {summary && (
          <>
            <StatusSemaphore status={summary.monthly_phase} map={MONTHLY_PHASE_SEMAPHORE} />
            <StatusSemaphore status={summary.overall_risk} map={COCKPIT_RISK_SEMAPHORE} />
            <span style={{ color: "var(--muted)" }}>Vence {formatDate(summary.nearest_due_date)}</span>
          </>
        )}
        {workspace?.sol_access && <Link href={`/clients/${clientId}/sol-access`}>Acceso SOL supervisado</Link>}
      </div>

      <div
        role="tablist"
        aria-label="Secciones del workspace"
        style={{ display: "flex", gap: "0.25rem", borderBottom: "1px solid var(--border)", overflowX: "auto" }}
      >
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={activeTab === t.key}
            onClick={() => urlPeriod && navigate(urlPeriod, t.key)}
            style={{
              padding: "0.5rem 0.85rem",
              border: "none",
              background: "none",
              whiteSpace: "nowrap",
              fontWeight: activeTab === t.key ? 700 : 400,
              borderBottom: activeTab === t.key ? "2px solid var(--accent)" : "2px solid transparent",
              cursor: "pointer",
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <p role="alert" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}
      {notice && (
        <p role="status" style={{ color: "var(--muted)" }}>
          {notice}
        </p>
      )}
      {loading && !workspace && <p>Cargando…</p>}
      {workspace && (
        <div role="tabpanel">
          <Section
            workspace={workspace}
            clientId={clientId}
            periodId={urlPeriod}
            busy={busy}
            canCommunicate={hasPermission(user, "communication:draft")}
            onSubmitOperations={submitOperations}
            onChanged={() => void load()}
          />
        </div>
      )}
    </div>
  );
}
