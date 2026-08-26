import type {
  ABCAssociationsResponse,
  Appointment,
  Behavior,
  BehaviorRecord,
  NoteExtraction,
  NoteFeaturesResponse,
  Patient,
  Prediction,
  TrainResult
} from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8020";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
    ...options
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail ?? `Erro HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  syncMock: () => request<{ ok: boolean; records: number; use_mock: boolean }>("/api/sync/bhave", {
    method: "POST",
    body: JSON.stringify({ use_mock: true })
  }),
  patients: () => request<Patient[]>("/api/patients"),
  behaviors: (patientId: string) => request<Behavior[]>(`/api/patients/${patientId}/behaviors`),
  appointments: (patientId: string) => request<Appointment[]>(`/api/patients/${patientId}/appointments`),
  appointmentNotes: (appointmentId: string) => request<Appointment>(`/api/appointments/${appointmentId}/notes`),
  extractAppointmentNotes: (appointmentId: string) =>
    request<NoteExtraction>(`/api/appointments/${appointmentId}/notes/extract`, { method: "POST" }),
  confirmNoteExtraction: (appointmentId: string, extraction: NoteExtraction) =>
    request<NoteExtraction>(`/api/notes/extractions/${appointmentId}/confirm`, {
      method: "POST",
      body: JSON.stringify({
        human_confirmed: true,
        human_corrected: false,
        corrected_extraction: extraction
      })
    }),
  batchExtractNotes: (patientId?: string) =>
    request<{ ok: boolean; extractions: number }>(
      `/api/notes/batch-extract${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ""}`,
      { method: "POST" }
    ),
  noteFeatures: (patientId: string, behaviorName: string) =>
    request<NoteFeaturesResponse>("/api/ml/features/from-notes", {
      method: "POST",
      body: JSON.stringify({ patient_id: patientId, behavior_name: behaviorName })
    }),
  history: (patientId: string, behaviorName: string) =>
    request<BehaviorRecord[]>(`/api/patients/${patientId}/history?behavior_name=${encodeURIComponent(behaviorName)}`),
  train: (patientId: string, behaviorName: string) =>
    request<TrainResult>("/api/ml/train", {
      method: "POST",
      body: JSON.stringify({ patient_id: patientId, behavior_name: behaviorName })
    }),
  predict: (patientId: string, behaviorName: string) =>
    request<Prediction>("/api/ml/predict", {
      method: "POST",
      body: JSON.stringify({ patient_id: patientId, behavior_name: behaviorName })
    }),
  predictionHistory: () => request<Prediction[]>("/api/predictions/history")
  ,
  abcAssociations: (patientToken?: string) =>
    request<ABCAssociationsResponse>(
      `/api/abc/analysis/associations${patientToken ? `?patient_token=${encodeURIComponent(patientToken)}&minimum_valid_intervals=1` : "?minimum_valid_intervals=1"}`
    )
};
