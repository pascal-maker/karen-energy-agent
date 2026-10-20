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
        navigator: { get userAgent() { return globalThis.testUserAgent || "jsdom" }, get mediaDevices() { return globalThis.testMediaDevices },
            get languages() { return globalThis.testLanguages || ["nl-BE"] }, get language() { return (globalThis.testLanguages || ["nl-BE"])[0] } },
        get MediaRecorder() { return globalThis.MediaRecorder }, Blob: window.Blob,
        setTimeout, SpeechSynthesisUtterance, TextDecoder,
    })
    return componentModule.exports
}
const Component = loadComponent("EnergyAgentConversation.tsx").default
const root = createRoot(document.querySelector("#root"))
const requests = []
const responses = []
globalThis.fetch = async (url, options) => {
    requests.push({ url, method: options.method, body: typeof options.body === "string" ? JSON.parse(options.body) : options.body })
    if (options.method === "DELETE") return { ok: true, json: async () => ({ deleted: true }) }
    assert.ok(responses.length, "Unexpected request")
    const reply = responses.shift()
    if (reply.stream) {
        // A streaming answer: JSON lines arrive in pieces that need not end on a line break.
        const encode = events => new TextEncoder().encode(events.map(event => JSON.stringify(event)).join("\n") + "\n")
        const first = encode(reply.stream)
        const pieces = [first.slice(0, 9), first.slice(9, 31), first.slice(31)]
        return { ok: true, status: 200, headers: { get: () => "application/x-ndjson" }, body: { getReader: () => ({
            read: async () => {
                if (pieces.length) return { value: pieces.shift(), done: false }
                if (reply.later) {
                    // Hold the rest back until the test has looked at the half-written answer.
                    await new Promise(resolve => { globalThis.releaseStream = resolve })
                    const rest = encode(reply.later)
                    reply.later = null
                    return { value: rest, done: false }
                }
                return { value: undefined, done: true }
            },
        }) } }
    }
    if (reply.status) return { ok: false, status: reply.status, json: async () => ({ detail: "Petra is nu niet beschikbaar." }) }
    if (typeof reply.blob === "function") return { ok: true, blob: reply.blob }
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
    assert.equal(recognizer.lang, "nl-BE", "Speech is Dutch when the browser lists Dutch")
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
    assert.equal(spoken.length, 0, "The browser voice is only a fallback when Petra fails")
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

    // Voice blocked: a denied microphone inside a frame names the frame, not a generic error.
    await act(async () => mic().click())
    await act(async () => recognizer.onerror({ error: "not-allowed" }))
    assert.match(document.querySelector('[role="alert"]').textContent, /Microfoontoegang is geweigerd|ingesloten venster/)
    await act(async () => recognizer.onend?.())

    // Petra speaks automatically only after a spoken question; a typed one shows a button.
    const petraCalls = () => requests.filter(r => r.url.endsWith("/api/petra-speech")).length
    const petraBefore = petraCalls()
    responses.push({ answer: "Getypt antwoord", comparison_session_id: null, petra_speech_token: "typed-token" })
    await typed("Wat is het capaciteitstarief?")
    assert.equal(petraCalls(), petraBefore, "Typed question: no automatic voice")
    assert.ok([...document.querySelectorAll("button")].some(b => b.textContent === "Beluister Karen"), "Typed question offers the button")

    // Public answers can be rated; 👎 asks what went wrong and sends it with the signed token.
    responses.push({ answer: "Prijsantwoord", comparison_session_id: null, feedback_token: "signed-feedback-token" })
    await typed("wat kost stroom vandaag?")
    await act(async () => document.querySelector('[aria-label="Niet nuttig"]').click())
    await act(async () => {
        const box = document.querySelector('[aria-label="Wat ging er mis?"]')
        Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set.call(box, "Geen gasprijs")
        box.dispatchEvent(new window.Event("input", { bubbles: true }))
    })
    responses.push({ stored: true })
    await act(async () => [...document.querySelectorAll("button")].find(b => b.textContent === "Verstuur feedback").click())
    assert.equal(requests.at(-1).url, "https://backend.example/api/feedback")
    assert.deepEqual(requests.at(-1).body, { feedback_token: "signed-feedback-token", rating: "down", comment: "Geen gasprijs" })
    assert.match(document.querySelector(".ea-chat, #root").textContent, /Bedankt! Karen leert van je feedback\./)
    // Streaming: the text shows while Karen writes it, then the full reply replaces it.
    responses.push({ stream: [{ delta: "Het capaciteits" }, { delta: "tarief rekent " }], later: [{ delta: "je piek aan." }, { reply: { answer: "Het capaciteitstarief rekent je piek aan.", comparison_session_id: null, sources: [] } }] })
    await typed("Wat is het capaciteitstarief?")
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
    assert.ok(requests.at(-1).url.endsWith("/api/answer-stream"))
    assert.equal(document.querySelector(".ea-answer").textContent, "Het capaciteitstarief rekent ", "Text shows while Karen writes")
    assert.equal(document.querySelector(".ea-state"), null, "No status line once text is showing")
    await act(async () => { globalThis.releaseStream(); await new Promise(resolve => setTimeout(resolve, 20)) })
    assert.equal(document.querySelector(".ea-answer").textContent, "Het capaciteitstarief rekent je piek aan.")
    // An error line ends the stream with Karen's message.
    responses.push({ stream: [{ delta: "Half" }, { error: "Gemma is tijdelijk overbelast of de API-limiet is bereikt.", status: 503 }] })
    await typed("Wat is het sociaal tarief?")
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
    assert.match(document.querySelector("#root").textContent, /Gemma is tijdelijk overbelast/)
    // An older backend without the streaming route: fall back to the plain answer route.
    responses.push({ status: 404 })
    responses.push({ answer: "Antwoord van de oude route", comparison_session_id: null })
    await typed("Wat is een voorschot?")
    assert.ok(requests.at(-1).url.endsWith("/api/answer"))
    assert.equal(document.querySelector(".ea-answer").textContent, "Antwoord van de oude route")
    responses.push({ answer: "Zonder token", comparison_session_id: null })
    await typed("Hoe werkt het capaciteitstarief?")
    assert.equal(document.querySelector(".ea-feedback"), null, "No rating without a feedback token")
    responses.push({ answer: "Gesproken antwoord", comparison_session_id: null, petra_speech_token: "voice-token" })
    responses.push({ blob: async () => new window.Blob(["RIFF"], { type: "audio/wav" }) })
    await voice("Wat is het sociaal tarief?")
    await act(async () => {})
    assert.equal(petraCalls(), petraBefore + 1, "Spoken question: answer is read aloud")
    assert.deepEqual(requests.at(-1).body, { token: "voice-token" })

    // Petra's daily GPU quota can run out: the browser's own voice reads the answer instead.
    responses.push({ answer: "Het sociaal tarief is een korting.", comparison_session_id: null, petra_speech_token: "busy-token" })
    responses.push({ status: 503 })
    await voice("Wat is het sociaal tarief?")
    await act(async () => {})
    assert.equal(spoken.at(-1), "Het sociaal tarief is een korting.")
    assert.match(document.querySelector(".ea-chat, #root").textContent, /Petra is nu niet beschikbaar, daarom leest/)
    const stop = [...document.querySelectorAll(".ea-voice-play")].find(button => button.textContent === "Stop voorlezen")
    assert.ok(stop, "A stop button shows while the browser voice reads")
    await act(async () => stop.click())
    assert.equal([...document.querySelectorAll(".ea-voice-play")].some(button => button.textContent === "Stop voorlezen"), false)

    // No built-in speech recognition (e.g. Chrome on iPhone): record a clip, the backend
    // transcribes it, and the text lands in the input as a draft to check first.
    const savedRecognition = window.SpeechRecognition
    delete window.SpeechRecognition
    let tracksStopped = 0
    globalThis.testMediaDevices = { getUserMedia: async () => ({ getTracks: () => [{ stop() { tracksStopped += 1 } }] }) }
    globalThis.MediaRecorder = class {
        static isTypeSupported(type) { return type === "audio/mp4" }
        constructor(stream, options) { this.mimeType = options?.mimeType || ""; this.state = "inactive" }
        start() { this.state = "recording" }
        stop() { this.state = "inactive"; this.ondataavailable({ data: new window.Blob(["clip"], { type: "audio/mp4" }) }); this.onstop() }
    }
    globalThis.testLanguages = ["en-US", "fr-BE"]
    responses.push({ text: "Wat kost stroom vandaag?" })
    await act(async () => mic().click())
    await act(async () => {})
    assert.equal(mic().getAttribute("data-listening"), "true", "Recording is shown as listening")
    await act(async () => mic().click())
    await act(async () => {})
    assert.match(requests.at(-1).url, /\/api\/transcribe$/)
    assert.equal(requests.at(-1).body.get("file").name, "question.mp4")
    assert.equal(requests.at(-1).body.get("language"), "fr", "French beats English; the recording's language goes along")
    globalThis.testLanguages = undefined
    assert.equal(document.querySelector(".ea-input").value, "Wat kost stroom vandaag?", "Transcript is a draft, not sent")
    assert.equal(tracksStopped, 1, "Microphone is released after recording")
    const clearInput = () => act(async () => {
        Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(document.querySelector(".ea-input"), "")
        document.querySelector(".ea-input").dispatchEvent(new window.Event("input", { bubbles: true }))
    })
    await clearInput()

    // Chrome's own speech service fails (it sends audio to Google): Karen records instead
    // of showing an error.
    window.SpeechRecognition = savedRecognition
    responses.push({ text: "Hoe hoog is mijn voorschot?" })
    await act(async () => mic().click())
    await act(async () => recognizer.onerror({ error: "network" }))
    assert.ok(![...document.querySelectorAll('[role="alert"]')].some(a => /niet bereikbaar/.test(a.textContent)), "A failing speech service is not shown as an error")
    await act(async () => recognizer.onend?.())
    await act(async () => {})
    assert.equal(mic().getAttribute("data-listening"), "true", "Karen records after the speech service failed")
    await act(async () => mic().click())
    await act(async () => {})
    assert.match(requests.at(-1).url, /\/api\/transcribe$/)
    assert.equal(document.querySelector(".ea-input").value, "Hoe hoog is mijn voorschot?")
    await clearInput()

    // Chrome on iPhone exposes the API but iOS blocks it there: record straight away.
    recognizer = undefined
    globalThis.testUserAgent = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/130.0 Mobile/15E148"
    responses.push({ text: "Wat is het capaciteitstarief?" })
    await act(async () => mic().click())
    await act(async () => {})
    assert.equal(recognizer, undefined, "No doomed recognition attempt in Chrome on iPhone")
    assert.equal(mic().getAttribute("data-listening"), "true")
    await act(async () => mic().click())
    await act(async () => {})
    assert.match(requests.at(-1).url, /\/api\/transcribe$/)
    assert.equal(document.querySelector(".ea-input").value, "Wat is het capaciteitstarief?")
    await clearInput()
    globalThis.testUserAgent = undefined
    delete window.SpeechRecognition

    globalThis.testMediaDevices = undefined
    globalThis.MediaRecorder = undefined
    globalThis.testUserAgent = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/130.0 Mobile/15E148"
    await act(async () => mic().click())
    assert.match(document.querySelector('[role="alert"]').textContent, /alleen in Safari/, "Without recording support, iPhone users are pointed to Safari")
    globalThis.testUserAgent = undefined
    window.SpeechRecognition = savedRecognition

    // Karen is one chat screen: no tabs, and the price chip asks the question in the chat.
    assert.equal(document.querySelector(".ea [role=\"tab\"]"), null, "No Prijzen tab")
    await act(async () => root.unmount())

    // The chat stays in this browser and earlier turns go along, so follow-ups work; PDF answers never do.
    const saved = () => JSON.parse(window.localStorage.getItem("karen-chat-v1") || "[]")
    assert.ok(saved().length > 0, "The conversation above was kept")
    assert.ok(!saved().some(turn => /Lokale PDF-analyse|Verwijder eerst je PDF/.test(turn.answer)), "PDF answers are not stored")
    window.localStorage.clear()
    const chatBox = document.createElement("div")
    document.body.appendChild(chatBox)
    let chatRoot = createRoot(chatBox)
    const renderChat = () => act(async () => chatRoot.render(React.createElement(Component, { apiEndpoint: "https://backend.example/api/answer" })))
    await renderChat()
    responses.push({ answer: "Stroom kost 22 cent.", comparison_session_id: null })
    await typed("Wat kost stroom vandaag?")
    assert.equal(requests.at(-1).body.history, undefined, "A first question has no history")
    responses.push({ answer: "Gas kost 8 cent.", comparison_session_id: null })
    await typed("en voor gas?")
    assert.deepEqual(requests.at(-1).body, { question: "en voor gas?", history: [{ question: "Wat kost stroom vandaag?", answer: "Stroom kost 22 cent." }] })
    assert.equal(chatBox.querySelector(".ea-past-answer").textContent, "Stroom kost 22 cent.", "The earlier turn stays on screen")
    assert.equal(chatBox.querySelector(".ea-answer").textContent, "Gas kost 8 cent.")
    assert.deepEqual(saved().map(turn => turn.question), ["Wat kost stroom vandaag?", "en voor gas?"])
    // Reloading the page brings the conversation back.
    await act(async () => chatRoot.unmount())
    chatRoot = createRoot(chatBox)
    await renderChat()
    assert.deepEqual([...chatBox.querySelectorAll(".ea-past-answer")].map(node => node.textContent), ["Stroom kost 22 cent.", "Gas kost 8 cent."])
    assert.equal(chatBox.querySelector("h1"), null, "The welcome text gives way to the restored chat")
    responses.push({ answer: "Ja.", comparison_session_id: null })
    await typed("en vast?")
    assert.equal(requests.at(-1).body.history.length, 2)
    await act(async () => [...chatBox.querySelectorAll("button")].find(b => b.textContent === "Nieuw gesprek").click())
    assert.equal(window.localStorage.getItem("karen-chat-v1"), null, "Nieuw gesprek clears the stored chat")
    assert.equal(chatBox.querySelector(".ea-past-answer"), null)
    assert.ok(chatBox.querySelector("h1"), "The welcome text is back")
    await act(async () => chatRoot.unmount())
    dom.window.close()
    console.log("Karen component: typed/voice session continuity, source cards, voice drafts, terminal reset, PDF isolation and chat history passed.")
}
main().catch(error => { console.error(error); process.exitCode = 1; dom.window.close() })
