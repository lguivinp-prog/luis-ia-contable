"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  StatusSemaphore,
  TASK_PRIORITY_SEMAPHORE,
  TASK_TYPE_LABEL,
} from "@/components/ui/StatusSemaphore";
import { ApiError } from "@/lib/apiClient";
import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import { getAgendaSummary, listTasks, syncTasks } from "@/lib/tasks";
import type { AgendaSummary, OperationalTask, TimeBucket } from "@/lib/types/task";

const BUCKETS: { key: TimeBucket; label: string }[] = [
  { key: "OVERDUE", label: "Vencidas" },
  { key: "TODAY", label: "Vencen hoy" },
  { key: "NEXT_7_DAYS", label: "Próximos 7 días" },
];

export default function AgendaPage() {
  const { user } = useCurrentUser();
  const [summary, setSummary] = useState<AgendaSummary | null>(null);
  const [tasksByBucket, setTasksByBucket] = useState<Record<TimeBucket, OperationalTask[]>>({
    OVERDUE: [],
    TODAY: [],
    NEXT_7_DAYS: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [summaryData, overdue, today, next7] = await Promise.all([
        getAgendaSummary(),
        listTasks({ time_bucket: "OVERDUE", limit: 10 }),
        listTasks({ time_bucket: "TODAY", limit: 10 }),
        listTasks({ time_bucket: "NEXT_7_DAYS", limit: 10 }),
      ]);
      setSummary(summaryData);
      setTasksByBucket({ OVERDUE: overdue.items, TODAY: today.items, NEXT_7_DAYS: next7.items });
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo cargar la agenda.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!hasPermission(user, "task:sync")) {
      void Promise.resolve().then(() => reload());
      return;
    }
    void Promise.resolve().then(() =>
      syncTasks("AGENDA_OPEN")
        .catch(() => {
          // La vista igual se muestra con lo ya existente si la sincronizacion falla.
        })
        .finally(() => reload()),
    );
  }, [reload, user]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Agenda</h1>
      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
      {loading || !summary ? (
        <p>Cargando…</p>
      ) : (
        <>
          <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
            <SummaryCard label="Vencidas" value={summary.overdue_tasks} href="/tasks?time_bucket=OVERDUE" />
            <SummaryCard label="Vencen hoy" value={summary.due_today} href="/tasks?time_bucket=TODAY" />
            <SummaryCard
              label="Próximos 7 días"
              value={summary.due_next_7_days}
              href="/tasks?time_bucket=NEXT_7_DAYS"
            />
            <SummaryCard label="Urgentes" value={summary.urgent_tasks} href="/tasks?priority=URGENT" />
            <SummaryCard
              label="Sin responsable"
              value={summary.unassigned_tasks}
              href="/tasks?assigned_to=unassigned"
            />
          </div>

          {BUCKETS.map((bucket) => (
            <section key={bucket.key}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <h2 style={{ fontSize: "1.1rem" }}>{bucket.label}</h2>
                <Link href={`/tasks?time_bucket=${bucket.key}`}>Ver todas</Link>
              </div>
              {tasksByBucket[bucket.key].length === 0 ? (
                <p style={{ color: "var(--muted)" }}>Sin tareas en este balde.</p>
              ) : (
                <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                  {tasksByBucket[bucket.key].map((task) => (
                    <li
                      key={task.id}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        border: "1px solid var(--border)",
                        borderRadius: "6px",
                        padding: "0.5rem 0.75rem",
                      }}
                    >
                      <Link href={`/tasks/${task.id}`}>{task.title}</Link>
                      <span style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                        <span style={{ fontSize: "0.75rem", color: "var(--muted)" }}>
                          {TASK_TYPE_LABEL[task.task_type] ?? task.task_type}
                        </span>
                        <StatusSemaphore status={task.priority} map={TASK_PRIORITY_SEMAPHORE} />
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}
        </>
      )}
    </div>
  );
}

function SummaryCard({ label, value, href }: { label: string; value: number; href: string }) {
  return (
    <Link
      href={href}
      style={{
        display: "block",
        border: "1px solid var(--border)",
        borderRadius: "8px",
        padding: "0.75rem 1rem",
        minWidth: "120px",
      }}
    >
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }}>{value}</div>
      <div style={{ fontSize: "0.8rem", color: "var(--muted)" }}>{label}</div>
    </Link>
  );
}
