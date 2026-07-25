"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import {
  StatusSemaphore,
  TASK_PRIORITY_SEMAPHORE,
  TASK_STATUS_SEMAPHORE,
  TASK_TYPE_LABEL,
} from "@/components/ui/StatusSemaphore";
import { ApiError } from "@/lib/apiClient";
import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import { classifyTaskMutationError } from "@/lib/taskMutationError";
import { isTaskOwnedByAsistente as isOwnedByAsistente } from "@/lib/taskOwnership";
import {
  cancelTask,
  completeTask,
  createTaskComment,
  getTask,
  listTaskComments,
  listTaskHistory,
  reassignTask,
  rescheduleTask,
  startTask,
  updateTask,
} from "@/lib/tasks";
import type {
  OperationalTaskDetail,
  TaskChangeHistoryEntry,
  TaskComment,
  TaskPriority,
} from "@/lib/types/task";

export default function TaskDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { user } = useCurrentUser();
  const taskId = params.id;

  const [task, setTask] = useState<OperationalTaskDetail | null>(null);
  const [history, setHistory] = useState<TaskChangeHistoryEntry[]>([]);
  const [comments, setComments] = useState<TaskComment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [editing, setEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [descriptionDraft, setDescriptionDraft] = useState("");
  const [priorityDraft, setPriorityDraft] = useState<TaskPriority>("NORMAL");

  const [rescheduling, setRescheduling] = useState(false);
  const [dueDateDraft, setDueDateDraft] = useState("");
  const [dueTimeDraft, setDueTimeDraft] = useState("");

  const [commentDraft, setCommentDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [taskData, historyData, commentsData] = await Promise.all([
        getTask(taskId),
        listTaskHistory(taskId),
        listTaskComments(taskId),
      ]);
      setTask(taskData);
      setHistory(historyData.items);
      setComments(commentsData.items);
      setTitleDraft(taskData.title);
      setDescriptionDraft(taskData.description ?? "");
      setPriorityDraft(taskData.priority);
      const due = new Date(taskData.due_at);
      setDueDateDraft(due.toISOString().slice(0, 10));
      setDueTimeDraft(taskData.due_has_time ? due.toTimeString().slice(0, 5) : "");
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo cargar la tarea.");
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  const handleApiError = (err: unknown) => {
    const classified = classifyTaskMutationError(err);
    if (classified.kind === "VERSION_CONFLICT") {
      setNotice(classified.message);
      void reload();
      return;
    }
    if (classified.kind === "NO_CHANGES") {
      setNotice(classified.message);
      return;
    }
    setError(classified.message);
  };

  if (loading) return <p>Cargando…</p>;
  if (!task) return <p style={{ color: "var(--danger)" }}>{error ?? "Tarea no encontrada."}</p>;

  const canEdit = hasPermission(user, "task:update") && isOwnedByAsistente(user, task);
  const canComplete = hasPermission(user, "task:complete") && isOwnedByAsistente(user, task);
  const canAssign = hasPermission(user, "task:assign");
  const canCancel = hasPermission(user, "task:cancel");
  const isTerminal = task.status === "COMPLETED" || task.status === "CANCELLED";
  const ownershipBlocked = user?.role === "asistente" && !isOwnedByAsistente(user, task);

  const submitEdit = async () => {
    if (!task) return;
    setBusy(true);
    try {
      const updated = await updateTask(task.id, {
        expected_version: task.version,
        title: titleDraft,
        description: descriptionDraft || null,
        priority: priorityDraft,
      });
      setTask(updated);
      setEditing(false);
      setNotice("Tarea actualizada.");
      void reload();
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const submitReschedule = async () => {
    if (!task || !dueDateDraft) return;
    setBusy(true);
    try {
      await rescheduleTask(task.id, task.version, {
        due_date: dueDateDraft,
        due_time: dueTimeDraft || null,
      });
      setRescheduling(false);
      setNotice("Vencimiento actualizado.");
      void reload();
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const handleStart = async () => {
    if (!task) return;
    setBusy(true);
    try {
      await startTask(task.id, task.version);
      void reload();
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const handleComplete = async () => {
    if (!task) return;
    setBusy(true);
    try {
      await completeTask(task.id, task.version);
      void reload();
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!task) return;
    const reason = window.prompt("Motivo de la cancelación:");
    if (!reason || !reason.trim()) return;
    if (!window.confirm("¿Confirmas cancelar esta tarea?")) return;
    setBusy(true);
    try {
      await cancelTask(task.id, task.version, reason.trim());
      void reload();
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const handleUnassign = async () => {
    if (!task) return;
    setBusy(true);
    try {
      await reassignTask(task.id, task.version, null);
      void reload();
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const handleAssignToMe = async () => {
    if (!task || !user) return;
    setBusy(true);
    try {
      await reassignTask(task.id, task.version, user.id);
      void reload();
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const submitComment = async () => {
    if (!task || !commentDraft.trim()) return;
    setBusy(true);
    try {
      await createTaskComment(task.id, commentDraft.trim());
      setCommentDraft("");
      void reload();
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "800px" }}>
      <Button type="button" variant="secondary" onClick={() => router.push("/tasks")}>
        ← Volver a tareas
      </Button>

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
      {notice && <p style={{ color: "var(--muted)" }}>{notice}</p>}
      {ownershipBlocked && (
        <p style={{ color: "var(--muted)" }}>
          Esta tarea no está asignada a ti — solo puedes verla, no operarla.
        </p>
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 style={{ fontSize: "1.3rem" }}>{task.title}</h1>
          <p style={{ color: "var(--muted)" }}>{TASK_TYPE_LABEL[task.task_type] ?? task.task_type}</p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <StatusSemaphore status={task.status} map={TASK_STATUS_SEMAPHORE} />
          <StatusSemaphore status={task.priority} map={TASK_PRIORITY_SEMAPHORE} />
        </div>
      </div>

      {task.description && <p>{task.description}</p>}

      <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "0.4rem 1rem", fontSize: "0.9rem" }}>
        <dt style={{ color: "var(--muted)" }}>Vencimiento</dt>
        <dd>{new Date(task.due_at).toLocaleString("es-PE")}</dd>
        {task.is_system_generated && (
          <>
            <dt style={{ color: "var(--muted)" }}>Origen</dt>
            <dd>
              Generada automáticamente
              {task.due_is_overridden && task.source_due_mismatch && (
                <span style={{ color: "var(--danger)" }}> — vencimiento reprogramado difiere de la fuente</span>
              )}
            </dd>
          </>
        )}
        <dt style={{ color: "var(--muted)" }}>Responsable</dt>
        <dd>
          {task.assigned_to ? (task.assignee_is_active ? "Activo" : "Usuario inactivo") : "Sin responsable"}
        </dd>
        {task.status === "CANCELLED" && (
          <>
            <dt style={{ color: "var(--muted)" }}>Motivo de cancelación</dt>
            <dd>{task.cancellation_reason}</dd>
          </>
        )}
      </dl>

      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
        {!isTerminal && task.status === "PENDING" && (
          <Button type="button" onClick={handleStart} disabled={!canEdit || busy} title={ownershipBlocked ? "Tarea no asignada a ti" : undefined}>
            Iniciar
          </Button>
        )}
        {!isTerminal && (
          <Button type="button" onClick={handleComplete} disabled={!canComplete || busy} title={ownershipBlocked ? "Tarea no asignada a ti" : undefined}>
            Completar
          </Button>
        )}
        {!isTerminal && canCancel && (
          <Button type="button" variant="danger" onClick={handleCancel} disabled={busy}>
            Cancelar
          </Button>
        )}
        {!isTerminal && canEdit && (
          <Button type="button" variant="secondary" onClick={() => setEditing((v) => !v)} disabled={busy}>
            {editing ? "Cancelar edición" : "Editar"}
          </Button>
        )}
        {!isTerminal && canEdit && (
          <Button type="button" variant="secondary" onClick={() => setRescheduling((v) => !v)} disabled={busy}>
            {rescheduling ? "Cancelar reprogramación" : "Reprogramar"}
          </Button>
        )}
        {!isTerminal && canAssign && task.assigned_to !== null && (
          <Button type="button" variant="secondary" onClick={handleUnassign} disabled={busy}>
            Quitar responsable
          </Button>
        )}
        {!isTerminal && canAssign && user && task.assigned_to !== user.id && (
          <Button type="button" variant="secondary" onClick={handleAssignToMe} disabled={busy}>
            Asignarme
          </Button>
        )}
      </div>

      {editing && (
        <div style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "1rem" }}>
          <Input label="Título" value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)} />
          <Input
            label="Descripción"
            value={descriptionDraft}
            onChange={(e) => setDescriptionDraft(e.target.value)}
          />
          <Select
            label="Prioridad"
            options={[
              { value: "LOW", label: "Baja" },
              { value: "NORMAL", label: "Normal" },
              { value: "HIGH", label: "Alta" },
              { value: "URGENT", label: "Urgente" },
            ]}
            value={priorityDraft}
            onChange={(e) => setPriorityDraft(e.target.value as TaskPriority)}
          />
          <Button type="button" onClick={submitEdit} disabled={busy} style={{ marginTop: "0.75rem" }}>
            Guardar cambios
          </Button>
        </div>
      )}

      {rescheduling && (
        <div style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "1rem" }}>
          <Input
            label="Fecha de vencimiento"
            type="date"
            value={dueDateDraft}
            onChange={(e) => setDueDateDraft(e.target.value)}
          />
          <Input
            label="Hora (opcional)"
            type="time"
            value={dueTimeDraft}
            onChange={(e) => setDueTimeDraft(e.target.value)}
          />
          <Button type="button" onClick={submitReschedule} disabled={busy} style={{ marginTop: "0.75rem" }}>
            Guardar vencimiento
          </Button>
        </div>
      )}

      <section>
        <h2 style={{ fontSize: "1.1rem" }}>Comentarios</h2>
        {comments.length === 0 ? (
          <p style={{ color: "var(--muted)" }}>Sin comentarios todavía.</p>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            {comments.map((comment) => (
              <li key={comment.id} style={{ border: "1px solid var(--border)", borderRadius: "6px", padding: "0.5rem 0.75rem" }}>
                <p style={{ fontSize: "0.75rem", color: "var(--muted)" }}>
                  {new Date(comment.created_at).toLocaleString("es-PE")}
                </p>
                <p>{comment.body}</p>
              </li>
            ))}
          </ul>
        )}
        {!isTerminal && canEdit && (
          <div style={{ marginTop: "0.75rem" }}>
            <Input
              label="Nuevo comentario"
              value={commentDraft}
              maxLength={4000}
              onChange={(e) => setCommentDraft(e.target.value)}
            />
            <Button type="button" onClick={submitComment} disabled={busy || !commentDraft.trim()}>
              Comentar
            </Button>
          </div>
        )}
      </section>

      <section>
        <h2 style={{ fontSize: "1.1rem" }}>Historial</h2>
        {history.length === 0 ? (
          <p style={{ color: "var(--muted)" }}>Sin historial.</p>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, fontSize: "0.85rem" }}>
            {history.map((entry) => (
              <li key={entry.id} style={{ padding: "0.35rem 0", borderBottom: "1px solid var(--border)" }}>
                <span style={{ color: "var(--muted)" }}>
                  {new Date(entry.created_at).toLocaleString("es-PE")} —{" "}
                </span>
                {entry.event_type} ({entry.origin === "SYSTEM" ? "automático" : "manual"})
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
