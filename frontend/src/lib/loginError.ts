import { ApiError } from "@/lib/apiClient";

// Mensaje del login segun la causa REAL: credenciales incorrectas, limite de intentos o servicio no disponible (nunca se mezclan).
export function describeLoginError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Correo o contraseña incorrectos.";
    if (err.status === 429) return typeof err.detail === "string" ? err.detail : "Demasiados intentos. Intenta nuevamente más tarde.";
    if (err.status >= 500) {
      return "El servicio de Luis IA no está disponible en este momento (error del servidor). No es un problema de tu contraseña; avisa a soporte o inténtalo cuando el servicio se restablezca.";
    }
    return "No se pudo iniciar sesión. Intenta nuevamente.";
  }
  // fallo de red antes de recibir respuesta (servidor caido o inalcanzable)
  return "No se pudo contactar con el servicio de Luis IA. Comprueba que esté en ejecución; no es un problema de tu contraseña.";
}
