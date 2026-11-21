/**
 * Painel Raio-X de Erros: displays simple Portuguese explanation, exact file & line,
 * and a 1-click "Copiar Erro para IA" button.
 */

let lastXRayPayload = null;

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

export async function copyToClipboard(text, toastMsg = 'Copiado para a area de transferencia!') {
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

export function showXRayError(payload) {
  const normalized = normalizePayload(payload);
  lastXRayPayload = normalized;

  const backdrop = document.getElementById('xray-backdrop');
  if (!backdrop) return;

  document.getElementById('xray-category').textContent = normalized.category;
  document.getElementById('xray-explanation').textContent = normalized.explanation_pt;
  document.getElementById('xray-location').textContent = `${normalized.file} : linha ${normalized.line}`;
  document.getElementById('xray-detail').textContent =
    normalized.technical_detail + (normalized.traceback ? `\n\n${normalized.traceback}` : '');

  backdrop.classList.add('open');
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
    copyToClipboard(lastXRayPayload.ai_copy_text, 'Relatorio Raio-X copiado! Cole diretamente para a IA.');
  });
}

function normalizePayload(err) {
  if (err && typeof err === 'object' && err.explanation_pt) {
    return {
      category: err.category || 'SISTEMA',
      explanation_pt: err.explanation_pt,
      file: err.file || 'backend/app.py',
      line: err.line || 0,
      technical_detail: err.technical_detail || '',
      traceback: err.traceback || '',
      ai_copy_text:
        err.ai_copy_text ||
        `[ClipRush Raio-X]\nCategoria: ${err.category}\nExplicacao: ${err.explanation_pt}\nArquivo: ${err.file}:${err.line}\nDetalhe: ${err.technical_detail}`,
    };
  }
  const msg = err instanceof Error ? err.message : String(err || 'Erro desconhecido');
  return {
    category: 'FRONTEND',
    explanation_pt: 'Ocorreu uma falha de comunicacao ou execucao na interface. Veja o detalhe tecnico abaixo.',
    file: 'frontend/js/app.js',
    line: 0,
    technical_detail: msg,
    traceback: err?.stack || '',
    ai_copy_text: `=== CLIPRUSH STUDIO PRO - ERRO FRONTEND ===\nMensagem: ${msg}\nStack:\n${err?.stack || ''}`,
  };
}
