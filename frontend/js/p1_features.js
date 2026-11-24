/**
 * ClipRush Studio Pro - P1 Modular Feature Controller:
 * - Theme Switcher (Escuro / Claro / Automatico Windows) with AA contrast
 * - 5-State Qwen Indicator + System Health Panel (FFmpeg, Ollama, Disk, RAM, Battery/Charger)
 * - "Qual e o jogo?" + Editable Game Sheet (data/games_db.json) + Trending Tags
 * - Interactive 9:16 Visual Title Editor (drag + center snap + Safe Zone warning -> overlay.json)
 * - "Ver mais 5 opcoes" Combinatorial Copy Modal (Jaccard < 0.35)
 * - PS4 Watch Folder Queue UI
 * - Guia "Pode / Nao Pode" + 3-Balloon Interactive Tour
 */

import { apiFetch } from './api.js';
import { flashButtonFeedback, showToast } from './xray.js';

const THEME_ORDER = ['dark', 'light', 'auto'];
const THEME_LABELS = {
  dark: '🌙 Escuro',
  light: '☀️ Claro',
  auto: '🖥️ Auto',
};

let lastHealthSnapshot = null;

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---- 1. Theme System (Escuro / Claro / Automatico) ----

export function applyThemeMode(mode = 'dark', persist = true) {
  const valid = THEME_ORDER.includes(mode) ? mode : 'dark';
  let resolved = valid;
  if (valid === 'auto') {
    const prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
    resolved = prefersLight ? 'light' : 'dark';
  }
  document.documentElement.setAttribute('data-theme', resolved);
  document.documentElement.setAttribute('data-theme-mode', valid);
  const lbl = document.getElementById('theme-toggle-label');
  if (lbl) lbl.textContent = THEME_LABELS[valid] || THEME_LABELS.dark;
  if (persist) {
    try {
      localStorage.setItem('cliprush_theme_mode', valid);
    } catch {
      // ignore storage quota errors
    }
  }
}

export function initThemeSwitcher() {
  let saved = 'dark';
  try {
    saved = localStorage.getItem('cliprush_theme_mode') || 'dark';
  } catch {
    saved = 'dark';
  }
  applyThemeMode(saved, false);

  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
      const cur = document.documentElement.getAttribute('data-theme-mode') || 'dark';
      if (cur === 'auto') applyThemeMode('auto', false);
    });
  }

  document.getElementById('btn-theme-toggle')?.addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme-mode') || 'dark';
    const next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
    applyThemeMode(next, true);
    apiFetch('/api/settings', { method: 'PUT', body: JSON.stringify({ theme: next }) }, { silentXRay: true }).catch(() => {});
    showToast(`Tema alterado: ${THEME_LABELS[next]}`);
  });
}

// ---- 2. 5-State Qwen Indicator & System Health Panel ----

export function updateQwenFiveStateChip(qwenState) {
  const chip = document.getElementById('chip-ollama');
  const lbl = document.getElementById('chip-ollama-label');
  if (!chip || !lbl || !qwenState) return;

  const state = qwenState.state || 'offline';
  const label = qwenState.label || 'Desligado';
  chip.dataset.qwenState = state;
  lbl.textContent = label;

  const classMap = {
    connected_vision: 'status-chip ok',
    loading_model: 'status-chip loading',
    analyzing_cut: 'status-chip active',
    no_vision: 'status-chip warn',
    offline: 'status-chip err',
  };
  chip.className = classMap[state] || 'status-chip warn';
}

export function setHealthSnapshot(st) {
  lastHealthSnapshot = st;
  if (st?.qwen_state) {
    updateQwenFiveStateChip(st.qwen_state);
  }
}

export function openSystemHealthModal() {
  const grid = document.getElementById('health-details-grid');
  const bd = document.getElementById('health-modal-backdrop');
  if (!grid || !bd) return;
  const st = lastHealthSnapshot || {};
  const hw = st.hardware || {};
  const ram = hw.ram || {};
  const bat = hw.battery || {};
  const qw = st.qwen_state || {};
  const ff = st.ffmpeg || {};
  const sec = st.security || {};

  grid.innerHTML = `
    <div class="health-item">
      <div class="text-xs">MOTOR FFMPEG</div>
      <div style="font-weight:700;margin-top:3px;">${ff.ok ? '✓ Operacional' : '✕ Ausente'} (${escapeHtml(ff.source || '-')})</div>
    </div>
    <div class="health-item">
      <div class="text-xs">QWEN 3.5 / OLLAMA (5 ESTADOS)</div>
      <div style="font-weight:700;margin-top:3px;">${escapeHtml(qw.label || 'Verificando')} (${escapeHtml(qw.model || 'qwen3.5:9b')})</div>
    </div>
    <div class="health-item">
      <div class="text-xs">DISCO LIVRE (D:)</div>
      <div style="font-weight:700;margin-top:3px;">${hw.disk_free_gb ?? '-'} GB disponiveis</div>
    </div>
    <div class="health-item">
      <div class="text-xs">MEMORIA RAM</div>
      <div style="font-weight:700;margin-top:3px;">${ram.used_gb ?? '-'} / ${ram.total_gb ?? '-'} GB (${ram.percent ?? 0}%)</div>
    </div>
    <div class="health-item">
      <div class="text-xs">CPU &amp; PROTECAO RYZEN 5</div>
      <div style="font-weight:700;margin-top:3px;">${hw.cpu_percent ?? 0}% (Pausa &gt; ${hw.cpu_pause_threshold_pct ?? 80}%, 1 tarefa por vez)</div>
    </div>
    <div class="health-item">
      <div class="text-xs">ENERGIA / BATERIA</div>
      <div style="font-weight:700;margin-top:3px;">${escapeHtml(bat.label || 'Energia AC / Desktop')}</div>
    </div>
    <div class="health-item" style="grid-column:1/-1;">
      <div class="text-xs">SEGURANCA LOCAL P0</div>
      <div style="font-weight:700;margin-top:3px;">Bind ${escapeHtml(sec.host_bound || '127.0.0.1:8765')} · Token Sessao Ativo · Cofre Windows DPAPI: ${sec.dpapi_vault ? 'Ativo ✓' : 'Inativo'}</div>
    </div>`;
  bd.classList.add('open');
}

export function initSystemHealthModal() {
  document.getElementById('btn-health-close')?.addEventListener('click', () => {
    document.getElementById('health-modal-backdrop')?.classList.remove('open');
  });
  document.getElementById('health-modal-backdrop')?.addEventListener('click', (e) => {
    if (e.target.id === 'health-modal-backdrop') {
      document.getElementById('health-modal-backdrop')?.classList.remove('open');
    }
  });
}

// ---- 3. "Qual e o jogo?" + Editable Game Sheet (data/games_db.json) + Trending Tags ----

export function initGameSheetUI(STATE, onProfileSync) {
  STATE.games = [];
  STATE.activeGameSheet = null;
  STATE.gameName = '';

  const input = document.getElementById('game-name-input');
  const dl = document.getElementById('games-datalist');
  const notice = document.getElementById('new-game-notice');
  const badge = document.getElementById('game-sheet-status-badge');
  const editor = document.getElementById('game-sheet-editor');
  const trendInput = document.getElementById('trending-tags-input');

  function fillSheetForm(sheet, rawName = '') {
    const s = sheet || {};
    document.getElementById('gs-official-name').value = s.official_name || rawName || '';
    document.getElementById('gs-aliases').value = (s.aliases || []).join(', ');
    document.getElementById('gs-genre').value = s.genre || 'acao';
    document.getElementById('gs-characters').value = (s.characters || []).join(', ');
    document.getElementById('gs-locations').value = (s.locations || []).join(', ');
    document.getElementById('gs-weapons').value = (s.weapons_items || []).join(', ');
    document.getElementById('gs-slangs').value = (s.slangs_br || []).join(', ');
    document.getElementById('gs-hashtags').value = (s.hashtags || []).join(', ');
  }

  function matchLocalGame(val) {
    const q = (val || '').trim().toLowerCase();
    if (!q) return null;
    return (
      STATE.games.find(
        (g) =>
          g.official_name.toLowerCase() === q ||
          (g.aliases || []).some((a) => String(a).toLowerCase() === q)
      ) ||
      STATE.games.find(
        (g) =>
          g.official_name.toLowerCase().includes(q) ||
          (g.aliases || []).some((a) => String(a).toLowerCase().includes(q))
      ) ||
      null
    );
  }

  function syncFromInput() {
    const val = (input?.value || '').trim();
    STATE.gameName = val;
    if (!val) {
      STATE.activeGameSheet = null;
      notice?.classList.add('hidden');
      if (badge) badge.textContent = 'Nenhum jogo definido';
      return;
    }
    const found = matchLocalGame(val);
    if (found) {
      STATE.activeGameSheet = found;
      notice?.classList.add('hidden');
      if (badge) {
        badge.className = 'badge badge-green';
        badge.textContent = `Ficha: ${found.official_name}`;
      }
      fillSheetForm(found);
      if (found.profile_id && onProfileSync) onProfileSync(found.profile_id);
    } else {
      STATE.activeGameSheet = null;
      notice?.classList.remove('hidden');
      if (badge) {
        badge.className = 'badge badge-amber';
        badge.textContent = 'Jogo Novo (Revisar Ficha)';
      }
      fillSheetForm(null, val);
    }
  }

  async function loadGamesDb() {
    try {
      const data = await apiFetch('/api/games', {}, { silentXRay: true });
      STATE.games = data.games || [];
      if (dl) {
        dl.innerHTML = STATE.games
          .map((g) => `<option value="${escapeHtml(g.official_name)}"></option>`)
          .join('');
      }
      if (trendInput && Array.isArray(data.trending_hashtags)) {
        trendInput.value = data.trending_hashtags.join(' ');
      }
    } catch {
      // ignore initial load error
    }
  }

  input?.addEventListener('input', syncFromInput);
  input?.addEventListener('change', syncFromInput);

  document.getElementById('btn-toggle-game-sheet')?.addEventListener('click', () => {
    editor?.classList.toggle('hidden');
  });

  document.getElementById('btn-detect-game-ai')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      const res = await apiFetch('/api/games/detect', {
        method: 'POST',
        body: JSON.stringify({
          project_id: STATE.project?.id || null,
          filename: STATE.project?.original_filename || STATE.project?.name || '',
        }),
      });
      if (input && res.detected_name) input.value = res.detected_name;
      STATE.gameName = res.detected_name || '';
      STATE.activeGameSheet = res.sheet || null;
      fillSheetForm(res.sheet, res.detected_name);
      if (res.is_new_game) {
        notice?.classList.remove('hidden');
        editor?.classList.remove('hidden');
        if (badge) {
          badge.className = 'badge badge-amber';
          badge.textContent = 'Jogo Novo Detectado';
        }
        showToast(`IA sugeriu "${res.detected_name}" (jogo novo — revise a ficha).`);
      } else {
        notice?.classList.add('hidden');
        if (badge) {
          badge.className = 'badge badge-green';
          badge.textContent = `Ficha: ${res.detected_name}`;
        }
        if (res.sheet?.profile_id && onProfileSync) onProfileSync(res.sheet.profile_id);
        showToast(`Jogo identificado: ${res.detected_name} ✓`);
      }
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById('btn-save-game-sheet')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    const official_name = document.getElementById('gs-official-name').value.trim();
    if (!official_name) {
      showToast('Informe o Nome Oficial do Jogo para salvar a ficha.', 'error');
      return;
    }
    const payload = {
      id: STATE.activeGameSheet?.id || '',
      official_name,
      aliases: document.getElementById('gs-aliases').value,
      genre: document.getElementById('gs-genre').value,
      profile_id: STATE.selectedProfileId || 'generico',
      characters: document.getElementById('gs-characters').value,
      locations: document.getElementById('gs-locations').value,
      weapons_items: document.getElementById('gs-weapons').value,
      slangs_br: document.getElementById('gs-slangs').value,
      hashtags: document.getElementById('gs-hashtags').value,
      is_custom: true,
    };
    const res = await apiFetch('/api/games', { method: 'POST', body: JSON.stringify(payload) });
    STATE.games = res.games || STATE.games;
    STATE.activeGameSheet = res.game;
    STATE.gameName = res.game.official_name;
    if (input) input.value = res.game.official_name;
    notice?.classList.add('hidden');
    if (badge) {
      badge.className = 'badge badge-green';
      badge.textContent = `Ficha: ${res.game.official_name}`;
    }
    if (dl) {
      dl.innerHTML = STATE.games.map((g) => `<option value="${escapeHtml(g.official_name)}"></option>`).join('');
    }
    flashButtonFeedback(btn, 'Salvo ✓');
    showToast(`Ficha de "${res.game.official_name}" salva ✓`);
  });

  document.getElementById('btn-save-trending')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    const raw = (trendInput?.value || '').trim();
    const tags = raw
      .split(/[\s,;]+/)
      .map((t) => t.trim())
      .filter(Boolean);
    const res = await apiFetch('/api/copy/trending', {
      method: 'PUT',
      body: JSON.stringify({ trending_hashtags: tags }),
    });
    if (trendInput) trendInput.value = (res.trending_hashtags || []).join(' ');
    flashButtonFeedback(btn, 'Salvo ✓');
    showToast('Tendencias do Dia salvas ✓');
  });

  loadGamesDb();
}

// ---- 4. Interactive 9:16 Visual Title Editor (270x480 <-> 1080x1920 overlay.json) ----

export function initOverlayEditor(STATE) {
  const SCALE = 4; // 270*4 = 1080, 480*4 = 1920
  const stage = document.getElementById('phone-stage-916');
  const el = document.getElementById('draggable-hook-el');
  const guide = document.getElementById('snap-guide-center');
  const warnEl = document.getElementById('overlay-safe-warning');
  const readout = document.getElementById('overlay-coords-readout');

  STATE.overlay = {
    text: 'OLHA ESSE FINAL ABSURDO...',
    x: 540,
    y: 250,
    font_family: 'Arial Black',
    font_size: 52,
    color: '#FFD700',
    outline_color: '#000000',
    outline_width: 4,
    animation: 'fade',
    duration_sec: 2.3,
    start_sec: 0.15,
  };

  if (!stage || !el) return;

  function renderPreview() {
    const ov = STATE.overlay;
    const pxX = ov.x / SCALE;
    const pxY = ov.y / SCALE;
    el.textContent = (ov.text || 'TITULO').toUpperCase();
    el.style.left = `${pxX}px`;
    el.style.top = `${pxY}px`;
    el.style.fontFamily = `"${ov.font_family}", Arial, sans-serif`;
    el.style.fontSize = `${Math.max(8, Math.round(ov.font_size / SCALE))}px`;
    el.style.color = ov.color;
    const ow = Math.max(1, Math.round(ov.outline_width / 2));
    el.style.textShadow = `-${ow}px -${ow}px 0 ${ov.outline_color}, ${ow}px -${ow}px 0 ${ov.outline_color}, -${ow}px ${ow}px 0 ${ov.outline_color}, ${ow}px ${ow}px 0 ${ov.outline_color}`;

    const estHalfW = Math.min(420, Math.max(120, el.offsetWidth * SCALE * 0.5));
    const outY = ov.y < 150 || ov.y > 400;
    const outX = ov.x - estHalfW < 120 || ov.x + estHalfW > 960;
    const unsafe = outY || outX;
    warnEl?.classList.toggle('hidden', !unsafe);
    if (readout) {
      readout.textContent = `Posicao 1080×1920: X=${ov.x} · Y=${ov.y} (${unsafe ? 'FORA DA SAFE ZONE!' : 'Safe Zone OK'})`;
    }
  }

  function syncInputsFromState() {
    const ov = STATE.overlay;
    const set = (id, v) => {
      const n = document.getElementById(id);
      if (n) n.value = v;
    };
    set('ov-text-input', ov.text);
    set('ov-font-select', ov.font_family);
    set('ov-size-input', ov.font_size);
    const sv = document.getElementById('ov-size-val');
    if (sv) sv.textContent = String(ov.font_size);
    set('ov-anim-select', ov.animation);
    set('ov-color-input', ov.color);
    set('ov-outline-color', ov.outline_color);
    set('ov-outline-width', ov.outline_width);
    set('ov-duration-sec', ov.duration_sec);
    renderPreview();
  }

  // Dragging with magnetic center snap (x=540 within +-24px in 1080 space)
  let dragging = false;
  el.addEventListener('pointerdown', (e) => {
    dragging = true;
    el.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  el.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const rect = stage.getBoundingClientRect();
    let rawX = Math.round(((e.clientX - rect.left) / rect.width) * 1080);
    let rawY = Math.round(((e.clientY - rect.top) / rect.height) * 1920);
    rawX = Math.max(60, Math.min(1020, rawX));
    rawY = Math.max(60, Math.min(1860, rawY));

    if (Math.abs(rawX - 540) <= 24) {
      rawX = 540;
      guide?.classList.add('visible');
    } else {
      guide?.classList.remove('visible');
    }
    STATE.overlay.x = rawX;
    STATE.overlay.y = rawY;
    renderPreview();
  });

  const endDrag = () => {
    if (!dragging) return;
    dragging = false;
    guide?.classList.remove('visible');
  };
  el.addEventListener('pointerup', endDrag);
  el.addEventListener('pointercancel', endDrag);

  // Live control listeners
  document.getElementById('ov-text-input')?.addEventListener('input', (e) => {
    STATE.overlay.text = e.target.value;
    renderPreview();
  });
  document.getElementById('ov-font-select')?.addEventListener('change', (e) => {
    STATE.overlay.font_family = e.target.value;
    renderPreview();
  });
  document.getElementById('ov-size-input')?.addEventListener('input', (e) => {
    STATE.overlay.font_size = Number(e.target.value) || 52;
    const sv = document.getElementById('ov-size-val');
    if (sv) sv.textContent = String(STATE.overlay.font_size);
    renderPreview();
  });
  document.getElementById('ov-anim-select')?.addEventListener('change', (e) => {
    STATE.overlay.animation = e.target.value;
  });
  document.getElementById('ov-color-input')?.addEventListener('input', (e) => {
    STATE.overlay.color = e.target.value;
    renderPreview();
  });
  document.getElementById('ov-outline-color')?.addEventListener('input', (e) => {
    STATE.overlay.outline_color = e.target.value;
    renderPreview();
  });
  document.getElementById('ov-outline-width')?.addEventListener('input', (e) => {
    STATE.overlay.outline_width = Number(e.target.value) || 4;
    renderPreview();
  });
  document.getElementById('ov-duration-sec')?.addEventListener('input', (e) => {
    STATE.overlay.duration_sec = Number(e.target.value) || 2.3;
  });

  document.getElementById('btn-overlay-center')?.addEventListener('click', () => {
    STATE.overlay.x = 540;
    STATE.overlay.y = 250;
    renderPreview();
  });

  document.getElementById('btn-overlay-save')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    const saved = await apiFetch('/api/overlay', {
      method: 'PUT',
      body: JSON.stringify(STATE.overlay),
    });
    STATE.overlay = saved;
    syncInputsFromState();
    flashButtonFeedback(btn, 'Salvo ✓');
    showToast('Posicao e estilo do titulo salvos em overlay.json ✓');
  });

  apiFetch('/api/overlay', {}, { silentXRay: true })
    .then((cfg) => {
      if (cfg && typeof cfg === 'object') {
        STATE.overlay = { ...STATE.overlay, ...cfg };
        syncInputsFromState();
      }
    })
    .catch(() => renderPreview());
}

// ---- 5. Modal "Ver mais 5 opcoes" ----

let _activeCopyCtx = null;

export async function openMoreCopyOptionsModal(ctx) {
  _activeCopyCtx = ctx;
  const bd = document.getElementById('copy-options-modal-backdrop');
  const list = document.getElementById('copy-options-list');
  if (!bd || !list) return;
  bd.classList.add('open');
  list.innerHTML = `<div class="empty-state" style="padding:24px;">Gerando 5 combinacoes unicas sem repeticao...</div>`;

  const res = await apiFetch('/api/copy/more-options', {
    method: 'POST',
    body: JSON.stringify({
      count: 5,
      game_name: ctx.game_name || '',
      profile_id: ctx.profile_id || 'generico',
      category: ctx.category || 'combate_intenso',
      platform_target: ctx.platform_target || 'youtube_shorts',
      exclude_titles: ctx.exclude_titles || [],
    }),
  });
  const opts = res.options || [];
  list.innerHTML = opts
    .map(
      (opt, idx) => `
      <div class="card" style="padding:12px 14px;" data-copy-opt-idx="${idx}">
        <div class="flex items-center justify-between gap-2 mb-2">
          <span class="badge badge-purple">Opcao #${idx + 1}</span>
          <span class="badge badge-cyan">Gancho: ${escapeHtml(opt.hook_2s)}</span>
        </div>
        <div style="font-weight:700;font-size:13.5px;margin-bottom:4px;">${escapeHtml(opt.title)}</div>
        <div class="text-xs" style="margin-bottom:4px;">${escapeHtml(opt.description)}</div>
        <div class="text-xs" style="color:#818CF8;margin-bottom:8px;">${escapeHtml((opt.hashtags || []).join(' '))}</div>
        <button class="btn btn-sm btn-success apply-copy-opt-btn" data-opt-idx="${idx}"><span>Usar Esta Opcao ✓</span></button>
      </div>`
    )
    .join('');

  list.querySelectorAll('.apply-copy-opt-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const chosen = opts[Number(btn.dataset.optIdx)];
      if (chosen && _activeCopyCtx?.onApply) {
        await _activeCopyCtx.onApply(chosen);
      }
      bd.classList.remove('open');
      showToast('Copy e hashtags aplicadas ✓');
    });
  });
}

export function initMoreCopyOptionsModal() {
  document.getElementById('btn-copy-more-close')?.addEventListener('click', () => {
    document.getElementById('copy-options-modal-backdrop')?.classList.remove('open');
  });
  document.getElementById('copy-options-modal-backdrop')?.addEventListener('click', (e) => {
    if (e.target.id === 'copy-options-modal-backdrop') {
      document.getElementById('copy-options-modal-backdrop')?.classList.remove('open');
    }
  });
  document.getElementById('btn-copy-more-reroll')?.addEventListener('click', () => {
    if (_activeCopyCtx) openMoreCopyOptionsModal(_activeCopyCtx);
  });
}

// ---- 6. PS4 Watch Folder UI ----

export function initWatcherUI(onProjectImported) {
  const toggle = document.getElementById('watcher-enabled-toggle');
  const folderIn = document.getElementById('watcher-folder-input');
  const badge = document.getElementById('watcher-status-badge');
  const list = document.getElementById('watcher-queue-list');

  function renderWatcher(st) {
    if (!st) return;
    if (toggle) toggle.checked = Boolean(st.enabled);
    if (folderIn && st.folder_path) folderIn.value = st.folder_path;
    if (badge) {
      if (st.enabled && st.folder_exists) {
        badge.className = 'badge badge-green';
        badge.textContent = `Monitorando (${st.pending_count || 0} na fila)`;
      } else if (st.enabled && !st.folder_exists) {
        badge.className = 'badge badge-amber';
        badge.textContent = 'Aguardando Pendrive/Pasta';
      } else {
        badge.className = 'badge badge-purple';
        badge.textContent = 'Desligado';
      }
    }
    if (!list) return;
    const pending = (st.queue || []).filter((q) => q.status === 'pendente');
    if (!pending.length) {
      list.innerHTML = `<div class="text-xs">Nenhum novo clipe pendente na pasta monitorada.</div>`;
      return;
    }
    list.innerHTML = pending
      .slice(0, 6)
      .map(
        (it) => `
        <div class="toggle-row" style="padding:8px 10px;">
          <div>
            <div style="font-weight:700;font-size:12.5px;">${escapeHtml(it.file_name)}</div>
            <div class="text-xs">${it.size_mb} MB</div>
          </div>
          <div class="flex gap-2">
            <button class="btn btn-sm btn-success wq-import-btn" data-wq-id="${escapeHtml(it.id)}"><span>Importar</span></button>
            <button class="btn btn-sm wq-dismiss-btn" data-wq-id="${escapeHtml(it.id)}"><span>×</span></button>
          </div>
        </div>`
      )
      .join('');

    list.querySelectorAll('.wq-import-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const res = await apiFetch(`/api/watcher/queue/${btn.dataset.wqId}/import`, { method: 'POST' });
        if (res.project && onProjectImported) onProjectImported(res.project);
        renderWatcher(res.watcher);
        showToast('Video da pasta PS4 importado ✓');
      });
    });
    list.querySelectorAll('.wq-dismiss-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const st2 = await apiFetch(`/api/watcher/queue/${btn.dataset.wqId}`, { method: 'DELETE' });
        renderWatcher(st2);
      });
    });
  }

  async function refreshWatcher() {
    try {
      const st = await apiFetch('/api/watcher/status', {}, { silentXRay: true });
      renderWatcher(st);
    } catch {
      // ignore
    }
  }

  toggle?.addEventListener('change', async (e) => {
    const st = await apiFetch('/api/watcher/config', {
      method: 'PUT',
      body: JSON.stringify({ enabled: e.target.checked, folder_path: folderIn?.value }),
    });
    renderWatcher(st);
    showToast(e.target.checked ? 'Monitoramento PS4 ativado ✓' : 'Monitoramento PS4 pausado.');
  });

  document.getElementById('btn-watcher-save')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    const st = await apiFetch('/api/watcher/config', {
      method: 'PUT',
      body: JSON.stringify({ folder_path: folderIn?.value }),
    });
    renderWatcher(st);
    flashButtonFeedback(btn, 'Salvo ✓');
  });

  document.getElementById('btn-watcher-scan')?.addEventListener('click', async () => {
    const res = await apiFetch('/api/watcher/scan-now', { method: 'POST' });
    await refreshWatcher();
    showToast(res.folder_exists ? `Varredura concluida: ${res.added} novo(s) video(s).` : 'Pasta ainda nao conectada.');
  });

  refreshWatcher();
  setInterval(refreshWatcher, 12000);
}

// ---- 7. Guia "Pode / Nao Pode" & 3-Balloon First-Run Tour ----

const TOUR_STEPS = [
  {
    title: '1. Escolha o Jogo & Destino (Passo 1)',
    desc: 'Informe o jogo (ou clique em Detectar com a IA), revise a Ficha do Jogo e escolha Modo Hibrido, TikTok 20–32s, TikTok Monetizavel 61–90s ou YouTube Shorts.',
  },
  {
    title: '2. Posicione o Titulo 9:16 & Layout (Passo 2)',
    desc: 'Escolha entre Fit, Big (~4:3 HUD), Fill ou Auto (Lanczos) e arraste o titulo direto na previa 9:16 dentro da Safe Zone verde.',
  },
  {
    title: '3. Exporte em Lote & Publique sem Repeticao',
    desc: 'Use "Ver mais 5 opcoes" para variar titulos/hashtags sem repeticao (Jaccard < 0.35), acompanhe o Qwen no topo e publique com protecao de conta.',
  },
];

export function initGuideAndTour() {
  const guideBd = document.getElementById('guide-modal-backdrop');
  const balloon = document.getElementById('tour-balloon');
  let stepIdx = 0;

  function renderTour() {
    if (!balloon) return;
    const s = TOUR_STEPS[stepIdx];
    document.getElementById('tour-step-badge').textContent = `Passo ${stepIdx + 1} de ${TOUR_STEPS.length}`;
    document.getElementById('tour-title').textContent = s.title;
    document.getElementById('tour-desc').textContent = s.desc;
    const prevBtn = document.getElementById('tour-prev-btn');
    const nextBtn = document.getElementById('tour-next-btn');
    if (prevBtn) prevBtn.disabled = stepIdx === 0;
    if (nextBtn) nextBtn.querySelector('span').textContent = stepIdx === TOUR_STEPS.length - 1 ? 'Concluir ✓' : 'Proximo';
    balloon.classList.remove('hidden');
  }

  function finishTour() {
    balloon?.classList.add('hidden');
    try {
      localStorage.setItem('cliprush_tour_done', '1');
    } catch {
      // ignore
    }
  }

  document.getElementById('btn-open-guide')?.addEventListener('click', () => guideBd?.classList.add('open'));
  document.getElementById('btn-guide-close')?.addEventListener('click', () => guideBd?.classList.remove('open'));
  guideBd?.addEventListener('click', (e) => {
    if (e.target.id === 'guide-modal-backdrop') guideBd.classList.remove('open');
  });
  document.getElementById('btn-replay-tour')?.addEventListener('click', () => {
    guideBd?.classList.remove('open');
    stepIdx = 0;
    renderTour();
  });

  document.getElementById('tour-skip-btn')?.addEventListener('click', finishTour);
  document.getElementById('tour-prev-btn')?.addEventListener('click', () => {
    if (stepIdx > 0) {
      stepIdx -= 1;
      renderTour();
    }
  });
  document.getElementById('tour-next-btn')?.addEventListener('click', () => {
    if (stepIdx < TOUR_STEPS.length - 1) {
      stepIdx += 1;
      renderTour();
    } else {
      finishTour();
    }
  });

  // First-run auto tour (only once per browser profile)
  try {
    if (!localStorage.getItem('cliprush_tour_done')) {
      setTimeout(() => renderTour(), 650);
    }
  } catch {
    // ignore
  }
}
