"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { ApiError } from "@/lib/apiClient";
import {
  deleteDocumentFile,
  downloadDocumentFile,
  listDocumentFiles,
  replaceDocumentFile,
  uploadDocumentFile,
} from "@/lib/documentFiles";
import { validateDocumentFileClientSide } from "@/lib/documentFileValidation";
import type { DeclarationDocumentFile } from "@/lib/types/declarationDocument";

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: "Activo",
  SUPERSEDED: "Reemplazado",
  DELETED: "Eliminado",
};

const STATUS_CLASS: Record<string, string> = {
  ACTIVE: "status-verde",
  SUPERSEDED: "status-gris",
  DELETED: "status-rojo",
};

interface DocumentItemFilesProps {
  declarationId: string;
  itemId: string;
  canUpload: boolean;
  canManageFiles: boolean;
}

interface Chain {
  logicalFileId: string;
  active: DeclarationDocumentFile | null;
  history: DeclarationDocumentFile[];
}

function groupIntoChains(files: DeclarationDocumentFile[]): Chain[] {
  const byChain = new Map<string, DeclarationDocumentFile[]>();
  for (const file of files) {
    const list = byChain.get(file.logical_file_id) ?? [];
    list.push(file);
    byChain.set(file.logical_file_id, list);
  }
  const chains: Chain[] = [];
  for (const [logicalFileId, chainFiles] of byChain) {
    const sorted = [...chainFiles].sort((a, b) => b.version - a.version);
    const active = sorted.find((f) => f.status === "ACTIVE") ?? null;
    const history = sorted.filter((f) => f.id !== active?.id);
    chains.push({ logicalFileId, active, history });
  }
  return chains;
}

async function triggerBrowserDownload(declarationId: string, itemId: string, fileId: string) {
  const { blob, filename } = await downloadDocumentFile(declarationId, itemId, fileId);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function DocumentItemFiles({
  declarationId,
  itemId,
  canUpload,
  canManageFiles,
}: DocumentItemFilesProps) {
  const [files, setFiles] = useState<DeclarationDocumentFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [showHistoryFor, setShowHistoryFor] = useState<Set<string>>(new Set());
  const [replacingChain, setReplacingChain] = useState<string | null>(null);
  const [replaceReason, setReplaceReason] = useState("");
  const [busyFileId, setBusyFileId] = useState<string | null>(null);

  const uploadInputRef = useRef<HTMLInputElement>(null);
  const replaceInputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listDocumentFiles(declarationId, itemId);
      setFiles(data);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudieron cargar los archivos.");
    } finally {
      setLoading(false);
    }
  }, [declarationId, itemId]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  async function handleUploadChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const validationError = validateDocumentFileClientSide(file);
    if (validationError) {
      setError(validationError);
      if (uploadInputRef.current) uploadInputRef.current.value = "";
      return;
    }
    setUploading(true);
    setError(null);
    try {
      await uploadDocumentFile(declarationId, itemId, file, "");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo subir el archivo.");
    } finally {
      setUploading(false);
      if (uploadInputRef.current) uploadInputRef.current.value = "";
    }
  }

  async function handleDownload(fileId: string) {
    setError(null);
    try {
      await triggerBrowserDownload(declarationId, itemId, fileId);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo descargar el archivo.");
    }
  }

  async function handleReplaceChange(event: React.ChangeEvent<HTMLInputElement>, fileId: string) {
    const file = event.target.files?.[0];
    if (!file || !replaceReason.trim()) {
      if (!replaceReason.trim()) setError("Indica un motivo para el reemplazo.");
      return;
    }
    const validationError = validateDocumentFileClientSide(file);
    if (validationError) {
      setError(validationError);
      return;
    }
    setBusyFileId(fileId);
    setError(null);
    try {
      await replaceDocumentFile(declarationId, itemId, fileId, file, replaceReason);
      setReplacingChain(null);
      setReplaceReason("");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo reemplazar el archivo.");
    } finally {
      setBusyFileId(null);
      if (replaceInputRef.current) replaceInputRef.current.value = "";
    }
  }

  async function handleDelete(fileId: string) {
    const reason = window.prompt("Motivo de la eliminación:");
    if (!reason || !reason.trim()) return;
    const confirmed = window.confirm(
      "¿Confirmas eliminar este archivo? El archivo físico se conserva para auditoría, pero dejará de estar activo.",
    );
    if (!confirmed) return;
    setBusyFileId(fileId);
    setError(null);
    try {
      await deleteDocumentFile(declarationId, itemId, fileId, reason);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo eliminar el archivo.");
    } finally {
      setBusyFileId(null);
    }
  }

  function toggleHistory(chainId: string) {
    setShowHistoryFor((prev) => {
      const next = new Set(prev);
      if (next.has(chainId)) next.delete(chainId);
      else next.add(chainId);
      return next;
    });
  }

  if (loading) {
    return <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>Cargando archivos…</p>;
  }

  const chains = groupIntoChains(files);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
      {error && <p style={{ color: "var(--danger)", fontSize: "0.85rem" }}>{error}</p>}

      {chains.length === 0 ? (
        <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          Este ítem no tiene archivos cargados todavía.
        </p>
      ) : (
        chains.map((chain) => (
          <div
            key={chain.logicalFileId}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "0.25rem",
              padding: "0.4rem",
              border: "1px solid var(--border)",
              borderRadius: "6px",
            }}
          >
            {chain.active ? (
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                <span
                  className={`status-pill ${STATUS_CLASS[chain.active.status]}`}
                  aria-label={`Estado: ${STATUS_LABEL[chain.active.status]}`}
                >
                  {STATUS_LABEL[chain.active.status]}
                </span>
                <span style={{ fontSize: "0.85rem" }}>
                  {chain.active.original_filename} (v{chain.active.version})
                </span>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => handleDownload(chain.active!.id)}
                >
                  Descargar
                </Button>
                {canManageFiles && (
                  <>
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={busyFileId === chain.active.id}
                      onClick={() => {
                        setReplacingChain(chain.logicalFileId);
                        setReplaceReason("");
                      }}
                    >
                      Reemplazar
                    </Button>
                    <Button
                      type="button"
                      variant="danger"
                      disabled={busyFileId === chain.active.id}
                      onClick={() => handleDelete(chain.active!.id)}
                    >
                      {busyFileId === chain.active.id ? "Eliminando…" : "Eliminar"}
                    </Button>
                  </>
                )}
              </div>
            ) : (
              <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
                Esta cadena de versiones ya no tiene una versión activa.
              </p>
            )}

            {replacingChain === chain.logicalFileId && chain.active && (
              <div
                style={{
                  display: "flex",
                  gap: "0.5rem",
                  alignItems: "flex-end",
                  flexWrap: "wrap",
                  padding: "0.5rem",
                  border: "1px solid var(--danger)",
                  borderRadius: "6px",
                }}
              >
                <Input
                  label="Motivo del reemplazo"
                  value={replaceReason}
                  onChange={(e) => setReplaceReason(e.target.value)}
                  required
                />
                <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                  <span style={{ fontSize: "0.8rem" }}>Nuevo archivo</span>
                  <input
                    ref={replaceInputRef}
                    type="file"
                    aria-label="Nuevo archivo para reemplazo"
                    onChange={(e) => handleReplaceChange(e, chain.active!.id)}
                    disabled={busyFileId === chain.active.id}
                  />
                </label>
                <Button type="button" variant="secondary" onClick={() => setReplacingChain(null)}>
                  Cancelar
                </Button>
              </div>
            )}

            {chain.history.length > 0 && (
              <div>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => toggleHistory(chain.logicalFileId)}
                >
                  {showHistoryFor.has(chain.logicalFileId) ? "Ocultar historial" : "Ver historial"}
                </Button>
                {showHistoryFor.has(chain.logicalFileId) && (
                  <ul style={{ listStyle: "none", padding: 0, marginTop: "0.4rem" }}>
                    {chain.history.map((f) => (
                      <li
                        key={f.id}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "0.5rem",
                          fontSize: "0.8rem",
                          marginBottom: "0.25rem",
                        }}
                      >
                        <span className={`status-pill ${STATUS_CLASS[f.status]}`}>
                          {STATUS_LABEL[f.status]}
                        </span>
                        <span>
                          {f.original_filename} (v{f.version})
                        </span>
                        <Button type="button" variant="secondary" onClick={() => handleDownload(f.id)}>
                          Descargar
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        ))
      )}

      {canUpload && (
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
          <span style={{ fontSize: "0.8rem" }}>Subir archivo nuevo</span>
          <input
            ref={uploadInputRef}
            type="file"
            aria-label="Subir archivo nuevo"
            onChange={handleUploadChange}
            disabled={uploading}
          />
          {uploading && <span style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Subiendo…</span>}
        </label>
      )}
    </div>
  );
}
