// Pure hand-rolled SVG line chart - no charting library needed. Draws a
// rolling client-side history of a numeric series (e.g. equity, captured
// tick-by-tick as the dashboard runs). Renders empty until enough points
// exist to trace a line.

interface Props {
  series: number[];
  width?: number;
  height?: number;
  color?: string;
}

export function Sparkline({ series, width = 320, height = 56, color = "#8fe3b4" }: Props) {
  if (series.length < 2) {
    return <div style={{ height: `${height}px` }} />;
  }

  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  const stepX = width / (series.length - 1);
  const points = series
    .map((v, i) => `${(i * stepX).toFixed(1)},${(height - ((v - min) / range) * height).toFixed(1)}`)
    .join(" ");
  const up = series[series.length - 1] >= series[0];

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <polyline
        points={points}
        fill="none"
        stroke={up ? color : "#ffb2cb"}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
