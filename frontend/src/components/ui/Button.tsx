import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

export function Button({ variant = "primary", className, ...props }: ButtonProps) {
  const variantClass = variant === "primary" ? "btn-primary" : variant === "danger" ? "btn-danger" : "btn-secondary";
  return <button className={`btn ${variantClass} ${className ?? ""}`} {...props} />;
}
