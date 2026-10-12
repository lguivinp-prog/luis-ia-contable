import { loginUrlFor } from "@/lib/authRedirect";
import { isDomainUnauthorizedBody } from "@/lib/apiAuthRedirect";
import { readCsrfTokenFromCookie } from "@/lib/csrf";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : "Error de la API");
    this.status = status;
    this.detail = detail;
  }
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };

  // CSRF double-submit cookie (ver ADR-0010): se reenvia en cada mutacion.
  // Nunca se lee/escribe en localStorage, solo la cookie no-HttpOnly.
  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCsrfTokenFromCookie();
    if (csrfToken) {
      headers["X-CSRF-Token"] = csrfToken;
    }
  }

  const response = await fetch(`/api/v1${path}`, {
    ...options,
    method,
    credentials: "include",
    headers,
  });

  if (response.status === 401 && typeof window !== "undefined") {
    let domainError = false;
    try {
      domainError = isDomainUnauthorizedBody(await response.clone().json());
    } catch {
      domainError = false;
    }
    if (!domainError && !window.location.pathname.startsWith("/login")) {
      window.location.href = loginUrlFor(window.location.pathname + window.location.search);
    }
  }

  if (!response.ok) {
    let detail: unknown = null;
    try {
      const body = await response.json();
      detail = body.detail ?? body;
    } catch {
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

async function requestFormData<T>(path: string, formData: FormData): Promise<T> {
  // No se fija "Content-Type": el navegador debe generar el boundary del
  // multipart/form-data automaticamente.
  const headers: Record<string, string> = {};
  const csrfToken = readCsrfTokenFromCookie();
  if (csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }

  const response = await fetch(`/api/v1${path}`, {
    method: "POST",
    credentials: "include",
    headers,
    body: formData,
  });

  if (response.status === 401 && typeof window !== "undefined") {
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = loginUrlFor(window.location.pathname + window.location.search);
    }
  }

  if (!response.ok) {
    let detail: unknown = null;
    try {
      const body = await response.json();
      detail = body.detail ?? body;
    } catch {
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

function _filenameFromContentDisposition(response: Response, fallback: string): string {
  const disposition = response.headers.get("content-disposition") ?? "";
  const utf8Match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      // sigue al siguiente intento
    }
  }
  const asciiMatch = /filename="([^"]+)"/.exec(disposition);
  return asciiMatch ? asciiMatch[1] : fallback;
}

async function requestGetBlob(path: string): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(`/api/v1${path}`, {
    method: "GET",
    credentials: "include",
  });

  if (response.status === 401 && typeof window !== "undefined") {
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = loginUrlFor(window.location.pathname + window.location.search);
    }
  }

  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = (await response.json()).detail ?? null;
    } catch {
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }

  const filename = _filenameFromContentDisposition(response, "documento");
  return { blob: await response.blob(), filename };
}

async function requestBlob(path: string, body: unknown): Promise<{ blob: Blob; filename: string }> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const csrfToken = readCsrfTokenFromCookie();
  if (csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }

  const response = await fetch(`/api/v1${path}`, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = (await response.json()).detail ?? null;
    } catch {
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }

  const disposition = response.headers.get("content-disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  const filename = match ? match[1] : "reporte";
  return { blob: await response.blob(), filename };
}

export const apiClient = {
  get: <T>(path: string) => request<T>(path, { method: "GET" }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  uploadFile: <T>(path: string, formData: FormData) => requestFormData<T>(path, formData),
  postForFile: (path: string, body: unknown) => requestBlob(path, body),
  getFile: (path: string) => requestGetBlob(path),
};
