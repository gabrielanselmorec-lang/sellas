import type { Prediction } from "../types/api";
import { RiskBadge } from "./RiskBadge";

export function PredictionHistoryTable({ history }: { history: Prediction[] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Paciente</th>
          <th>Comportamento</th>
          <th>Probabilidade</th>
          <th>Risco</th>
          <th>Modelo</th>
        </tr>
      </thead>
      <tbody>
        {history.slice(-8).reverse().map((item, index) => (
          <tr key={`${item.created_at}-${index}`}>
            <td>{item.patient_id}</td>
            <td>{item.behavior_name}</td>
            <td>{Math.round(item.probability * 100)}%</td>
            <td><RiskBadge risk={item.risk} /></td>
            <td>{item.model}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
