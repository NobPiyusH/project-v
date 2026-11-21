import type { Budget } from './api'

/** One spend bar; it turns hot past 85 % of the cap. `text` is the label, e.g. "Gemini ₹120 / ₹1000". */
function Meter({ text, spent, cap, title }: { text: string; spent: number; cap: number; title: string }) {
  const pct = cap > 0 ? Math.min(100, (spent / cap) * 100) : 0
  return (
    <div className={`meter ${pct > 85 ? 'hot' : ''}`} title={title}>
      <span>{text}</span>
      <div className="bar"><div className="fill" style={{ width: `${pct}%` }} /></div>
    </div>
  )
}

function InrMeter({ label, spent, cap, title }: { label: string; spent: number; cap: number; title: string }) {
  return <Meter text={`${label} ₹${spent.toFixed(0)} / ₹${cap.toFixed(0)}`} spent={spent} cap={cap} title={title} />
}

/** Top bar: Gemini, GPU (Modal's own bill when known, else the studio's estimate) and Claude spend this month. */
export function BudgetMeters({ budget }: { budget: Budget }) {
  const modal = budget.modal
  return (
    <div className="meters">
      <InrMeter label="Gemini" spent={Number(budget.geminiSpentInr)} cap={Number(budget.geminiCapInr)}
        title="Paid Gemini spend this month (the studio's own hard cap)" />
      {modal
        ? <Meter text={`Modal $${modal.meteredUsd.toFixed(2)} / $${modal.freeCreditUsd.toFixed(0)} credit`} spent={modal.meteredUsd} cap={modal.freeCreditUsd}
            title={`Modal's own bill this month: $${modal.meteredUsd.toFixed(2)} used, $${modal.creditLeftUsd.toFixed(2)} of the $${modal.freeCreditUsd.toFixed(0)} free credit left, $${modal.billedUsd.toFixed(2)} billed (updated ${new Date(modal.fetchedAt).toLocaleTimeString()}). Studio's own GPU estimate: ₹${Number(budget.gpuSpentInr ?? 0).toFixed(0)}.`} />
        : <InrMeter label="GPU" spent={Number(budget.gpuSpentInr ?? 0)} cap={Number(budget.gpuCapInr ?? 0)}
            title="Modal GPU spend this month recorded by the studio (cap = modal.monthlyCapUsd)" />}
      {budget.claudeCapInr != null && (
        <InrMeter label="Claude" spent={Number(budget.claudeSpentInr ?? 0)} cap={Number(budget.claudeCapInr)}
          title="Claude API spend this month (script, shot plan, concept, Dream Scout); at the cap Gemini takes over" />)}
    </div>
  )
}
