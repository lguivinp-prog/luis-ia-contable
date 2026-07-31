"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { apiClient } from "@/lib/apiClient";
import { buildCommunicationDraftPayload } from "@/lib/communicationDraftPayload";
import { classifyCommunicationMutationError } from "@/lib/communicationMutationError";
import { createCommunicationDraft, listTemplates, previewTemplate } from "@/lib/communications";
import { listTasks } from "@/lib/tasks";
import type { Client, ClientListResponse } from "@/lib/types/client";
import type { OperationalTask } from "@/lib/types/task";
import type { TemplateOut } from "@/lib/types/communication";

export default function NewCommunicationDraftPage() {
  const router = useRouter();

  const [clients, setClients] = useState<Client[]>([]);
  const [tasks, setTasks] = useState<OperationalTask[]>([]);
  const [templates, setTemplates] = useState<TemplateOut[]>([]);

  const [clientId, setClientId] = useState("");
  const [relatedTaskId, setRelatedTaskId] = useState("");
  const [relatedDocumentFileId, setRelatedDocumentFileId] = useState("");
  const [templateCode, setTemplateCode] = useState("");
  const [variableValues, setVariableValues] = useState<Record<string, string>>({});
  const [body, setBody] = useState("");
  const [recipientPhone, setRecipientPhone] = useState("");

  const [previewWarnings, setPreviewWarnings] = useState<string[]>([]);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void apiClient
      .get<ClientListResponse>("/clients?limit=200")
      .then((data) => setClients(data.items))
      .catch(() => setClients([]));
    void listTemplates()
      .then((data) => setTemplates(data.items))
      .catch(() => setTemplates([]));
  }, []);

  useEffect(() => {
    if (!clientId) {
      void Promise.resolve().then(() => {
        setRelatedTaskId("");
        setTasks([]);
      });
      return;
    }
    void listTasks({ client_id: clientId, limit: 200 })
      .then((data) => {
        setRelatedTaskId("");
        setTasks(data.items);
      })
      .catch(() => {
        setRelatedTaskId("");
        setTasks([]);
      });
  }, [clientId]);

  const selectedClient = clients.find((c) => c.id === clientId) ?? null;
  const selectedTemplate = templates.find((t) => t.code === templateCode) ?? null;

  const handleTemplateChange = (code: string) => {
    setTemplateCode(code);
    const template = templates.find((t) => t.code === code);
    const initial: Record<string, string> = {};
    for (const variable of template?.variables ?? []) initial[variable] = "";
    setVariableValues(initial);
    setPreviewWarnings([]);
  };

  const handlePreview = async () => {
    if (!clientId || !templateCode) return;
    setPreviewing(true);
    setError(null);
    try {
      const result = await previewTemplate(templateCode, {
        client_id: clientId,
        related_task_id: relatedTaskId || undefined,
        related_document_file_id: relatedDocumentFileId || undefined,
        variables: variableValues,
      });
      setBody(result.body_rendered);
      setPreviewWarnings(result.warnings);
    } catch (err) {
      setError(classifyCommunicationMutationError(err).message);
    } finally {
      setPreviewing(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!clientId || !body.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const draft = await createCommunicationDraft(
        buildCommunicationDraftPayload({
          clientId,
          relatedTaskId,
          relatedDocumentFileId,
          templateCode,
          templateVersion: selectedTemplate?.version ?? null,
          body,
          recipientPhone,
        }),
      );
      router.push(`/communications/${draft.id}`);
    } catch (err) {
      setError(classifyCommunicationMutationError(err).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ maxWidth: "640px" }}>
      <h1 style={{ fontSize: "1.4rem", marginBottom: "0.25rem" }}>Nuevo borrador de comunicación</h1>
      <p style={{ color: "var(--muted)", marginBottom: "1rem" }}>
        Este borrador nunca se envía automáticamente. Tú revisas el texto y decides abrir WhatsApp para
        enviarlo a mano.
      </p>
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
        <Select
          label="Cliente"
          placeholder="Selecciona un cliente"
          options={clients.map((c) => ({ value: c.id, label: `${c.business_name} — ${c.ruc}` }))}
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
          required
        />
        {selectedClient && (
          <p style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
            Número de WhatsApp del cliente: {selectedClient.whatsapp_number ?? "no registrado"}
          </p>
        )}

        <Select
          label="Tarea relacionada (opcional)"
          placeholder="Ninguna"
          options={tasks.map((t) => ({ value: t.id, label: t.title }))}
          value={relatedTaskId}
          onChange={(e) => setRelatedTaskId(e.target.value)}
          disabled={!clientId}
        />

        <Input
          label="Documento relacionado (opcional, ID)"
          value={relatedDocumentFileId}
          onChange={(e) => setRelatedDocumentFileId(e.target.value)}
          hint={
            <span style={{ fontSize: "0.8rem", color: "var(--muted)" }}>
              Debe pertenecer al mismo cliente seleccionado.
            </span>
          }
        />

        <Select
          label="Plantilla (opcional)"
          placeholder="Sin plantilla — redactar libremente"
          options={templates.map((t) => ({ value: t.code, label: t.display_name }))}
          value={templateCode}
          onChange={(e) => handleTemplateChange(e.target.value)}
        />

        {selectedTemplate && selectedTemplate.variables.length > 0 && (
          <div style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "0.75rem" }}>
            <p style={{ fontSize: "0.85rem", color: "var(--muted)", marginBottom: "0.5rem" }}>
              {selectedTemplate.description}
            </p>
            {selectedTemplate.variables.map((variable) => (
              <Input
                key={variable}
                label={variable}
                value={variableValues[variable] ?? ""}
                onChange={(e) => setVariableValues((prev) => ({ ...prev, [variable]: e.target.value }))}
              />
            ))}
            <Button type="button" variant="secondary" onClick={handlePreview} disabled={previewing}>
              {previewing ? "Generando…" : "Previsualizar texto"}
            </Button>
          </div>
        )}

        {previewWarnings.length > 0 && (
          <p style={{ color: "var(--danger)" }}>
            {previewWarnings.includes("WHATSAPP_NUMBER_REQUIRED")
              ? "El cliente no tiene un número de WhatsApp registrado. Puedes indicar uno manualmente abajo."
              : "El número de WhatsApp registrado no es válido. Puedes indicar uno manualmente abajo."}
          </p>
        )}

        <div className="field">
          <label htmlFor="body">Texto del mensaje</label>
          <textarea
            id="body"
            className="input"
            rows={6}
            maxLength={4000}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            required
          />
        </div>

        <Input
          label="Número de WhatsApp (opcional, reemplaza el del cliente)"
          value={recipientPhone}
          onChange={(e) => setRecipientPhone(e.target.value)}
          placeholder="+51987654321"
        />

        {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

        <div style={{ display: "flex", gap: "0.5rem" }}>
          <Button type="submit" disabled={submitting || !clientId || !body.trim()}>
            {submitting ? "Guardando…" : "Guardar borrador"}
          </Button>
          <Button type="button" variant="secondary" onClick={() => router.push("/communications")}>
            Cancelar
          </Button>
        </div>
      </form>
    </div>
  );
}
