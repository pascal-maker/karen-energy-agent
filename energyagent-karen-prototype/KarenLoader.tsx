import * as React from "react"
import { useEffect, useState, type ComponentType } from "react"
import { addPropertyControls, ControlType } from "framer"

// Paste this once into the Framer code file that holds EnergyAgentConversation.
// It loads the latest chat component from Karen's backend, so a merged change
// reaches the site without pasting code or republishing.

const DEFAULT_BACKEND = "https://pascal-maker-belgian-energy-voice-assistant.hf.space"
const DEMO_QUESTION = "Waarom verschilt mijn voorschot van mijn jaarafrekening?"

type Props = { apiEndpoint: string; sampleQuestion: string }

let loading: Promise<ComponentType<Props>> | null = null

function componentUrl(apiEndpoint: string): string {
    let origin = DEFAULT_BACKEND
    try {
        if (apiEndpoint.trim()) origin = new URL(apiEndpoint.trim()).origin
    } catch {}
    // A new address every five minutes, so browsers pick up a fresh deploy.
    return `${origin}/karen-component.js?v=${Math.floor(Date.now() / 300_000)}`
}

function loadKaren(apiEndpoint: string): Promise<ComponentType<Props>> {
    ;(globalThis as any).__KAREN_REACT__ = React
    loading ??= import(/* @vite-ignore */ componentUrl(apiEndpoint)).then((module) => module.default)
    loading.catch(() => (loading = null))
    return loading
}

/**
 * @framerSupportedLayoutWidth any
 * @framerSupportedLayoutHeight any
 * @framerIntrinsicWidth 1200
 * @framerIntrinsicHeight 820
 */
export default function EnergyAgentConversation(props: Props) {
    const [Karen, setKaren] = useState<ComponentType<Props> | null>(null)
    const [failed, setFailed] = useState(false)
    const [attempt, setAttempt] = useState(0)

    useEffect(() => {
        let active = true
        setFailed(false)
        loadKaren(props.apiEndpoint ?? "").then(
            (component) => active && setKaren(() => component),
            () => active && setFailed(true)
        )
        return () => {
            active = false
        }
    }, [attempt])

    if (Karen) return <Karen {...props} />
    return (
        <div style={{ width: "100%", height: "100%", minHeight: 320, display: "grid", placeItems: "center", fontFamily: "Inter, system-ui, sans-serif", color: "#475569", background: "#f8fafc", borderRadius: 24, textAlign: "center", padding: 24, boxSizing: "border-box" }}>
            {failed ? (
                <div>
                    <p style={{ margin: "0 0 12px" }}>Karen kon niet laden.</p>
                    <button type="button" onClick={() => setAttempt(attempt + 1)} style={{ font: "inherit", padding: "8px 16px", borderRadius: 999, border: "1px solid #cbd5e1", background: "white", cursor: "pointer" }}>
                        Opnieuw proberen
                    </button>
                </div>
            ) : (
                <p style={{ margin: 0 }}>Karen wordt geladen…</p>
            )}
        </div>
    )
}

addPropertyControls(EnergyAgentConversation, {
    apiEndpoint: { title: "Backend URL", type: ControlType.String, placeholder: "https://…", defaultValue: "" },
    sampleQuestion: { title: "Voorbeeldvraag", type: ControlType.String, defaultValue: DEMO_QUESTION },
})
