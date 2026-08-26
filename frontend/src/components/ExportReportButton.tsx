import { FileDown } from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8020";

export function ExportReportButton() {
  return (
    <button onClick={() => window.open(`${API_BASE}/api/reports/export`, "_blank", "noopener,noreferrer")}>
      <FileDown size={18} /> Exportar
    </button>
  );
}
