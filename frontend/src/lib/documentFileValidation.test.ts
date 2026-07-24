import { describe, expect, it } from "vitest";

import { DOCUMENT_FILE_MAX_BYTES, validateDocumentFileClientSide } from "./documentFileValidation";

function makeFile(name: string, size: number): File {
  const content = size > 0 ? new Uint8Array(size) : new Uint8Array(0);
  const file = new File([content], name);
  Object.defineProperty(file, "size", { value: size });
  return file;
}

describe("validateDocumentFileClientSide", () => {
  it("accepts an allowed extension within the size limit", () => {
    expect(validateDocumentFileClientSide(makeFile("comprobante.pdf", 1000))).toBeNull();
  });

  it("accepts uppercase extensions", () => {
    expect(validateDocumentFileClientSide(makeFile("COMPROBANTE.PDF", 1000))).toBeNull();
  });

  it("rejects an unsupported extension", () => {
    expect(validateDocumentFileClientSide(makeFile("malicioso.exe", 1000))).not.toBeNull();
  });

  it("rejects an empty file", () => {
    expect(validateDocumentFileClientSide(makeFile("vacio.pdf", 0))).not.toBeNull();
  });

  it("rejects a file exceeding the configured maximum", () => {
    expect(
      validateDocumentFileClientSide(makeFile("grande.pdf", DOCUMENT_FILE_MAX_BYTES + 1)),
    ).not.toBeNull();
  });

  it("accepts a file exactly at the maximum size", () => {
    expect(validateDocumentFileClientSide(makeFile("limite.pdf", DOCUMENT_FILE_MAX_BYTES))).toBeNull();
  });
});
