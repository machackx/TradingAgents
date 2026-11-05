import {
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";

import { useDarkMode, useUpDown } from "../updown";

export interface PriceLevel {
  price: number;
  label: string;
  kind: "entry" | "stop" | "target";
}

interface Props {
  records: Record<string, unknown>[];
  currency?: string;
  levels: PriceLevel[];
  marker?: { text: string; tone: "rise" | "fall" | "level" | "warn" };
}

interface Bar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

const RANGES: { label: string; bars: number | null }[] = [
  { label: "3 个月", bars: 63 },
  { label: "6 个月", bars: 126 },
  { label: "1 年", bars: 252 },
  { label: "3 年", bars: 756 },
  { label: "全部", bars: null },
];

function toBars(records: Record<string, unknown>[]): Bar[] {
  return records
    .map((r) => ({
      time: String(r.Date ?? r.date ?? "").slice(0, 10),
      open: Number(r.Open),
      high: Number(r.High),
      low: Number(r.Low),
      close: Number(r.Close),
      volume: Number(r.Volume ?? 0),
    }))
    .filter((b) => /^\d{4}-\d{2}-\d{2}$/.test(b.time) && [b.open, b.high, b.low, b.close].every(Number.isFinite));
}

function sma(bars: Bar[], n: number): (number | null)[] {
  let sum = 0;
  return bars.map((b, i) => {
    sum += b.close;
    if (i >= n) sum -= bars[i - n].close;
    return i >= n - 1 ? sum / n : null;
  });
}

function colors() {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    panel: v("--panel"),
    ink: v("--ink"),
    muted: v("--muted"),
    rule: v("--rule"),
    rise: v("--rise"),
    fall: v("--fall"),
    series1: v("--series-1"),
    series2: v("--series-2"),
    warn: v("--warn"),
    danger: v("--danger"),
    accent: v("--accent"),
  };
}

function withAlpha(hex: string, alpha: number): string {
  const m = hex.match(/^#([0-9a-f]{6})$/i);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export function fmtVolume(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (n >= 1e8) return `${(n / 1e8).toFixed(2)} 亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)} 万`;
  return String(Math.round(n));
}

const fmtPrice = (n: number | null | undefined) => (n == null || !Number.isFinite(n) ? "—" : n.toFixed(2));

export function PriceChart({ records, currency, levels, marker }: Props) {
  const bars = useMemo(() => toBars(records), [records]);
  const averages = useMemo(() => ({ ma50: sma(bars, 50), ma200: sma(bars, 200) }), [bars]);
  const index = useMemo(() => new Map(bars.map((b, i) => [b.time, i])), [bars]);
  const indexRef = useRef(index);
  indexRef.current = index;
  const updown = useUpDown();
  const dark = useDarkMode();

  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<{
    candles: ISeriesApi<"Candlestick">;
    volume: ISeriesApi<"Histogram">;
    ma50: ISeriesApi<"Line">;
    ma200: ISeriesApi<"Line">;
  } | null>(null);
  const lines = useRef<IPriceLine[]>([]);
  const [hover, setHover] = useState<number | null>(null);
  const [range, setRange] = useState(1);

  // The chart is made once; data, colours and levels are applied below.
  useEffect(() => {
    if (!box.current) return;
    const made = createChart(box.current, {
      autoSize: true,
      crosshair: { mode: CrosshairMode.Normal },
      grid: { vertLines: { visible: false } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.26 } },
      timeScale: { borderVisible: false, rightOffset: 6 },
      localization: { locale: "zh-CN", dateFormat: "yyyy-MM-dd" },
    });
    const candles = made.addCandlestickSeries({ priceLineVisible: false });
    const volume = made.addHistogramSeries({
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    // Volume sits in its own band at the foot, with no axis of its own: one
    // y-axis on the chart, the volume figure in the readout.
    made.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 }, visible: false });
    const line = { lineWidth: 2 as const, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    const ma50 = made.addLineSeries(line);
    const ma200 = made.addLineSeries(line);
    made.subscribeCrosshairMove((param) => {
      const t = param.time as string | undefined;
      setHover(t && indexRef.current.has(t) ? indexRef.current.get(t)! : null);
    });
    chart.current = made;
    series.current = { candles, volume, ma50, ma200 };
    return () => {
      made.remove();
      chart.current = null;
      series.current = null;
      lines.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Data, and the colours that follow the theme and the up/down convention.
  useEffect(() => {
    const s = series.current;
    if (!s || !chart.current) return;
    const c = colors();
    chart.current.applyOptions({
      layout: { background: { color: c.panel }, textColor: c.muted, fontSize: 11 },
      grid: { horzLines: { color: c.rule } },
      crosshair: { vertLine: { color: c.muted }, horzLine: { color: c.muted } },
    });
    s.candles.applyOptions({
      upColor: "rgba(0, 0, 0, 0)", // hollow when up, filled when down: direction without relying on hue
      borderUpColor: c.rise,
      wickUpColor: c.rise,
      downColor: c.fall,
      borderDownColor: c.fall,
      wickDownColor: c.fall,
    });
    s.ma50.applyOptions({ color: c.series1 });
    s.ma200.applyOptions({ color: c.series2 });
    s.candles.setData(bars.map(({ time, open, high, low, close }) => ({ time: time as Time, open, high, low, close })));
    s.volume.setData(
      bars.map((b) => ({ time: b.time as Time, value: b.volume, color: withAlpha(b.close >= b.open ? c.rise : c.fall, 0.35) })),
    );
    const line = (values: (number | null)[]) =>
      bars.flatMap((b, i) => (values[i] == null ? [] : [{ time: b.time as Time, value: values[i]! }]));
    s.ma50.setData(line(averages.ma50));
    s.ma200.setData(line(averages.ma200));
  }, [bars, averages, updown, dark]);

  // Levels and the decision marker; keyed by value, as the parent makes new objects each render.
  const levelKey = JSON.stringify(levels);
  const markerKey = marker ? `${marker.text}|${marker.tone}` : "";
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    const c = colors();
    for (const l of lines.current) s.candles.removePriceLine(l);
    const tone = { entry: c.ink, stop: c.warn, target: c.accent };
    lines.current = levels.map((level) =>
      s.candles.createPriceLine({
        price: level.price,
        color: tone[level.kind],
        lineWidth: 1,
        lineStyle: LineStyle.Dashed, // a level, not data
        axisLabelVisible: true,
        title: level.label,
      }),
    );
    const last = bars[bars.length - 1];
    const markerColor = marker ? { rise: c.rise, fall: c.fall, level: c.muted, warn: c.warn }[marker.tone] : c.ink;
    s.candles.setMarkers(
      last && marker
        ? [{ time: last.time as Time, position: "aboveBar", shape: "arrowDown", color: markerColor, text: marker.text }]
        : [],
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars, levelKey, markerKey, updown, dark]);

  // The range presets count trading days back from the latest bar.
  useEffect(() => {
    const ts = chart.current?.timeScale();
    if (!ts || !bars.length) return;
    const want = RANGES[range].bars;
    if (want == null) ts.fitContent();
    else ts.setVisibleLogicalRange({ from: Math.max(0, bars.length - want), to: bars.length + 5 });
  }, [range, bars]);

  if (!bars.length) return <p className="muted">没有可画的日线数据。</p>;

  const at = hover ?? bars.length - 1;
  const bar = bars[at];
  const previous = bars[at - 1];
  const change = previous ? (bar.close / previous.close - 1) * 100 : null;
  const direction = change == null ? "" : change >= 0 ? "rise" : "fall";

  return (
    <div className="price-chart">
      <div className="chart-toolbar">
        <div className="segmented" role="group" aria-label="时间范围">
          {RANGES.map((r, i) => (
            <button key={r.label} className={i === range ? "on" : ""} onClick={() => setRange(i)} aria-pressed={i === range}>
              {r.label}
            </button>
          ))}
        </div>
        <ul className="chart-legend small">
          <li>
            <span className="key key-candle" aria-hidden /> 日 K（空心阳线 · 实心阴线）
          </li>
          <li>
            <span className="key key-line" style={{ background: "var(--series-1)" }} aria-hidden /> 50 日均线
          </li>
          <li>
            <span className="key key-line" style={{ background: "var(--series-2)" }} aria-hidden /> 200 日均线
          </li>
        </ul>
      </div>
      <dl className="readout small" aria-live="off">
        <div>
          <dt>{bar.time}</dt>
          <dd className={direction}>
            {fmtPrice(bar.close)} {currency}
            {change != null && ` ${change >= 0 ? "+" : ""}${change.toFixed(2)}%`}
          </dd>
        </div>
        <div>
          <dt>开</dt>
          <dd>{fmtPrice(bar.open)}</dd>
        </div>
        <div>
          <dt>高</dt>
          <dd>{fmtPrice(bar.high)}</dd>
        </div>
        <div>
          <dt>低</dt>
          <dd>{fmtPrice(bar.low)}</dd>
        </div>
        <div>
          <dt>量</dt>
          <dd>{fmtVolume(bar.volume)}</dd>
        </div>
        <div>
          <dt>MA50</dt>
          <dd>{fmtPrice(averages.ma50[at])}</dd>
        </div>
        <div>
          <dt>MA200</dt>
          <dd>{fmtPrice(averages.ma200[at])}</dd>
        </div>
      </dl>
      <div className="chart-box" ref={box} role="img" aria-label={`日 K 线图，${bars[0].time} 至 ${bars[bars.length - 1].time}`} />
      <details className="small">
        <summary>数据表（最近 30 个交易日）</summary>
        <table className="table">
          <thead>
            <tr>
              <th>日期</th>
              <th>开</th>
              <th>高</th>
              <th>低</th>
              <th>收</th>
              <th>量</th>
            </tr>
          </thead>
          <tbody>
            {bars
              .slice(-30)
              .reverse()
              .map((b) => (
                <tr key={b.time}>
                  <td>{b.time}</td>
                  <td>{fmtPrice(b.open)}</td>
                  <td>{fmtPrice(b.high)}</td>
                  <td>{fmtPrice(b.low)}</td>
                  <td>{fmtPrice(b.close)}</td>
                  <td>{fmtVolume(b.volume)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
