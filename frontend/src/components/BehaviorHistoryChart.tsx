import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { BehaviorRecord } from "../types/api";

export function BehaviorHistoryChart({ records }: { records: BehaviorRecord[] }) {
  const data = records.slice(-24).map((record) => ({
    date: record.date.slice(5),
    frequency: record.frequency,
    intensity: record.intensity
  }));

  return (
    <div className="chartShell">
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="date" tick={{ fontSize: 12 }} />
          <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
          <Tooltip />
          <Bar dataKey="frequency" fill="#2563eb" radius={[3, 3, 0, 0]} />
          <Bar dataKey="intensity" fill="#f59e0b" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
