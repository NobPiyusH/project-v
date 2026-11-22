export type EditKey = 'musicEdit' | 'ambience' | 'pacing'
export const EDIT_SWITCHES: [EditKey, string][] = [
  ['musicEdit', 'Cut to the music (bars, rise, energy)'],
  ['ambience', 'J-cut ambience'],
  ['pacing', 'Pacing (hook first, no shot under 3.4 s, climax mid-way)'],
]
export const EDIT_COLUMN: Record<EditKey, 'edit_music' | 'edit_ambience' | 'edit_pacing'> = {
  musicEdit: 'edit_music', ambience: 'edit_ambience', pacing: 'edit_pacing',
}

export const HOOK_COLOURS = ['red', 'yellow', 'orange', 'pink', 'cyan']

/** Lenient JSON parse for the backend's stored JSON columns: bad or missing JSON reads as {}. */
export function parseJson(s: string | null): Record<string, unknown> {
  try { return s ? JSON.parse(s) : {} } catch { return {} }
}

export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))
