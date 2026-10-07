"use client";

// Secciones operativas de una declaracion (documentos, impuestos/pagos, presentacion, cierre) reutilizables:
// las usan el detalle de la declaracion y el workspace cliente/periodo. No contienen logica propia.
import { ClosePanel } from "@/components/declarations/ClosePanel";
import { DocumentsPanel } from "@/components/declarations/DocumentsPanel";
import { PresentationPanel } from "@/components/declarations/PresentationPanel";
import { TaxAssessmentPanel } from "@/components/declarations/TaxAssessmentPanel";
import { TaxPaymentsPanel } from "@/components/declarations/TaxPaymentsPanel";
import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import { type DeclarationWork, useDeclarationWork } from "@/lib/useDeclarationWork";

export type DeclarationSection = "documentos" | "impuestos" | "presentacion" | "cierre";

const CLOSED_WORKFLOW = ["CLOSED", "CANCELLED", "NOT_APPLICABLE"];

export function DeclarationSectionView({ work, section }: { work: DeclarationWork; section: DeclarationSection }) {
  const { user } = useCurrentUser();
  const { declaration, history, taxPeriod } = work;
  if (work.loadError) return <p style={{ color: "var(--danger)" }}>{work.loadError}</p>;
  if (!declaration || !history || !taxPeriod) return <p>Cargando…</p>;

  const canActOnDeclaration = taxPeriod.is_open && !CLOSED_WORKFLOW.includes(declaration.workflow_status);

  if (section === "documentos") {
    return (
      <DocumentsPanel
        declaration={declaration}
        items={work.documentItems}
        requirements={work.requirements}
        catalog={work.catalog}
        periodIsOpen={taxPeriod.is_open}
        canManage={hasPermission(user, "document:manage")}
        canWriteItems={hasPermission(user, "document:item_write")}
        canManageFiles={hasPermission(user, "document:file_manage")}
        onSync={work.syncDocuments}
        onChangeStatus={work.changeDocumentStatus}
      />
    );
  }
  if (section === "impuestos") {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
        <section>
          <h2 style={{ fontSize: "1.1rem" }}>Determinación tributaria</h2>
          <TaxAssessmentPanel
            assessments={work.assessments}
            canAct={canActOnDeclaration && hasPermission(user, "tax_assessment:write")}
            onCreate={work.createAssessment}
            onCorrect={work.correctAssessment}
          />
        </section>
        <section>
          <h2 style={{ fontSize: "1.1rem" }}>Pagos tributarios</h2>
          <TaxPaymentsPanel
            taxPaymentStatus={declaration.tax_payment_status}
            payments={work.payments}
            assessments={work.assessments}
            canAct={canActOnDeclaration && work.assessments.some((a) => a.is_current) && hasPermission(user, "tax_payment:write")}
            canVoid={canActOnDeclaration && hasPermission(user, "tax_payment:void")}
            onCreate={work.createPayment}
            onVoid={work.voidPayment}
          />
        </section>
      </div>
    );
  }
  if (section === "presentacion") {
    return (
      <PresentationPanel
        declaration={declaration}
        filingHistory={history.filing_history}
        canAct={canActOnDeclaration && hasPermission(user, "declaration:file")}
        onFile={work.fileDeclaration}
        onCorrect={work.correctFiling}
      />
    );
  }
  return (
    <ClosePanel
      declaration={declaration}
      canClose={hasPermission(user, "declaration:close")}
      canOverrideClose={hasPermission(user, "declaration:close_override")}
      onClose={work.closeDeclaration}
    />
  );
}

// Carga los datos de una declaracion y muestra UNA seccion (un hook por declaracion en el workspace).
export function DeclarationSectionLoader({
  declarationId,
  section,
  onChanged,
}: {
  declarationId: string;
  section: DeclarationSection;
  onChanged?: () => void;
}) {
  const work = useDeclarationWork(declarationId, onChanged);
  return <DeclarationSectionView work={work} section={section} />;
}
