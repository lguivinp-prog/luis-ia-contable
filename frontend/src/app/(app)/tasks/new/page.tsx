"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { ApiError, apiClient } from "@/lib/apiClient";
import { buildTaskCreatePayload } from "@/lib/taskCreatePayload";
import { createTask } from "@/lib/tasks";
import type { ClientListResponse } from "@/lib/types/client";
import { MANUAL_TASK_TYPES } from "@/lib/types/task";
import type { TaskPriority, TaskType } from "@/lib/types/task";

const PRIORITY_OPTIONS = [
  { value: "LOW", label: "Baja" },
  { value: "NORMAL", label: "Normal" },
  { value: "HIGH", label: "Alta" },
  { value: "URGENT", label: "Urgente" },
];

export default function NewTaskPage() {
  const router = useRouter();

  const [clients, setClients] = useState<ClientListResponse["items"]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [taskType, setTaskType] = useState<TaskType>("GENERAL");
  const [priority, setPriority] = useState<TaskPriority>("NORMAL");
  const [dueDate, setDueDate] = useState("");
  const [dueTime, setDueTime] = useState("");
  const [clientId, setClientId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void apiClient
      .get<ClientListResponse>("/clients?limit=200")
      .then((data) => setClients(data.items))
      .catch(() => setClients([]));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !dueDate) return;
    setSubmitting(true);
    setError(null);
    try {
      const task = await createTask(
        buildTaskCreatePayload({
          title,
          description,
          taskType,
          priority,
          dueDate,
          dueTime,
          clientId,
        }),
      );
      router.push(`/tasks/${task.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo crear la tarea.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ maxWidth: "560px" }}>
      <h1 style={{ fontSize: "1.4rem", marginBottom: "1rem" }}>Nueva tarea</h1>
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
        <Input label="Título" value={title} onChange={(e) => setTitle(e.target.value)} required />
        <Input
          label="Descripción (opcional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <Select
          label="Tipo"
          options={MANUAL_TASK_TYPES}
          value={taskType}
          onChange={(e) => setTaskType(e.target.value as TaskType)}
        />
        <Select
          label="Prioridad"
          options={PRIORITY_OPTIONS}
          value={priority}
          onChange={(e) => setPriority(e.target.value as TaskPriority)}
        />
        <Input
          label="Fecha de vencimiento"
          type="date"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
          required
        />
        <Input
          label="Hora (opcional)"
          type="time"
          value={dueTime}
          onChange={(e) => setDueTime(e.target.value)}
        />
        <Select
          label="Cliente"
          placeholder="Sin cliente"
          options={clients.map((c) => ({ value: c.id, label: `${c.business_name} — ${c.ruc}` }))}
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
        />

        {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

        <div style={{ display: "flex", gap: "0.5rem" }}>
          <Button type="submit" disabled={submitting}>
            Crear tarea
          </Button>
          <Button type="button" variant="secondary" onClick={() => router.push("/tasks")}>
            Cancelar
          </Button>
        </div>
      </form>
    </div>
  );
}
