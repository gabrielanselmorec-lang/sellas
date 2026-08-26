import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export function FeatureImportanceChart({ factors }: { factors: Array<{ factor: string; value: string | number }> }) {
  const data = factors.map((item, index) => ({
    factor: item.factor,
    weight: typeof item.value === "number" ? item.value : factors.length - index
  }));

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} layout="vertical" margin={{ left: 80 }}>
        <CartesianGrid strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" tick={{ fontSize: 12 }} />
        <YAxis type="category" dataKey="factor" tick={{ fontSize: 12 }} width={120} />
        <Tooltip />
        <Bar dataKey="weight" fill="#0f766e" radius={[0, 3, 3, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
