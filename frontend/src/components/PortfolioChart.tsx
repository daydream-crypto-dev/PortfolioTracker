import { useEffect, useRef } from "react";
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import type { EChartsOption } from "echarts";
import { CanvasRenderer } from "echarts/renderers";

import type { ChartPoint } from "../types";

// Register only the ECharts pieces this chart uses. This keeps the bundle
// smaller than importing the entire ECharts package.
echarts.use([GridComponent, LineChart, TooltipComponent, CanvasRenderer]);

interface PortfolioChartProps {
  points: ChartPoint[];
}

const POSITIVE_COLOR = "#16c784";
const NEGATIVE_COLOR = "#ef4444";

interface ChartScale {
  min: number;
  max: number;
  interval: number;
  labelInterval: number;
}

function formatCompactCurrency(value: number): string {
  const sign = value < 0 ? "-" : "";
  const absoluteValue = Math.abs(value);

  if (absoluteValue >= 1_000_000_000) {
    return `${sign}$${Math.floor(absoluteValue / 1_000_000_000)}B`;
  }

  if (absoluteValue >= 1_000_000) {
    return `${sign}$${Math.floor(absoluteValue / 1_000_000)}M`;
  }

  if (absoluteValue >= 100_000) {
    return `${sign}$${Math.floor(absoluteValue / 1_000)}K`;
  }

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0
  }).format(value);
}

function formatExactCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }).format(value);
}

function getNiceInterval(target: number, baseUnit: number): number {
  const normalizedTarget = Math.max(target / baseUnit, 1);
  const power = 10 ** Math.floor(Math.log10(normalizedTarget));
  const fraction = normalizedTarget / power;
  const niceFraction = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;

  return niceFraction * power * baseUnit;
}

function getChartScale(points: ChartPoint[]): ChartScale {
  if (points.length === 0) {
    return { min: 0, max: 1, interval: 0.25, labelInterval: 0.25 };
  }

  const values = points.map((point) => point.totalUsd);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const range = Math.max(high - low, 1);
  const axisMagnitude = Math.max(Math.abs(low), Math.abs(high));

  if (axisMagnitude >= 100_000) {
    const labelInterval = getNiceInterval(range / 4, 1_000);
    let min = Math.floor(low / labelInterval) * labelInterval;
    let max = Math.ceil(high / labelInterval) * labelInterval;

    if (min >= low) {
      min -= labelInterval;
    }
    if (max <= high) {
      max += labelInterval;
    }

    return {
      min,
      max,
      interval: labelInterval === 1_000 && max - min <= 4_000 ? 500 : labelInterval,
      labelInterval
    };
  }

  const spread = Math.max(range, high * 0.01, 1);
  const padding = spread * 0.16;
  const min = Math.max(0, Math.floor(low - padding));
  const max = Math.ceil(high + padding);

  return {
    min,
    max,
    interval: (max - min) / 4,
    labelInterval: (max - min) / 4
  };
}

function shouldShowAxisLabel(value: number, scale: ChartScale): boolean {
  if (scale.labelInterval <= 0) {
    return true;
  }

  const multiple = (value - scale.min) / scale.labelInterval;
  return Math.abs(multiple - Math.round(multiple)) < 0.000001;
}

function formatTimeAxis(value: string | number, spanMs: number): string {
  const date = new Date(value);

  if (spanMs <= 36 * 60 * 60 * 1000) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  if (spanMs <= 100 * 24 * 60 * 60 * 1000) {
    return date.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  return date.toLocaleDateString([], { month: "short", year: "2-digit" });
}

export function PortfolioChart({ points }: PortfolioChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    // ECharts owns its canvas once initialized; React just passes new options
    // whenever the filtered point list changes.
    const chart = echarts.init(containerRef.current);
    const firstValue = points[0]?.totalUsd ?? 0;
    const lastValue = points[points.length - 1]?.totalUsd ?? firstValue;
    const isPositive = lastValue >= firstValue;
    const lineColor = isPositive ? POSITIVE_COLOR : NEGATIVE_COLOR;
    const scale = getChartScale(points);
    const firstTime = points[0] ? new Date(points[0].timestamp).getTime() : Date.now();
    const lastTime = points[points.length - 1]
      ? new Date(points[points.length - 1].timestamp).getTime()
      : firstTime;
    const spanMs = Math.max(lastTime - firstTime, 0);

    const option: EChartsOption = {
      animationDuration: 450,
      color: [lineColor],
      grid: {
        bottom: 32,
        left: 16,
        right: 72,
        top: 24
      },
      tooltip: {
        trigger: "axis",
        borderColor: "#e2e8f0",
        padding: 10,
        valueFormatter: (value) =>
          typeof value === "number" ? formatExactCurrency(value) : String(value)
      },
      xAxis: {
        type: "time",
        boundaryGap: [0, 0],
        axisTick: { show: false },
        axisLine: { lineStyle: { color: "#cbd5e1" } },
        axisLabel: {
          color: "#94a3b8",
          formatter: (value: string | number) => formatTimeAxis(value, spanMs)
        }
      },
      yAxis: {
        type: "value",
        position: "right",
        min: scale.min,
        max: scale.max,
        interval: scale.interval,
        axisLabel: {
          color: "#64748b",
          formatter: (value: number) => (shouldShowAxisLabel(value, scale) ? formatCompactCurrency(value) : "")
        },
        splitLine: { lineStyle: { color: "#e2e8f0" } },
        splitNumber: 4
      },
      series: [
        {
          name: "Portfolio value",
          type: "line",
          smooth: true,
          showSymbol: false,
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                {
                  offset: 0,
                  color: isPositive ? "rgba(22, 199, 132, 0.22)" : "rgba(239, 68, 68, 0.2)"
                },
                {
                  offset: 1,
                  color: isPositive ? "rgba(22, 199, 132, 0.02)" : "rgba(239, 68, 68, 0.02)"
                }
              ]
            }
          },
          lineStyle: { width: 3 },
          data: points.map((point) => [point.timestamp, point.totalUsd])
        }
      ]
    };

    chart.setOption(option);

    const resize = () => chart.resize();
    window.addEventListener("resize", resize);

    return () => {
      window.removeEventListener("resize", resize);
      // Dispose on unmount/update to avoid leaking canvas event handlers.
      chart.dispose();
    };
  }, [points]);

  return <div ref={containerRef} className="h-[320px] w-full" />;
}
