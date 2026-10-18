// Run with NODE_PATH pointing to temporary react@18, react-dom@18, jsdom@26 and esbuild dependencies.
// No browser speech service, backend, or paid comparator is contacted.
const assert = require("node:assert/strict")
const path = require("node:path")
const vm = require("node:vm")
const { JSDOM } = require("jsdom")
const { buildSync } = require("esbuild")

const dom = new JSDOM('<div id="root"></div>', { url: "https://preview.example/" })
Object.assign(globalThis, {
    window: dom.window, document: dom.window.document,
    HTMLElement: dom.window.HTMLElement, FormData: dom.window.FormData,
    IS_REACT_ACT_ENVIRONMENT: true,
})
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator })
const React = require("react")
const { act } = React
const { createRoot } = require("react-dom/client")
const spoken = []
globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text } }
window.speechSynthesis = { getVoices: () => [{ lang: "nl-BE" }], cancel() {}, speak: utterance => spoken.push(utterance.text) }
let recognizer
window.SpeechRecognition = class {
    constructor() { recognizer = this }
    start() {}
    stop() { this.stopCalls = (this.stopCalls || 0) + 1; this.onend?.() }
}
// Each component file is compiled on its own, the way Framer stores code files;
// relative imports between them are resolved here.
function loadComponent(file) {
    const source = require("node:fs").readFileSync(path.join(__dirname, "../energyagent-karen-prototype", file), "utf8")
    const compiled = buildSync({
        stdin: { contents: source, loader: "tsx", resolveDir: path.join(__dirname, "../energyagent-karen-prototype") },
        bundle: false, write: false, format: "cjs", jsx: "automatic",
    }).outputFiles[0].text
    const componentModule = { exports: {} }
    vm.runInNewContext(compiled, {
        module: componentModule, exports: componentModule.exports,
        require: name => name === "framer" ? { addPropertyControls() {}, ControlType: { String: "string" } }
            : name.startsWith("./") ? loadComponent(name.slice(2)) : require(name),
        window, document, console, URL, fetch: (...args) => globalThis.fetch(...args), FormData, AbortController,
        setTimeout, SpeechSynthesisUtterance,
    })
    return componentModule.exports
}
const Component = loadComponent("EnergyAgentConversation.tsx").default
const marketFixture = require("./fixtures/market.json")
const root = createRoot(document.querySelector("#root"))
const requests = []
const responses = []
globalThis.fetch = async (url, options) => {
    requests.push({ url, method: options.method, body: typeof options.body === "string" ? JSON.parse(options.body) : options.body })
    if (options.method === "DELETE") return { ok: true, json: async () => ({ deleted: true }) }
    assert.ok(responses.length, "Unexpected request")
    const reply = responses.shift()
    return { ok: true, json: async () => reply }
}
const token = "structured-comparison-session"
const collecting = { answer: "In welk gewest woon je?", comparison_status: "collecting", comparison_session_id: token, sources: [] }
const success = {
    answer: "V-test toont dit aanbod.\n\n1. Voorbeeld · Variabel: € 1.234,56 per jaar. Gecontroleerd op 28-09-2026 om 14:00 CEST.",
    comparison_status: "success", comparison_session_id: null, live_source_status: "checked",
    sources: [{ title: "Voorbeeld · Variabel", url: "https://www.vlaamsenutsregulator.be/results", detail: "V-test · gecontroleerd 28-09-2026" }],
}
const mic = () => document.querySelector(".ea-mic")
async function typed(question) {
    const input = document.querySelector(".ea-input")
    await act(async () => {
        Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(input, question)
        input.dispatchEvent(new window.Event("input", { bubbles: true }))
    })
    await act(async () => document.querySelector('[aria-label="Verstuur vraag"]').click())
}
async function voice(question) {
    assert.equal(mic().disabled, false, "Comparison must keep voice available between turns")
    await act(async () => mic().click())
    await act(async () => {
        recognizer.onresult({ results: [[{ transcript: question }]] })
        recognizer.onend?.()
    })
    assert.equal(recognizer.stopCalls, 1, "Recognition must stop once a transcript arrives")
    // The transcript is a draft: it lands in the input for review and is only sent on confirm.
    assert.equal(document.querySelector(".ea-input").value, question)
    await act(async () => document.querySelector('[aria-label="Verstuur vraag"]').click())
}
async function main() {
    await act(async () => root.render(React.createElement(Component, { apiEndpoint: "https://backend.example/api/answer", sampleQuestion: "sample" })))
    responses.push(collecting)
    await typed("Vergelijk energieaanbiedingen")
    assert.deepEqual(requests.at(-1).body, { question: "Vergelijk energieaanbiedingen" })
    assert.equal(mic().disabled, false)
    responses.push({ ...collecting, answer: "Wat is je postcode?" })
    await voice("Vlaanderen")
    assert.deepEqual(requests.at(-1).body, { question: "Vlaanderen", comparison_session_id: token })
    responses.push({ ...collecting, answer: "Ben je prosument? Antwoord ja of nee.", required_information: ["is_prosumer"] })
    await voice("Postcode 9000, elektriciteit, jaarlijks 3500 kWh")
    assert.equal(requests.at(-1).body.comparison_session_id, token)
    assert.equal(mic().disabled, false)
    responses.push(success)
    await voice("nee")
    assert.equal(requests.at(-1).body.comparison_session_id, token)
    assert.equal(document.querySelector(".ea-answer").textContent, success.answer)
    assert.equal(document.querySelector(".ea-rail a").href, success.sources[0].url)
    assert.equal(spoken.length, 0, "Browser speech synthesis is no longer used; Petra plays only on request")
    assert.equal(mic().disabled, false)
    responses.push({ answer: "FAQ antwoord", comparison_session_id: null })
    await typed("Waarom verschilt mijn voorschot?")
    assert.equal(requests.at(-1).body.comparison_session_id, undefined)
    responses.push(collecting)
    await typed("Vergelijk gas")
    responses.push({ answer: "Lokale PDF-analyse", session_id: "separate-pdf-session", filename: "bill.pdf", processing: "local-only" })
    await act(async () => {
        const input = document.querySelector('input[type="file"]')
        Object.defineProperty(input, "files", { configurable: true, value: [new window.File(["synthetic"], "bill.pdf", { type: "application/pdf" })] })
        input.dispatchEvent(new window.Event("change", { bubbles: true }))
    })
    assert.ok(requests.some(r => r.method === "DELETE" && r.url.endsWith(`/api/comparison-session/${token}`)))
    assert.equal(mic().disabled, true, "PDF session must disable external voice")
    responses.push({ answer: "Verwijder eerst je PDF", comparison_status: "pdf-blocked", comparison_session_id: null, processing: "local-only" })
    await typed("Vergelijk gas")
    assert.deepEqual(requests.at(-1).body, { question: "Vergelijk gas", session_id: "separate-pdf-session" })
    await act(async () => [...document.querySelectorAll("button")].find(button => button.textContent === "Verwijder").click())
    assert.equal(mic().disabled, false)

    // Prijzen tab: fetches market.json once, shows today's stats, keeps the chat state.
    const tab = name => [...document.querySelectorAll('[role="tab"]')].find(button => button.textContent === name)
    responses.push({ answer: "Antwoord voor de tabwissel", comparison_session_id: null })
    await typed("Wat is een capaciteitstarief?")
    const answerBefore = document.querySelector(".ea-answer").textContent
    responses.push(marketFixture)
    await act(async () => tab("Prijzen").click())
    await act(async () => {})
    assert.match(requests.at(-1).url, /raw\.githubusercontent\.com\/pascal-maker\/karen-energy-agent\/market-data\/market\.json$/)
    assert.equal(document.querySelector(".ea-body").hidden, true)
    assert.match(document.querySelector(".em").textContent, /Stroomprijs vandaag/)
    assert.match(document.querySelector(".em").textContent, /Energy-Charts/)
    assert.ok(!document.querySelector(".em-badge"), "Fresh data has no stale badge")
    await act(async () => [...document.querySelectorAll(".em-switch button")].find(b => b.textContent === "Morgen").click())
    assert.match(document.querySelector(".em").textContent, /Stroomprijs morgen/)
    await act(async () => tab("Verwachting").click())
    assert.match(document.querySelector(".em").textContent, /eerdere momenten/)
    await act(async () => tab("Historiek").click())
    assert.match(document.querySelector(".em").textContent, /sinds 2019/)
    await act(async () => tab("Chat").click())
    assert.equal(document.querySelector(".ea-answer").textContent, answerBefore, "Switching tabs keeps the conversation")

    // Stale data is badged; an unreachable data file shows the official comparators instead of numbers.
    const Dashboard = loadComponent("EnergyMarketDashboard.tsx").default
    const box = document.createElement("div")
    document.body.appendChild(box)
    const dashboardRoot = createRoot(box)
    responses.push({ ...marketFixture, spot: { ...marketFixture.spot, stale: true } })
    await act(async () => dashboardRoot.render(React.createElement(Dashboard, { dataUrl: "https://data.example/stale.json" })))
    await act(async () => {})
    assert.equal(box.querySelector(".em-badge").textContent, "Verouderd")
    const realFetch = globalThis.fetch
    globalThis.fetch = async () => ({ ok: false, status: 404, json: async () => ({}) })
    await act(async () => dashboardRoot.render(React.createElement(Dashboard, { dataUrl: "https://data.example/missing.json" })))
    await act(async () => {})
    globalThis.fetch = realFetch
    assert.match(box.textContent, /Prijzen niet beschikbaar/)
    assert.ok(!/c€\/kWh/.test(box.textContent), "No prices are shown when the data is unavailable")
    assert.ok(box.querySelector('a[href="https://www.vtest.be/"]'))
    await act(async () => dashboardRoot.unmount())
    await act(async () => root.unmount())
    dom.window.close()
    console.log("Karen component: typed/voice session continuity, source cards, voice drafts, terminal reset, PDF isolation and Prijzen tab passed.")
}
main().catch(error => { console.error(error); process.exitCode = 1; dom.window.close() })
