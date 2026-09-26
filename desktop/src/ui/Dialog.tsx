import { X } from 'lucide-react';
import { useEffect, useId, useRef, type ReactNode } from 'react';

/**
 * 네이티브 <dialog> 기반 모달·서랍. Esc·포커스 가두기·배경 비활성화를 브라우저(WebView2) 기본 동작으로 처리한다.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  variant = 'modal',
  width,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  variant?: 'modal' | 'drawer';
  width?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) {
      returnFocus.current = document.activeElement as HTMLElement | null;
      el.showModal();
    } else if (!open && el.open) {
      el.close();
      returnFocus.current?.focus?.();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={`dialog dialog-${variant}`}
      aria-labelledby={titleId}
      style={width ? ({ '--dialog-width': width } as React.CSSProperties) : undefined}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onMouseDown={(e) => {
        // 바깥(backdrop) 클릭으로 닫기
        if (e.target === ref.current) onClose();
      }}
    >
      {open ? (
        <div className="dialog-inner">
          <header className="dialog-head">
            <div>
              <h2 id={titleId} className="dialog-title">
                {title}
              </h2>
              {description ? <div className="dialog-desc">{description}</div> : null}
            </div>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="닫기" title="닫기 (Esc)">
              <X size={18} aria-hidden="true" />
            </button>
          </header>
          <div className="dialog-body">{children}</div>
          {footer ? <footer className="dialog-foot">{footer}</footer> : null}
        </div>
      ) : null}
    </dialog>
  );
}
