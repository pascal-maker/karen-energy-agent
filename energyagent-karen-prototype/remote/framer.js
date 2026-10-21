// Property controls belong to KarenLoader.tsx in Framer; the remote bundle ignores them.
export function addPropertyControls() {}
export const ControlType = new Proxy({}, { get: (_target, name) => String(name) })
