"use client";

import { useSyncExternalStore, type ComponentProps } from "react";

const subscribeNever = () => () => {};

/**
 * Formulario para datos sensibles (contraseñas, claves SOL, codigos de recuperacion).
 *
 * Garantiza que ningun secreto salga por un envio HTML NATIVO (GET/POST del navegador) cuando React aun no se ha
 * hidratado, no se pudo cargar el bundle o el JavaScript esta deshabilitado:
 * - hasta la hidratacion TODOS los controles estan deshabilitados (`<fieldset disabled>` renderizado por el servidor):
 *   no se puede escribir, el Enter no envia y el navegador nunca incluye controles deshabilitados en un envio;
 * - una vez hidratado, el envio SIEMPRE se intercepta (`preventDefault`) y solo lo procesa el manejador de JavaScript;
 * - `method="post"` + `action` explicito: un envio nativo jamas llevaria datos en la URL.
 */
export function SecureForm({ onSubmit, children, ...props }: Omit<ComponentProps<"form">, "method" | "action">) {
  const hydrated = useSyncExternalStore(subscribeNever, () => true, () => false);

  function handleSubmit(event: Parameters<NonNullable<ComponentProps<"form">["onSubmit"]>>[0]) {
    event.preventDefault();
    if (!hydrated) return;
    onSubmit?.(event);
  }

  return (
    <form {...props} method="post" action="#" onSubmit={handleSubmit}>
      <fieldset
        disabled={!hydrated}
        data-secure-form={hydrated ? "ready" : "pending"}
        style={{ display: "contents", border: 0, margin: 0, padding: 0, minWidth: 0 }}
      >
        {children}
      </fieldset>
    </form>
  );
}
