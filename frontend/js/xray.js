/**
 * Human-Friendly Error Drawer, Discrete Inline Error Bar, Painel Raio-X & Micro-Feedbacks (P1).
 * The app never crashes on error: every error gets a stable code (E-FFMPEG-001, E-OLLAMA-002...),
 * probable cause, actionable fix, "Copiar detalhes" and "Abrir logs".
 */

let lastXRayPayload = null;
const PROBLEMS_LOG = [];

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 180);
  }, 3400);
}

export function flashButtonFeedback(btn, label = 'Salvo ✓', durationMs = 1800) {
  if (!btn) return;
  const span = btn.querySelector('span');
  const target = span || btn;
  const prev = target.dataset.origLabel || target.textContent;
  target.dataset.origLabel = prev;
  target.textContent = label;
  setTimeout(() => {
    target.textContent = prev;
  }, durationMs);
}

export async function copyToClipboard(text, toastMsg = 'Copiado ✓') {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    if (toastMsg) showToast(toastMsg, 'success');
    return true;
  } catch {
    showToast('Nao foi possivel copiar automaticamente.', 'error');
    return false;
  }
}

function updateProblemsIndicators() {
  const chip = document.getElementById('chip-problems');
  const lbl = document.getElementById('chip-problems-label');
  const count = PROBLEMS_LOG.length;
  if (lbl) {
    lbl.textContent = count === 1 ? '● 1 problema' : `● ${count} problemas`;
  }
  if (chip) {
    chip.classList.toggle('problems-has-errors', count > 0);
  }
  renderProblemsDrawer();
}

function showDiscreteInlineError(normalized) {
  const bar = document.getElementById('inline-error-strip');
  const textEl = document.getElementById('inline-error-text');
  if (!bar || !textEl) return;
  const where = normalized.file ? `${normalized.file.split(/[\\/]/).pop()}${normalized.line ? ':' + normalized.line : ''}` : normalized.category;
  textEl.textContent = `[${normalized.code}] ${normalized.explanation_pt} · ${where}`;
  bar.classList.remove('hidden');
}

export function renderProblemsDrawer() {
  const body = document.getElementById('problems-drawer-body');
  if (!body) return;
  if (!PROBLEMS_LOG.length) {
    body.innerHTML = `<div class="empty-state" style="padding:26px;">Nenhum problema registrado nesta sessao. Tudo operando perfeitamente!</div>`;
    return;
  }
  body.innerHTML = PROBLEMS_LOG.map(
    (err, idx) => `
      <div class="problem-item">
        <div class="flex items-center justify-between gap-2 mb-2">
          <span class="badge badge-red">${escapeHtml(err.code)}</span>
          <span class="text-xs">${escapeHtml(err.time_label)} · ${escapeHtml(err.category)}</span>
        </div>
        <div style="font-weight:700;font-size:13px;margin-bottom:4px;">${escapeHtml(err.explanation_pt)}</div>
        <div class="text-xs" style="margin-bottom:4px;"><strong>Onde:</strong> ${escapeHtml(err.file)} (linha ${err.line})</div>
        <div class="text-xs" style="margin-bottom:4px;"><strong>Causa provavel:</strong> ${escapeHtml(err.probable_cause_pt)}</div>
        <div class="text-xs" style="margin-bottom:8px;"><strong>Como resolver:</strong> ${escapeHtml(err.how_to_fix_pt)}</div>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-sm prob-copy-btn" data-pidx="${idx}"><span>Copiar detalhes</span></button>
          <button class="btn btn-sm prob-xray-btn" data-pidx="${idx}"><span>Raio-X Completo</span></button>
        </div>
      </div>`
  ).join('');

  body.querySelectorAll('.prob-copy-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const item = PROBLEMS_LOG[Number(btn.dataset.pidx)];
      if (item) {
        copyToClipboard(item.ai_copy_text, 'Copiado ✓');
        flashButtonFeedback(btn, 'Copiado ✓');
      }
    });
  });
  body.querySelectorAll('.prob-xray-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const item = PROBLEMS_LOG[Number(btn.dataset.pidx)];
      if (item) openXRayModal(item);
    });
  });
}

export function openProblemsDrawer() {
  renderProblemsDrawer();
  document.getElementById('problems-drawer-backdrop')?.classList.add('open');
}

export function closeProblemsDrawer() {
  document.getElementById('problems-drawer-backdrop')?.classList.remove('open');
}

function openXRayModal(normalized) {
  lastXRayPayload = normalized;
  const backdrop = document.getElementById('xray-backdrop');
  if (!backdrop) return;
  document.getElementById('xray-category').textContent = `${normalized.code} · ${normalized.category}`;
  document.getElementById('xray-explanation').textContent = normalized.explanation_pt;
  document.getElementById('xray-location').textContent = `${normalized.file} : linha ${normalized.line}`;
  document.getElementById('xray-detail').textContent =
    `Causa provavel: ${normalized.probable_cause_pt}\nComo resolver: ${normalized.how_to_fix_pt}\n\n` +
    normalized.technical_detail +
    (normalized.traceback ? `\n\n${normalized.traceback}` : '');
  backdrop.classList.add('open');
}

export function showXRayError(payload, { openModal = false } = {}) {
  const normalized = normalizePayload(payload);
  lastXRayPayload = normalized;
  PROBLEMS_LOG.unshift(normalized);
  if (PROBLEMS_LOG.length > 40) PROBLEMS_LOG.pop();

  showDiscreteInlineError(normalized);
  updateProblemsIndicators();
  if (openModal) {
    openXRayModal(normalized);
  }
}

export function closeXRayError() {
  const backdrop = document.getElementById('xray-backdrop');
  if (backdrop) backdrop.classList.remove('open');
}

export function initXRayPanel() {
  const closeBtn = document.getElementById('xray-close-btn');
  const copyBtn = document.getElementById('xray-copy-ai-btn');
  const backdrop = document.getElementById('xray-backdrop');

  closeBtn?.addEventListener('click', closeXRayError);
  backdrop?.addEventListener('click', (e) => {
    if (e.target === backdrop) closeXRayError();
  });
  copyBtn?.addEventListener('click', () => {
    if (!lastXRayPayload) return;
    copyToClipboard(lastXRayPayload.ai_copy_text, 'Copiado ✓');
    flashButtonFeedback(copyBtn, 'Copiado ✓');
  });

  // Problems chip & drawer controls
  document.getElementById('chip-problems')?.addEventListener('click', openProblemsDrawer);
  document.getElementById('btn-inline-error-details')?.addEventListener('click', openProblemsDrawer);
  document.getElementById('btn-inline-error-dismiss')?.addEventListener('click', () => {
    document.getElementById('inline-error-strip')?.classList.add('hidden');
  });
  document.getElementById('problems-drawer-close')?.addEventListener('click', closeProblemsDrawer);
  document.getElementById('problems-drawer-backdrop')?.addEventListener('click', (e) => {
    if (e.target.id === 'problems-drawer-backdrop') closeProblemsDrawer();
  });
  document.getElementById('problems-drawer-clear')?.addEventListener('click', () => {
    PROBLEMS_LOG.length = 0;
    document.getElementById('inline-error-strip')?.classList.add('hidden');
    updateProblemsIndicators();
  });
  document.getElementById('problems-drawer-logs')?.addEventListener('click', () => {
    const token = window.__CLIPRUSH_TOKEN__ || '';
    fetch('/api/system/open-logs', {
      method: 'POST',
      headers: token ? { 'X-ClipRush-Token': token } : {},
    }).catch(() => {});
    showToast('Abrindo pasta de logs no Explorer...');
  });

  updateProblemsIndicators();
}

function normalizePayload(err) {
  const nowStr = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  if (err && typeof err === 'object' && err.explanation_pt) {
    return {
      code: err.code || 'E-SYS-099',
      category: err.category || 'SISTEMA',
      explanation_pt: err.explanation_pt,
      probable_cause_pt: err.probable_cause_pt || 'Falha operacional durante a requisicao.',
      how_to_fix_pt: err.how_to_fix_pt || 'Verifique os detalhes tecnicos ou tente novamente.',
      file: err.file || 'backend/app.py',
      line: err.line || 0,
      technical_detail: err.technical_detail || '',
      traceback: err.traceback || '',
      time_label: nowStr,
      ai_copy_text:
        err.ai_copy_text ||
        `[ClipRush ${err.code || 'E-SYS-099'}]\nCategoria: ${err.category}\nExplicacao: ${err.explanation_pt}\nArquivo: ${err.file}:${err.line}\nDetalhe: ${err.technical_detail}`,
    };
  }
  const msg = err instanceof Error ? err.message : String(err || 'Erro desconhecido');
  return {
    code: 'E-UI-098',
    category: 'FRONTEND',
    explanation_pt: 'Ocorreu uma falha de comunicacao ou execucao na interface.',
    probable_cause_pt: 'Resposta inesperada do servidor local ou erro temporario no navegador.',
    how_to_fix_pt: 'Verifique se o servidor local esta ativo em 127.0.0.1:8765 e recarregue a pagina.',
    file: 'frontend/js/app.js',
    line: 0,
    technical_detail: msg,
    traceback: err?.stack || '',
    time_label: nowStr,
    ai_copy_text: `=== CLIPRUSH STUDIO PRO [E-UI-098] ===\nMensagem: ${msg}\nStack:\n${err?.stack || ''}`,
  };
}
