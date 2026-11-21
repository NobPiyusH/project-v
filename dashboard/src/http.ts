// The one fetch helper behind every control-plane client (api.ts, dreamApi.ts, autopilotApi.ts).

/** Calls `url` with an optional JSON body; throws "<status> <text>: <body>" on a non-2xx; an empty reply reads as undefined. */
export async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await res.text()
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ''}`)
  return (text ? JSON.parse(text) : undefined) as T
}
