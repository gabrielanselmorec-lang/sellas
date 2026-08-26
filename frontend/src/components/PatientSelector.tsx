import type { Patient } from "../types/api";

export function PatientSelector({
  patients,
  value,
  onChange
}: {
  patients: Patient[];
  value: string;
  onChange: (patientId: string) => void;
}) {
  return (
    <label>
      Paciente
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {patients.map((patient) => <option key={patient.patient_id}>{patient.patient_id}</option>)}
      </select>
    </label>
  );
}
