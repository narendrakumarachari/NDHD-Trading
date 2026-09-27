import React from "react";
import { html } from "../html.js";

// Pure hand-rolled SVG line chart - no charting library needed. Draws a
// rolling client-side history of a numeric series (e.g. equity, captured
// tick-by-tick as the dashboard runs). Renders empty until enough points
// exist to trace a line.
export function Sparkline({ series, width = 320, height = 56, color = "#8fe3b4" }) {
  if (!series || series.length < 2) {
    return html`<div style=${{ height: `${height}px` }} />`;
  }

  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  const stepX = width / (series.length - 1);

  const points = series
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const last = series[series.length - 1];
  const first = series[0];
  const up = last >= first;

  return html`
    <svg width=${width} height=${height} viewBox=${`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <polyline
        points=${points}
        fill="none"
        stroke=${up ? color : "#ffb2cb"}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  `;
}
