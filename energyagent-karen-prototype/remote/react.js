// The remote Karen bundle uses the React that the Framer site already runs,
// handed over by KarenLoader.tsx, so hooks work across both.
const React = globalThis.__KAREN_REACT__
export default React
export const { createElement, Fragment, useEffect, useRef, useState, useMemo, useCallback, useLayoutEffect, useReducer, useContext, createContext } = React
