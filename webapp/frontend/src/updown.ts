import { createContext, useContext, useEffect, useState } from "react";

// Which colour means "up": red, as on Chinese markets, or green, as on US ones.
export type UpDown = "red" | "green";

const KEY = "tradingagents.updown";

function stored(): UpDown {
  try {
    return localStorage.getItem(KEY) === "green" ? "green" : "red";
  } catch {
    return "red";
  }
}

export function useUpDownSetting(): [UpDown, () => void] {
  const [mode, setMode] = useState<UpDown>(stored);
  useEffect(() => {
    document.documentElement.dataset.updown = mode;
    try {
      localStorage.setItem(KEY, mode);
    } catch {
      /* private window: the choice lasts this visit */
    }
  }, [mode]);
  return [mode, () => setMode((m) => (m === "red" ? "green" : "red"))];
}

export const UpDownContext = createContext<UpDown>("red");
export const useUpDown = () => useContext(UpDownContext);

/** Whether the page is in dark mode, following the system setting. */
export function useDarkMode(): boolean {
  const query = "(prefers-color-scheme: dark)";
  const [dark, setDark] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const onChange = () => setDark(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return dark;
}
