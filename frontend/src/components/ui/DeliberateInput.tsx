"use client";

import { useId, useState, type ChangeEvent, type InputHTMLAttributes, type ReactNode } from "react";

interface DeliberateInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "name" | "autoComplete" | "readOnly"> {
  label: string;
  hint?: ReactNode;
  /** Campo secreto (clave SOL, contraseña de bóveda): se enmascara y se declara como contraseña NUEVA. */
  secret?: boolean;
}

/** Tipos de evento `input` que produce una persona (teclear, pegar, arrastrar, dictar, borrar, deshacer). */
export function isDeliberateInput(inputType: string | undefined | null): boolean {
  if (!inputType) return false; // sin `inputType`: relleno programatico o del gestor de contraseñas
  if (inputType === "insertReplacementText") return false; // asi marca Chrome el autocompletado
  return inputType.startsWith("insert") || inputType.startsWith("delete") || inputType.startsWith("history");
}

/**
 * Campo para credenciales de OTRO sistema (SOL/SUNAT) o de la bóveda, que jamas debe rellenarse con credenciales
 * guardadas del navegador (p. ej. el acceso del administrador a Luis IA). No se confia solo en `autocomplete="off"`:
 * 1. `name` unico y sin significado (no coincide con ninguna credencial guardada para el origen);
 * 2. `autocomplete="new-password"` (secretos) / `"off"` + marcas de gestores de contraseñas de terceros;
 * 3. `readOnly` hasta que la persona enfoca el campo (un campo de solo lectura no se autocompleta);
 * 4. solo se aceptan cambios DELIBERADOS (`isDeliberateInput`): un relleno automatico no llega al estado de React, y
 *    como el campo es controlado el navegador vuelve a mostrarlo vacio.
 */
export function DeliberateInput({ label, hint, secret = false, id, className, onChange, ...props }: DeliberateInputProps) {
  const uid = useId();
  const [armed, setArmed] = useState(false);
  const fieldId = id ?? `sol-field-${uid}`;

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    if (!isDeliberateInput((event.nativeEvent as InputEvent).inputType)) return;
    onChange?.(event);
  }

  return (
    <div className="field">
      <label htmlFor={fieldId}>{label}</label>
      <input
        {...props}
        id={fieldId}
        name={`f-${uid.replace(/[^a-zA-Z0-9]/g, "")}`}
        type={secret ? "password" : (props.type ?? "text")}
        autoComplete={secret ? "new-password" : "off"}
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
        readOnly={!armed}
        onFocus={(event) => {
          setArmed(true);
          props.onFocus?.(event);
        }}
        onPointerDown={(event) => {
          setArmed(true);
          props.onPointerDown?.(event);
        }}
        onChange={handleChange}
        className={`input ${className ?? ""}`}
        data-deliberate-input="true"
        data-lpignore="true"
        data-1p-ignore="true"
        data-bwignore="true"
        data-form-type="other"
      />
      {hint}
    </div>
  );
}
