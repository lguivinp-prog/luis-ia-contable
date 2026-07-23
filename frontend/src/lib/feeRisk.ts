// Espejo en TypeScript de app/services/professional_fee_risk.py -- funcion
// pura calculada en vivo, nunca persistida (ver "Riesgo de cobranza de
// honorarios" del plan / ADR-0045). El backend no expone el riesgo de un
// cargo individual via API, asi que se recalcula aqui con los mismos datos
// (status, due_date, balance) que ya llegan al frontend.
export type FeeCollectionRisk = "CANCELLED" | "PAID" | "ON_TRACK" | "OVERDUE" | "DUE_TODAY";

export function computeFeeCollectionRisk(
  dueDate: string | null,
  status: string,
  balance: number,
  today: string,
): FeeCollectionRisk {
  if (status === "CANCELLED") return "CANCELLED";
  if (status === "PAID" || balance <= 0) return "PAID";
  if (!dueDate) return "ON_TRACK";
  if (dueDate < today) return "OVERDUE";
  if (dueDate === today) return "DUE_TODAY";
  return "ON_TRACK";
}
