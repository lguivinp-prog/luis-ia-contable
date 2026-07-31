import { describe, expect, it } from "vitest";

import { buildCommunicationDraftPayload } from "@/lib/communicationDraftPayload";

const BASE_FORM = {
  clientId: "client-1",
  relatedTaskId: "",
  relatedDocumentFileId: "",
  templateCode: "",
  templateVersion: null,
  body: "  Hola, este es un mensaje.  ",
  recipientPhone: "",
};

describe("buildCommunicationDraftPayload", () => {
  it("recorta el body y siempre incluye client_id/body", () => {
    const payload = buildCommunicationDraftPayload(BASE_FORM);
    expect(payload).toEqual({ client_id: "client-1", body: "Hola, este es un mensaje." });
  });

  it("omite related_task_id por completo cuando está vacío", () => {
    const payload = buildCommunicationDraftPayload(BASE_FORM);
    expect("related_task_id" in payload).toBe(false);
  });

  it("incluye related_task_id cuando se selecciona una tarea", () => {
    const payload = buildCommunicationDraftPayload({ ...BASE_FORM, relatedTaskId: "task-1" });
    expect(payload.related_task_id).toBe("task-1");
  });

  it("omite related_document_file_id por completo cuando está vacío", () => {
    const payload = buildCommunicationDraftPayload(BASE_FORM);
    expect("related_document_file_id" in payload).toBe(false);
  });

  it("incluye template_code y template_version juntos cuando hay plantilla", () => {
    const payload = buildCommunicationDraftPayload({
      ...BASE_FORM,
      templateCode: "GENERAL_MESSAGE",
      templateVersion: 1,
    });
    expect(payload.template_code).toBe("GENERAL_MESSAGE");
    expect(payload.template_version).toBe(1);
  });

  it("omite template_code y template_version cuando no se usó plantilla", () => {
    const payload = buildCommunicationDraftPayload(BASE_FORM);
    expect("template_code" in payload).toBe(false);
    expect("template_version" in payload).toBe(false);
  });

  it("recorta y omite recipient_phone cuando queda vacío tras recortar", () => {
    const payload = buildCommunicationDraftPayload({ ...BASE_FORM, recipientPhone: "   " });
    expect("recipient_phone" in payload).toBe(false);
  });

  it("incluye recipient_phone recortado cuando se proporciona un override", () => {
    const payload = buildCommunicationDraftPayload({ ...BASE_FORM, recipientPhone: " +51987654321 " });
    expect(payload.recipient_phone).toBe("+51987654321");
  });
});
