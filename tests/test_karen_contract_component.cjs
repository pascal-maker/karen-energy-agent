// Run with NODE_PATH pointing to temporary react@18, react-dom@18, jsdom@26 and esbuild dependencies.
// The contract check screen: three answers or an uploaded bill in, the saving out. No backend is contacted.
const assert = require("node:assert/strict")
const path = require("node:path")
const vm = require("node:vm")
const { JSDOM } = require("jsdom")
const { buildSync } = require("esbuild")

const dom = new JSDOM('<div id="root"></div>', { url: "https://preview.example/" })
Object.assign(globalThis, {
    window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement,
    FormData: dom.window.FormData, IS_REACT_ACT_ENVIRONMENT: true,
})
const React = require("react")
const { act } = React
const { createRoot } = require("react-dom/client")

const source = require("node:fs").readFileSync(path.join(__dirname, "../energyagent-karen-prototype/ContractCheck.tsx"), "utf8")
const compiled = buildSync({ stdin: { contents: source, loader: "tsx" }, bundle: false, write: false, format: "cjs", jsx: "automatic" }).outputFiles[0].text
const componentModule = { exports: {} }
vm.runInNewContext(compiled, {
    module: componentModule, exports: componentModule.exports,
    require: name => name === "framer" ? { addPropertyControls() {}, ControlType: { String: "string" } } : require(name),
    window, document, console, URL, FormData, fetch: (...args) => globalThis.fetch(...args), encodeURIComponent,
})
const { default: ContractCheck, contractEndpoint } = componentModule.exports

const requests = []
const responses = []
globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, method: options.method || "GET", body: typeof options.body === "string" ? JSON.parse(options.body) : options.body })
    if (options.method === "DELETE") return { ok: true, json: async () => ({ deleted: true }) }
    assert.ok(responses.length, `Unexpected request ${url}`)
    const reply = responses.shift()
    return { ok: reply.status ? reply.status < 400 : true, json: async () => reply.body ?? reply }
}

const OPTIONS = { tariffs_as_of: "2026-10-19", options: {
    electricity: [
        { supplier: "Alfa", product: "Alfa Flex", label: "Alfa Flex", contract_type: "variable" },
        { supplier: "Beta", product: "Beta Vast", label: "Beta Vast", contract_type: "fixed" },
    ],
    gas: [{ supplier: "Alfa", product: "Alfa Gas", label: "Alfa Gas", contract_type: "variable" }],
} }
const offer = (supplier, label, type, months, yearly, current = false) => ({
    supplier, product: label, label, contract_type: type, duration_months: months, price_per_kwh_eur: "0.19",
    fixed_fee_eur_per_year: "25.00", yearly_eur: yearly, source_url: `https://example.test/${supplier}`, is_current: current,
})
const SWITCHING = { text: "Je nieuwe leverancier regelt alles.\n- **Kosten:** gratis.", sources: [{ title: "Vlaanderen.be", url: "https://www.vlaanderen.be/x", excerpt: "gratis" }] }
const COMPARED = {
    based_on: "answers", tariffs_as_of: "2026-10-19", region: "flanders", excluded: "Netkosten zitten er niet in.", switching: SWITCHING,
    total: { saving_eur_per_year: "129.00", saving_eur_per_month: "10.75" },
    energies: { electricity: {
        status: "compared", reason: null, message: "Vergeleken.", usage_kwh: "3200",
        current: { supplier: "Alfa", product: "Alfa Flex", yearly_eur: "633.00", price_per_kwh_eur: "0.19", fixed_fee_eur_per_year: "25.00", price_source: "tariff_card", evidence: [] },
        offers: [offer("Beta", "Beta Vast", "fixed", 36, "504.00"), offer("Alfa", "Alfa Flex", "variable", null, "633.00", true)],
        best: offer("Beta", "Beta Vast", "fixed", 36, "504.00"),
        best_by_type: { variable: null, fixed: offer("Beta", "Beta Vast", "fixed", 36, "504.00") },
        saving_eur_per_year: "129.00", saving_eur_per_month: "10.75",
        warnings: [{ code: "price_from_chosen_product", message: "Je prijs komt van de tariefkaart." }],
    } },
}

const root = createRoot(document.querySelector("#root"))
const $ = selector => document.querySelector(selector)
const text = () => document.body.textContent
async function type(element, value) {
    const proto = element.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype
    await act(async () => {
        Object.getOwnPropertyDescriptor(proto, "value").set.call(element, value)
        element.dispatchEvent(new window.Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }))
    })
}
const inputs = () => [...document.querySelectorAll(".cc-form input")]
const button = label => [...document.querySelectorAll("button")].find(item => item.textContent.includes(label))

async function main() {
    // The endpoint is derived from whatever the Backend URL property holds.
    assert.equal(contractEndpoint("https://karen.example/api/answer", "contract-check"), "https://karen.example/api/contract-check")
    assert.equal(contractEndpoint("https://karen.example", "upload"), "https://karen.example/api/upload")

    responses.push(OPTIONS)
    await act(async () => root.render(React.createElement(ContractCheck, { apiEndpoint: "https://karen.example/api/answer" })))
    await act(async () => {})
    assert.match(requests[0].url, /\/api\/contract-options$/)
    assert.ok([...document.querySelectorAll("optgroup")].map(group => group.label).includes("Beta"), "Products are grouped by supplier")
    // Retention is said as it is: kept up to 60 minutes in memory for the check, never "not kept".
    assert.ok(!/niet bewaard/.test(text()), "No blanket 'niet bewaard'")
    assert.match($(".cc-privacy").textContent, /maximaal 60 minuten/)

    // Missing answers are asked for before anything is sent.
    await act(async () => $(".cc-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })))
    assert.match($('[role="alert"]').textContent, /postcode/)
    assert.equal(requests.length, 1)

    // Three answers: postcode, a household-size chip for the usage, and the current product.
    await type(inputs()[0], "9000")
    await act(async () => button("3 personen").click())
    assert.equal(inputs()[1].value, "3200")
    await type(document.querySelectorAll(".cc-form select")[1], "Alfa Flex")
    responses.push(COMPARED)
    await act(async () => $(".cc-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })))
    await act(async () => {})
    assert.deepEqual(requests.at(-1).body, { postcode: "9000", meter: "single", electricity: { usage_kwh: 3200, supplier: "Alfa", product: "Alfa Flex" } })
    assert.match($(".cc-headline").textContent, /129/)
    assert.match($(".cc-best").textContent, /Beta Vast/)
    assert.match($(".cc-best").textContent, /3 jaar vast/)
    assert.match($(".cc-saving").textContent, /10,75/)
    assert.equal($(".cc-switch strong").textContent, "Kosten:", "Switching steps keep their bold labels")
    assert.ok($('.cc-offers li[data-current]').textContent.includes("jouw contract"))
    assert.match($(".cc-offers li").textContent, /· 19 c€\/kWh/, "A single-meter offer shows one price")
    assert.ok(!/dag .*nacht/.test($(".cc-offers li").textContent), "No day/night split for a single meter")
    assert.match(text(), /Je prijs komt van de tariefkaart/)
    assert.equal($(".cc-vcheck"), null, "No V-check block without a code")

    // An uploaded bill without yearly usage: Karen asks for the kWh and checks the same session again.
    await act(async () => button("Opnieuw beginnen").click())
    assert.ok($(".cc-form"), "Back to the start")
    responses.push({ session_id: "session-1234567890abcdef", filename: "factuur.pdf" })
    responses.push({ ...COMPARED, based_on: "invoice", total: null, energies: { electricity: {
        ...COMPARED.energies.electricity, status: "not_comparable", reason: "no_usage", message: "Deze factuur vermeldt geen jaarverbruik.",
        usage_kwh: null, current: null, offers: [], best: null, best_by_type: { variable: null, fixed: null },
        saving_eur_per_year: null, saving_eur_per_month: null, warnings: [],
    } } })
    const file = $('input[type="file"]')
    Object.defineProperty(file, "files", { configurable: true, value: [new window.File(["%PDF"], "factuur.pdf", { type: "application/pdf" })] })
    await act(async () => file.dispatchEvent(new window.Event("change", { bubbles: true })))
    await act(async () => {})
    assert.match(requests.at(-2).url, /\/api\/upload$/)
    assert.deepEqual(requests.at(-1).body, { session_id: "session-1234567890abcdef" })
    assert.match(text(), /geen jaarverbruik/)
    assert.match($(".cc-based").textContent, /factuur\.pdf/)
    await type($(".cc-inline input"), "3.500")
    responses.push({ ...COMPARED, based_on: "invoice", v_check: {
        url: "https://www.vtest.be/v-check", codes: [{ code: "KNGP250400030125", energy: "electricity", page: 1, usage_kwh: "3500" }],
    } })
    await act(async () => $(".cc-inline button").click())
    await act(async () => {})
    assert.deepEqual(requests.at(-1).body, { session_id: "session-1234567890abcdef", electricity: { usage_kwh: 3500 } })
    assert.match($(".cc-headline").textContent, /129/)
    // The official check next to Karen's: the code from the bill and the kWh to type in the V-test.
    assert.equal($(".cc-vcheck code").textContent, "KNGP 2504 0003 0125")
    assert.match($(".cc-vcheck li").textContent, /Elektriciteit · vul 3\.500 kWh in/)
    assert.equal($(".cc-vcheck-link").href, "https://www.vtest.be/v-check")

    // Starting over deletes the bill's session on the backend.
    await act(async () => button("Opnieuw beginnen").click())
    assert.ok(requests.some(request => request.method === "DELETE" && request.url.endsWith("/api/session/session-1234567890abcdef")))

    // Review of 9480de0: a combined bill with electricity read and gas missing. The form still holds 3200 kWh
    // from the first check; only the gas kWh typed for this bill may be sent, never that old answer.
    assert.ok(inputs()[1].value, "An earlier answer is still in the form")
    const gasMissing = { ...COMPARED, based_on: "invoice", energies: {
        electricity: { ...COMPARED.energies.electricity, usage_kwh: "3500", warnings: [] },
        gas: { ...COMPARED.energies.electricity, status: "not_comparable", reason: "no_usage", message: "Geen gasverbruik op deze factuur.",
            usage_kwh: null, current: null, offers: [], best: null, best_by_type: { variable: null, fixed: null },
            saving_eur_per_year: null, saving_eur_per_month: null, warnings: [] },
    } }
    responses.push({ session_id: "session-combined-0000000000", filename: "gecombineerd.pdf" })
    responses.push(gasMissing)
    const freshFile = $('input[type="file"]')  // the start screen was drawn again
    Object.defineProperty(freshFile, "files", { configurable: true, value: [new window.File(["%PDF"], "gecombineerd.pdf", { type: "application/pdf" })] })
    await act(async () => freshFile.dispatchEvent(new window.Event("change", { bubbles: true })))
    await act(async () => {})
    assert.equal(document.querySelectorAll(".cc-inline input").length, 1, "Only the gas card asks for kWh")
    await type($(".cc-inline input"), "10.000")
    responses.push(gasMissing)
    await act(async () => $(".cc-inline button").click())
    await act(async () => {})
    assert.deepEqual(requests.at(-1).body, { session_id: "session-combined-0000000000", gas: { usage_kwh: 10000 } })
    await act(async () => button("Opnieuw beginnen").click())

    // Meter unknown: the saving holds for a single meter only, and the screen says so with the number.
    await type(document.querySelectorAll(".cc-form select")[0], "")
    responses.push({ ...COMPARED, energies: { electricity: { ...COMPARED.energies.electricity, warnings: [
        { code: "meter_unknown", message: "Dit geldt voor een enkelvoudige meter." },
        { code: "price_from_chosen_product", message: "Je prijs komt van de tariefkaart." },
    ] } } })
    await act(async () => $(".cc-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })))
    await act(async () => {})
    assert.equal(requests.at(-1).body.meter, undefined, "'Weet ik niet' sends no meter")
    assert.match($(".cc-headline").textContent, /bij een enkelvoudige meter/)
    assert.match($(".cc-caveat").textContent, /enkelvoudige meter/)
    assert.ok(![...document.querySelectorAll(".cc-warnings li")].some(li => /enkelvoudige meter/.test(li.textContent)), "Said once, next to the saving")
    await act(async () => button("Opnieuw beginnen").click())

    // Backend errors are shown as they come.
    await type(inputs()[0], "1050")
    await type(inputs()[1], "2600")
    responses.push({ status: 503, body: { detail: "Karen kon nu niet rekenen." } })
    await act(async () => $(".cc-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })))
    await act(async () => {})
    assert.match($('[role="alert"]').textContent, /niet rekenen/)

    await act(async () => root.unmount())
    console.log("Contract check screen: options, three answers, upload with missing usage, restart and errors passed.")
}

main().catch(error => { console.error(error); process.exit(1) })
