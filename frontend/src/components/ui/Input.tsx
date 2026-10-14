import { useId, type InputHTMLAttributes, type ReactNode } from "react";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: ReactNode;
}

export function Input({ label, hint, id, className, ...props }: FieldProps) {
  const generatedId = useId();
  const inputId = id ?? props.name ?? generatedId; // la etiqueta SIEMPRE queda asociada al campo (accesibilidad)
  return (
    <div className="field">
      <label htmlFor={inputId}>{label}</label>
      <input id={inputId} className={`input ${className ?? ""}`} {...props} />
      {hint}
    </div>
  );
}
