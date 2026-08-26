import type { Prediction } from "../types/api";

const tone = {
  baixo: "riskLow",
  moderado: "riskModerate",
  alto: "riskHigh"
};

export function RiskBadge({ risk }: { risk: Prediction["risk"] }) {
  return <span className={`riskBadge ${tone[risk]}`}>{risk}</span>;
}
