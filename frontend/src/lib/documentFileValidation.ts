// Validacion de archivo en el cliente (Bloque 5, Seccion 13 del plan) --
// solo UX: nunca sustituye la validacion real por contenido del servidor.
const ALLOWED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".docx", ".xlsx"];

export const DOCUMENT_FILE_MAX_BYTES = 15_000_000;

export function validateDocumentFileClientSide(file: File): string | null {
  const lowerName = file.name.toLowerCase();
  const hasAllowedExtension = ALLOWED_EXTENSIONS.some((ext) => lowerName.endsWith(ext));
  if (!hasAllowedExtension) {
    return "Formato no permitido. Usa PDF, JPG, PNG, DOCX o XLSX.";
  }
  if (file.size === 0) {
    return "El archivo está vacío.";
  }
  if (file.size > DOCUMENT_FILE_MAX_BYTES) {
    const maxMb = (DOCUMENT_FILE_MAX_BYTES / 1_000_000).toFixed(0);
    return `El archivo excede el tamaño máximo permitido (${maxMb} MB).`;
  }
  return null;
}
