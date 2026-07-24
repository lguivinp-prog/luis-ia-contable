import { apiClient } from "@/lib/apiClient";
import type { DeclarationDocumentFile } from "@/lib/types/declarationDocument";

export function listDocumentFiles(
  declarationId: string,
  itemId: string,
): Promise<DeclarationDocumentFile[]> {
  return apiClient.get<DeclarationDocumentFile[]>(
    `/declarations/${declarationId}/documents/${itemId}/files`,
  );
}

export function uploadDocumentFile(
  declarationId: string,
  itemId: string,
  file: File,
  description: string,
): Promise<DeclarationDocumentFile> {
  const formData = new FormData();
  formData.append("file", file);
  if (description) formData.append("description", description);
  return apiClient.uploadFile<DeclarationDocumentFile>(
    `/declarations/${declarationId}/documents/${itemId}/files`,
    formData,
  );
}

export function replaceDocumentFile(
  declarationId: string,
  itemId: string,
  fileId: string,
  file: File,
  reason: string,
): Promise<DeclarationDocumentFile> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("reason", reason);
  return apiClient.uploadFile<DeclarationDocumentFile>(
    `/declarations/${declarationId}/documents/${itemId}/files/${fileId}/replace`,
    formData,
  );
}

export function deleteDocumentFile(
  declarationId: string,
  itemId: string,
  fileId: string,
  reason: string,
): Promise<DeclarationDocumentFile> {
  return apiClient.post<DeclarationDocumentFile>(
    `/declarations/${declarationId}/documents/${itemId}/files/${fileId}/delete`,
    { reason },
  );
}

export function downloadDocumentFile(
  declarationId: string,
  itemId: string,
  fileId: string,
): Promise<{ blob: Blob; filename: string }> {
  return apiClient.getFile(
    `/declarations/${declarationId}/documents/${itemId}/files/${fileId}/download`,
  );
}
