#!/usr/bin/env sh
# Builds EnergyAgentConversation.tsx into build/karen-component.js, the module
# the Space serves at /karen-component.js and KarenLoader.tsx loads in Framer.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
esbuild=${ESBUILD:-"npx --yes esbuild@0.24.2"}
mkdir -p "$root/build"
$esbuild "$root/energyagent-karen-prototype/EnergyAgentConversation.tsx" \
  --bundle --format=esm --minify --target=es2019 \
  --jsx=transform --jsx-factory=__KarenReact.createElement --jsx-fragment=__KarenReact.Fragment \
  --banner:js="const __KarenReact = globalThis.__KAREN_REACT__;" \
  --alias:react="$here/react.js" --alias:framer="$here/framer.js" \
  --outfile="$root/build/karen-component.js"
