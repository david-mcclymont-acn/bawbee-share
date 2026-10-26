import * as Dialog from '@radix-ui/react-dialog'
import { useId, type ReactNode } from 'react'
import { formatPence, parsePounds } from './money.ts'

/** A figure: always tabular, always to the penny, negative with a true minus. */
export function Money({ pence, className }: { pence: number; className?: string }) {
  return (
    <span
      className={`money${pence < 0 ? ' money-negative' : ''}${className ? ` ${className}` : ''}`}
    >
      {formatPence(pence)}
    </span>
  )
}

export function PageHeading({
  title,
  lead,
  children,
}: {
  title: string
  lead?: ReactNode
  children?: ReactNode
}) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {lead && <p className="page-lead">{lead}</p>}
      </div>
      {children && <div className="page-actions">{children}</div>}
    </header>
  )
}

export function SectionHeading({
  title,
  aside,
  id,
}: {
  title: string
  aside?: ReactNode
  id?: string
}) {
  return (
    <div className="section-head">
      <h2 id={id}>{title}</h2>
      {aside && <div className="section-aside">{aside}</div>}
    </div>
  )
}

export function Notice({
  tone = 'info',
  children,
  action,
  urgent = false,
}: {
  tone?: 'info' | 'warn' | 'danger' | 'ok'
  children: ReactNode
  action?: ReactNode
  /** Interrupt a screen reader. Only for something that just happened, never a standing state. */
  urgent?: boolean
}) {
  return (
    <div className="notice" data-tone={tone} role={urgent ? 'alert' : 'status'}>
      <div className="notice-body">
        <span className="notice-dot" aria-hidden="true" />
        <div>{children}</div>
      </div>
      {action && <div className="notice-action">{action}</div>}
    </div>
  )
}

export function Loading({ what }: { what: string }) {
  return (
    <p className="loading" role="status">
      Loading {what}…
    </p>
  )
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <p className="error-text" role="alert">
      {error instanceof Error ? error.message : 'Something went wrong.'}
    </p>
  )
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: ReactNode
  /** Given the control's id, and the hint's id for aria-describedby when there is one. */
  children: (id: string, hintId?: string) => ReactNode
}) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id, hintId)}
      {hint && (
        <p className="field-hint" id={hintId}>
          {hint}
        </p>
      )}
    </div>
  )
}

export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: ReactNode
  children: ReactNode
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="sheet-overlay" />
        <Dialog.Content className="sheet-panel">
          <div className="sheet-head">
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Close className="button button-quiet" aria-label="Close">
              Close
            </Dialog.Close>
          </div>
          {description ? (
            <Dialog.Description className="sheet-description">{description}</Dialog.Description>
          ) : (
            <Dialog.Description className="visually-hidden">{title}</Dialog.Description>
          )}
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

/** An amount in pounds, typed as text and parsed to pence by the caller (see money.ts). */
export function MoneyInput({
  id,
  value,
  onChange,
  label,
  autoFocus,
}: {
  id?: string
  value: string
  onChange: (value: string) => void
  label?: string
  autoFocus?: boolean
}) {
  return (
    <span className="money-input">
      <span aria-hidden="true">£</span>
      <input
        id={id}
        inputMode="decimal"
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={value !== '' && parsePounds(value) === null}
        aria-label={label}
        autoFocus={autoFocus}
      />
    </span>
  )
}
