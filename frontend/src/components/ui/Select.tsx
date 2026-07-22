import type { SelectHTMLAttributes } from "react";

interface Option {
  value: string;
  label: string;
}

interface SelectFieldProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  options: Option[];
  placeholder?: string;
}

export function Select({ label, options, placeholder, id, className, ...props }: SelectFieldProps) {
  const selectId = id ?? props.name;
  return (
    <div className="field">
      <label htmlFor={selectId}>{label}</label>
      <select id={selectId} className={`select ${className ?? ""}`} {...props}>
        {placeholder && <option value="">{placeholder}</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}
