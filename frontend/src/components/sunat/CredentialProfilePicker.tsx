"use client";

// Selector EXPLICITO del perfil de credencial SOL (M3). Con un solo perfil no se muestra (compatibilidad M2). Con varios, no hay preseleccion: el
// usuario elige y el acceso se abre SOLO con ese perfil; si falla no se prueba otro (ni mas privilegiado) automaticamente. El nombre del perfil es una
// etiqueta interna: lo que puede abrir lo comprueba el portal. Las claves se registran unicamente en el formulario seguro de la boveda.
import Link from "next/link";

import { usableProfiles } from "@/lib/solAccessModel";
import type { SolCredentialStatusOut } from "@/lib/types/solVault";

const KIND_LABEL: Record<string, string> = { SECONDARY: "secundaria", PRIMARY_OWN_RUC: "principal", PRIMARY_EXCEPTIONAL: "principal (excepción)" };

interface Props {
  clientId: string;
  profiles: SolCredentialStatusOut[];
  chosenId: string | null;
  onChoose: (id: string | null) => void;
  disabled?: boolean;
}

export function CredentialProfilePicker({ clientId, profiles, chosenId, onChoose, disabled }: Props) {
  if (profiles.length <= 1) return null;
  const usable = new Set(usableProfiles(profiles).map((p) => p.id));
  return (
    <fieldset data-testid="credential-picker" style={{ border: "1px solid var(--border, #ddd)", borderRadius: "8px", padding: "0.7rem" }} disabled={disabled}>
      <legend style={{ fontSize: "0.9rem", fontWeight: 600 }}>¿Con qué credencial SOL se abre el acceso?</legend>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
        {profiles.map((p) => (
          <label key={p.id} style={{ display: "flex", gap: "0.5rem", alignItems: "center", opacity: usable.has(p.id) ? 1 : 0.6 }}>
            <input type="radio" name="sol-profile" value={p.id} checked={chosenId === p.id} disabled={!usable.has(p.id)} onChange={() => onChoose(p.id)} />
            <span>
              <strong>{p.profile_name ?? "Credencial"}</strong> <span style={{ color: "var(--muted)" }}>({KIND_LABEL[p.credential_kind] ?? p.credential_kind}{usable.has(p.id) ? "" : " · no utilizable"})</span>
            </span>
          </label>
        ))}
      </div>
      <p style={{ color: "var(--muted)", fontSize: "0.8rem", marginTop: "0.4rem" }}>
        Si el portal no permite un destino con esta credencial, Luis IA no probará otra por su cuenta.{" "}
        <Link href={`/clients/${clientId}/sol-credential`}>Administrar perfiles</Link>
      </p>
    </fieldset>
  );
}
