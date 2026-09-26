import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: 'sm' | 'md';
  icon?: ReactNode;
  /** 단축키 안내 (표시만) */
  shortcut?: string;
  pressed?: boolean;
  busy?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = 'secondary', size = 'md', icon, shortcut, pressed, busy, className, children, type = 'button', disabled, title, ...rest },
  ref,
) {
  const cls = ['btn', `btn-${variant}`, `btn-${size}`, pressed ? 'is-pressed' : '', busy ? 'is-busy' : '', className ?? ''].filter(Boolean).join(' ');
  const fullTitle = title ?? (shortcut && typeof children === 'string' ? `${children} (${shortcut})` : undefined);
  return (
    <button
      ref={ref}
      type={type}
      className={cls}
      aria-pressed={pressed}
      aria-busy={busy || undefined}
      disabled={disabled || busy}
      title={fullTitle}
      {...rest}
    >
      {icon ? <span className="btn-icon" aria-hidden="true">{icon}</span> : null}
      {children !== undefined ? <span className="btn-label">{children}</span> : null}
      {shortcut ? (
        <kbd className="btn-kbd" aria-hidden="true">
          {shortcut}
        </kbd>
      ) : null}
    </button>
  );
});
