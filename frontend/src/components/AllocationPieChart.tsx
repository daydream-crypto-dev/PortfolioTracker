import { PieChart } from "echarts/charts";
import { LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import type { EChartsOption } from "echarts";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

echarts.use([PieChart, LegendComponent, TooltipComponent, CanvasRenderer]);

interface AllocationSlice {
  label: string;
  value: number;
}

interface AllocationPieChartProps {
  slices: AllocationSlice[];
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2
  }).format(value);
}

export function AllocationPieChart({ slices }: AllocationPieChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    const chart = echarts.init(containerRef.current);
    const option: EChartsOption = {
      color: ["#2563eb", "#16c784", "#f59e0b", "#8b5cf6", "#ef4444", "#06b6d4"],
      legend: {
        bottom: 0,
        icon: "circle",
        itemGap: 14,
        textStyle: { color: "#64748b" }
      },
      tooltip: {
        trigger: "item",
        borderColor: "#e2e8f0",
        padding: 10,
        valueFormatter: (value) =>
          typeof value === "number" ? formatCurrency(value) : String(value)
      },
      series: [
        {
          name: "Value",
          type: "pie",
          radius: ["48%", "72%"],
          center: ["50%", "44%"],
          avoidLabelOverlap: true,
          label: {
            formatter: "{b}",
            color: "#334155"
          },
          labelLine: { length: 10, length2: 8 },
          data: slices.map((slice) => ({ name: slice.label, value: Number(slice.value.toFixed(2)) }))
        }
      ]
    };

    chart.setOption(option);

    const resize = () => chart.resize();
    window.addEventListener("resize", resize);

    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [slices]);

  return <div ref={containerRef} className="h-[300px] w-full" />;
}
