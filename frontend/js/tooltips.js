/**
 * Smart Minimalist Tooltip Engine
 * Cleanly aligns sidebar tooltips inside the sidebar column (never overlapping neighbor card titles)
 * with controlled z-index, max-width 220px, and crisp viewport offsets.
 */
export function initTooltips() {
  let tip = document.getElementById('cr-floating-tooltip');
  if (!tip) {
    tip = document.createElement('div');
    tip.id = 'cr-floating-tooltip';
    tip.className = 'cr-tooltip';
    tip.setAttribute('role', 'tooltip');
    document.body.appendChild(tip);
  }

  let currentTarget = null;

  function positionTip(el) {
    const rect = el.getBoundingClientRect();
    const inSidebar = Boolean(el.closest('.app-sidebar'));
    tip.classList.toggle('sidebar-tip', inSidebar);

    const tipRect = tip.getBoundingClientRect();
    const offset = inSidebar ? 6 : 10;

    let top;
    let left;

    if (inSidebar) {
      // Align neatly underneath the sidebar item inside the sidebar bounds so it NEVER covers main card titles
      const sidebar = el.closest('.app-sidebar');
      const sbRect = sidebar ? sidebar.getBoundingClientRect() : rect;
      left = Math.max(8, sbRect.left + 10);
      if (left + tipRect.width > sbRect.right - 6) {
        left = Math.max(8, sbRect.right - tipRect.width - 6);
      }
      top = rect.bottom + offset;
      if (top + tipRect.height > window.innerHeight - 10) {
        top = rect.top - tipRect.height - offset;
      }
    } else {
      top = rect.top - tipRect.height - offset;
      if (top < 10) {
        top = rect.bottom + offset;
      }
      left = rect.left + (rect.width - tipRect.width) / 2;
      left = Math.max(10, Math.min(window.innerWidth - tipRect.width - 10, left));
    }

    tip.style.top = `${Math.round(top)}px`;
    tip.style.left = `${Math.round(left)}px`;
  }

  function show(el) {
    const text = el?.getAttribute('data-tooltip');
    if (!text) return;
    currentTarget = el;
    tip.textContent = text;
    tip.classList.toggle('sidebar-tip', Boolean(el.closest('.app-sidebar')));
    tip.classList.add('visible');
    requestAnimationFrame(() => {
      if (currentTarget === el) positionTip(el);
    });
  }

  function hide() {
    currentTarget = null;
    tip.classList.remove('visible');
  }

  document.addEventListener('pointerover', (e) => {
    const target = e.target.closest?.('[data-tooltip]');
    if (target && (target !== currentTarget || !tip.classList.contains('visible'))) {
      show(target);
    } else if (!target && currentTarget) {
      hide();
    }
  });

  document.addEventListener('pointerout', (e) => {
    if (!currentTarget) return;
    const related = e.relatedTarget?.closest?.('[data-tooltip]');
    if (related !== currentTarget) {
      hide();
    }
  });

  document.addEventListener('focusin', (e) => {
    const target = e.target.closest?.('[data-tooltip]');
    if (target) show(target);
  });

  document.addEventListener('focusout', () => hide());
  window.addEventListener(
    'scroll',
    () => {
      if (currentTarget && currentTarget.matches(':hover')) {
        positionTip(currentTarget);
      } else {
        hide();
      }
    },
    true
  );
}
