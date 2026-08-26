import type { Prediction } from "../types/api";
import { RiskBadge } from "./RiskBadge";

export function PredictionCard({ prediction }: { prediction: Prediction | null }) {
  if (!prediction) return <p className="muted">Sincronize, selecione um comportamento e gere uma previsao.</p>;
  return (
    <>
      <div className="probability">{Math.round(prediction.probability * 100)}%</div>
      <RiskBadge risk={prediction.risk} />
      <p className="muted">Modelo: {prediction.model} | Janela: {prediction.prediction_window}</p>
    </>
  );
}
