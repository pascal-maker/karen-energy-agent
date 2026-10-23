import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react"
import { addPropertyControls, ControlType } from "framer"

type SpeechResult = { transcript: string }
type SpeechErrorEvent = { error?: string }
type SpeechRecognizer = {
    lang: string
    interimResults: boolean
    continuous: boolean
    onresult: ((event: { results: ArrayLike<ArrayLike<SpeechResult>> }) => void) | null
    onerror: ((event: SpeechErrorEvent) => void) | null
    onend: (() => void) | null
    onaudiostart?: (() => void) | null
    onspeechstart?: (() => void) | null
    start(): void
    stop(): void
}
type SpeechWindow = Window & { SpeechRecognition?: new () => SpeechRecognizer; webkitSpeechRecognition?: new () => SpeechRecognizer }

type Source = { title: string; detail?: string; url?: string; excerpt?: string }
type Reply = { answer: string; sources?: Source[]; suggested_sources?: Source[]; petra_speech_token?: string; feedback_token?: string; processing?: string; live_source_status?: "checked" | "failed" | "not-needed"; comparison_status?: string; visual?: PriceVisual }

// What Karen does while she answers, shown in the chat as she works.
const stepSummary = (steps: Step[]) => {
    const route = steps.find((step) => step.kind === "route")
    const jev = route && route.kind === "route" ? `Jev: ${ROUTE_LABELS[route.choice] || route.choice} ${percent(route.probabilities?.[route.choice] ?? route.confidence)}` : ""
    const searches = steps.filter((step) => step.kind === "search").length
    if (!searches) return `${jev}.`
    const pages = steps.reduce((total, step) => total + (step.kind === "read" ? step.pages.length : 0), 0)
    const failed = steps.some((step) => step.kind === "search_error")
    const read = pages === 0 ? (failed ? "de zoekdienst gaf een fout" : "vond geen bruikbare pagina") : `las ${pages === 1 ? "één pagina" : `${pages} pagina's`}`
    const search = `Karen zocht ${searches === 1 ? "één keer" : `${searches} keer`} en ${read}.`
    return jev ? `${jev} · ${search}` : search
}
type Step =
    | { kind: "route"; choice: string; confidence: number; probabilities: Record<string, number>; used: boolean }
    | { kind: "search"; sites: string[] }
    | { kind: "read"; pages: { title: string; url: string }[] }
    | { kind: "search_error"; message: string }
// Jev's routes, in the words a visitor reads.
const ROUTE_LABELS: Record<string, string> = {
    price: "Prijsvraag", compare: "Aanbiedingen vergelijken", contract_advice: "Vast of variabel",
    energy_other: "Energievraag", small_talk: "Praatje", off_topic: "Buiten energie",
}
const percent = (value: number) => `${Math.round(value * 100)}%`
const siteList = (sites: string[]) => sites.length > 3 ? `${sites.slice(0, 3).join(", ")} en ${sites.length - 3} andere` : sites.join(", ")

const hostOf = (url: string) => {
    try {
        return new URL(url).hostname.replace(/^www\./, "")
    } catch {
        return ""
    }
}

// The route Karen takes, drawn as she goes: question, search (the sites light up when a page
// from them is read), read (the pages), answer.
function KarenFlow({ steps, writing }: { steps: Step[]; writing: boolean }) {
    const sites = [...new Set(steps.flatMap((step) => step.kind === "search" ? step.sites : []))]
    const pages = steps.flatMap((step) => step.kind === "read" ? step.pages : [])
    const readHosts = pages.map((page) => hostOf(page.url))
    const last = steps[steps.length - 1]
    const route = steps.find((step) => step.kind === "route")
    const stage = writing ? 4 : pages.length ? 3 : sites.length ? 2 : 1
    const caption = writing
        ? "Schrijft het antwoord…"
        : last.kind === "search" ? `Zoekt op ${siteList(last.sites)}…`
        : last.kind === "read" ? `Leest ${last.pages.map((page) => page.title).join(", ")}`
        : last.kind === "search_error" ? "Zoeken lukt nu niet · Karen probeert verder"
        : last.used ? `Jev herkent: ${ROUTE_LABELS[last.choice] || last.choice}` : "Jev twijfelt · Karen kiest zelf de route"
    const ranked = route && route.kind === "route"
        ? Object.entries(route.probabilities).sort((a, b) => b[1] - a[1]).slice(0, 3)
        : []
    return <div className="ea-flow" role="status" aria-label="Wat Karen doet">
        <ol className="ea-flow-stages">
            {["Vraag", "Begrijpen", "Zoeken", "Lezen", "Antwoord"].map((label, index) => <li key={label} data-state={index < stage ? "done" : index === stage ? "active" : "todo"}>{label}</li>)}
        </ol>
        <p className="ea-flow-caption">{caption}</p>
        {ranked.length > 0 && route && route.kind === "route" && <div className="ea-jev" aria-label="Kansen volgens Jev">
            {ranked.map(([name, value]) => <div className="ea-jev-row" key={name} data-chosen={name === route.choice && route.used}>
                <span>{ROUTE_LABELS[name] || name}</span>
                <span className="ea-jev-bar"><span style={{ width: percent(value) }} /></span>
                <span>{percent(value)}</span>
            </div>)}
        </div>}
        <div className="ea-flow-sites">
            {sites.map((site) => <span key={site} className="ea-site" data-read={readHosts.some((host) => host === site || host.endsWith(`.${site}`))}>{site}</span>)}
        </div>
        {pages.length > 0 && <div className="ea-flow-pages">
            {pages.map((page) => <a key={page.url} className="ea-page" href={page.url} target="_blank" rel="noreferrer">{page.title}</a>)}
        </div>}
    </div>
}

// Prices Karen read from the market data, drawn as a day curve and bars next to the answer.
type PriceVisual = {
    kind: "prices"
    lead: string
    unit: string
    digits?: number
    groups: { title: string; items: { label: string; value: number; note?: string }[] }[]
    day?: { title: string; points: [string, number][]; cheapest_hours: number[]; cheap_label: string; low: number; high: number }
}
const formatNumber = (value: number, unit: string, digits = 1) => {
    const text = value.toFixed(digits)
    return unit.startsWith("cents") ? text : text.replace(".", ",")
}

// Amounts, percentages and prices stand out in the text; **bold** from the model becomes bold.
const AMOUNT = /(€\s?\d[\d.,]*|\d[\d.,]*\s?(?:%|(?:cent\/kWh|c€\/kWh|cents?|euro|EUR|kWh|MWh|kW)\b))/
function inline(text: string): ReactNode[] {
    return text.split(/(\*\*[^*]+\*\*)/g).flatMap((part, index) => /^\*\*[^*]+\*\*$/.test(part)
        ? [<strong key={index}>{part.slice(2, -2)}</strong>]
        : part.split(AMOUNT).map((piece, inner) => inner % 2 ? <span className="ea-num" key={`${index}-${inner}`}>{piece}</span> : piece))
}

type Block = { kind: "p"; text: string } | { kind: "points" | "steps"; items: string[] }
// An answer as a short lead, points as cards and numbered steps; a one-paragraph answer stays plain text.
function FormattedAnswer({ text }: { text: string }) {
    const blocks: Block[] = []
    for (const raw of text.split("\n")) {
        const line = raw.trim()
        if (!line) continue
        const step = line.match(/^\d+[.)]\s+(.+)$/)
        const point = line.match(/^[-*•]\s+(.+)$/)
        const kind = step ? "steps" : point ? "points" : "p"
        const body = step ? step[1] : point ? point[1] : line
        const last = blocks[blocks.length - 1]
        if (kind !== "p" && last && last.kind === kind) last.items.push(body)
        else blocks.push(kind === "p" ? { kind, text: body } : { kind, items: [body] })
    }
    if (blocks.length <= 1 && (!blocks[0] || blocks[0].kind === "p")) return <>{inline(text)}</>
    return <div className="ea-rich">
        {blocks.map((block, index) => block.kind === "p"
            ? <p key={index} className={index === 0 ? "ea-lead" : undefined}>{inline(block.text)}</p>
            : block.kind === "steps"
                ? <ol key={index} className="ea-steps">{block.items.map((item, inner) => <li key={inner}>{inline(item)}</li>)}</ol>
                : <ul key={index} className="ea-points">{block.items.map((item, inner) => {
                    const labelled = item.match(/^\*\*([^*]+?)\*\*:?\s*(.*)$/)
                    return <li key={inner}>{labelled
                        ? <><strong>{labelled[1].replace(/:$/, "")}</strong><span>{inline(labelled[2])}</span></>
                        : inline(item)}</li>
                })}</ul>)}
    </div>
}

function DayCurve({ day, unit }: { day: NonNullable<PriceVisual["day"]>; unit: string }) {
    const values = day.points.map((point) => point[1])
    const low = Math.min(...values), high = Math.max(...values)
    const width = 300, top = 6, bottom = 70
    const x = (index: number) => (index / (day.points.length - 1)) * width
    const y = (value: number) => bottom - (high === low ? 0.5 : (value - low) / (high - low)) * (bottom - top)
    const line = day.points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point[1]).toFixed(1)}`).join(" ")
    const cheap = day.points.map((point, index) => ({ index, hour: parseInt(point[0], 10) })).filter((point) => day.cheapest_hours.includes(point.hour))
    return <figure className="ea-day">
        <figcaption>{day.title}</figcaption>
        <svg viewBox={`0 0 ${width} 76`} role="img" aria-label={`${day.title}: ${formatNumber(day.low ?? low, unit)} – ${formatNumber(day.high ?? high, unit)} ${unit}`}>
            <path d={`${line} L${width},${bottom} L0,${bottom} Z`} className="ea-day-area" />
            <path d={line} className="ea-day-line" />
            {cheap.map((point) => <circle key={point.index} cx={x(point.index)} cy={y(day.points[point.index][1])} r="3.5" className="ea-day-cheap" />)}
        </svg>
        <div className="ea-day-axis"><span>{day.points[0][0]}</span><span>12:00</span><span>{day.points[day.points.length - 1][0]}</span></div>
        <div className="ea-day-legend">
            {cheap.length > 0 && <span><i />{day.cheap_label}</span>}
            <span>{formatNumber(day.low ?? low, unit)}–{formatNumber(day.high ?? high, unit)} {unit}</span>
        </div>
    </figure>
}

function PriceChart({ visual }: { visual: PriceVisual }) {
    const max = Math.max(...visual.groups.flatMap((group) => group.items.map((item) => item.value)))
    return <div className="ea-viz">
        <p className="ea-lead">{inline(visual.lead)}</p>
        {visual.day && <DayCurve day={visual.day} unit={visual.unit} />}
        {visual.groups.map((group) => <div className="ea-bars" key={group.title}>
            <h4>{group.title} <small>{visual.unit}</small></h4>
            {group.items.map((item, index) => <div className="ea-bar-row" key={item.label} data-best={index === 0}>
                <span className="ea-bar-label">{item.label}{item.note && <small>{item.note}</small>}</span>
                <span className="ea-bar-track"><span style={{ width: `${(item.value / max) * 100}%` }} /></span>
                <span className="ea-bar-value">{formatNumber(item.value, visual.unit, visual.digits)}</span>
            </div>)}
        </div>)}
    </div>
}

type Props = {
    apiEndpoint: string
    sampleQuestion: string
    style?: CSSProperties
}

const KAREN_LOGO = "data:image/webp;base64,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"

const demoReply: Reply = {
    answer:
        "Je voorschot is een maandelijkse inschatting van je energiekosten. Op je jaarafrekening wordt de werkelijke kost verrekend met de voorschotten die al zijn aangerekend. Heb je te veel vooruitbetaald, dan krijg je het verschil terug; anders betaal je het verschil bij.",
    sources: [
        {
            title: "Luminus · voorschotten en jaarafrekening",
            detail: "Voorbeeldlink · niet live opgehaald",
            url: "https://www.luminus.be/nl/prive/energie/energiefactuur-uitgelegd/",
        },
        {
            title: "Kennisbasis EnergyAgent",
            detail: "Voorbeeldfragment · PDF-pagina 4",
            excerpt:
                "Luminus werkt met voorschotfacturen en afrekeningen; het ideale voorschot kan op basis van digitale meterdata worden herberekend.",
        },
    ],
}

type Today = { hour: number; now_cents: number; cheapest_hour: number | null }
const formatCents = (cents: number) => cents.toLocaleString("nl-BE", { maximumFractionDigits: 1 })
const DEMO_QUESTION = "Waarom verschilt mijn voorschot van mijn jaarafrekening?"
// Speech follows the browser's languages: Dutch whenever it is listed, else French or English, else Dutch.
const SPEECH_LOCALES = { nl: "nl-BE", fr: "fr-BE", en: "en-GB" } as const
type VoiceLanguage = keyof typeof SPEECH_LOCALES
const voiceLanguage = (): VoiceLanguage => {
    const listed = typeof navigator === "undefined" ? [] : [...(navigator.languages || []), navigator.language || ""]
    const codes = listed.map((language) => language.toLowerCase().slice(0, 2))
    return (["nl", "fr", "en"] as const).find((code) => codes.includes(code)) ?? "nl"
}
// Language of an answer, for the browser voice: Karen answers in the language of the question.
const answerLanguage = (text: string): VoiceLanguage => {
    const words = text.toLowerCase().match(/[a-zàâçéèêëîïôûùüÿœ']+/g) || []
    const count = (list: string[]) => words.filter((word) => list.includes(word)).length
    const scores = { nl: count(["de", "het", "een", "en", "is", "je", "u", "van", "voor"]), fr: count(["le", "la", "les", "est", "et", "des", "vous", "pour", "une"]), en: count(["the", "is", "and", "you", "your", "for", "of", "to", "a"]) }
    const best = (Object.keys(scores) as VoiceLanguage[]).sort((a, b) => scores[b] - scores[a])[0]
    return scores[best] ? best : voiceLanguage()
}
type Turn = { question: string; answer: string; visual?: PriceVisual }
// The conversation is kept only in this visitor's browser; PDF answers are never stored or sent along.
const HISTORY_KEY = "karen-chat-v1"
const MAX_TURNS = 20
const SENT_TURNS = 4
const isPriceVisual = (value: any): value is PriceVisual => value?.kind === "prices" && typeof value.lead === "string" && typeof value.unit === "string"
    && Array.isArray(value.groups) && value.groups.every((group: any) => Array.isArray(group?.items) && group.items.every((item: any) => typeof item?.label === "string" && typeof item?.value === "number"))
    && (value.day === undefined || (Array.isArray(value.day.points) && value.day.points.length > 1 && Array.isArray(value.day.cheapest_hours)))
function loadTurns(): Turn[] {
    try {
        const saved = JSON.parse(window.localStorage.getItem(HISTORY_KEY) || "[]")
        return Array.isArray(saved)
            ? saved.filter((turn) => typeof turn?.question === "string" && typeof turn?.answer === "string").slice(-MAX_TURNS)
                // A stored chart is only drawn again when it still has the shape this version draws.
                .map((turn) => isPriceVisual(turn.visual) ? turn : { question: turn.question, answer: turn.answer })
            : []
    } catch {
        return []
    }
}
function saveTurns(turns: Turn[]) {
    try {
        if (turns.length) window.localStorage.setItem(HISTORY_KEY, JSON.stringify(turns.slice(-MAX_TURNS)))
        else window.localStorage.removeItem(HISTORY_KEY)
    } catch {
        // Private windows or blocked storage: the chat still works, it just isn't kept.
    }
}
const normalizeQuestion = (text: string) => text.trim().toLocaleLowerCase("nl-BE").replace(/[?!.]+$/, "")

const css = `
  * { box-sizing: border-box; }
  .ea-shell { width:100%; height:100%; min-width:320px; container-type:inline-size; }
  .ea { width:100%; height:100%; min-height:590px; display:flex; flex-direction:column; gap:14px; padding:18px; color:#1e2b23; background:#f6f7f3; font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif; }
  .ea-top { display:flex; align-items:center; justify-content:space-between; gap:12px; }
  .ea-top .ea-brand { padding:0; border:0; }
  .ea-body { flex:1; min-height:0; display:flex; justify-content:center; }
  .ea-main { display:flex; flex-direction:column; width:100%; max-width:780px; min-height:0; }
  .ea-hello { display:flex; align-items:flex-start; gap:12px; margin:24px 0 16px; }
  .ea-hello-face { flex:none; width:44px; height:44px; border-radius:50%; object-fit:cover; background:#183d2c; }
  .ea-hello-bubble { max-width:560px; padding:16px 18px; border:1px solid #e3ebe2; border-radius:4px 18px 18px; background:#fff; font-size:14px; line-height:1.55; }
  .ea-hello-bubble p { margin:6px 0 0; color:#4f6156; }
  .ea h1 { margin:0; font-size:20px; line-height:1.25; letter-spacing:-.02em; }
  .ea-today { padding-top:8px; border-top:1px solid #eef2ec; color:#234f38!important; }
  .ea-chips { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 16px 56px; }
  .ea-chip { min-height:38px; padding:8px 12px; border:1px solid #d6e3d4; border-radius:999px; color:#234f38; background:#fff; font:inherit; font-size:13px; text-align:left; cursor:pointer; }
  .ea-chip:hover { background:#f2f7ef; }
  .ea-composer { padding:10px 0 4px; background:#f6f7f3; }
  .ea-controls { display:flex; align-items:center; gap:4px; padding:5px; border:1px solid #dfe5de; border-radius:18px; background:#fff; box-shadow:0 2px 10px #1e2b230d; }
  .ea-controls:focus-within { border-color:#72a584; box-shadow:0 0 0 3px #72a58420; }
  .ea-input { flex:1; min-width:0; height:42px; padding:0 6px; border:0; outline:none; color:#27372c; background:transparent; font:inherit; font-size:14px; }
  .ea-attach, .ea-mic { position:relative; flex:none; display:grid; place-items:center; width:40px; height:40px; border:0; border-radius:12px; color:#56705f; background:transparent; cursor:pointer; }
  .ea-attach:hover, .ea-mic:hover { background:#f2f7ef; color:#234f38; }
  .ea-attach svg, .ea-mic svg { width:20px; height:20px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .ea-attach input { position:absolute; inset:0; width:100%; height:100%; opacity:0; cursor:pointer; }
  .ea-attach[data-disabled="true"] { opacity:.45; cursor:not-allowed; }
  .ea-mic[data-listening="true"] { color:white; background:#a84639; }
  .ea-mic[data-listening="true"] svg { fill:currentColor; }
  .ea-mic:disabled { opacity:.45; cursor:not-allowed; }
  .ea-button { flex:none; display:grid; place-items:center; width:42px; height:42px; border:0; border-radius:13px; color:white; background:#234f38; font-size:18px; cursor:pointer; }
  .ea-button:hover { background:#173d29; }
  .ea-pill { display:flex; align-items:center; gap:8px; width:fit-content; margin:0 0 8px; padding:6px 8px 6px 12px; border:1px solid #d6e3d4; border-radius:999px; color:#234f38; background:#eef6ef; font-size:12px; }
  .ea-pill button { padding:3px 9px; border:1px solid #c9d9c8; border-radius:999px; color:#234f38; background:#fff; font:inherit; font-size:11px; cursor:pointer; }
  .ea-sources { margin-top:12px; padding-top:10px; border-top:1px solid #edf0eb; white-space:normal; }
  .ea-source-row { display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin:0 0 6px; }
  .ea-source-row > span:first-child { margin-right:2px; color:#7d8b82; font-size:11px; }
  .ea-source { max-width:100%; padding:3px 9px; border:1px solid #d6e3d4; border-radius:999px; color:#234f38; background:#fff; font-size:11px; text-decoration:none; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ea-source-more { color:#5d6b61; border-style:dashed; }
  .ea-passages summary { color:#56705f; font-size:11px; cursor:pointer; }
  .ea-excerpt { margin:8px 0 0!important; padding:8px 10px; border-left:2px solid #9bc294; border-radius:0 9px 9px 0; color:#48594d; background:#f2f7ef; font-size:12px; }
  .ea-excerpt small { display:block; margin-top:4px; color:#7d8b82; font-size:10px; }
  .ea-actions { display:flex; align-items:center; gap:4px; margin:6px 0 4px; }
  .ea-voice-play, .ea-feedback button { padding:5px 9px; border:0; border-radius:9px; color:#56705f; background:transparent; font:inherit; font-size:12px; cursor:pointer; }
  .ea-voice-play::before { content:"▶ "; font-size:9px; }
  .ea-voice-play:hover, .ea-feedback button:hover { background:#eef2ec; color:#234f38; }
  .ea-voice-play:disabled, .ea-feedback button:disabled { opacity:.6; cursor:wait; }
  .ea-feedback { display:flex; align-items:center; gap:2px; }
  .ea-feedback-open { flex-wrap:wrap; gap:6px; max-width:94%; margin:4px 0 8px; }
  .ea-feedback-open button { border:1px solid #d7e2d5; background:#fff; }
  .ea-feedback textarea { flex-basis:100%; min-height:54px; padding:8px 10px; border:1px solid #d7e2d5; border-radius:10px; font:inherit; font-size:12px; resize:vertical; }
  .ea-body[hidden] { display:none!important; }
  .ea-brand { display:flex; align-items:center; gap:10px; padding-bottom:18px; border-bottom:1px solid #e8ece7; color:#637168; font-size:12px; }
  .ea-mark { display:block; width:38px; height:38px; border:1px solid #e5e9df; border-radius:50%; object-fit:cover; background:#183d2c; }
  .ea-brand strong { color:#23342a; font-size:14px; }
  .ea-thread { flex:1; min-height:160px; overflow:auto; padding:4px 0 16px; }
  .ea-question { width:fit-content; max-width:88%; margin:0 0 14px auto; padding:12px 15px; border-radius:16px 16px 4px 16px; color:white; background:#234f38; font-size:13px; line-height:1.5; }
  .ea-turn { margin-bottom:16px; }
  .ea-new { padding:8px 12px; border:1px solid #d6e3d4; border-radius:10px; color:#234f38; background:#f2f7ef; font:inherit; font-size:12px; font-weight:600; cursor:pointer; }
  .ea-new:disabled { opacity:.55; cursor:wait; }
  .ea-answer, .ea-past-answer { max-width:94%; padding:15px 17px; border:1px solid #e8ece7; border-radius:4px 16px 16px; background:#fbfcfa; font-size:13px; line-height:1.65; white-space:pre-line; }
  .ea-rich { white-space:normal; }
  .ea-rich p { margin:0 0 8px; }
  .ea-lead { margin:0 0 10px!important; color:#1e2b23; font-size:14px; font-weight:600; line-height:1.5; }
  .ea-num { padding:0 4px; border-radius:5px; color:#234f38; background:#e6f1e7; font-weight:650; white-space:nowrap; }
  .ea-points { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:8px; margin:0 0 8px; padding:0; list-style:none; }
  .ea-points li { padding:10px 12px; border:1px solid #e3ebe2; border-radius:12px; background:white; }
  .ea-points li strong { display:block; margin-bottom:2px; color:#234f38; }
  .ea-steps { display:grid; gap:8px; margin:0 0 8px; padding:0; list-style:none; counter-reset:ea-step; }
  .ea-steps li { position:relative; padding-left:32px; counter-increment:ea-step; }
  .ea-steps li::before { content:counter(ea-step); position:absolute; left:0; top:0; display:grid; place-items:center; width:22px; height:22px; border-radius:50%; color:white; background:#55a573; font-size:11px; font-weight:700; }
  .ea-viz { white-space:normal; }
  .ea-day { margin:0 0 14px; padding:12px; border:1px solid #e3ebe2; border-radius:12px; background:white; }
  .ea-day figcaption, .ea-bars h4 { margin:0 0 8px; color:#4f6156; font-size:11px; font-weight:650; text-transform:uppercase; letter-spacing:.06em; }
  .ea-bars h4 small { font-weight:500; text-transform:none; letter-spacing:0; }
  .ea-day svg { display:block; width:100%; height:auto; overflow:visible; }
  .ea-day-area { fill:#55a5731f; }
  .ea-day-line { fill:none; stroke:#55a573; stroke-width:2; stroke-linejoin:round; }
  .ea-day-cheap { fill:#234f38; stroke:white; stroke-width:1.5; }
  .ea-day-axis, .ea-day-legend { display:flex; justify-content:space-between; gap:8px; color:#7d8b82; font-size:10px; }
  .ea-day-legend { margin-top:6px; font-size:11px; }
  .ea-day-legend i { display:inline-block; width:8px; height:8px; margin-right:5px; border-radius:50%; background:#234f38; }
  .ea-bars { margin:0 0 12px; }
  .ea-bar-row { display:grid; grid-template-columns:minmax(0,1.3fr) minmax(60px,1fr) 3.4em; gap:10px; align-items:center; padding:5px 0; font-size:12px; }
  .ea-bar-label { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ea-bar-label small { margin-left:6px; color:#7d8b82; }
  .ea-bar-track { height:8px; border-radius:4px; background:#edf2ec; overflow:hidden; }
  .ea-bar-track span { display:block; height:100%; border-radius:4px; background:#b9cdbd; animation:ea-grow .6s ease-out both; transform-origin:left; }
  .ea-bar-value { text-align:right; font-variant-numeric:tabular-nums; }
  .ea-bar-row[data-best="true"] { color:#234f38; font-weight:650; }
  .ea-bar-row[data-best="true"] .ea-bar-track span { background:#55a573; }
  .ea-state { display:flex; align-items:center; gap:8px; margin:4px 0 12px; color:#68776d; font-size:12px; }
  .ea-dot { width:8px; height:8px; border-radius:50%; background:#55a573; }
  .ea-flow { max-width:94%; margin:4px 0 14px; padding:14px 14px 12px; border:1px solid #e3ebe2; border-radius:14px; background:linear-gradient(180deg,#f5f9f3,#fbfcfa); }
  .ea-flow-stages { display:grid; grid-template-columns:repeat(5,1fr); margin:0; padding:0; list-style:none; }
  .ea-flow-stages li { position:relative; display:grid; justify-items:center; gap:6px; color:#9aa79e; font-size:11px; font-weight:600; }
  .ea-flow-stages li::before { content:""; position:relative; z-index:1; width:12px; height:12px; border-radius:50%; background:#dfe7de; box-shadow:0 0 0 3px #f6f9f4; transition:background .3s; }
  .ea-flow-stages li:not(:first-child)::after { content:""; position:absolute; top:5px; right:50%; width:100%; height:2px; background:#dfe7de; transition:background .3s; }
  .ea-flow-stages li[data-state="done"], .ea-flow-stages li[data-state="active"] { color:#234f38; }
  .ea-flow-stages li[data-state="done"]::before, .ea-flow-stages li[data-state="active"]::before,
  .ea-flow-stages li[data-state="done"]::after, .ea-flow-stages li[data-state="active"]::after { background:#55a573; }
  .ea-flow-stages li[data-state="active"]::before { animation:ea-ring 1.2s ease-out infinite; }
  .ea-flow-caption { margin:12px 0 8px; color:#4f6156; font-size:12px; text-align:center; }
  .ea-flow-sites, .ea-flow-pages { display:flex; flex-wrap:wrap; justify-content:center; gap:6px; }
  .ea-flow-pages { margin-top:8px; }
  .ea-site { padding:3px 9px; border:1px solid #dfe7de; border-radius:999px; color:#7d8b82; background:white; font-size:11px; animation:ea-pop .3s ease-out both; transition:all .3s; }
  .ea-site[data-read="true"] { border-color:#55a573; color:#234f38; background:#eef6ef; }
  .ea-page { max-width:100%; padding:6px 10px; border-radius:10px; color:#234f38; background:#e6f1e7; font-size:11px; font-weight:600; text-decoration:none; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; animation:ea-pop .35s ease-out both; }
  .ea-jev { display:grid; gap:5px; max-width:340px; margin:0 auto 10px; }
  .ea-jev-row { display:grid; grid-template-columns:minmax(0,9em) 1fr 3.2em; gap:8px; align-items:center; color:#7d8b82; font-size:11px; }
  .ea-jev-row span:first-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ea-jev-row span:last-child { text-align:right; font-variant-numeric:tabular-nums; }
  .ea-jev-row[data-chosen="true"] { color:#234f38; font-weight:600; }
  .ea-jev-bar { height:6px; border-radius:3px; background:#e4ebe3; overflow:hidden; }
  .ea-jev-bar span { display:block; height:100%; border-radius:3px; background:#b9cdbd; animation:ea-grow .6s ease-out both; transform-origin:left; }
  .ea-jev-row[data-chosen="true"] .ea-jev-bar span { background:#55a573; }
  @keyframes ea-grow { from { transform:scaleX(0); } to { transform:scaleX(1); } }
  .ea-flow-done { margin:-4px 0 12px; color:#8a978e; font-size:11px; }
  @keyframes ea-pop { from { opacity:0; transform:translateY(4px) scale(.96); } to { opacity:1; transform:none; } }
  @keyframes ea-ring { 0% { box-shadow:0 0 0 3px #f6f9f4, 0 0 0 3px #55a57366; } 100% { box-shadow:0 0 0 3px #f6f9f4, 0 0 0 11px #55a57300; } }
  @media (prefers-reduced-motion: reduce) { .ea-jev-bar span, .ea-bar-track span, .ea-site, .ea-page, .ea-flow-stages li[data-state="active"]::before { animation:none; } }
  .ea-input:focus { border-color:#72a584; box-shadow:0 0 0 3px #72a58420; }
  .ea-button:hover { background:#173d29; }
  .ea-button:disabled { opacity:.55; cursor:wait; }
  .ea-foot { margin:9px 0 0; color:#7b877e; font-size:10px; line-height:1.5; }
  .ea-tag { display:inline-block; margin:10px 0 0; padding:3px 9px; border-radius:999px; background:#eef1ec; color:#5d6b61; font-size:11px; }
  .ea-privacy summary { margin:9px 0 0; color:#7b877e; font-size:10px; line-height:1.5; list-style:none; cursor:pointer; }
  .ea-privacy summary::-webkit-details-marker { display:none; }
  .ea-privacy summary span { color:#234f38; text-decoration:underline; text-underline-offset:2px; }
  .ea-privacy[open] summary span { display:none; }
  .ea-audio { margin-top:10px; width:100%; height:34px; }
  /* Sized by the space Karen is given, not the screen: a Framer frame or page column
     narrower than 680px gets the phone layout even when the viewport is wider. */
  @container (max-width:680px) {
    .ea { min-height:100dvh; height:auto; padding:0; gap:0; background:#fff; }
    .ea-top { position:sticky; top:0; z-index:3; padding:10px 14px; border-bottom:1px solid #e8ece7; background:#fffffff0; backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px); }
    .ea-top .ea-brand span { font-size:0; }
    .ea-top .ea-brand strong { display:block; }
    .ea-top .ea-brand br { display:none; }
    .ea-top .ea-brand strong { font-size:15px; }
    .ea-body { display:block; }
    .ea-main { min-height:calc(100dvh - 63px); padding:0 16px; }
    .ea-hello { margin-top:16px; }
    .ea-chips { margin-left:0; }
    .ea-chip { font-size:14px; }
    .ea-composer { position:sticky; bottom:0; z-index:2; margin:0 -16px; padding:10px 16px calc(10px + env(safe-area-inset-bottom)); border-top:1px solid #edf0eb; background:#fff; }
    .ea-input { font-size:16px; }
    .ea-hello-bubble { font-size:15px; }
    .ea-source { font-size:12px; }
    .ea-thread { min-height:0; }
    .ea-question, .ea-answer, .ea-past-answer { max-width:100%; font-size:15px; }
    .ea-foot, .ea-privacy summary { font-size:12px; }
    .ea-bar-row { grid-template-columns:minmax(0,1fr) 3.4em; row-gap:4px; padding:6px 0; font-size:14px; }
    .ea-bar-label { grid-column:1 / -1; white-space:normal; }
    .ea-lead { font-size:16px; }
  }
`

function isIosOtherBrowser(userAgent: string): boolean {
    return /iPhone|iPad|iPod/.test(userAgent) && /CriOS|FxiOS|EdgiOS|GSA\/|FBAN|FBAV|Instagram|WhatsApp/.test(userAgent)
}

// Where the browser's speech recognition is missing or blocked, say why and what to do.
function voiceUnsupportedMessage(userAgent: string): string {
    if (isIosOtherBrowser(userAgent)) {
        return "Spraakherkenning werkt op iPhone en iPad alleen in Safari. Open deze pagina in Safari, of typ je vraag hieronder."
    }
    return "Deze browser heeft geen spraakherkenning (bijvoorbeeld Firefox of Brave). Gebruik Chrome, Edge of Safari, of typ je vraag hieronder."
}

function microphoneDeniedMessage(): string {
    let framed = false
    try {
        framed = window.self !== window.top
    } catch {
        framed = true
    }
    if (framed) {
        return "De microfoon is geblokkeerd omdat Karen hier in een ingesloten venster draait (zoals de Framer-preview). Open de gepubliceerde pagina rechtstreeks, of typ je vraag hieronder."
    }
    return "Microfoontoegang is geweigerd voor deze site. Tik op het slot- of instellingenicoon naast het adres, zet Microfoon op Toestaan en probeer opnieuw."
}

/**
 * Framer layout hints: Karen may be set to Fill (any width/height) and, when
 * first placed, starts as a full desktop-size block instead of a small default.
 * Below 680px of its own width it switches to the full-screen phone layout.
 *
 * @framerSupportedLayoutWidth any
 * @framerSupportedLayoutHeight any
 * @framerIntrinsicWidth 1200
 * @framerIntrinsicHeight 820
 */
export default function EnergyAgentConversation({
    apiEndpoint = "",
    sampleQuestion = DEMO_QUESTION,
    style,
}: Props) {
    const [question, setQuestion] = useState("")
    const [asked, setAsked] = useState("")
    const [reply, setReply] = useState<Reply | null>(null)
    const [turns, setTurns] = useState<Turn[]>([])
    const restored = useRef(false)
    const thread = useRef<HTMLDivElement | null>(null)
    const [status, setStatus] = useState("Klaar om te helpen")
    const [busy, setBusy] = useState(false)
    // The answer as Karen writes it; replaced by the full reply when it arrives.
    const [draft, setDraft] = useState("")
    const [steps, setSteps] = useState<Step[]>([])
    const [answerError, setAnswerError] = useState("")
    const [uploading, setUploading] = useState(false)
    const [sessionId, setSessionId] = useState("")
    const [uploadName, setUploadName] = useState("")
    const [uploadError, setUploadError] = useState("")
    const [listening, setListening] = useState(false)
    const [voiceDraft, setVoiceDraft] = useState(false)
    const [voiceError, setVoiceError] = useState("")
    const [petraBusy, setPetraBusy] = useState(false)
    const [browserSpeaking, setBrowserSpeaking] = useState(false)
    const [petraError, setPetraError] = useState("")
    const [petraAudioUrl, setPetraAudioUrl] = useState("")
    const [recognition, setRecognition] = useState<SpeechRecognizer | null>(null)
    const voiceTimeout = useRef<number | null>(null)
    const recorder = useRef<MediaRecorder | null>(null)
    const recordingStream = useRef<MediaStream | null>(null)
    const [transcribing, setTranscribing] = useState(false)
    const [feedback, setFeedback] = useState<"idle" | "sending" | "sent" | "failed">("idle")
    const [feedbackOpen, setFeedbackOpen] = useState(false)
    const [feedbackComment, setFeedbackComment] = useState("")
    const [today, setToday] = useState<Today | null>(null)
    const petraAbort = useRef<AbortController | null>(null)
    const petraPlayer = useRef<HTMLAudioElement | null>(null)
    const petraAudioUrlRef = useRef("")

    // Declared before the restore below, so the first run never overwrites the saved chat with an empty one.
    useEffect(() => {
        if (!restored.current) return
        const current = asked && reply && reply.processing !== "local-only" ? [{ question: asked, answer: reply.answer, ...(reply.visual ? { visual: reply.visual } : {}) }] : []
        saveTurns([...turns, ...current])
    }, [turns, asked, reply])
    useEffect(() => {
        setTurns(loadTurns())
        restored.current = true
    }, [])
    useEffect(() => {
        if (thread.current) thread.current.scrollTop = thread.current.scrollHeight
    }, [turns, asked, reply, busy])
    // The exchange on screen moves into the history before a new question or PDF replaces it.
    function archiveCurrent(): Turn[] {
        const next = asked && reply && reply.processing !== "local-only"
            ? [...turns, { question: asked, answer: reply.answer, ...(reply.visual ? { visual: reply.visual } : {}) }].slice(-MAX_TURNS)
            : turns
        if (next !== turns) setTurns(next)
        return next
    }
    async function newConversation() {
        clearPetra()
        if (listening) stopVoice()
        if (sessionId) await clearPdf()
        setTurns([])
        setAsked("")
        setReply(null)
        setAnswerError("")
        setFeedback("idle")
        setFeedbackOpen(false)
        setStatus("Klaar om te helpen")
    }
    useEffect(() => () => {
        // Leaving mid-recording: release the microphone without sending the clip.
        if (recorder.current) recorder.current.onstop = null
        if (recorder.current?.state === "recording") recorder.current.stop()
        recordingStream.current?.getTracks().forEach(track => track.stop())
        petraAbort.current?.abort()
        petraPlayer.current?.pause()
        if (petraAudioUrlRef.current) URL.revokeObjectURL(petraAudioUrlRef.current)
        window.speechSynthesis?.cancel()
    }, [])

    // Today's wholesale price for the greeting; the greeting simply leaves it out when this fails.
    useEffect(() => {
        if (!apiEndpoint.trim()) return
        let cancelled = false
        fetch(endpointFor("today"), { method: "GET" })
            .then((response) => response.ok ? response.json() : null)
            .then((data) => { if (!cancelled && typeof data?.now_cents === "number") setToday(data) })
            .catch(() => {})
        return () => { cancelled = true }
    }, [apiEndpoint])

    function endpointFor(path: "today" | "answer-stream" | "upload" | "session" | "petra-speech" | "transcribe" | "feedback"): string {
        const url = new URL(apiEndpoint.trim())
        url.pathname = url.pathname.replace(/\/api\/answer\/?$/, `/api/${path}`)
        if (!url.pathname.endsWith(`/api/${path}`)) {
            url.pathname = `${url.pathname.replace(/\/$/, "")}/api/${path}`
        }
        url.search = ""
        return url.toString().replace(/\/$/, "")
    }

    function clearPetra() {
        petraAbort.current?.abort()
        petraAbort.current = null
        petraPlayer.current?.pause()
        petraPlayer.current = null
        if (petraAudioUrlRef.current) URL.revokeObjectURL(petraAudioUrlRef.current)
        petraAudioUrlRef.current = ""
        setPetraAudioUrl("")
        setPetraBusy(false)
        setPetraError("")
        if (typeof window !== "undefined") window.speechSynthesis?.cancel()
        setBrowserSpeaking(false)
    }

    // Petra runs on a shared free GPU service with a daily limit; when it fails,
    // the browser's own voice reads the answer instead. Returns false without one.
    function speakWithBrowser(text: string): boolean {
        const synth = window.speechSynthesis
        if (!synth || typeof SpeechSynthesisUtterance === "undefined") return false
        const lang = SPEECH_LOCALES[answerLanguage(text)]
        const voices = synth.getVoices().filter((voice) => voice.lang?.toLowerCase().startsWith(lang.slice(0, 2)))
        // Prefer voices that run on the device, so the text stays in the browser.
        const voice = voices.find((item) => item.lang === lang && item.localService) || voices.find((item) => item.localService) || voices.find((item) => item.lang === lang) || voices[0]
        const utterance = new SpeechSynthesisUtterance(text.replace(/[*#_`]+/g, "").replace(/\s+/g, " ").trim())
        utterance.lang = lang
        if (voice) utterance.voice = voice
        utterance.onend = () => setBrowserSpeaking(false)
        utterance.onerror = () => setBrowserSpeaking(false)
        synth.cancel()
        synth.speak(utterance)
        setBrowserSpeaking(true)
        return true
    }

    async function playPetra(token: string, text: string) {
        clearPetra()
        const controller = new AbortController()
        petraAbort.current = controller
        setPetraBusy(true)
        try {
            const response = await fetch(endpointFor("petra-speech"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ token }),
                signal: controller.signal,
            })
            if (!response.ok) throw new Error(`Petra is niet beschikbaar (${response.status})`)
            const blob = await response.blob()
            if (controller.signal.aborted || !blob.type.includes("audio/")) return
            const url = URL.createObjectURL(blob)
            petraAudioUrlRef.current = url
            setPetraAudioUrl(url)
            const player = new Audio(url)
            petraPlayer.current = player
            try {
                await player.play()
            } catch {
                setPetraError("Druk op afspelen om Karen te horen.")
            }
        } catch {
            if (!controller.signal.aborted) {
                setPetraError(speakWithBrowser(text)
                    ? "Petra is nu niet beschikbaar, daarom leest de stem van je browser het antwoord voor."
                    : "Karens stem is nu niet beschikbaar. Je kunt het antwoord hierboven lezen.")
            }
        } finally {
            if (petraAbort.current === controller) {
                petraAbort.current = null
                setPetraBusy(false)
            }
        }
    }

    async function uploadPdf(file?: File) {
        if (!file) return
        clearPetra()
        if (listening) stopVoice()
        setVoiceDraft(false)
        setUploadError("")
        if (!apiEndpoint.trim()) {
            setUploadError("Verbind eerst Karen’s backend om je factuur te analyseren.")
            return
        }
        if (file.size > 15 * 1024 * 1024) {
            setUploadError("Kies een bestand kleiner dan 15 MB.")
            return
        }
        const previousSession = sessionId
        archiveCurrent()
        setUploading(true)
        setStatus(file.type.startsWith("image/") ? "Foto wordt op Karen’s backend gelezen…" : "PDF wordt op Karen’s backend gelezen…")
        if (previousSession) {
            void fetch(`${endpointFor("session")}/${encodeURIComponent(previousSession)}`, { method: "DELETE" }).catch(() => undefined)
        }
        try {
            const form = new FormData()
            form.set("file", file)
            const response = await fetch(endpointFor("upload"), { method: "POST", body: form })
            if (!response.ok) {
                const errorData = (await response.json().catch(() => null)) as { detail?: string } | null
                throw new Error(errorData?.detail || `Upload mislukt (${response.status})`)
            }
            const data = (await response.json()) as Reply & { session_id: string; filename: string; processing?: string }
            if (!data.session_id || !data.answer) throw new Error("De PDF-analyse ontbreekt in de API-respons")
            setSessionId(data.session_id)
            setUploadName(data.filename || file.name)
            setAsked(`Eerste analyse · ${data.filename || file.name}`)
            setReply(data)
            setStatus("Factuur verwerkt op Karen’s backend · geen externe modelverwerking")
        } catch (error) {
            setSessionId("")
            setUploadName("")
            setUploadError(error instanceof Error ? error.message : "Uploaden is mislukt")
            setStatus("PDF niet geanalyseerd")
        } finally {
            setUploading(false)
        }
    }

    async function clearPdf() {
        clearPetra()
        if (sessionId && apiEndpoint.trim()) {
            try {
                await fetch(`${endpointFor("session")}/${encodeURIComponent(sessionId)}`, { method: "DELETE" })
            } catch {
                // The server-side session also expires automatically.
            }
        }
        setSessionId("")
        setUploadName("")
        setUploadError("")
        setVoiceError("")
        setVoiceDraft(false)
        setReply(null)
        setAsked("")
        setStatus("PDF-sessie gewist")
    }

    async function ask(text: string) {
        const clean = text.trim()
        if (!clean || busy || uploading) return
        // Speak the answer automatically only when the question itself was spoken;
        // typed questions get a "Beluister Karen" button instead.
        const askedByVoice = voiceDraft
        // Every exchange stays in the chat; a PDF chat sends none of it along as history.
        const past = archiveCurrent()
        const history = sessionId
            ? []
            : past.slice(-SENT_TURNS).map((turn) => ({ question: turn.question, answer: turn.answer.slice(0, 800) }))
        clearPetra()
        if (listening) stopVoice()
        setAnswerError("")
        setAsked(clean)
        setQuestion("")
        setVoiceDraft(false)
        setReply(null)
        setFeedback("idle")
        setFeedbackOpen(false)
        setFeedbackComment("")
        setBusy(true)
        setDraft("")
        setSteps([])
        setStatus("Karen haalt relevante bronpassages op…")
        try {
            if (apiEndpoint.trim()) {
                const request = {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ question: clean, ...(sessionId ? { session_id: sessionId } : {}), ...(history.length ? { history } : {}) }),
                }
                let response = await fetch(endpointFor("answer-stream"), request)
                // A backend without the streaming route still answers on the plain one.
                if (response.status === 404 || response.status === 405) response = await fetch(apiEndpoint, request)
                if (!response.ok) {
                    const errorData = (await response.json().catch(() => null)) as { detail?: string } | null
                    throw new Error(errorData?.detail || `Verzoek mislukt (${response.status})`)
                }
                const streamed = Boolean(response.headers?.get?.("content-type")?.includes("ndjson") && response.body?.getReader)
                const data = streamed ? await readAnswerStream(response) : (await response.json()) as Reply
                if (!data.answer) throw new Error("Het antwoord ontbreekt in de API-respons")
                setReply(data)
                if (askedByVoice && !sessionId && data.processing !== "local-only") {
                    if (data.petra_speech_token) void playPetra(data.petra_speech_token, data.answer)
                    else setPetraError("Voor dit antwoord is geen audio beschikbaar. Je kunt het antwoord hierboven lezen.")
                }
                setStatus(data.processing === "local-only"
                    ? "Antwoord uit PDF-passage · op Karen’s backend verwerkt"
                    : data.processing === "general-knowledge"
                    ? "Antwoord klaar · algemene uitleg zonder bron"
                    : data.processing === "small-talk"
                    ? "Antwoord klaar"
                    : data.live_source_status === "failed"
                    ? "Antwoord klaar · live controle niet volledig bevestigd"
                        : data.sources?.length ? "Antwoord klaar · bewijs bijgevoegd" : "Antwoord klaar · geen bronfragment gevonden")
            } else {
                if (normalizeQuestion(clean) !== normalizeQuestion(DEMO_QUESTION)) {
                    throw new Error("Karen is hier nog niet verbonden met haar live backend. De voorbeeldmodus ondersteunt alleen de vraag over voorschot en jaarafrekening.")
                }
                await new Promise((resolve) => setTimeout(resolve, 800))
                setReply(demoReply)
                setStatus("Voorbeeldweergave · geen live AI-antwoord")
            }
        } catch (error) {
            setAnswerError(error instanceof Error ? error.message : "Karen kon de vraag niet beantwoorden.")
        } finally {
            setBusy(false)
            setDraft("")
        }
    }

    // Reads the streaming answer: JSON lines with text pieces, then the full reply or an error.
    async function readAnswerStream(response: Response): Promise<Reply> {
        const reader = response.body!.getReader()
        const decoder = new TextDecoder()
        let buffer = ""
        let text = ""
        for (;;) {
            const { value, done } = await reader.read()
            buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
            let newline = buffer.indexOf("\n")
            while (newline >= 0) {
                const line = buffer.slice(0, newline).trim()
                buffer = buffer.slice(newline + 1)
                newline = buffer.indexOf("\n")
                if (!line) continue
                const event = JSON.parse(line) as { delta?: string; reply?: Reply; error?: string; step?: Step }
                if (event.reply) return event.reply
                if (event.error) throw new Error(event.error)
                if (event.step) {
                    const step = event.step
                    setSteps((previous) => [...previous, step])
                    // A new search after text means the first pages did not answer: Karen starts over.
                    if (text) {
                        text = ""
                        setDraft("")
                    }
                }
                if (event.delta) {
                    text += event.delta
                    setDraft(text)
                }
            }
            if (done) throw new Error("Het antwoord werd onderbroken. Probeer het opnieuw.")
        }
    }

    async function sendFeedback(rating: "up" | "down", comment = "") {
        if (!reply?.feedback_token) return
        setFeedback("sending")
        try {
            const response = await fetch(endpointFor("feedback"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ feedback_token: reply.feedback_token, rating, ...(comment.trim() ? { comment: comment.trim() } : {}) }),
            })
            setFeedback(response.ok ? "sent" : "failed")
        } catch {
            setFeedback("failed")
        }
    }

    function startVoice() {
        setVoiceError("")
        setVoiceDraft(false)
        if (sessionId) {
            setVoiceError("Schakel de PDF-sessie uit voor je spreekt. Deel geen factuurgegevens via voice.")
            return
        }
        const speechWindow = window as SpeechWindow
        const SpeechRecognitionCtor = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition
        if (window.isSecureContext === false) {
            setVoiceError("Spraak werkt alleen op een beveiligde (https) pagina. Typ je vraag hieronder.")
            return
        }
        const canRecord = Boolean(apiEndpoint.trim() && navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined")
        // iOS only lets Safari use speech recognition; Chrome and other iPhone browsers
        // expose the API but every attempt fails, so record from the start there.
        if (!SpeechRecognitionCtor || (canRecord && isIosOtherBrowser(navigator.userAgent))) {
            // No usable built-in recognition (e.g. Chrome or the Google app on iPhone, Firefox):
            // record a short clip and let Karen's backend transcribe it instead.
            if (canRecord) {
                void startRecording()
            } else {
                setVoiceError(voiceUnsupportedMessage(navigator.userAgent))
            }
            return
        }
        clearPetra()
        const nextRecognition = new SpeechRecognitionCtor()
        nextRecognition.lang = SPEECH_LOCALES[voiceLanguage()]
        nextRecognition.interimResults = false
        nextRecognition.continuous = false
        let hasTranscript = false
        let recordInstead = false
        nextRecognition.onresult = (event) => {
            const transcript = event.results[0]?.[0]?.transcript?.trim()
            if (!transcript) {
                setVoiceError("De spraakdienst gaf geen herkende tekst terug. Typ je vraag of probeer opnieuw.")
                return
            }
            hasTranscript = true
            if (voiceTimeout.current !== null) window.clearTimeout(voiceTimeout.current)
            voiceTimeout.current = null
            nextRecognition.stop()
            setListening(false)
            setQuestion(transcript)
            setVoiceDraft(true)
            setStatus("Controleer de herkende vraag en druk op verzenden. Je kunt woorden zoals CREG eerst verbeteren.")
        }
        nextRecognition.onaudiostart = () => {
            setStatus("Microfoon actief · spreek je publieke vraag in…")
        }
        nextRecognition.onspeechstart = () => {
            setStatus("Spraak gehoord · Karen herkent je vraag…")
        }
        nextRecognition.onerror = (event) => {
            if (hasTranscript) return
            setListening(false)
            if (voiceTimeout.current !== null) window.clearTimeout(voiceTimeout.current)
            voiceTimeout.current = null
            // The browser's speech service itself failed (Chrome sends audio to Google,
            // which can be unreachable): record and transcribe on Karen's backend instead.
            if (canRecord && ["network", "service-not-allowed", "language-not-supported"].includes(event.error || "")) {
                recordInstead = true
                setStatus("De spraakdienst van je browser werkt niet · Karen neemt zelf op…")
                return
            }
            const messages: Record<string, string> = {
                "no-speech": "Geen spraak ontvangen. Spreek meteen na het indrukken van de microfoon.",
                "not-allowed": microphoneDeniedMessage(),
                "service-not-allowed": "De browser blokkeert zijn spraakherkenningsdienst voor deze pagina.",
                "audio-capture": "De browser vindt geen werkende microfoon. Controleer je invoerapparaat.",
                network: "De browser-spraakdienst is niet bereikbaar. Controleer je verbinding en probeer opnieuw.",
                "language-not-supported": `Deze browser-spraakdienst ondersteunt ${SPEECH_LOCALES[voiceLanguage()]} niet. Typ je vraag hieronder.`,
                aborted: "Spraakherkenning is gestopt. Typ je vraag of probeer opnieuw.",
            }
            setVoiceError(messages[event.error || ""] || `Spraakherkenning mislukte${event.error ? ` (${event.error})` : ""}. Typ je vraag of probeer opnieuw.`)
        }
        nextRecognition.onend = () => {
            setListening(false)
            if (voiceTimeout.current !== null) window.clearTimeout(voiceTimeout.current)
            voiceTimeout.current = null
            setRecognition(null)
            // Start only after recognition has fully ended, so its cleanup can't stop the recording.
            if (recordInstead) void startRecording()
        }
        try {
            setRecognition(nextRecognition)
            setListening(true)
            setStatus("Karen luistert… Spreek nu je publieke vraag in.")
            nextRecognition.start()
            voiceTimeout.current = window.setTimeout(() => {
                nextRecognition.stop()
                setListening(false)
                setVoiceError("Geen vraag herkend. Typ je vraag of probeer de microfoon opnieuw.")
            }, 15000)
        } catch {
            setListening(false)
            setRecognition(null)
            setVoiceError("De microfoon kon niet starten. Controleer de browsertoestemming.")
        }
    }

    async function startRecording() {
        clearPetra()
        let stream: MediaStream
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        } catch (error) {
            const name = (error as { name?: string })?.name
            setVoiceError(name === "NotAllowedError" || name === "SecurityError"
                ? microphoneDeniedMessage()
                : "De browser vindt geen werkende microfoon. Controleer je invoerapparaat of typ je vraag hieronder.")
            return
        }
        recordingStream.current = stream
        const mimeType = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm", "audio/ogg"].find(type => MediaRecorder.isTypeSupported?.(type))
        const nextRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
        const chunks: Blob[] = []
        nextRecorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data) }
        nextRecorder.onstop = () => {
            stream.getTracks().forEach(track => track.stop())
            recordingStream.current = null
            recorder.current = null
            setListening(false)
            if (voiceTimeout.current !== null) window.clearTimeout(voiceTimeout.current)
            voiceTimeout.current = null
            const type = (nextRecorder.mimeType || mimeType || "audio/webm").split(";")[0]
            void transcribeRecording(new Blob(chunks, { type }))
        }
        recorder.current = nextRecorder
        nextRecorder.start()
        setListening(true)
        setStatus("Karen neemt op… Stel je publieke vraag en tik op Stop.")
        // Keep clips short: the backend accepts about 30 seconds.
        voiceTimeout.current = window.setTimeout(() => { if (nextRecorder.state === "recording") nextRecorder.stop() }, 15000)
    }

    async function transcribeRecording(audio: Blob) {
        if (!audio.size) {
            setVoiceError("Er werd geen geluid opgenomen. Probeer opnieuw of typ je vraag.")
            return
        }
        setTranscribing(true)
        setStatus("Karen zet je opname om naar tekst…")
        try {
            const form = new FormData()
            const extension = audio.type.includes("mp4") ? "mp4" : audio.type.includes("ogg") ? "ogg" : "webm"
            form.append("file", audio, `question.${extension}`)
            form.append("language", voiceLanguage())
            const response = await fetch(endpointFor("transcribe"), { method: "POST", body: form })
            const payload = await response.json().catch(() => ({}))
            if (!response.ok || typeof payload.text !== "string" || !payload.text.trim()) {
                throw new Error(typeof payload.detail === "string" ? payload.detail : "")
            }
            setQuestion(payload.text.trim())
            setVoiceDraft(true)
            setStatus("Controleer de herkende vraag en druk op verzenden.")
        } catch (error) {
            setVoiceError((error as Error).message || "Je opname kon niet worden omgezet naar tekst. Typ je vraag of probeer opnieuw.")
        } finally {
            setTranscribing(false)
        }
    }

    function stopVoice() {
        if (recorder.current?.state === "recording") {
            recorder.current.stop()
            return
        }
        recognition?.stop()
        setListening(false)
        if (voiceTimeout.current !== null) window.clearTimeout(voiceTimeout.current)
        voiceTimeout.current = null
    }

    const sources = reply?.sources ?? []

    return (
        <div className="ea-shell" style={style}>
        <div className="ea">
            <style dangerouslySetInnerHTML={{ __html: css }} />
            <header className="ea-top">
                <div className="ea-brand"><img className="ea-mark" src={KAREN_LOGO} alt="Karen-logo" /><span><strong>EnergyAgent</strong><br />EEN HELDERE KIJK OP ENERGIE</span></div>
                {(turns.length > 0 || Boolean(asked)) && <button className="ea-new" type="button" onClick={() => void newConversation()} disabled={busy || uploading}>Nieuw gesprek</button>}
            </header>
            <div className="ea-body">
            <section className="ea-main">
                <div className="ea-thread" aria-live="polite" ref={thread}>
                    {!asked && !turns.length && <div className="ea-hello">
                        <img className="ea-hello-face" src={KAREN_LOGO} alt="" />
                        <div className="ea-hello-bubble">
                            <h1>Hoi, ik ben Karen.</h1>
                            <p>Vraag me alles over je energiefactuur, je contract of de prijzen. Ik toon je altijd waar mijn antwoord vandaan komt.</p>
                            {today && <p className="ea-today">Stroom op de groothandelsmarkt kost nu <b>{formatCents(today.now_cents)} cent/kWh</b>{today.cheapest_hour !== null && today.cheapest_hour !== today.hour ? `, het goedkoopst vandaag om ${today.cheapest_hour}u.` : "."}</p>}
                        </div>
                    </div>}
                    {!asked && !turns.length && <div className="ea-chips">
                        {[sampleQuestion, "Wat kost stroom vandaag?", "Hoe check ik mijn energiecontract?"].map((text) => <button key={text} className="ea-chip" onClick={() => void ask(text)}>{text}</button>)}
                    </div>}
                    {turns.map((turn, index) => <div className="ea-turn" key={index}><div className="ea-question">{turn.question}</div><div className="ea-past-answer">{turn.visual?.kind === "prices" && turn.visual.groups?.length ? <PriceChart visual={turn.visual} /> : <FormattedAnswer text={turn.answer} />}</div></div>)}
                    {asked && <div className="ea-question">{asked}</div>}
                    {busy && steps.length > 0 && <KarenFlow steps={steps} writing={Boolean(draft)} />}
                    {(busy || uploading || listening || transcribing) && !draft && !(busy && steps.length > 0) && <div className="ea-state"><span className="ea-dot" />{status}</div>}
                    {!busy && steps.length > 0 && reply && <p className="ea-flow-done">{stepSummary(steps)}</p>}
                    {busy && draft && <div className="ea-answer"><FormattedAnswer text={draft} /></div>}
                    {reply && <><div className="ea-answer">{reply.visual?.kind === "prices" && reply.visual.groups?.length ? <PriceChart visual={reply.visual} /> : <FormattedAnswer text={reply.answer} />}{reply.processing === "general-knowledge" && <p className="ea-tag">Algemene uitleg · geen bron gevonden</p>}
                        {(sources.length > 0 || Boolean(reply.suggested_sources?.length)) && <div className="ea-sources" id="ea-sources">
                            {reply.live_source_status === "failed" && <p className="ea-foot">De live controle is niet volledig bevestigd.</p>}
                            {sources.length > 0 && <div className="ea-source-row"><span>Bronnen</span>{sources.filter((source, index) => sources.findIndex((other) => (other.url || other.title) === (source.url || source.title)) === index).map((source, index) => source.url
                                ? <a className="ea-source" key={`${source.title}-${index}`} href={source.url} target="_blank" rel="noreferrer" title={source.detail || source.title}>{source.title} ↗</a>
                                : <span className="ea-source" key={`${source.title}-${index}`} title={source.detail || ""}>{source.title}</span>)}</div>}
                            {sources.some((source) => source.excerpt) && <details className="ea-passages"><summary>Toon de gebruikte passages</summary>{sources.filter((source) => source.excerpt).map((source, index) => <p className="ea-excerpt" key={`excerpt-${index}`}>“{source.excerpt}”<small>{source.title}</small></p>)}</details>}
                            {Boolean(reply.suggested_sources?.length) && <div className="ea-source-row"><span>Zelf raadplegen</span>{reply.suggested_sources?.map((source, index) => <a className="ea-source ea-source-more" key={`suggested-${index}`} href={source.url} target="_blank" rel="noreferrer" title={source.detail}>{source.title} ↗</a>)}</div>}
                        </div>}
                    </div>
                    {!apiEndpoint.trim() && <div className="ea-foot">Gesimuleerd antwoord om de interface te bekijken.</div>}
                    <div className="ea-actions">
                        {reply.petra_speech_token && !sessionId && !petraAudioUrl && !browserSpeaking && (petraBusy ? <p className="ea-foot" role="status">Audio wordt voorbereid…</p> : <button className="ea-voice-play" onClick={() => void playPetra(reply.petra_speech_token!, reply.answer)}>Beluister Karen</button>)}
                        {browserSpeaking && <button className="ea-voice-play" onClick={() => clearPetra()}>Stop voorlezen</button>}
                        {reply.feedback_token && apiEndpoint.trim() && !sessionId && (feedback === "sent"
                            ? <p className="ea-foot" role="status">Bedankt! Karen leert van je feedback.</p>
                            : !feedbackOpen && <div className="ea-feedback">
                                <button aria-label="Nuttig antwoord" title="Nuttig" disabled={feedback === "sending"} onClick={() => void sendFeedback("up")}>👍</button>
                                <button aria-label="Niet nuttig" title="Niet nuttig" disabled={feedback === "sending"} onClick={() => setFeedbackOpen(true)}>👎</button>
                            </div>)}
                    </div>
                    {reply.feedback_token && apiEndpoint.trim() && !sessionId && feedback !== "sent" && feedbackOpen && <div className="ea-feedback ea-feedback-open">
                        <textarea aria-label="Wat ging er mis?" placeholder="Wat ging er mis? (optioneel, geen persoonlijke gegevens)" value={feedbackComment} maxLength={500} onChange={(event) => setFeedbackComment(event.target.value)} />
                        <button disabled={feedback === "sending"} onClick={() => void sendFeedback("down", feedbackComment)}>Verstuur feedback</button>
                    </div>}
                    {feedback === "failed" && <p className="ea-foot" role="status">Feedback versturen lukte niet. Probeer het later opnieuw.</p>}
                    {petraAudioUrl && <audio className="ea-audio" controls src={petraAudioUrl} />}
                    {petraError && <p className="ea-foot" role="status">{petraError}</p>}</>}
                </div>
                <div className="ea-composer">
                {uploadName && <p className="ea-pill">📄 {uploadName} · Karen leest mee<button type="button" onClick={() => void clearPdf()} disabled={uploading || busy}>Verwijder</button></p>}
                {uploadError && <p className="ea-foot" role="alert" style={{ color: "#a13333" }}>{uploadError}</p>}
                {voiceError && <p className="ea-foot" role="alert" style={{ color: "#a13333" }}>{voiceError}</p>}
                {voiceDraft && <p className="ea-foot" role="status">Controleer de herkende vraag hieronder. Je kunt die aanpassen voordat Karen zoekt.</p>}
                <div className="ea-controls">
                    <label className="ea-attach" title="Voeg je factuur toe (PDF of foto)" data-disabled={uploading || busy || listening}>
                        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 11.5 12.6 19.9a5.5 5.5 0 0 1-7.8-7.8l8.5-8.5a3.7 3.7 0 0 1 5.2 5.2l-8.5 8.5a1.8 1.8 0 0 1-2.6-2.6l7.8-7.8" /></svg>
                        <input type="file" accept="application/pdf,.pdf,image/jpeg,image/png,image/webp" aria-label="Upload je energiefactuur als PDF of foto" disabled={uploading || busy || listening} onChange={(event) => { void uploadPdf(event.target.files?.[0]); event.target.value = "" }} />
                    </label>
                    <input className="ea-input" value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => event.key === "Enter" && void ask(question)} placeholder={uploadName ? "Vraag iets over je factuur…" : "Vraag aan Karen…"} aria-label="Vraag aan Karen" disabled={busy || uploading} />
                    <button className="ea-mic" type="button" data-listening={listening} onClick={() => listening ? stopVoice() : startVoice()} disabled={busy || uploading || transcribing || Boolean(sessionId)} aria-label={listening ? "Stop luisteren" : "Stel je vraag met je stem"} title={listening ? "Stop luisteren" : "Stel je vraag met je stem"}>
                        {listening ? <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" /></svg> : <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" /></svg>}
                    </button>
                    <button className="ea-button" onClick={() => void ask(question)} disabled={busy || uploading || !question.trim()} aria-label="Verstuur vraag">↗</button>
                </div>
                {answerError && <p className="ea-foot" role="alert" style={{ color: "#a13333" }}>{answerError}</p>}
                <details className="ea-privacy">
                    <summary>Vragen worden anoniem bewaard om Karen te verbeteren. <span>Meer over privacy</span></summary>
                    <p className="ea-foot">Je browser herkent je gesproken vraag. Heeft je browser geen eigen spraakherkenning (zoals Chrome op iPhone), dan gaat een korte opname naar Karen’s backend en zet OpenAI die om naar tekst; de opname wordt niet bewaard. Controleer de tekst voor je die verstuurt; Karen verwerkt en beantwoordt de vraag. Voor aanbiedingen deelt Karen je gewest, postcode, energietype, jaarverbruik en eventueel expliciet opgegeven meter- en prosumentgegevens met Browser Use en de officiële vergelijker; deze invoer blijft maximaal 60 minuten in sessiegeheugen. Deel geen factuurgegevens via voice. Voice is uitgeschakeld bij een actieve PDF. Publieke energievragen gebruiken Gemma (of een OpenAI-model als Gemma overbelast is) en zo nodig officiële webbronnen via Browserbase. Na een gesproken vraag leest Karen haar publieke antwoord voor; bij een getypte vraag doet ze dat pas als je op “Beluister Karen” tikt. Alleen daarvoor gaat de antwoordtekst naar de Babbeldoos-stemservice, met de Vlaamse Petra-stem; PDF-antwoorden blijven op Karen’s backend. Is Petra niet beschikbaar, dan leest de stem van je browser het antwoord voor. Je gesprek staat in deze browser, en je vorige vragen en antwoorden gaan mee zodat Karen vervolgvragen begrijpt; met “Nieuw gesprek” wis je het. Om Karen te verbeteren bewaart de beheerder publieke vragen en antwoorden zonder naam of account; e-mailadressen, rekening- en EAN-nummers in een vraag worden eerst onleesbaar gemaakt. Vragen over je eigen factuur worden niet bewaard. Een factuur (PDF of foto, max. 15 MB) leest Karen op haar eigen backend, zonder extern model; de tekst blijft maximaal 60 minuten in sessiegeheugen.</p>
                </details>
                </div>
            </section>
            </div>
        </div>
        </div>
    )
}

addPropertyControls(EnergyAgentConversation, {
    apiEndpoint: { title: "Backend URL", type: ControlType.String, placeholder: "https://…", defaultValue: "" },
    sampleQuestion: { title: "Voorbeeldvraag", type: ControlType.String, defaultValue: DEMO_QUESTION },
})
