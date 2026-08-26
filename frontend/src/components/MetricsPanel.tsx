import type { TrainResult } from "../types/api";

export function MetricsPanel({ result }: { result: TrainResult | null }) {
  if (!result) return <p className="muted">As metricas aparecem depois do treinamento.</p>;
  return (
    <div className="metricsGrid">
      <div className="metric"><span>Modelo</span><strong>{result.selected_model}</strong></div>
      <div className="metric"><span>Amostras</span><strong>{result.samples}</strong></div>
      <div className="metric"><span>Taxa historica</span><strong>{Math.round(result.event_rate * 100)}%</strong></div>
    </div>
  );
}
