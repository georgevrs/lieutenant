/* ── i18n — Greek / English UI strings ───────────────────────────── */

export type Lang = "el" | "en";

const strings = {
  // State indicator
  "state.idle":        { el: "Αναμονή",     en: "Idle" },
  "state.listening":   { el: "Ακούω…",      en: "Listening…" },
  "state.thinking":    { el: "Σκέφτομαι…",  en: "Thinking…" },
  "state.speaking":    { el: "Μιλάω…",      en: "Speaking…" },
  "state.conversing":  { el: "Συνομιλία…",  en: "Conversing…" },
  "state.connected":   { el: "Συνδεδεμένο", en: "Connected" },
  "state.disconnected":{ el: "Αποσυνδεδεμένο", en: "Disconnected" },

  // Controls
  "ctrl.wake":         { el: "🎤  Υπολοχαγέ", en: "🎤  Lieutenant" },
  "ctrl.conversing":   { el: "💬  Συνομιλία…", en: "💬  Conversing…" },
  "ctrl.listening":    { el: "Ακούω…",       en: "Listening…" },
  "ctrl.stop":         { el: "■ Στοπ",       en: "■ Stop" },
  "ctrl.settings":     { el: "Ρυθμίσεις",   en: "Settings" },
  "ctrl.langSwitch":   { el: "Switch to English", en: "Αλλαγή σε Ελληνικά" },

  // Chat panel
  "chat.you":          { el: "Εσύ",          en: "You" },
  "chat.lieutenant":   { el: "Υπολοχαγός",  en: "Lieutenant" },
  "chat.placeholder":  { el: "Η συνομιλία θα εμφανιστεί εδώ…", en: "Conversation will appear here…" },

  // Log panel
  "logs.title":        { el: "Αρχείο καταγραφής", en: "Logs" },
  "logs.show":         { el: "Εμφάνιση αρχείου",  en: "Show Logs" },
  "logs.hide":         { el: "Απόκρυψη αρχείου",  en: "Hide Logs" },

  // Settings panel
  "settings.title":      { el: "Ρυθμίσεις",            en: "Settings" },
  "settings.wakeWords":  { el: "Λέξεις αφύπνισης",    en: "Wake Words" },
  "settings.wakeEl":     { el: "Ελληνικά",              en: "Greek" },
  "settings.wakeEn":     { el: "Αγγλικά",              en: "English" },
  "settings.display":    { el: "Εμφάνιση",              en: "Display" },
  "settings.chatName":   { el: "Όνομα στη συνομιλία",  en: "Chat display name" },
  "settings.save":       { el: "Αποθήκευση",          en: "Save" },
  "settings.saving":     { el: "Αποθήκευση…",         en: "Saving…" },
  "settings.saved":      { el: "Αποθηκεύτηκε ✓",     en: "Saved ✓" },
  "settings.connection": { el: "Σύνδεση",              en: "Connection" },
} as const;

type Key = keyof typeof strings;

export function t(key: Key, lang: Lang): string {
  return strings[key]?.[lang] ?? strings[key]?.["en"] ?? key;
}
