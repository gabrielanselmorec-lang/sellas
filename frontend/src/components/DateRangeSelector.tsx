export function DateRangeSelector({
  startDate,
  endDate,
  onChange
}: {
  startDate: string;
  endDate: string;
  onChange: (value: { startDate: string; endDate: string }) => void;
}) {
  return (
    <div className="dateRange">
      <label>
        Inicio
        <input type="date" value={startDate} onChange={(event) => onChange({ startDate: event.target.value, endDate })} />
      </label>
      <label>
        Fim
        <input type="date" value={endDate} onChange={(event) => onChange({ startDate, endDate: event.target.value })} />
      </label>
    </div>
  );
}
