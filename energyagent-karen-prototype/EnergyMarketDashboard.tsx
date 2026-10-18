import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type RefObject } from "react"

// Karen's Prijzen tab. Reads market.json published by .github/workflows/market-daily.yml
// on the market-data branch. No npm dependencies so it also works as a Framer code file.

export const DEFAULT_MARKET_DATA_URL =
    "https://raw.githubusercontent.com/pascal-maker/karen-energy-agent/market-data/market.json"

type DayView = { date: string; resolution_minutes: number; points: [string, number][]; min: number; max: number; avg: number; cheapest_hours: number[] }
type Section = { stale?: boolean; updated_at?: string }
type MarketData = {
    generated_at: string
    spot: (Section & { today: DayView; tomorrow: DayView | null }) | null
    history: (Section & { months: Record<string, number>; current_month?: { month: string; avg_so_far: number | null } }) | null
    forecast: (Section & {
        method: string
        band: string
        trained_until: string
        backtest: { origins: number; mape_1m: number; mape_6m: number; mape_12m: number }
        months: { month: string; point: number; low: number; high: number }[]
    }) | null
    contracts: unknown
    sources: { title: string; url: string; licence?: string }[]
}

type Props = { dataUrl?: string; style?: CSSProperties }
type View = "today" | "history" | "forecast" | "contracts"

// Validated with the dataviz palette checker (light surface #fff): all checks pass.
const MEASURED = "#1f8a55"
const ACCENT = "#7b5cd6"
const REFERENCE = "#8a958d"
const GRID = "#e8ece7"

const MONTHS_NL = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
const toCents = (eurPerMwh: number) => eurPerMwh / 10
const fmt = (eurPerMwh: number, digits = 1) =>
    `${toCents(eurPerMwh).toLocaleString("nl-BE", { minimumFractionDigits: digits, maximumFractionDigits: digits })} c€/kWh`
const monthLabel = (month: string) => `${MONTHS_NL[Number(month.slice(5, 7)) - 1]} ${month.slice(0, 4)}`
const dateLabel = (iso: string) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString("nl-BE", { weekday: "long", day: "numeric", month: "long" })

const css = `
  .em { display:flex; flex-direction:column; gap:14px; min-width:0; color:#1e2b23; font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  .em-views { display:flex; gap:6px; overflow-x:auto; scrollbar-width:none; }
  .em-views::-webkit-scrollbar { display:none; }
  .em-view { flex:none; min-height:40px; padding:0 14px; border:1px solid #dfe5de; border-radius:999px; color:#3d4d42; background:#fff; font:inherit; font-size:13px; cursor:pointer; }
  .em-view[aria-selected="true"] { color:#fff; background:#234f38; border-color:#234f38; }
  .em-card { min-width:0; padding:18px; border:1px solid #e2e7df; border-radius:20px; background:#fff; }
  .em-card h2 { margin:0; font-size:16px; letter-spacing:-.01em; }
  .em-sub { margin:4px 0 14px; color:#637168; font-size:13px; line-height:1.5; }
  .em-tiles { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin-bottom:14px; }
  .em-tile { padding:10px 12px; border-radius:14px; background:#f4f7f2; }
  .em-tile span { display:block; color:#637168; font-size:12px; }
  .em-tile strong { display:block; margin-top:2px; font-size:20px; font-weight:650; white-space:nowrap; }
  .em-tile small { color:#637168; font-size:12px; font-weight:500; }
  .em-chart { position:relative; width:100%; }
  .em-chart svg { display:block; width:100%; overflow:visible; touch-action:pan-y; }
  .em-tip { position:absolute; z-index:2; pointer-events:none; min-width:120px; padding:8px 10px; border:1px solid #e2e7df; border-radius:10px; background:#fff; box-shadow:0 6px 18px #1e2b2314; font-size:12px; line-height:1.45; white-space:nowrap; }
  .em-tip strong { display:block; font-size:12px; }
  .em-key { display:inline-block; width:10px; height:10px; margin-right:6px; border-radius:3px; vertical-align:-1px; }
  .em-legend { display:flex; flex-wrap:wrap; gap:6px 14px; margin:10px 0 0; padding:0; list-style:none; color:#4a5a4f; font-size:12px; }
  .em-note { margin:12px 0 0; color:#728076; font-size:12px; line-height:1.5; }
  .em-note a, .em-card a { color:#234f38; }
  .em-badge { display:inline-block; margin-left:8px; padding:2px 8px; border-radius:999px; color:#7a4b00; background:#fff2d6; font-size:11px; font-weight:600; vertical-align:2px; }
  .em-switch { display:inline-flex; gap:4px; margin-bottom:12px; padding:3px; border-radius:12px; background:#f0f3ef; }
  .em-switch button { min-height:34px; padding:0 12px; border:0; border-radius:9px; color:#3d4d42; background:transparent; font:inherit; font-size:13px; cursor:pointer; }
  .em-switch button[aria-pressed="true"] { color:#1e2b23; background:#fff; box-shadow:0 1px 3px #1e2b2320; }
  .em details { margin-top:10px; font-size:12px; }
  .em summary { color:#234f38; cursor:pointer; }
  .em-table { width:100%; margin-top:8px; border-collapse:collapse; font-variant-numeric:tabular-nums; }
  .em-table th, .em-table td { padding:5px 6px; border-bottom:1px solid #edf0eb; text-align:right; }
  .em-table th:first-child, .em-table td:first-child { text-align:left; }
  .em-table-wrap { max-height:240px; overflow:auto; }
  .em-links { display:grid; gap:8px; margin-top:12px; }
  .em-links a { display:block; padding:12px 14px; border:1px solid #e2e7df; border-radius:14px; text-decoration:none; font-size:14px; }
  .em-links small { display:block; margin-top:2px; color:#728076; font-size:12px; }
  @media (max-width:680px) {
    .em-card { padding:16px 14px; border-radius:16px; }
    .em-views { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:4px; }
    .em-view { padding:0 4px; font-size:13px; }
    .em-tiles { gap:6px; }
    .em-tile { padding:10px; }
    .em-tile strong { font-size:18px; }
    .em-tile small { display:block; font-size:11px; }
  }
`

function useWidth<T extends HTMLElement>(): [RefObject<T>, number] {
    const ref = useRef<T>(null)
    const [width, setWidth] = useState(0)
    useEffect(() => {
        const node = ref.current
        if (!node) return
        setWidth(node.clientWidth)
        if (typeof ResizeObserver === "undefined") return
        const observer = new ResizeObserver(entries => setWidth(Math.floor(entries[0].contentRect.width)))
        observer.observe(node)
        return () => observer.disconnect()
    }, [])
    return [ref, width]
}

function niceTicks(min: number, max: number, count = 4): number[] {
    const span = max - min || 1
    const raw = span / count
    const magnitude = 10 ** Math.floor(Math.log10(raw))
    const step = [1, 2, 2.5, 5, 10].map(m => m * magnitude).find(s => s >= raw) ?? raw
    const ticks: number[] = []
    const first = Math.floor(min / step) * step
    for (let t = first; ticks.length < 2 || ticks[ticks.length - 1] < max - step * 0.001; t += step) ticks.push(Math.round(t * 1000) / 1000)
    return ticks
}

type Tip = { x: number; y: number; title: string; rows: { color?: string; label: string; value: string }[] }

function Tooltip({ tip, width }: { tip: Tip | null; width: number }) {
    if (!tip) return null
    const left = Math.min(Math.max(tip.x + 12, 0), Math.max(width - 170, 0))
    return (
        <div className="em-tip" style={{ left, top: Math.max(tip.y - 64, 0) }} role="status">
            <strong>{tip.title}</strong>
            {tip.rows.map(row => <div key={row.label}>{row.color && <span className="em-key" style={{ background: row.color }} />}{row.label}: {row.value}</div>)}
        </div>
    )
}

const PAD = { top: 24, right: 8, bottom: 26, left: 40 }

// Quarter-hour/hour columns from a zero baseline; negative prices grow downwards.
function DayChart({ day }: { day: DayView }) {
    const [ref, width] = useWidth<HTMLDivElement>()
    const [hover, setHover] = useState<number | null>(null)
    const height = 220
    const values = day.points.map(([, v]) => toCents(v))
    const ticks = niceTicks(Math.min(0, ...values), Math.max(...values))
    const lo = ticks[0], hi = ticks[ticks.length - 1]
    const plotW = Math.max(width - PAD.left - PAD.right, 1)
    const plotH = height - PAD.top - PAD.bottom
    const y = (v: number) => PAD.top + ((hi - v) / (hi - lo)) * plotH
    const slot = plotW / values.length
    const gap = slot > 6 ? 2 : slot > 3 ? 1 : 0
    const barW = Math.min(24, Math.max(slot - gap, 1))
    const cheapest = new Set(day.cheapest_hours)
    const hourOf = (i: number) => Number(day.points[i][0].slice(0, 2))
    const radius = Math.min(4, barW / 2)

    function move(event: ReactPointerEvent<SVGSVGElement>) {
        const box = event.currentTarget.getBoundingClientRect()
        const index = Math.floor((event.clientX - box.left - PAD.left) / slot)
        setHover(index >= 0 && index < values.length ? index : null)
    }

    return (
        <div className="em-chart" ref={ref}>
            {width > 0 && <svg height={height} role="img" aria-label={`Day-ahead prijs per ${day.resolution_minutes} minuten op ${dateLabel(day.date)}`} onPointerMove={move} onPointerLeave={() => setHover(null)}>
                {ticks.map(t => <g key={t}><line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke={GRID} /><text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" fontSize="11" fill="#728076">{t.toLocaleString("nl-BE")}</text></g>)}
                {values.map((v, i) => {
                    const top = Math.min(y(v), y(0)), h = Math.max(Math.abs(y(v) - y(0)), 1)
                    const x = PAD.left + i * slot + (slot - barW) / 2
                    const r = Math.min(radius, h)
                    // Rounded data end, square at the baseline (top for positive, bottom for negative).
                    const d = v >= 0
                        ? `M${x},${top + h}V${top + r}Q${x},${top} ${x + r},${top}H${x + barW - r}Q${x + barW},${top} ${x + barW},${top + r}V${top + h}Z`
                        : `M${x},${top}V${top + h - r}Q${x},${top + h} ${x + r},${top + h}H${x + barW - r}Q${x + barW},${top + h} ${x + barW},${top + h - r}V${top}Z`
                    return <path key={i} d={d} fill={cheapest.has(hourOf(i)) ? ACCENT : MEASURED} opacity={hover === null || hover === i ? 1 : 0.55} />
                })}
                <line x1={PAD.left} x2={width - PAD.right} y1={y(0)} y2={y(0)} stroke="#b9c3bb" />
                {[0, 6, 12, 18].map(hour => {
                    const index = day.points.findIndex(([t]) => Number(t.slice(0, 2)) === hour)
                    return index < 0 ? null : <text key={hour} x={PAD.left + index * slot} y={height - 8} fontSize="11" fill="#728076">{`${String(hour).padStart(2, "0")}:00`}</text>
                })}
                <text x={0} y={10} fontSize="11" fill="#728076">c€/kWh</text>
            </svg>}
            <Tooltip width={width} tip={hover === null ? null : {
                x: PAD.left + hover * slot, y: y(Math.max(values[hover], 0)),
                title: `${day.points[hover][0]}${cheapest.has(hourOf(hover)) ? " · goedkoopst" : ""}`,
                rows: [{ label: "Prijs", value: fmt(day.points[hover][1], 2) }],
            }} />
        </div>
    )
}

type Series = { name: string; color: string; values: (number | null)[]; muted?: boolean }

function LineChart({ labels, series, band, height = 230, label }: { labels: string[]; series: Series[]; band?: { low: (number | null)[]; high: (number | null)[]; color: string }; height?: number; label: string }) {
    const [ref, width] = useWidth<HTMLDivElement>()
    const [hover, setHover] = useState<number | null>(null)
    const all = [...series.flatMap(s => s.values), ...(band ? [...band.low, ...band.high] : [])].filter((v): v is number => v !== null)
    const ticks = niceTicks(Math.min(0, ...all), Math.max(...all))
    const lo = ticks[0], hi = ticks[ticks.length - 1]
    const plotW = Math.max(width - PAD.left - PAD.right, 1)
    const plotH = height - PAD.top - PAD.bottom
    const x = (i: number) => PAD.left + (labels.length > 1 ? (i / (labels.length - 1)) * plotW : plotW / 2)
    const y = (v: number) => PAD.top + ((hi - v) / (hi - lo)) * plotH
    const path = (values: (number | null)[]) => values.reduce((d, v, i) => v === null ? d : `${d}${d && values[i - 1] !== null && i > 0 ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`, "")
    const yearTicks = labels.map((m, i) => [m, i] as const).filter(([m]) => m.endsWith("-01"))
    const step = Math.ceil(yearTicks.length / Math.max(Math.floor(plotW / 56), 1))

    function move(event: ReactPointerEvent<SVGSVGElement>) {
        const box = event.currentTarget.getBoundingClientRect()
        const index = Math.round(((event.clientX - box.left - PAD.left) / plotW) * (labels.length - 1))
        setHover(index >= 0 && index < labels.length ? index : null)
    }

    let bandPath = ""
    if (band) {
        const idx = labels.map((_, i) => i).filter(i => band.low[i] !== null && band.high[i] !== null)
        if (idx.length) bandPath = `M${idx.map(i => `${x(i)},${y(band.high[i]!)}`).join("L")}L${[...idx].reverse().map(i => `${x(i)},${y(band.low[i]!)}`).join("L")}Z`
    }

    return (
        <div className="em-chart" ref={ref}>
            {width > 0 && <svg height={height} role="img" aria-label={label} onPointerMove={move} onPointerLeave={() => setHover(null)}>
                {ticks.map(t => <g key={t}><line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke={GRID} /><text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" fontSize="11" fill="#728076">{t.toLocaleString("nl-BE")}</text></g>)}
                {yearTicks.filter((_, k) => k % step === 0).map(([m, i]) => <text key={m} x={x(i)} y={height - 8} fontSize="11" fill="#728076" textAnchor="middle">{m.slice(0, 4)}</text>)}
                <text x={0} y={10} fontSize="11" fill="#728076">c€/kWh</text>
                {band && bandPath && <path d={bandPath} fill={band.color} opacity={0.12} />}
                {series.map(s => <path key={s.name} d={path(s.values)} fill="none" stroke={s.color} strokeWidth={s.muted ? 1.5 : 2} strokeLinejoin="round" strokeLinecap="round" />)}
                {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + plotH} stroke="#b9c3bb" />}
                {hover !== null && series.map(s => s.values[hover] === null ? null : <circle key={s.name} cx={x(hover)} cy={y(s.values[hover]!)} r={4} fill={s.color} stroke="#fff" strokeWidth={2} />)}
            </svg>}
            <Tooltip width={width} tip={hover === null ? null : {
                x: x(hover), y: PAD.top + 40, title: monthLabel(labels[hover]),
                rows: [
                    ...series.filter(s => s.values[hover] !== null).map(s => ({ color: s.color, label: s.name, value: `${s.values[hover]!.toLocaleString("nl-BE", { maximumFractionDigits: 1 })} c€/kWh` })),
                    ...(band && band.low[hover] !== null ? [{ label: "Bandbreedte", value: `${band.low[hover]!.toLocaleString("nl-BE", { maximumFractionDigits: 1 })}–${band.high[hover]!.toLocaleString("nl-BE", { maximumFractionDigits: 1 })}` }] : []),
                ],
            }} />
        </div>
    )
}

function Legend({ items }: { items: { color: string; label: string; area?: boolean }[] }) {
    return <ul className="em-legend">{items.map(item => <li key={item.label}><span className="em-key" style={{ background: item.color, opacity: item.area ? 0.25 : 1 }} />{item.label}</li>)}</ul>
}

function Stale({ section }: { section: Section | null }) {
    return section?.stale ? <span className="em-badge">Verouderd</span> : null
}

function Updated({ section }: { section: Section | null }) {
    if (!section?.updated_at) return null
    return <>Bijgewerkt {new Date(section.updated_at).toLocaleString("nl-BE", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}. </>
}

function rolling(values: number[], window: number): (number | null)[] {
    return values.map((_, i) => i + 1 < window ? null : values.slice(i + 1 - window, i + 1).reduce((a, b) => a + b, 0) / window)
}

function TodayView({ data }: { data: MarketData }) {
    const spot = data.spot!
    const [which, setWhich] = useState<"today" | "tomorrow">("today")
    const day = which === "tomorrow" && spot.tomorrow ? spot.tomorrow : spot.today
    const hours = day.cheapest_hours.map(h => `${String(h).padStart(2, "0")}u`).join(", ")
    return (
        <section className="em-card" aria-labelledby="em-today">
            <h2 id="em-today">Stroomprijs {which === "today" ? "vandaag" : "morgen"}<Stale section={spot} /></h2>
            <p className="em-sub">Groothandelsprijs (day-ahead) voor België, {dateLabel(day.date)}. Zonder netkosten, heffingen en btw.</p>
            {spot.tomorrow && <div className="em-switch">{(["today", "tomorrow"] as const).map(w => <button key={w} aria-pressed={which === w} onClick={() => setWhich(w)}>{w === "today" ? "Vandaag" : "Morgen"}</button>)}</div>}
            <div className="em-tiles">
                {([["Laagste", day.min], ["Gemiddeld", day.avg], ["Hoogste", day.max]] as const).map(([name, value]) =>
                    <div className="em-tile" key={name}><span>{name}</span><strong>{toCents(value).toLocaleString("nl-BE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}<small> c€/kWh</small></strong></div>)}
            </div>
            <DayChart day={day} />
            <Legend items={[{ color: MEASURED, label: "Prijs" }, { color: ACCENT, label: `Goedkoopste uren (${hours})` }]} />
            <p className="em-note">Met een dynamisch contract betaal je ongeveer deze prijs plus de toeslag van je leverancier. Verbruik verschuiven naar de goedkoopste uren (wassen, laden) loont dan het meest.</p>
            <details><summary>Toon als tabel</summary><div className="em-table-wrap"><table className="em-table"><thead><tr><th>Tijd</th><th>c€/kWh</th></tr></thead><tbody>{day.points.map(([t, v]) => <tr key={t}><td>{t}</td><td>{toCents(v).toLocaleString("nl-BE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td></tr>)}</tbody></table></div></details>
        </section>
    )
}

function HistoryView({ data }: { data: MarketData }) {
    const history = data.history!
    const labels = Object.keys(history.months).sort()
    const values = labels.map(m => toCents(history.months[m]))
    const avg12 = rolling(values, 12)
    const last = values[values.length - 1], lastAvg = avg12[avg12.length - 1]
    return (
        <section className="em-card" aria-labelledby="em-history">
            <h2 id="em-history">Historiek & trend<Stale section={history} /></h2>
            <p className="em-sub">Gemiddelde groothandelsprijs per maand sinds {labels[0].slice(0, 4)}. Laatste volledige maand ({monthLabel(labels[labels.length - 1])}): <strong>{last.toLocaleString("nl-BE", { maximumFractionDigits: 1 })} c€/kWh</strong>{lastAvg !== null && <>, gemiddelde van de laatste 12 maanden: {lastAvg.toLocaleString("nl-BE", { maximumFractionDigits: 1 })} c€/kWh</>}.</p>
            <LineChart labels={labels} label="Maandgemiddelde groothandelsprijs en 12-maands gemiddelde" series={[{ name: "Maandgemiddelde", color: MEASURED, values }, { name: "12-maands gemiddelde", color: REFERENCE, values: avg12, muted: true }]} />
            <Legend items={[{ color: MEASURED, label: "Maandgemiddelde" }, { color: REFERENCE, label: "12-maands gemiddelde (trend)" }]} />
            <details><summary>Toon als tabel</summary><div className="em-table-wrap"><table className="em-table"><thead><tr><th>Maand</th><th>c€/kWh</th></tr></thead><tbody>{[...labels].reverse().map(m => <tr key={m}><td>{monthLabel(m)}</td><td>{toCents(history.months[m]).toLocaleString("nl-BE", { maximumFractionDigits: 1 })}</td></tr>)}</tbody></table></div></details>
        </section>
    )
}

function ForecastView({ data }: { data: MarketData }) {
    const forecast = data.forecast!
    const months = data.history ? Object.keys(data.history.months).sort().slice(-24) : []
    const labels = [...months, ...forecast.months.map(m => m.month)]
    const measured = [...months.map(m => toCents(data.history!.months[m])), ...forecast.months.map(() => null)]
    // The forecast line starts at the last measured month so the two lines connect.
    const anchor = months.length ? [...months.slice(0, -1).map(() => null), measured[months.length - 1]] : []
    const point = [...anchor, ...forecast.months.map(m => toCents(m.point))]
    const low = [...months.map(() => null), ...forecast.months.map(m => toCents(m.low))]
    const high = [...months.map(() => null), ...forecast.months.map(m => toCents(m.high))]
    const { mape_1m, mape_12m } = forecast.backtest
    return (
        <section className="em-card" aria-labelledby="em-forecast">
            <h2 id="em-forecast">Verwachting komende 12 maanden<Stale section={forecast} /></h2>
            <p className="em-sub">Een eenvoudige statistische inschatting op basis van het seizoenspatroon en het recente prijsniveau. Geen advies: energieprijzen kunnen snel en sterk veranderen.</p>
            <LineChart labels={labels} label="Gemeten maandprijzen en verwachting met bandbreedte" series={[{ name: "Gemeten", color: MEASURED, values: measured }, { name: "Verwachting", color: ACCENT, values: point }]} band={{ low, high, color: ACCENT }} />
            <Legend items={[{ color: MEASURED, label: "Gemeten" }, { color: ACCENT, label: "Verwachting (middenlijn)" }, { color: ACCENT, label: "Bandbreedte 10–90%", area: true }]} />
            <p className="em-note">Hoe betrouwbaar? Toegepast op {forecast.backtest.origins} eerdere momenten zat deze methode gemiddeld {mape_1m.toLocaleString("nl-BE")}% naast de werkelijke prijs één maand later, en {mape_12m.toLocaleString("nl-BE")}% twaalf maanden later. In de backtest viel de prijs in 8 op de 10 gevallen binnen de band.</p>
            <details><summary>Toon als tabel</summary><table className="em-table"><thead><tr><th>Maand</th><th>Verwachting</th><th>Band</th></tr></thead><tbody>{forecast.months.map(m => <tr key={m.month}><td>{monthLabel(m.month)}</td><td>{toCents(m.point).toLocaleString("nl-BE", { maximumFractionDigits: 1 })}</td><td>{toCents(m.low).toLocaleString("nl-BE", { maximumFractionDigits: 1 })}–{toCents(m.high).toLocaleString("nl-BE", { maximumFractionDigits: 1 })}</td></tr>)}</tbody></table></details>
        </section>
    )
}

const COMPARATORS = [
    { title: "V-test (Vlaanderen)", detail: "Vlaamse Nutsregulator", url: "https://www.vtest.be/" },
    { title: "BruSim (Brussel)", detail: "BRUGEL", url: "https://brugel.brussels/nl_BE/outils/brusim-2" },
    { title: "CompaCWaPE (Wallonië)", detail: "CWaPE", url: "https://www.compacwape.be/" },
]

function ContractsView() {
    return (
        <section className="em-card" aria-labelledby="em-contracts">
            <h2 id="em-contracts">Contracten vergelijken</h2>
            <p className="em-sub">Binnenkort: de energieprijs per kWh en de vaste vergoeding van de contracten van Luminus, Engie, Eneco, TotalEnergies, Mega, Bolt, Elegant, Energyvision, Octa+, Aspiravi en Frank Energie, maandelijks uit hun officiële tariefkaarten. Tot dan vergelijk je het best bij de officiële vergelijker van je gewest:</p>
            <div className="em-links">{COMPARATORS.map(c => <a key={c.url} href={c.url} target="_blank" rel="noreferrer"><strong>{c.title} ↗</strong><small>{c.detail}</small></a>)}</div>
        </section>
    )
}

export default function EnergyMarketDashboard({ dataUrl = DEFAULT_MARKET_DATA_URL, style }: Props) {
    // Results are keyed by URL, so a new URL never shows the previous URL's prices.
    const [loaded, setLoaded] = useState<{ url: string; data: MarketData | null; failed: boolean } | null>(null)
    const [view, setView] = useState<View>("today")
    const current = loaded?.url === dataUrl ? loaded : null
    const data = current?.data ?? null
    const failed = current?.failed ?? false

    useEffect(() => {
        const controller = new AbortController()
        fetch(dataUrl, { signal: controller.signal, cache: "no-cache" })
            .then(response => { if (!response.ok) throw new Error(String(response.status)); return response.json() })
            .then(json => setLoaded({ url: dataUrl, data: json as MarketData, failed: false }))
            .catch(() => { if (!controller.signal.aborted) setLoaded({ url: dataUrl, data: null, failed: true }) })
        return () => controller.abort()
    }, [dataUrl])

    const views = useMemo(() => ([
        ["today", "Vandaag", Boolean(data?.spot)],
        ["history", "Historiek", Boolean(data?.history)],
        ["forecast", "Verwachting", Boolean(data?.forecast)],
        ["contracts", "Contracten", true],
    ] as const).filter(([, , available]) => available), [data])
    const active = views.some(([key]) => key === view) ? view : views[0]?.[0]
    const source = data?.sources?.[0]

    return (
        <div className="em" style={style}>
            <style dangerouslySetInnerHTML={{ __html: css }} />
            {!data && !failed && <div className="em-card" role="status">Prijzen worden geladen…</div>}
            {failed && <div className="em-card" role="alert"><h2>Prijzen niet beschikbaar</h2><p className="em-sub">De marktgegevens konden nu niet geladen worden. Probeer later opnieuw of bekijk de officiële vergelijker van je gewest.</p><ContractsView /></div>}
            {data && <>
                <div className="em-views" role="tablist" aria-label="Prijsoverzicht">
                    {views.map(([key, title]) => <button key={key} className="em-view" role="tab" aria-selected={active === key} onClick={() => setView(key)}>{title}</button>)}
                </div>
                {active === "today" && <TodayView data={data} />}
                {active === "history" && <HistoryView data={data} />}
                {active === "forecast" && <ForecastView data={data} />}
                {active === "contracts" && <ContractsView />}
                {source && <p className="em-note"><Updated section={active === "history" ? data.history : active === "forecast" ? data.forecast : data.spot} />Bron: <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>{source.licence ? ` (${source.licence})` : ""}. Groothandelsprijzen zijn niet wat je op je factuur betaalt.</p>}
            </>}
        </div>
    )
}
