"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Pagination } from "@/components/ui/Pagination";
import { Select } from "@/components/ui/Select";
import {
  StatusSemaphore,
  TASK_PRIORITY_SEMAPHORE,
  TASK_STATUS_SEMAPHORE,
  TASK_TYPE_LABEL,
} from "@/components/ui/StatusSemaphore";
import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import { listTasks, syncTasks } from "@/lib/tasks";
import type { OperationalTask } from "@/lib/types/task";
import { ApiError } from "@/lib/apiClient";

const LIMIT = 20;

const STATUS_OPTIONS = [
  { value: "PENDING", label: "Pendiente" },
  { value: "IN_PROGRESS", label: "En proceso" },
  { value: "COMPLETED", label: "Completada" },
  { value: "CANCELLED", label: "Cancelada" },
];

const PRIORITY_OPTIONS = [
  { value: "LOW", label: "Baja" },
  { value: "NORMAL", label: "Normal" },
  { value: "HIGH", label: "Alta" },
  { value: "URGENT", label: "Urgente" },
];

const TIME_BUCKET_OPTIONS = [
  { value: "OVERDUE", label: "Vencidas" },
  { value: "TODAY", label: "Vencen hoy" },
  { value: "NEXT_7_DAYS", label: "Próximos 7 días" },
];

export default function TasksPage() {
  return (
    <Suspense fallback={<p>Cargando…</p>}>
      <TasksPageContent />
    </Suspense>
  );
}

function TasksPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user } = useCurrentUser();

  const status = searchParams.get("status") ?? "";
  const priority = searchParams.get("priority") ?? "";
  const timeBucket = searchParams.get("time_bucket") ?? "";
  const assignedTo = searchParams.get("assigned_to") ?? "";
  const offset = Number(searchParams.get("offset") ?? "0");

  const [tasks, setTasks] = useState<OperationalTask[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  const updateParam = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(searchParams.toString());
      if (value) next.set(key, value);
      else next.delete(key);
      next.delete("offset");
      router.push(`/tasks?${next.toString()}`);
    },
    [router, searchParams],
  );

  const setOffset = useCallback(
    (newOffset: number) => {
      const next = new URLSearchParams(searchParams.toString());
      next.set("offset", String(newOffset));
      router.push(`/tasks?${next.toString()}`);
    },
    [router, searchParams],
  );

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listTasks({
        status: status || undefined,
        priority: priority || undefined,
        time_bucket: timeBucket || undefined,
        assigned_to: assignedTo || undefined,
        limit: LIMIT,
        offset,
      });
      setTasks(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo cargar la lista de tareas.");
    } finally {
      setLoading(false);
    }
  }, [status, priority, timeBucket, assignedTo, offset]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  const handleSync = async () => {
    setSyncing(true);
    setError(null);
    try {
      await syncTasks("MANUAL");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo sincronizar.");
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ fontSize: "1.4rem" }}>Tareas</h1>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {hasPermission(user, "task:sync") && (
            <Button type="button" variant="secondary" onClick={handleSync} disabled={syncing}>
              {syncing ? "Sincronizando…" : "Sincronizar"}
            </Button>
          )}
          {hasPermission(user, "task:create") && (
            <Link href="/tasks/new">
              <Button type="button">Nueva tarea</Button>
            </Link>
          )}
        </div>
      </div>

      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
        <Select
          label="Estado"
          placeholder="Todos"
          options={STATUS_OPTIONS}
          value={status}
          onChange={(e) => updateParam("status", e.target.value)}
        />
        <Select
          label="Prioridad"
          placeholder="Todas"
          options={PRIORITY_OPTIONS}
          value={priority}
          onChange={(e) => updateParam("priority", e.target.value)}
        />
        <Select
          label="Balde de vencimiento"
          placeholder="Todos"
          options={TIME_BUCKET_OPTIONS}
          value={timeBucket}
          onChange={(e) => updateParam("time_bucket", e.target.value)}
        />
        <Select
          label="Responsable"
          placeholder="Todos"
          options={[
            { value: "me", label: "Asignadas a mí" },
            { value: "unassigned", label: "Sin responsable" },
          ]}
          value={assignedTo}
          onChange={(e) => updateParam("assigned_to", e.target.value)}
        />
      </div>

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      {loading ? (
        <p>Cargando…</p>
      ) : tasks.length === 0 ? (
        <p style={{ color: "var(--muted)" }}>No hay tareas para los filtros seleccionados.</p>
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Título</th>
                  <th>Tipo</th>
                  <th>Prioridad</th>
                  <th>Estado</th>
                  <th>Vencimiento</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={task.id}>
                    <td>{task.title}</td>
                    <td>{TASK_TYPE_LABEL[task.task_type] ?? task.task_type}</td>
                    <td>
                      <StatusSemaphore status={task.priority} map={TASK_PRIORITY_SEMAPHORE} />
                    </td>
                    <td>
                      <StatusSemaphore status={task.status} map={TASK_STATUS_SEMAPHORE} />
                    </td>
                    <td>{new Date(task.due_at).toLocaleString("es-PE")}</td>
                    <td>
                      <Link href={`/tasks/${task.id}`}>Ver detalle</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />
        </>
      )}
    </div>
  );
}
