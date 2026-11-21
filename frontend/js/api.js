/**
 * Centralized API Client with automatic Painel Raio-X error interception.
 */
import { showXRayError } from './xray.js';

export async function apiFetch(url, options = {}, { silentXRay = false } = {}) {
  const fetchOpts = {
    cache: 'no-store',
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  };

  let resp;
  try {
    resp = await fetch(url, fetchOpts);
  } catch (networkErr) {
    const payload = {
      category: 'REDE',
      explanation_pt: 'Nao foi possivel conectar ao servidor local do ClipRush Studio Pro (http://127.0.0.1:8765).',
      file: 'frontend/js/api.js',
      line: 18,
      technical_detail: String(networkErr),
      ai_copy_text: `Erro de rede ao acessar ${url}: ${networkErr}`,
    };
    if (!silentXRay) showXRayError(payload);
    throw payload;
  }

  let data = null;
  const contentType = resp.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    data = await resp.json();
  } else {
    data = await resp.text();
  }

  if (!resp.ok) {
    const payload =
      data && typeof data === 'object' && data.explanation_pt
        ? data
        : {
            category: 'API',
            explanation_pt: `A operacao retornou erro HTTP ${resp.status}.`,
            file: url,
            line: 0,
            technical_detail: typeof data === 'string' ? data : JSON.stringify(data),
            ai_copy_text: `HTTP ${resp.status} em ${url}\nResposta: ${JSON.stringify(data)}`,
          };
    if (!silentXRay) showXRayError(payload);
    throw payload;
  }

  return data;
}

export function pollJob(jobId, onProgress) {
  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      try {
        const job = await apiFetch(`/api/jobs/${jobId}`, {}, { silentXRay: true });
        if (onProgress) onProgress(job);
        if (job.status === 'done') {
          clearInterval(timer);
          resolve(job.result);
        } else if (job.status === 'error') {
          clearInterval(timer);
          if (job.xray_error) showXRayError(job.xray_error);
          reject(job.xray_error || new Error(job.message || 'Erro no job'));
        }
      } catch (err) {
        clearInterval(timer);
        showXRayError(err);
        reject(err);
      }
    }, 600);
  });
}
