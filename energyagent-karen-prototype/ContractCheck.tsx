import { useEffect, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react"
import { addPropertyControls, ControlType } from "framer"

// Karen's contract check: your bill or three answers in, your yearly saving out.
// Everything is computed on Karen's backend (POST /api/contract-check) from today's verified tariff cards,
// without a language model. This component only asks, uploads and shows.

type Option = { supplier: string; product: string; label: string; contract_type: string }
type Evidence = { page: number; source: string; excerpt: string; field: string }
type Offer = {
    supplier: string; product: string | null; label: string; contract_type: string; duration_months: number | null
    price_per_kwh_eur: string; fixed_fee_eur_per_year: string; yearly_eur: string; source_url: string | null; is_current: boolean
    price_day_eur?: string; price_night_eur?: string
}
type Warning = { code: string; message: string }
type EnergyResult = {
    status: "compared" | "already_cheapest" | "offers_only" | "not_comparable"; reason: string | null; message: string
    usage_kwh: string | null
    day_share?: string
    tariffs_as_of?: string
    current: { supplier: string | null; product: string | null; yearly_eur: string; price_per_kwh_eur: string; fixed_fee_eur_per_year: string; price_source: string; evidence: Evidence[] } | null
    offers: Offer[]; best: Offer | null; best_by_type: { variable: Offer | null; fixed: Offer | null }
    saving_eur_per_year: string | null; saving_eur_per_month: string | null; warnings: Warning[]
}
type Source = { title: string; url?: string; excerpt?: string }
type CheckResult = {
    based_on: "invoice" | "answers"; tariffs_as_of: string | null; region: string | null
    energies: Partial<Record<Energy, EnergyResult>>
    total: { saving_eur_per_year: string; saving_eur_per_month: string } | null
    excluded: string; switching: { text: string; sources: Source[] }
    // The official check next to Karen's: the V-check code printed on Flemish bills since July 2025.
    v_check?: { url: string; codes: { code: string; energy: Energy | null; page: number; usage_kwh: string | null }[] } | null
}
type Energy = "electricity" | "gas"
type EnergyForm = { usage: string; product: string }

// VREG household averages for electricity, as Karen uses them in the chat.
const HOUSEHOLDS: [string, number][] = [["2 personen", 2350], ["3 personen", 3200], ["4 personen", 4000]]
const ENERGY_NAME: Record<Energy, string> = { electricity: "Elektriciteit", gas: "Gas" }
const UNKNOWN = ""

export function contractEndpoint(apiEndpoint: string, path: "contract-check" | "contract-options" | "upload" | "session"): string {
    const url = new URL(apiEndpoint.trim())
    url.pathname = url.pathname.replace(/\/api\/[\w-]+\/?$/, "").replace(/\/$/, "") + `/api/${path}`
    url.search = ""
    return url.toString()
}

const euro = (value: string | number, digits = 0) =>
    Number(value).toLocaleString("nl-BE", { style: "currency", currency: "EUR", minimumFractionDigits: digits, maximumFractionDigits: digits })
const cents = (value: string) => `${(Number(value) * 100).toLocaleString("nl-BE", { maximumFractionDigits: 2 })} c€/kWh`

function contractKind(offer: Offer): string {
    if (offer.contract_type !== "fixed") return "variabel"
    if (!offer.duration_months) return "vast"
    return offer.duration_months % 12 === 0 ? `${offer.duration_months / 12} jaar vast` : `${offer.duration_months} mnd vast`
}

// "- **Kosten:** gratis" → a list item with the bold part bold; nothing else of markdown is needed here.
function richText(text: string): ReactNode {
    const lines = text.split("\n").filter(Boolean)
    const inline = (line: string) => line.split(/(\*\*[^*]+\*\*)/).map((part, index) =>
        part.startsWith("**") ? <strong key={index}>{part.slice(2, -2)}</strong> : part)
    const items = lines.filter(line => line.startsWith("- "))
    return (
        <>
            {lines.filter(line => !line.startsWith("- ")).map((line, index) => <p key={index}>{inline(line)}</p>)}
            {items.length > 0 && <ul>{items.map((line, index) => <li key={index}>{inline(line.slice(2))}</li>)}</ul>}
        </>
    )
}

/**
 * @framerSupportedLayoutWidth any
 * @framerSupportedLayoutHeight auto
 * @framerIntrinsicWidth 1000
 * @framerIntrinsicHeight 760
 */
export default function ContractCheck({ apiEndpoint = "" }: { apiEndpoint: string }) {
    const [options, setOptions] = useState<Record<Energy, Option[]>>({ electricity: [], gas: [] })
    const [postcode, setPostcode] = useState("")
    const [meter, setMeter] = useState("single")
    const [form, setForm] = useState<Record<Energy, EnergyForm>>({ electricity: { usage: "", product: UNKNOWN }, gas: { usage: "", product: UNKNOWN } })
    const [withGas, setWithGas] = useState(false)
    // kWh typed for a bill that lacks them, kept apart from the form: answers from an earlier check never
    // replace what Karen read from the bill.
    const [billUsage, setBillUsage] = useState<Record<Energy, string>>({ electricity: "", gas: "" })
    const [sessionId, setSessionId] = useState("")
    const [filename, setFilename] = useState("")
    const [busy, setBusy] = useState("")
    const [error, setError] = useState("")
    const [result, setResult] = useState<CheckResult | null>(null)
    const configured = Boolean(apiEndpoint.trim())

    useEffect(() => {
        if (!configured) return
        let active = true
        fetch(contractEndpoint(apiEndpoint, "contract-options"))
            .then(response => (response.ok ? response.json() : null))
            .then(data => { if (active && data?.options) setOptions(data.options) })
            .catch(() => {})
        return () => { active = false }
    }, [apiEndpoint, configured])

    const setEnergy = (energy: Energy, field: keyof EnergyForm, value: string) =>
        setForm(current => ({ ...current, [energy]: { ...current[energy], [field]: value } }))

    function energyAnswer(energy: Energy) {
        const chosen = options[energy].find(option => option.label === form[energy].product)
        const usage = Number(form[energy].usage.replace(/[.\s]/g, ""))
        return {
            ...(usage > 0 ? { usage_kwh: Math.round(usage) } : {}),
            ...(chosen ? { supplier: chosen.supplier, product: chosen.product } : {}),
        }
    }

    async function check(body: Record<string, unknown>) {
        setBusy("Karen rekent…")
        setError("")
        try {
            const response = await fetch(contractEndpoint(apiEndpoint, "contract-check"), {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
            })
            const data = await response.json().catch(() => null)
            if (!response.ok || !data?.energies) throw new Error(data?.detail || "Karen kon nu niet rekenen. Probeer het zo opnieuw.")
            setResult(data as CheckResult)
        } catch (failure) {
            setError((failure as Error).message)
        } finally {
            setBusy("")
        }
    }

    function submitAnswers(event: FormEvent) {
        event.preventDefault()
        if (!/^\d{4}$/.test(postcode)) return setError("Vul je postcode in (4 cijfers).")
        if (!(Number(form.electricity.usage.replace(/[.\s]/g, "")) > 0)) return setError("Vul je jaarverbruik in, of kies je gezinsgrootte.")
        void check({
            postcode,
            ...(meter ? { meter } : {}),
            electricity: energyAnswer("electricity"),
            ...(withGas ? { gas: energyAnswer("gas") } : {}),
        })
    }

    async function upload(event: ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0]
        event.target.value = ""
        if (!file) return
        setBusy("Karen leest je factuur…")
        setError("")
        setBillUsage({ electricity: "", gas: "" })
        try {
            const data = new FormData()
            data.append("file", file)
            const response = await fetch(contractEndpoint(apiEndpoint, "upload"), { method: "POST", body: data })
            const payload = await response.json().catch(() => null)
            if (!response.ok || !payload?.session_id) throw new Error(payload?.detail || "Karen kon deze factuur niet lezen.")
            setSessionId(payload.session_id)
            setFilename(payload.filename || file.name)
            await check({ session_id: payload.session_id })
        } catch (failure) {
            setError((failure as Error).message)
            setBusy("")
        }
    }

    // A bill without yearly kWh (an advance bill, gas in m³): ask for the number and check again. Only kWh
    // typed for this bill are sent, so the usage Karen read from the bill stays as it was.
    function recheckWithUsage(energy: Energy) {
        const kwh = (name: Energy) => Number(billUsage[name].replace(/[.\s]/g, ""))
        if (!(kwh(energy) > 0)) return setError("Vul je jaarverbruik in kWh in.")
        const typed = Object.fromEntries((["electricity", "gas"] as Energy[])
            .filter(name => kwh(name) > 0)
            .map(name => [name, { usage_kwh: Math.round(kwh(name)) }]))
        void check({ session_id: sessionId, ...typed })
    }

    function restart() {
        if (sessionId) void fetch(contractEndpoint(apiEndpoint, "session") + `/${encodeURIComponent(sessionId)}`, { method: "DELETE" }).catch(() => {})
        setSessionId("")
        setFilename("")
        setBillUsage({ electricity: "", gas: "" })
        setResult(null)
        setError("")
    }

    function productSelect(energy: Energy) {
        const bySupplier = options[energy].reduce<Record<string, Option[]>>((groups, option) => {
            ;(groups[option.supplier] ||= []).push(option)
            return groups
        }, {})
        return (
            <label className="cc-field">
                <span>Je huidige contract</span>
                <select value={form[energy].product} onChange={event => setEnergy(energy, "product", event.target.value)}>
                    <option value={UNKNOWN}>Weet ik niet / ander contract</option>
                    {Object.entries(bySupplier).map(([supplier, group]) => (
                        <optgroup key={supplier} label={supplier}>
                            {group.map(option => <option key={option.label} value={option.label}>{option.label}</option>)}
                        </optgroup>
                    ))}
                </select>
            </label>
        )
    }

    function energyCard(energy: Energy, entry: EnergyResult) {
        const best = entry.best
        // The cards are for a single meter: when the meter isn't known, say so with the saving, not below it.
        const meterCaveat = entry.warnings.find(warning => warning.code === "meter_unknown")
        const otherWarnings = entry.warnings.filter(warning => warning !== meterCaveat)
        const other = entry.best_by_type && best
            ? entry.best_by_type[best.contract_type === "fixed" ? "variable" : "fixed"]
            : null
        const needsUsage = sessionId && (entry.reason === "no_usage" || entry.reason === "gas_in_m3")
        return (
            <section className="cc-card" key={energy} aria-label={ENERGY_NAME[energy]}>
                <h3>{ENERGY_NAME[energy]}{entry.usage_kwh && <small> · {Number(entry.usage_kwh).toLocaleString("nl-BE")} kWh per jaar{
                    entry.day_share ? `, ${Math.round(Number(entry.day_share) * 100)}% overdag` : ""}</small>}</h3>
                {entry.status === "compared" && entry.current && best && (
                    <div className="cc-compare">
                        <div className="cc-now">
                            <span>Nu</span>
                            <strong>{euro(entry.current.yearly_eur)}</strong>
                            <small>{[entry.current.supplier, entry.current.product].filter(Boolean).join(" · ") || "je huidige contract"}</small>
                        </div>
                        <div className="cc-best">
                            <span>Goedkoopste</span>
                            <strong>{euro(best.yearly_eur)}</strong>
                            <small>{best.label} · {contractKind(best)}</small>
                        </div>
                        <p className="cc-saving">Je bespaart <strong>{euro(entry.saving_eur_per_year!)}</strong> per jaar ({euro(entry.saving_eur_per_month!, 2)} per maand).</p>
                    </div>
                )}
                {meterCaveat && entry.status !== "not_comparable" && <p className="cc-caveat" role="note">{meterCaveat.message}</p>}
                {entry.status !== "compared" && <p className="cc-message">{entry.message}</p>}
                {entry.status === "offers_only" && best && (
                    <p className="cc-message">Goedkoopste bij jouw verbruik: <strong>{best.label}</strong> ({contractKind(best)}), {euro(best.yearly_eur)} per jaar.</p>
                )}
                {other && other.label !== best?.label && (
                    <p className="cc-alt">Liever {contractKind(other).includes("vast") ? "vast" : "variabel"}? {other.label}: {euro(other.yearly_eur)} per jaar.</p>
                )}
                {needsUsage && (
                    <div className="cc-inline">
                        <input inputMode="numeric" placeholder="kWh per jaar" aria-label={`${ENERGY_NAME[energy]}verbruik in kWh per jaar`}
                            value={billUsage[energy]} onChange={event => setBillUsage(current => ({ ...current, [energy]: event.target.value }))} />
                        <button type="button" onClick={() => recheckWithUsage(energy)}>Opnieuw berekenen</button>
                    </div>
                )}
                {entry.offers.length > 0 && (
                    <details>
                        <summary>Alle {entry.offers.length} geverifieerde aanbiedingen</summary>
                        <ol className="cc-offers">
                            {entry.offers.map(offer => (
                                <li key={offer.label} data-current={offer.is_current || undefined}>
                                    <span>{offer.source_url ? <a href={offer.source_url} target="_blank" rel="noreferrer">{offer.label} ↗</a> : offer.label}
                                        <small>{contractKind(offer)} · {offer.price_day_eur && offer.price_night_eur
                                            ? `dag ${cents(offer.price_day_eur)}, nacht ${cents(offer.price_night_eur)}`
                                            : cents(offer.price_per_kwh_eur)} + {euro(offer.fixed_fee_eur_per_year)} vast{offer.is_current ? " · jouw contract" : ""}</small></span>
                                    <strong>{euro(offer.yearly_eur)}</strong>
                                </li>
                            ))}
                        </ol>
                    </details>
                )}
                {entry.current?.evidence?.length ? (
                    <details>
                        <summary>Waar Karen dit las</summary>
                        <ul className="cc-evidence">{entry.current.evidence.map((item, index) => (
                            <li key={index}>{item.page ? `Pagina ${item.page}: ` : ""}„{item.field}“</li>
                        ))}</ul>
                    </details>
                ) : null}
                {otherWarnings.length > 0 && <ul className="cc-warnings">{otherWarnings.map(warning => <li key={warning.code}>{warning.message}</li>)}</ul>}
            </section>
        )
    }

    if (!configured) {
        return <div className="cc"><style dangerouslySetInnerHTML={{ __html: css }} /><p className="cc-message">Vul in Framer de Backend URL in.</p></div>
    }

    return (
        <div className="cc">
            <style dangerouslySetInnerHTML={{ __html: css }} />
            <header>
                <h2>Betaal je te veel voor je energie?</h2>
                <p>Laad je factuur op of beantwoord drie vragen. Karen vergelijkt je contract met de geverifieerde tariefkaarten van vandaag.</p>
            </header>

            {!result && (
                <div className="cc-start">
                    <label className="cc-upload" data-busy={busy ? "true" : undefined}>
                        <input type="file" accept="application/pdf,image/jpeg,image/png" onChange={upload} disabled={Boolean(busy)} aria-label="Factuur opladen" />
                        <strong>Laad je jaarafrekening op</strong>
                        <span>PDF of foto, max. 15 MB · Karen leest hem op haar eigen server</span>
                    </label>
                    <p className="cc-or">of</p>
                    <form className="cc-form" onSubmit={submitAnswers}>
                        <label className="cc-field">
                            <span>Postcode</span>
                            <input inputMode="numeric" maxLength={4} value={postcode} onChange={event => setPostcode(event.target.value.replace(/\D/g, ""))} placeholder="9000" />
                        </label>
                        <label className="cc-field">
                            <span>Elektriciteitsverbruik per jaar</span>
                            <input inputMode="numeric" value={form.electricity.usage} onChange={event => setEnergy("electricity", "usage", event.target.value)} placeholder="kWh" />
                        </label>
                        <div className="cc-chips" aria-label="Gemiddeld verbruik per gezinsgrootte (VREG)">
                            {HOUSEHOLDS.map(([label, kwh]) => (
                                <button type="button" key={label} onClick={() => setEnergy("electricity", "usage", String(kwh))}>{label} ≈ {kwh.toLocaleString("nl-BE")} kWh</button>
                            ))}
                        </div>
                        <label className="cc-field">
                            <span>Meter</span>
                            <select value={meter} onChange={event => setMeter(event.target.value)}>
                                <option value="single">Enkelvoudige meter</option>
                                <option value="day_night">Dag/nachtmeter</option>
                                <option value="">Weet ik niet</option>
                            </select>
                        </label>
                        {productSelect("electricity")}
                        <label className="cc-check"><input type="checkbox" checked={withGas} onChange={event => setWithGas(event.target.checked)} /> Ik heb ook aardgas</label>
                        {withGas && (
                            <>
                                <label className="cc-field">
                                    <span>Gasverbruik per jaar</span>
                                    <input inputMode="numeric" value={form.gas.usage} onChange={event => setEnergy("gas", "usage", event.target.value)} placeholder="kWh (staat op je jaarafrekening)" />
                                </label>
                                {productSelect("gas")}
                            </>
                        )}
                        <button className="cc-submit" type="submit" disabled={Boolean(busy)}>Bereken mijn besparing</button>
                    </form>
                    {/* What really happens with a bill: matches the upload session's 60-minute expiry in energy_voice_app.py. */}
                    <p className="cc-privacy">Je factuur wordt niet als bestand opgeslagen en niet gelogd. De gelezen tekst blijft maximaal 60 minuten in het geheugen van Karen’s server voor deze berekening; met “Opnieuw beginnen” wis je hem meteen. Alleen als de beheerder de taalmodelhulp aanzet en Karen niet alles vindt, gaan passages rond bedragen en datums naar een OpenAI-model, met namen, adressen en nummers vervangen door [X].</p>
                </div>
            )}

            {busy && <p className="cc-busy" role="status">{busy}</p>}
            {error && <p className="cc-error" role="alert">{error}</p>}

            {result && (
                <div className="cc-result">
                    {result.total ? (
                        <p className="cc-headline">Je kan ongeveer <strong>{euro(result.total.saving_eur_per_year)}</strong> per jaar besparen, dat is {euro(result.total.saving_eur_per_month, 2)} per maand{
                            Object.values(result.energies).some(entry => entry?.warnings.some(warning => warning.code === "meter_unknown")) ? ", bij een enkelvoudige meter" : ""}.</p>
                    ) : (
                        <p className="cc-headline">Dit is wat Karen kon vergelijken.</p>
                    )}
                    {filename && <p className="cc-based">Op basis van je factuur {filename}.</p>}
                    <div className="cc-cards">
                        {(["electricity", "gas"] as Energy[]).map(energy => result.energies[energy] && energyCard(energy, result.energies[energy]!))}
                    </div>
                    {result.v_check && result.v_check.codes.length > 0 && (
                        <section className="cc-vcheck">
                            <h3>Controleer het ook officieel</h3>
                            <p>De V-check van de Vlaamse Nutsregulator vergelijkt je contract met het marktaanbod. Vul de code van je factuur in en geef daarna in de V-test je eigen verbruik in: dan vergelijkt hij op jouw verbruik in plaats van op een gemiddeld gezin.</p>
                            <ul>{result.v_check.codes.map(item => (
                                <li key={item.code}>
                                    <code>{item.code.replace(/(.{4})(?=.)/g, "$1 ")}</code>
                                    {item.energy && <span> · {ENERGY_NAME[item.energy]}</span>}
                                    {item.usage_kwh && <span> · vul {Number(item.usage_kwh).toLocaleString("nl-BE")} kWh in</span>}
                                </li>
                            ))}</ul>
                            <a className="cc-vcheck-link" href={result.v_check.url} target="_blank" rel="noreferrer">Open de V-check ↗</a>
                        </section>
                    )}
                    <section className="cc-switch">
                        <h3>Zo stap je over</h3>
                        {richText(result.switching.text)}
                        <p className="cc-sources">{result.switching.sources.map(source => source.url
                            ? <a key={source.url + source.excerpt} href={source.url} target="_blank" rel="noreferrer">{source.title} ↗</a> : null)}</p>
                    </section>
                    <p className="cc-note">{result.excluded} Tariefkaarten {(["electricity", "gas"] as Energy[])
                        .filter(energy => result.energies[energy]?.tariffs_as_of)
                        .map(energy => `${ENERGY_NAME[energy].toLowerCase()}: ${new Date(result.energies[energy]!.tariffs_as_of!).toLocaleDateString("nl-BE", { day: "numeric", month: "long", year: "numeric" })}`)
                        .join(", ") || `van ${result.tariffs_as_of ?? "deze maand"}`}, energieprijs incl. 6% btw.</p>
                    <button type="button" className="cc-again" onClick={restart}>Opnieuw beginnen</button>
                </div>
            )}
        </div>
    )
}

const css = `
  .cc { box-sizing:border-box; width:100%; max-width:1000px; margin:0 auto; padding:24px 18px; color:#1e2b23; background:#f6f7f3; font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif; font-size:14px; line-height:1.5; }
  .cc * { box-sizing:border-box; }
  .cc header h2 { margin:0; font-size:26px; letter-spacing:-.02em; }
  .cc header p { margin:6px 0 18px; color:#4f6156; }
  .cc-start { display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1.4fr); gap:18px; align-items:start; }
  .cc-upload { position:relative; display:grid; gap:6px; place-items:center; min-height:180px; padding:20px; border:2px dashed #b8cfbb; border-radius:18px; background:#fff; text-align:center; cursor:pointer; }
  .cc-upload:hover { border-color:#3f7a57; background:#f2f7ef; }
  .cc-upload input { position:absolute; inset:0; opacity:0; cursor:pointer; }
  .cc-upload span { color:#56705f; font-size:12px; }
  .cc-or { align-self:center; margin:0; color:#7d8b82; }
  .cc-privacy { grid-column:1 / -1; margin:0; color:#7d8b82; font-size:12px; }
  .cc-form { display:grid; gap:12px; padding:18px; border:1px solid #e3ebe2; border-radius:18px; background:#fff; }
  .cc-field { display:grid; gap:4px; }
  .cc-field span { color:#4f6156; font-size:12px; font-weight:600; }
  .cc-field input, .cc-field select, .cc-inline input { min-height:42px; padding:0 10px; border:1px solid #dfe5de; border-radius:10px; color:#1e2b23; background:#fff; font:inherit; font-size:16px; }
  .cc-chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:-6px; }
  .cc-chips button { padding:5px 10px; border:1px solid #d6e3d4; border-radius:999px; color:#234f38; background:#fff; font:inherit; font-size:12px; cursor:pointer; }
  .cc-check { display:flex; align-items:center; gap:8px; }
  .cc-submit, .cc-inline button, .cc-again { min-height:44px; padding:0 18px; border:0; border-radius:12px; color:#fff; background:#234f38; font:inherit; font-weight:600; cursor:pointer; }
  .cc-submit:disabled { opacity:.6; cursor:progress; }
  .cc-busy { color:#234f38; }
  .cc-error { padding:10px 12px; border-radius:10px; color:#7a2a20; background:#fbecea; }
  .cc-headline { margin:0 0 6px; font-size:22px; letter-spacing:-.01em; }
  .cc-headline strong { color:#1f7a45; }
  .cc-based { margin:0 0 12px; color:#56705f; font-size:12px; }
  .cc-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; margin:12px 0; }
  .cc-card { padding:18px; border:1px solid #e3ebe2; border-radius:18px; background:#fff; }
  .cc-card h3, .cc-switch h3 { margin:0 0 12px; font-size:16px; }
  .cc-card h3 small { color:#7d8b82; font-weight:400; }
  .cc-compare { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .cc-now, .cc-best { display:grid; gap:2px; padding:12px; border-radius:12px; background:#f6f7f3; }
  .cc-best { background:#eaf5ec; }
  .cc-now span, .cc-best span { color:#56705f; font-size:12px; }
  .cc-now strong, .cc-best strong { font-size:22px; }
  .cc-now small, .cc-best small { color:#4f6156; font-size:12px; }
  .cc-saving { grid-column:1 / -1; margin:4px 0 0; }
  .cc-saving strong { color:#1f7a45; }
  .cc-message, .cc-alt { margin:8px 0 0; color:#4f6156; }
  .cc-caveat { margin:10px 0 0; padding:8px 10px; border-radius:10px; color:#6b4a12; background:#fdf4e3; font-size:13px; }
  .cc-inline { display:flex; gap:8px; margin-top:10px; }
  .cc-inline input { flex:1; min-width:0; }
  .cc details { margin-top:12px; }
  .cc summary { color:#234f38; cursor:pointer; }
  .cc-offers { display:grid; gap:6px; margin:8px 0 0; padding:0; list-style:none; }
  .cc-offers li { display:flex; justify-content:space-between; gap:12px; padding:8px 0; border-bottom:1px solid #eef2ec; }
  .cc-offers li[data-current] { color:#7d8b82; }
  .cc-offers small { display:block; color:#728076; font-size:12px; }
  .cc-offers a { color:#234f38; }
  .cc-evidence, .cc-warnings { margin:8px 0 0; padding-left:18px; color:#56705f; font-size:12px; }
  .cc-switch { padding:18px; border:1px solid #e3ebe2; border-radius:18px; background:#fff; }
  .cc-switch p, .cc-switch ul { margin:0 0 8px; }
  .cc-sources { display:flex; flex-wrap:wrap; gap:6px; }
  .cc-sources a { padding:3px 9px; border:1px solid #d6e3d4; border-radius:999px; color:#234f38; font-size:11px; text-decoration:none; }
  .cc-note { color:#7d8b82; font-size:12px; }
  .cc-vcheck { margin:0 0 14px; padding:18px; border:1px solid #cfe0f0; border-radius:18px; background:#f3f8fc; }
  .cc-vcheck h3 { margin:0 0 8px; font-size:16px; }
  .cc-vcheck p { margin:0 0 8px; color:#4f6156; }
  .cc-vcheck ul { margin:0 0 10px; padding-left:18px; }
  .cc-vcheck code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:15px; letter-spacing:.04em; }
  .cc-vcheck-link { display:inline-block; padding:8px 14px; border-radius:10px; color:#fff; background:#235a8a; text-decoration:none; font-weight:600; }
  @media (max-width:720px) {
    .cc { padding:16px 12px; }
    .cc header h2 { font-size:22px; }
    .cc-start { grid-template-columns:1fr; }
    .cc-or { justify-self:center; }
    .cc-compare { grid-template-columns:1fr; }
  }
`

addPropertyControls(ContractCheck, {
    apiEndpoint: { title: "Backend URL", type: ControlType.String, placeholder: "https://…", defaultValue: "" },
})
