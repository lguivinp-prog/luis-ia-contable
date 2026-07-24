export type Role = "admin" | "contador" | "asistente";

// Coincide con `ManagedUserOut` del backend -- deliberadamente sin
// `permissions` calculado (ver schemas/user.py): no hay necesidad funcional
// de mostrar los permisos efectivos de cada fila en esta pantalla.
export interface ManagedUser {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
  deactivated_at: string | null;
  last_login_at: string | null;
  created_at: string;
}
