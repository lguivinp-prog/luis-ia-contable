import type { InputHTMLAttributes, ReactNode } from "react";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: ReactNode;
}

export function Input({ label, hint, id, className, ...props }: FieldProps) {
  const inputId = id ?? props.name;
  return (
    <div className="field">
      <label htmlFor={inputId}>{label}</label>
      <input id={inputId} className={`input ${className ?? ""}`} {...props} />
      {hint}
    </div>
  );
}
