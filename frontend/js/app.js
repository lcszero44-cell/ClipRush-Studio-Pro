/**
 * ClipRush Studio Pro - Main Modular Frontend Controller
 */
import { apiFetch, getSessionToken, pollJob } from './api.js';
import {
  initGameSheetUI,
  initGuideAndTour,
  initMoreCopyOptionsModal,
  initOverlayEditor,
  initSystemHealthModal,
  initThemeSwitcher,
  initWatcherUI,
  openMoreCopyOptionsModal,
  openSystemHealthModal,
  setHealthSnapshot,
} from './p1_features.js';
import {
  initAnalyticsUI,
  initBackupZipUI,
  initDiagnosticsUI,
  initGrowthUI,
  loadAnalyticsView,
  loadDiagnosticsView,
  loadGrowthPanel,
  loadProjectStorageBreakdown,
} from './p2_features.js';
import { initTooltips } from './tooltips.js';
import { copyToClipboard, flashButtonFeedback, initXRayPanel, showToast, showXRayError } from './xray.js';

const STATE = {
  view: 'novo-projeto',
  step: 1,
  mode: 'viral_9_16', // viral_9_16 | long_16_9
  target_mode: 'hibrido', // hibrido | tiktok_reels | tiktok_long | youtube_shorts
  scene_mode: 'keep_dialogue', // keep_dialogue | prioritize_gameplay | ignore_cutscenes
  libraryFilter: 'all', // all | tiktok_reels | tiktok_long | youtube_shorts
  style: 'fit', // fit | big | fill | auto | clipe_limpo
  subtitles_mode: 'whisper', // whisper (default ON) | off
  subtitle_style: 'amarelo_viral', // amarelo_viral | branco_clean | destaque_keyword
  enable_hook: true,
  enable_impact_zoom: true,
  enable_preview_flash: true,
  enable_infinite_loop: true,
  enable_cta_comment: true,
  enable_impact_sfx: true,
  enable_smart_pacing: true,
  enable_progress_bar: true,
  enable_audio_norm: true,
  enable_action_tracking: true,
  enable_slowmo: true,
  enable_anti_factory: true,
  enable_seamless_loop: false,
  profiles: [],
  selectedProfileId: 'generico',
  project: null,
  selectedMomentIds: new Set(),
  categoryFilter: 'all',
  clips: [],
  schedule: [],
  autopilot: null,
  disk: null,
  settings: null,
  activeJobId: null,
  progressTimerInterval: null,
  progressStartedAt: 0,
};

const PUBLISH_URLS = {
  youtube_studio: 'https://studio.youtube.com',
  youtube_shorts: 'https://studio.youtube.com',
  youtube_long: 'https://studio.youtube.com',
  tiktok_studio: 'https://www.tiktok.com/tiktokstudio/upload',
  tiktok: 'https://www.tiktok.com/tiktokstudio/upload',
  facebook_reels: 'https://business.facebook.com/latest/reels_composer',
  instagram_reels: 'https://business.facebook.com/latest/reels_composer',
};

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---- Navigation & Stepper ----

function syncHeaderStepper() {
  const stepper = document.querySelector('.header-stepper');
  const inStudio = STATE.view === 'novo-projeto';
  stepper?.classList.toggle('inactive-mode', !inStudio);
  [1, 2, 3].forEach((n) => {
    const btn = document.getElementById(`step-btn-${n}`);
    btn?.classList.toggle('active', inStudio && n === STATE.step);
    btn?.classList.toggle('completed', inStudio && n < STATE.step);
  });
}

function setView(viewName) {
  STATE.view = viewName;
  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.view === viewName);
  });
  document.querySelectorAll('.view-section').forEach((sec) => {
    sec.classList.toggle('active', sec.id === `view-${viewName}`);
  });
  syncHeaderStepper();

  if (viewName === 'biblioteca') loadClipsLibrary();
  if (viewName === 'desempenho') loadAnalyticsView(STATE);
  if (viewName === 'agendador') {
    loadSchedule();
    loadGrowthPanel(STATE);
  }
  if (viewName === 'limpeza') {
    loadDiskScan();
    loadProjectStorageBreakdown();
  }
  if (viewName === 'diagnostico') loadDiagnosticsView();
  if (viewName === 'configuracoes') loadSettings();
}

function setStep(stepNum) {
  STATE.step = stepNum;
  if (STATE.view !== 'novo-projeto') setView('novo-projeto');

  [1, 2, 3].forEach((n) => {
    const panel = document.getElementById(`step-panel-${n}`);
    panel?.classList.toggle('hidden', n !== stepNum);
  });
  syncHeaderStepper();

  if (stepNum === 3) renderRankingView();
}

// ---- Health Status Chips ----

async function refreshHealth() {
  try {
    const st = await apiFetch('/api/health/status', {}, { silentXRay: true });
    const sysChip = document.getElementById('chip-system');
    const ffChip = document.getElementById('chip-ffmpeg');
    const ffLbl = document.getElementById('chip-ffmpeg-label');

    if (sysChip) sysChip.className = 'status-chip ok';
    setHealthSnapshot(st);

    if (st.ffmpeg?.ok) {
      if (ffChip) ffChip.className = 'status-chip ok';
      if (ffLbl) ffLbl.textContent = 'FFmpeg OK';
    } else {
      if (ffChip) ffChip.className = 'status-chip err';
      if (ffLbl) ffLbl.textContent = 'FFmpeg Ausente';
    }
  } catch {
    const sysChip = document.getElementById('chip-system');
    if (sysChip) sysChip.className = 'status-chip err';
  }
}

// ---- Profiles ----

async function loadProfiles() {
  const data = await apiFetch('/api/profiles');
  STATE.profiles = data.profiles || [];
  renderProfilesGrid();
}

function renderProfilesGrid() {
  const grid = document.getElementById('profiles-grid');
  if (!grid) return;
  grid.innerHTML = STATE.profiles
    .map(
      (p) => `
      <div class="profile-card ${p.id === STATE.selectedProfileId ? 'selected' : ''}"
           data-profile-id="${escapeHtml(p.id)}"
           data-tooltip="${escapeHtml(p.description)}">
        <div class="profile-top">
          <div class="flex items-center gap-2">
            <span class="profile-dot" style="background:${escapeHtml(p.accent)};box-shadow:0 0 10px ${escapeHtml(p.accent)}88;"></span>
            <span class="profile-name">${escapeHtml(p.name)}</span>
          </div>
          ${p.id === STATE.selectedProfileId ? '<span class="badge badge-purple">Ativo</span>' : ''}
        </div>
        <div class="profile-desc">${escapeHtml(p.description)}</div>
      </div>`
    )
    .join('');

  grid.querySelectorAll('.profile-card').forEach((card) => {
    card.addEventListener('click', () => {
      STATE.selectedProfileId = card.dataset.profileId;
      renderProfilesGrid();
      showToast(`Perfil selecionado: ${STATE.profiles.find((x) => x.id === STATE.selectedProfileId)?.short_name || ''}`);
    });
  });
}

// ---- Projects & Import ----

async function loadRecentProjects() {
  try {
    const data = await apiFetch('/api/projects', {}, { silentXRay: true });
    const projects = data.projects || [];
    const wrap = document.getElementById('recent-projects-wrap');
    const list = document.getElementById('recent-projects-list');
    if (!projects.length) {
      wrap.classList.add('hidden');
      return;
    }
    wrap.classList.remove('hidden');
    list.innerHTML = projects
      .slice(0, 8)
      .map(
        (p) => `
        <button class="btn btn-sm" data-resume-project="${escapeHtml(p.id)}"
          data-tooltip="Retoma o projeto '${escapeHtml(p.name)}' (${p.moments?.length || 0} momentos analisados).">
          <span>${escapeHtml(p.name)}</span>
          <span class="badge badge-purple">${p.moments?.length || 0} clipes</span>
        </button>`
      )
      .join('');
    list.querySelectorAll('[data-resume-project]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const proj = await apiFetch(`/api/projects/${btn.dataset.resumeProject}`);
        applyLoadedProject(proj);
        if (proj.moments?.length) setStep(3);
        else setStep(2);
      });
    });
  } catch {
    // ignore silent load
  }
}

function showImportLoading(message, pctLabel = 'Lendo...', pctWidth = 55) {
  const banner = document.getElementById('import-loading-banner');
  const msgEl = document.getElementById('import-loading-msg');
  const pctEl = document.getElementById('import-loading-pct');
  const fillEl = document.getElementById('import-loading-fill');
  if (!banner) return;
  banner.classList.remove('hidden');
  if (msgEl) msgEl.textContent = message;
  if (pctEl) pctEl.textContent = pctLabel;
  if (fillEl) fillEl.style.width = `${Math.max(8, Math.min(100, pctWidth))}%`;
}

function hideImportLoading() {
  document.getElementById('import-loading-banner')?.classList.add('hidden');
}

function formatFileSize(meta) {
  const mb = Number(meta?.size_mb || 0);
  const gb = meta?.size_gb !== undefined ? Number(meta.size_gb) : Number((mb / 1024).toFixed(2));
  if (mb >= 1024) {
    return `${gb.toFixed(2)} GB`;
  }
  return `${gb.toFixed(2)} GB (${mb} MB)`;
}

function applyLoadedProject(proj) {
  STATE.project = proj;
  if (proj.profile_id) {
    STATE.selectedProfileId = proj.profile_id;
    renderProfilesGrid();
  }
  const banner = document.getElementById('active-video-banner');
  banner.classList.remove('hidden');
  document.getElementById('active-video-name').textContent = proj.original_filename || proj.name;
  const meta = proj.meta || {};
  document.getElementById('active-video-meta').textContent =
    `${meta.duration_str || '00:00'} • ${meta.width || 1920}x${meta.height || 1080} • ${meta.fps || 30}fps • ${formatFileSize(meta)}`;

  const localInput = document.getElementById('local-path-input');
  if (localInput && proj.video_path) localInput.value = proj.video_path;

  // Automatically enable next step & scale extraction buttons
  ['btn-step1-next', 'btn-auto-next10-step1', 'btn-auto-all-step1', 'btn-auto-quick-step1'].forEach((id) => {
    const b = document.getElementById(id);
    if (b) b.disabled = false;
  });

  document.getElementById('auto-profile-badge').textContent = `Perfil: ${
    STATE.profiles.find((p) => p.id === STATE.selectedProfileId)?.short_name || STATE.selectedProfileId
  }`;

  const estBanner = document.getElementById('time-estimate-banner');
  if (estBanner) {
    if (proj.time_estimate?.summary_pt) {
      estBanner.textContent = proj.time_estimate.summary_pt;
      estBanner.classList.remove('hidden');
    } else {
      estBanner.classList.add('hidden');
    }
  }
}

async function browseNativeLocalFile() {
  showImportLoading('Abrindo seletor nativo do Windows... escolha sua gameplay e clique em Abrir.', 'Aguardando', 35);
  try {
    const res = await apiFetch('/api/project/browse-local-file', {
      method: 'POST',
      body: JSON.stringify({
        profile_id: STATE.selectedProfileId === 'generico' ? 'auto' : STATE.selectedProfileId,
      }),
    });
    if (res?.cancelled || !res?.project) {
      showToast('Selecao de arquivo cancelada.');
      return;
    }
    showImportLoading('Carregando gameplay e lendo duracao/FPS via FFprobe...', '100%', 100);
    applyLoadedProject(res.project);
    showToast(`Gameplay "${res.project.name}" pronta! (Leitura instantanea sem copiar arquivo)`);
    loadRecentProjects();
  } finally {
    hideImportLoading();
  }
}

function handleFileUpload(file) {
  if (!file) return Promise.resolve();
  showImportLoading(`Carregando "${file.name}" e lendo duracao/FPS via FFprobe...`, '0%', 10);

  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('profile_id', STATE.selectedProfileId === 'generico' ? 'auto' : STATE.selectedProfileId);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/projects/upload', true);
    xhr.setRequestHeader('Cache-Control', 'no-store');
    const tok = getSessionToken();
    if (tok) xhr.setRequestHeader('X-ClipRush-Token', tok);

    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable && ev.total > 0) {
        const pct = Math.min(95, Math.round((ev.loaded / ev.total) * 100));
        const label = pct >= 95 ? 'FFprobe...' : `${pct}%`;
        const msg =
          pct >= 95
            ? 'Carregando gameplay e lendo duracao/FPS via FFprobe...'
            : `Carregando gameplay "${file.name}" (${pct}%)...`;
        showImportLoading(msg, label, Math.max(12, pct));
      }
    };

    xhr.onload = () => {
      hideImportLoading();
      let data = null;
      try {
        data = JSON.parse(xhr.responseText || '{}');
      } catch {
        data = null;
      }

      if (xhr.status >= 200 && xhr.status < 300 && data && data.id) {
        applyLoadedProject(data);
        showToast(`Gameplay "${data.name}" carregada e pronta!`);
        loadRecentProjects();
        resolve(data);
      } else {
        const payload =
          data && data.explanation_pt
            ? data
            : {
                category: 'UPLOAD',
                explanation_pt: `Falha ao importar "${file.name}" (HTTP ${xhr.status}). Use o botao "Escolher Arquivo do PC (Instantaneo)" para gravacoes grandes.`,
                file: 'frontend/js/app.js',
                line: 278,
                technical_detail: xhr.responseText || `HTTP ${xhr.status}`,
                ai_copy_text: `Erro no upload de ${file.name}: HTTP ${xhr.status}\n${xhr.responseText || ''}`,
              };
        showXRayError(payload);
        reject(payload);
      }
    };

    xhr.onerror = () => {
      hideImportLoading();
      const payload = {
        category: 'UPLOAD',
        explanation_pt: `Erro de conexao ao enviar "${file.name}". Para videos grandes (GBs), clique em "Escolher Arquivo do PC (Instantaneo)" que abre direto do disco sem copiar.`,
        file: 'frontend/js/app.js',
        line: 295,
        technical_detail: 'XMLHttpRequest network error during multipart upload',
        ai_copy_text: `XMLHttpRequest network error ao enviar ${file.name} para /api/projects/upload`,
      };
      showXRayError(payload);
      reject(payload);
    };

    xhr.send(fd);
  });
}

async function handleLocalImport() {
  const input = document.getElementById('local-path-input');
  const filePath = input.value.trim();
  if (!filePath) {
    showToast('Informe o caminho completo do arquivo de video no disco.', 'error');
    return;
  }
  showImportLoading('Carregando gameplay e lendo duracao/FPS via FFprobe...', 'FFprobe', 65);
  try {
    const proj = await apiFetch('/api/projects/import-local', {
      method: 'POST',
      body: JSON.stringify({
        file_path: filePath,
        profile_id: STATE.selectedProfileId === 'generico' ? 'auto' : STATE.selectedProfileId,
      }),
    });
    applyLoadedProject(proj);
    showToast(`Gameplay "${proj.name}" importada direto do disco!`);
    loadRecentProjects();
  } finally {
    hideImportLoading();
  }
}

// ---- Global Alive Progress Panel (Stopwatch + Shimmer + Instant Cancel) ----

function formatElapsed(ms) {
  const secs = Math.max(0, Math.floor(ms / 1000));
  const m = String(Math.floor(secs / 60)).padStart(2, '0');
  const s = String(secs % 60).padStart(2, '0');
  return `${m}:${s}`;
}

function startLiveProgress(initialMsg) {
  STATE.activeJobId = null;
  STATE.progressStartedAt = Date.now();
  const box = document.getElementById('global-live-progress');
  const msgEl = document.getElementById('global-progress-msg');
  const pctEl = document.getElementById('global-progress-pct');
  const fillEl = document.getElementById('global-progress-fill');
  const timerEl = document.getElementById('global-progress-timer');
  const cancelBtn = document.getElementById('btn-cancel-job');

  if (box) box.classList.remove('hidden');
  if (msgEl) msgEl.textContent = initialMsg || 'Etapa 1/4: Iniciando processamento...';
  if (pctEl) pctEl.textContent = '2%';
  if (fillEl) fillEl.style.width = '2%';
  if (timerEl) timerEl.textContent = '00:00';
  if (cancelBtn) {
    cancelBtn.disabled = false;
    cancelBtn.querySelector('span').textContent = 'Cancelar Processamento';
  }

  if (STATE.progressTimerInterval) clearInterval(STATE.progressTimerInterval);
  STATE.progressTimerInterval = setInterval(() => {
    if (timerEl) timerEl.textContent = formatElapsed(Date.now() - STATE.progressStartedAt);
  }, 500);
}

function updateLiveProgress(pct, msg) {
  const msgEl = document.getElementById('global-progress-msg');
  const pctEl = document.getElementById('global-progress-pct');
  const fillEl = document.getElementById('global-progress-fill');
  const safePct = Math.max(2, Math.min(100, Number(pct || 0)));
  if (msgEl && msg) msgEl.textContent = msg;
  if (pctEl) pctEl.textContent = `${safePct}%`;
  if (fillEl) fillEl.style.width = `${safePct}%`;
}

function stopLiveProgress(delayMs = 1200) {
  STATE.activeJobId = null;
  if (STATE.progressTimerInterval) {
    clearInterval(STATE.progressTimerInterval);
    STATE.progressTimerInterval = null;
  }
  setTimeout(() => {
    if (!STATE.activeJobId) {
      document.getElementById('global-live-progress')?.classList.add('hidden');
    }
  }, delayMs);
}

async function cancelActiveJob() {
  if (!STATE.activeJobId) return;
  const cancelBtn = document.getElementById('btn-cancel-job');
  if (cancelBtn) {
    cancelBtn.disabled = true;
    cancelBtn.querySelector('span').textContent = 'Cancelando...';
  }
  const jid = STATE.activeJobId;
  try {
    await apiFetch(`/api/jobs/${jid}/cancel`, { method: 'POST' }, { silentXRay: true });
    showToast('Processamento cancelado! O servidor continua livre.');
  } finally {
    stopLiveProgress(500);
  }
}

// ---- Analysis Job ----

async function startAnalysis() {
  if (!STATE.project) {
    showToast('Importe uma gameplay na Etapa 1 antes de analisar.', 'error');
    setStep(1);
    return;
  }
  setStep(2);
  const btn = document.getElementById('btn-start-analysis');
  btn.disabled = true;
  startLiveProgress('Etapa 1/4: Lendo video e buscando lutas, tiros e cenas de dialogo...');

  try {
    const res = await apiFetch(`/api/projects/${STATE.project.id}/analyze`, {
      method: 'POST',
      body: JSON.stringify({
        profile_id: STATE.selectedProfileId,
        target_mode: STATE.target_mode,
        scene_mode: STATE.scene_mode,
        game_name: STATE.gameName || '',
      }),
    });
    STATE.activeJobId = res.job_id;
    const updatedProject = await pollJob(res.job_id, (job) => {
      updateLiveProgress(job.progress, job.message);
    });
    if (updatedProject?.cancelled) {
      showToast('Analise cancelada pelo usuario.');
      return;
    }
    STATE.project = updatedProject;
    STATE.selectedMomentIds = new Set();
    showToast(`Analise concluida! ${updatedProject.moments?.length || 0} cenas completas (25s-55s) detectadas.`);
    setStep(3);
  } finally {
    btn.disabled = false;
    stopLiveProgress(1200);
  }
}

async function startAutoPipeline(topN = 10) {
  if (!STATE.project) {
    showToast('Importe uma gameplay na Etapa 1 antes de iniciar o lote automatico.', 'error');
    setStep(1);
    return;
  }

  const autoButtons = [
    document.getElementById('btn-auto-next10-step1'),
    document.getElementById('btn-auto-all-step1'),
    document.getElementById('btn-auto-quick-step1'),
    document.getElementById('btn-auto-next10-step2'),
    document.getElementById('btn-auto-all-step2'),
    document.getElementById('btn-start-analysis'),
  ].filter(Boolean);

  autoButtons.forEach((b) => (b.disabled = true));
  startLiveProgress(
    topN <= 0
      ? 'Etapa 1/4: Lendo video completo para gerar TODOS os momentos ineditos...'
      : `Etapa 1/4: Lendo video e preparando os proximos ${topN} clipes ineditos...`
  );

  try {
    const res = await apiFetch(`/api/projects/${STATE.project.id}/auto-pipeline`, {
      method: 'POST',
      body: JSON.stringify({
        profile_id: STATE.selectedProfileId,
        target_mode: STATE.target_mode,
        scene_mode: STATE.scene_mode,
        game_name: STATE.gameName || '',
        top_n: topN,
        skip_exported: true,
        style: STATE.style,
        subtitles_mode: STATE.subtitles_mode,
        subtitle_style: STATE.subtitle_style,
        enable_hook: STATE.enable_hook,
        enable_impact_zoom: STATE.enable_impact_zoom,
        enable_preview_flash: STATE.enable_preview_flash,
        enable_infinite_loop: STATE.enable_infinite_loop,
        enable_cta_comment: STATE.enable_cta_comment,
        enable_impact_sfx: STATE.enable_impact_sfx,
        enable_smart_pacing: STATE.enable_smart_pacing,
        enable_progress_bar: STATE.enable_progress_bar,
        enable_audio_norm: STATE.enable_audio_norm,
        enable_action_tracking: STATE.enable_action_tracking,
        enable_slowmo: STATE.enable_slowmo,
        enable_anti_factory: STATE.enable_anti_factory,
        enable_seamless_loop: STATE.enable_seamless_loop,
        overlay_config: STATE.overlay || null,
      }),
    });
    STATE.activeJobId = res.job_id;

    const out = await pollJob(res.job_id, (job) => {
      updateLiveProgress(job.progress, job.message);
    });

    if (out?.cancelled) {
      showToast('Lote cancelado pelo usuario.');
      await loadClipsLibrary();
      return;
    }

    if (out?.project) {
      STATE.project = out.project;
    }
    showToast(
      `Concluido! ${out?.count || 0} clipe(s) ineditos prontos na Biblioteca (restam ${out?.remaining_unseen ?? 0} ineditos).`
    );
    await loadClipsLibrary();
    setView('biblioteca');
  } finally {
    autoButtons.forEach((b) => (b.disabled = false));
    stopLiveProgress(1400);
  }
}

// ---- Step 3: Ranking, Batch Export & Mode 2 YouTube Long ----

function renderRankingView() {
  const proj = STATE.project;
  const grid = document.getElementById('moments-grid');
  const summary = document.getElementById('ranking-summary');
  const longPanel = document.getElementById('long-export-panel');

  if (!proj || !proj.moments || !proj.moments.length) {
    summary.textContent = 'Importe uma gameplay e clique em "Analisar Gameplay com IA" para listar todos os momentos epicos.';
    longPanel.classList.add('hidden');
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1;">
        <div style="font-size:15px;font-weight:700;color:#fff;margin-bottom:6px;">Nenhum momento analisado ainda</div>
        <div>Volte na Etapa 1 ou 2, selecione sua gravacao de PS4 e clique em <strong>Analisar Gameplay com IA</strong>.</div>
      </div>`;
    return;
  }

  const profileObj = STATE.profiles.find((p) => p.id === proj.profile_id);
  const unseenTotal = proj.moments.filter((m) => !m.already_exported).length;
  const exportedTotal = proj.moments.length - unseenTotal;
  summary.textContent = `${proj.moments.length} momentos encontrados (${unseenTotal} ineditos • ${exportedTotal} ja exportados • ${proj.boring_skipped_sec || 0}s de menus ignorados) • Perfil: ${profileObj?.name || proj.profile_id}`;

  // Populate Mode 2 panel
  if (STATE.mode === 'long_16_9') {
    longPanel.classList.remove('hidden');
  }
  populateLongPanel(proj);

  // Filter moments
  const filtered =
    STATE.categoryFilter === 'all'
      ? proj.moments
      : proj.moments.filter((m) => m.category === STATE.categoryFilter);

  document.getElementById('selected-count').textContent = String(STATE.selectedMomentIds.size);

  if (!filtered.length) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">Nenhum momento encontrado neste filtro de categoria.</div>`;
    return;
  }

  grid.innerHTML = filtered
    .map((m) => {
      const checked = STATE.selectedMomentIds.has(m.id);
      const catBadgeClass =
        m.category === 'morte_cinematografica'
          ? 'badge-red'
          : m.category === 'susto_tensao'
          ? 'badge-amber'
          : m.category === 'acrobacia_cinema'
          ? 'badge-cyan'
          : 'badge-purple';

      return `
        <article class="moment-card ${checked ? 'checked' : ''}" data-moment-id="${escapeHtml(m.id)}">
          <div class="moment-thumb-wrap">
            ${
              m.thumb_url
                ? `<img src="${escapeHtml(m.thumb_url)}" alt="Momento #${m.rank}" class="moment-thumb" />`
                : `<div style="height:100%;display:flex;align-items:center;justify-content:center;color:var(--text-muted);">Sem preview</div>`
            }
            <span class="moment-rank-tag">#${m.rank}</span>
            <span class="moment-score-tag">Score ${m.score}</span>
            <span class="moment-time-tag">${escapeHtml(m.start_str)} → ${escapeHtml(m.end_str)} (${Math.round(m.duration)}s)</span>
          </div>
          <div class="moment-body">
            <div class="flex items-center justify-between gap-2 flex-wrap">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="badge ${catBadgeClass}">${escapeHtml(m.category_label)}</span>
                ${
                  m.platform_target === 'tiktok_reels'
                    ? `<span class="badge badge-green platform-badge">${escapeHtml(m.platform_badge || 'Ideal TikTok / Reels')}</span>`
                    : `<span class="badge badge-blue platform-badge">${escapeHtml(m.platform_badge || 'Ideal YouTube Shorts')}</span>`
                }
                ${
                  m.already_exported
                    ? '<span class="badge badge-amber export-status-badge" data-tooltip="Este trecho ja foi exportado antes e sera pulado automaticamente no lote de clipes ineditos.">Ja Exportado</span>'
                    : '<span class="badge badge-green export-status-badge" data-tooltip="Cena 100% inedita ainda nao cortada deste video.">Inedito</span>'
                }
                ${
                  m.learned_boost
                    ? `<span class="badge badge-cyan" data-tooltip="Ajuste automatico baseado no seu historico real da aba Desempenho.">Ranking IA ${m.learned_boost > 0 ? '+' : ''}${m.learned_boost}</span>`
                    : ''
                }
                ${
                  m.copyright_risk?.flagged
                    ? `<span class="badge badge-amber copyright-risk-badge" data-tooltip="${escapeHtml(m.copyright_risk.reason_pt || 'Possivel risco de direitos autorais (musica licenciada ou cutscene longa).')}">⚠ Risco Copyright (${escapeHtml(m.copyright_risk.level || 'medio')})</span>`
                    : ''
                }
              </div>
              <label class="flex items-center gap-2 text-xs" style="cursor:pointer;"
                data-tooltip="Marque este momento para incluir no botao Gerar Selecionados.">
                <input type="checkbox" class="moment-check" data-mid="${escapeHtml(m.id)}" ${checked ? 'checked' : ''} />
                <span>Selecionar</span>
              </label>
            </div>

            <div>
              <label class="text-xs">GANCHO CURIOSIDADE ABERTA (2,5s TOPO • MAX 6 PALAVRAS)</label>
              <input class="input moment-hook-input" data-mid="${escapeHtml(m.id)}" value="${escapeHtml(m.hook_2s)}"
                style="margin-top:3px;font-weight:700;font-size:12px;padding:6px 9px;"
                data-tooltip="Edite o gancho de curiosidade aberta exibido nos 2,5s iniciais no topo." />
            </div>

            <div>
              <label class="text-xs">GATILHO VISUAL DE COMENTARIOS (CTA 2s FINAIS)</label>
              <input class="input moment-cta-input" data-mid="${escapeHtml(m.id)}" value="${escapeHtml(m.cta_comment || 'Voce escaparia dessa?')}"
                style="margin-top:3px;font-size:12px;padding:6px 9px;color:#67E8F9;"
                data-tooltip="Pergunta curta exibida no badge discreto dos 2 segundos finais para estimular comentarios." />
            </div>

            <div>
              <label class="text-xs">TITULO VIRAL</label>
              <input class="input moment-title-input" data-mid="${escapeHtml(m.id)}" value="${escapeHtml(m.title)}"
                style="margin-top:3px;font-size:12.5px;padding:6px 9px;"
                data-tooltip="Titulo gerado pelo Qwen 3.5 pronto para YouTube Shorts, TikTok e Reels." />
            </div>

            <div class="text-xs" style="color:var(--text-secondary);">${escapeHtml(m.reason)}</div>

            <div class="flex flex-wrap gap-2 mt-2" style="margin-top:auto;padding-top:6px;">
              <button class="btn btn-sm btn-primary btn-render-one" data-mid="${escapeHtml(m.id)}" style="flex:1;"
                data-tooltip="Renderiza imediatamente este clipe em 9:16 com o estilo e efeitos escolhidos.">
                <span>Renderizar #${m.rank}</span>
              </button>
              <button class="btn btn-sm btn-cyan btn-regen-moment" data-mid="${escapeHtml(m.id)}"
                data-tooltip="Pede ao Qwen 3.5 Vision uma nova sugestao criativa de gancho, titulo, descricao e hashtags para esta cena.">
                <span>Refazer Texto / Gerar Outra Opcao</span>
              </button>
              <button class="btn btn-sm btn-copy-moment-kit" data-mid="${escapeHtml(m.id)}"
                data-tooltip="Copia Titulo Viral + Descricao com gancho + Hashtags deste momento.">
                <span>Copiar Tudo</span>
              </button>
            </div>
          </div>
        </article>`;
    })
    .join('');

  // Bind card events
  grid.querySelectorAll('.moment-check').forEach((chk) => {
    chk.addEventListener('change', () => {
      const mid = chk.dataset.mid;
      if (chk.checked) STATE.selectedMomentIds.add(mid);
      else STATE.selectedMomentIds.delete(mid);
      chk.closest('.moment-card')?.classList.toggle('checked', chk.checked);
      document.getElementById('selected-count').textContent = String(STATE.selectedMomentIds.size);
    });
  });

  grid.querySelectorAll('.moment-hook-input').forEach((inp) => {
    inp.addEventListener('change', () => patchMoment(inp.dataset.mid, { hook_2s: inp.value }));
  });
  grid.querySelectorAll('.moment-cta-input').forEach((inp) => {
    inp.addEventListener('change', () => patchMoment(inp.dataset.mid, { cta_comment: inp.value }));
  });
  grid.querySelectorAll('.moment-title-input').forEach((inp) => {
    inp.addEventListener('change', () => patchMoment(inp.dataset.mid, { title: inp.value }));
  });
  grid.querySelectorAll('.btn-render-one').forEach((btn) => {
    btn.addEventListener('click', () => runBatchRender({ moment_ids: [btn.dataset.mid] }));
  });
  grid.querySelectorAll('.btn-regen-moment').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const mid = btn.dataset.mid;
      const origText = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Gerando nova opcao...';
      try {
        const updated = await apiFetch(`/api/projects/${STATE.project.id}/moments/${mid}/regenerate-metadata`, {
          method: 'POST',
        });
        const idx = STATE.project.moments.findIndex((x) => x.id === mid);
        if (idx >= 0) STATE.project.moments[idx] = updated;
        showToast(`Nova variacao criada para o momento #${updated.rank}!`);
        renderRankingView();
      } finally {
        btn.disabled = false;
        btn.textContent = origText;
      }
    });
  });
  grid.querySelectorAll('.btn-copy-moment-kit').forEach((btn) => {
    btn.addEventListener('click', () => {
      const m = STATE.project.moments.find((x) => x.id === btn.dataset.mid);
      if (!m) return;
      const text = `${m.title}\n\n${m.description}\n\n${(m.hashtags || []).join(' ')}`;
      copyToClipboard(text, `Kit Viral do momento #${m.rank} copiado!`);
    });
  });
}

function populateLongPanel(proj) {
  const le = proj.long_edit || {};
  const kit = proj.youtube_kit || {};
  document.getElementById('long-stats-text').textContent =
    `Duracao editada: ${le.edited_duration_str || '00:00'} • Removidos ${le.removed_sec || 0}s de menus/loadings/partes mortas • ${(le.keep_segments || []).length} blocos limpos.`;

  const thumbImg = document.getElementById('long-thumb-img');
  if (le.best_thumb_url) thumbImg.src = le.best_thumb_url;

  document.getElementById('long-chapters-box').value = kit.chapters_text || '';
  document.getElementById('long-desc-box').value = kit.description || '';
  document.getElementById('long-tags-box').value = (kit.tags || []).join(', ');

  const titlesWrap = document.getElementById('long-titles-list');
  titlesWrap.innerHTML = (kit.titles || [])
    .map(
      (t, i) => `
      <div class="flex items-center gap-2">
        <input class="input" readonly value="${escapeHtml(t)}" />
        <button class="btn btn-sm btn-copy-yt-title" data-title="${escapeHtml(t)}"
          data-tooltip="Copia a sugestao de titulo #${i + 1} para o YouTube.">Copiar</button>
      </div>`
    )
    .join('');

  titlesWrap.querySelectorAll('.btn-copy-yt-title').forEach((b) => {
    b.addEventListener('click', () => copyToClipboard(b.dataset.title, 'Titulo YouTube copiado!'));
  });
}

async function patchMoment(momentId, patch) {
  if (!STATE.project) return;
  const updated = await apiFetch(`/api/projects/${STATE.project.id}/moments/${momentId}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
  const idx = STATE.project.moments.findIndex((m) => m.id === momentId);
  if (idx >= 0) STATE.project.moments[idx] = updated;
}

async function runBatchRender({ limit = null, moment_ids = null } = {}) {
  if (!STATE.project) {
    showToast('Nenhum projeto analisado para renderizar.', 'error');
    return;
  }
  startLiveProgress('Etapa 3/4: Iniciando renderizacao em lote com zoom de impacto e legenda...');

  try {
    const res = await apiFetch(`/api/projects/${STATE.project.id}/render-batch`, {
      method: 'POST',
      body: JSON.stringify({
        limit,
        moment_ids,
        skip_exported: !moment_ids,
        style: STATE.style,
        subtitles_mode: STATE.subtitles_mode,
        subtitle_style: STATE.subtitle_style,
        enable_hook: STATE.enable_hook,
        enable_impact_zoom: STATE.enable_impact_zoom,
        enable_preview_flash: STATE.enable_preview_flash,
        enable_infinite_loop: STATE.enable_infinite_loop,
        enable_cta_comment: STATE.enable_cta_comment,
        enable_impact_sfx: STATE.enable_impact_sfx,
        enable_smart_pacing: STATE.enable_smart_pacing,
        enable_progress_bar: STATE.enable_progress_bar,
        enable_audio_norm: STATE.enable_audio_norm,
        enable_action_tracking: STATE.enable_action_tracking,
        enable_slowmo: STATE.enable_slowmo,
        enable_anti_factory: STATE.enable_anti_factory,
        enable_seamless_loop: STATE.enable_seamless_loop,
        overlay_config: STATE.overlay || null,
      }),
    });
    STATE.activeJobId = res.job_id;
    const out = await pollJob(res.job_id, (job) => {
      updateLiveProgress(job.progress, job.message);
    });
    if (out?.cancelled) {
      showToast('Renderizacao cancelada pelo usuario.');
      await loadClipsLibrary();
      return;
    }
    if (out?.project) {
      STATE.project = out.project;
      renderRankingView();
    }
    showToast(`${out.count || 0} clipe(s) exportados com sucesso! Restam ${out?.remaining_unseen ?? 0} ineditos.`);
    await loadClipsLibrary();
  } finally {
    stopLiveProgress(1600);
  }
}

async function runLongExport() {
  if (!STATE.project) return;
  startLiveProgress('Etapa 3/4: Exportando Gameplay Longa 16:9 (1920x1080) e Thumbnail...');

  try {
    const res = await apiFetch(`/api/projects/${STATE.project.id}/render-long`, { method: 'POST' });
    STATE.activeJobId = res.job_id;
    const out = await pollJob(res.job_id, (job) => {
      updateLiveProgress(job.progress, job.message);
    });
    if (out?.cancelled) {
      showToast('Exportacao 16:9 cancelada.');
      return;
    }
    showToast('Gameplay 16:9 e Thumbnail 1080p salvas na pasta de saida!');
    await loadClipsLibrary();
  } finally {
    stopLiveProgress(1600);
  }
}

// ---- 1-Click Social Publish Helper ----

async function oneClickPublish(platform, { title, description, hashtags, file_path }) {
  const hashStr = Array.isArray(hashtags) ? hashtags.join(' ') : String(hashtags || '');
  const fullCopy = `${title}\n\n${description}\n\n${hashStr}`.trim();
  await copyToClipboard(fullCopy, null);
  const url = PUBLISH_URLS[platform] || PUBLISH_URLS.youtube_shorts;
  window.open(url, '_blank', 'noopener');
  if (file_path) {
    apiFetch('/api/system/reveal-file', {
      method: 'POST',
      body: JSON.stringify({ file_path }),
    }, { silentXRay: true }).catch(() => {});
  }
  showToast('Textos virais copiados, pagina de upload aberta e pasta do MP4 exibida!');
}

// ---- View 2: Biblioteca de Clipes ----

async function loadClipsLibrary() {
  const data = await apiFetch('/api/clips');
  STATE.clips = data.clips || [];
  document.getElementById('nav-clips-count').textContent = String(STATE.clips.length);
  renderClipsLibrary();
}

function _inferClipPlatform(c) {
  if (
    c.platform_target === 'tiktok_reels' ||
    c.platform_target === 'tiktok_long' ||
    c.platform_target === 'youtube_shorts'
  ) {
    return c.platform_target;
  }
  const d = Number(c.duration || 0);
  if (d >= 60) return 'tiktok_long';
  return d <= 34 ? 'tiktok_reels' : 'youtube_shorts';
}

function renderClipsLibrary() {
  const grid = document.getElementById('clips-library-grid');
  const filteredClips =
    STATE.libraryFilter === 'all'
      ? STATE.clips
      : STATE.clips.filter((c) => _inferClipPlatform(c) === STATE.libraryFilter);

  if (!filteredClips.length) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1;">
        <div style="font-size:15px;font-weight:700;color:#fff;margin-bottom:6px;">${
          STATE.clips.length ? 'Nenhum clipe encontrado neste filtro de plataforma' : 'Sua Biblioteca de Clipes esta vazia'
        }</div>
        <div>${
          STATE.clips.length
            ? 'Clique em "Todos" ou gere novos clipes no modo de destino desejado.'
            : 'Renderize clipes no Modo 1 (9:16) ou Gameplay 16:9 no Novo Projeto para visualizar e publicar por aqui.'
        }</div>
      </div>`;
    return;
  }

  grid.innerHTML = filteredClips
    .map((c) => {
      const plat = _inferClipPlatform(c);
      const platBadge =
        c.platform_badge ||
        (plat === 'tiktok_long'
          ? 'TikTok Monetizavel (61s-90s)'
          : plat === 'tiktok_reels'
          ? 'Ideal TikTok / Reels'
          : 'Ideal YouTube Shorts');
      const platBadgeClass =
        plat === 'tiktok_long' ? 'badge-amber' : plat === 'tiktok_reels' ? 'badge-green' : 'badge-blue';
      const canCreateShort25 =
        c.file_exists && c.mode !== 'long_16_9' && Number(c.duration || 0) > 35 && !c.is_short_25s_variant;
      const canCreateTikTokLong =
        c.file_exists &&
        c.mode !== 'long_16_9' &&
        Number(c.duration || 0) < 60 &&
        !c.is_tiktok_long_variant &&
        Boolean(c.source_video_path || c.project_id);

      return `
      <article class="card flex" style="flex-direction:column;gap:10px;" data-clip-card="${escapeHtml(c.id)}" data-clip-platform="${escapeHtml(plat)}">
        <div class="flex items-center justify-between gap-2 flex-wrap">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="badge ${platBadgeClass} lib-platform-badge">${escapeHtml(platBadge)}</span>
            <span class="badge ${c.mode === 'long_16_9' ? 'badge-cyan' : 'badge-purple'}">${escapeHtml(c.category_label || c.style || 'Clipe')}</span>
            ${c.detected_game ? `<span class="badge badge-purple">${escapeHtml(c.detected_game)}</span>` : ''}
          </div>
          <span class="text-xs">${c.file_size_mb || 0} MB • ${Math.round(c.duration || 0)}s</span>
        </div>

        ${
          c.file_exists
            ? `<video class="clip-video" controls preload="metadata" playsinline ${c.cover_url || c.thumb_url ? `poster="${escapeHtml(c.cover_url || c.thumb_url)}"` : ''} src="${escapeHtml(c.media_url)}"></video>`
            : `<div class="empty-state" style="padding:20px;">Arquivo movido ou apagado do disco.</div>`
        }
        ${
          c.cover_url
            ? `<div class="flex items-center justify-between text-xs"><span class="badge badge-green">Capa Shorts ✓</span><a href="${escapeHtml(c.cover_url)}" target="_blank" rel="noopener" style="color:var(--accent-cyan);text-decoration:none;">Ver Capa 1080×1920 ↗</a></div>`
            : ''
        }

        <div class="clip-title-text" style="font-weight:700;color:#fff;font-size:13.5px;">${escapeHtml(c.title)}</div>
        <div class="text-xs clip-desc-text" style="color:var(--text-secondary);">${escapeHtml(c.description)}</div>
        <div class="text-xs clip-tags-text" style="color:#818CF8;">${escapeHtml((c.hashtags || []).join(' '))}</div>

        ${
          canCreateShort25
            ? `<button class="btn btn-sm btn-success btn-create-tiktok-25s" data-clip-id="${escapeHtml(c.id)}"
                data-tooltip="Gera em segundos uma versao enxuta de 25s preservando os 3,5s iniciais do gancho + os 21,5s mais intensos do climax, pronta para TikTok/Reels.">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
                <span>Criar Versao Curta TikTok (25s)</span>
              </button>`
            : ''
        }

        ${
          canCreateTikTokLong
            ? `<button class="btn btn-sm btn-primary btn-create-tiktok-long" data-clip-id="${escapeHtml(c.id)}"
                data-tooltip="Expande contexto antes e depois da acao no video original para criar uma Versao Completa TikTok Monetizavel (61s-90s).">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                <span>Criar Versao TikTok Monetizavel (61-90s)</span>
              </button>`
            : ''
        }

        <!-- Regenerate Metadata + Ver mais 5 opcoes -->
        <div class="flex gap-2">
          <button class="btn btn-sm btn-cyan regen-clip-btn" style="flex:1;" data-clip-id="${escapeHtml(c.id)}"
            data-tooltip="Usa o Qwen 3.5 Vision para gerar uma nova sugestao criativa de Titulo, Descricao e Hashtags sem re-renderizar o video.">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
            <span>Refazer Texto</span>
          </button>
          <button class="btn btn-sm more-options-btn" data-clip-id="${escapeHtml(c.id)}"
            data-tooltip="Gera 5 novas combinacoes unicas de Titulo, Descricao e Hashtags (Jaccard < 0.35) usando a Ficha do Jogo.">
            <span>Ver mais 5 opcoes</span>
          </button>
        </div>

        <!-- Direct 1-Click Social Publishing -->
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-sm btn-yt pub-btn" data-clip-id="${escapeHtml(c.id)}" data-platform="youtube_studio"
            data-tooltip="Copia Titulo + Descricao + Hashtags, abre https://studio.youtube.com e abre o Explorer com o .mp4 selecionado.">
            <span>Publicar no YouTube Studio</span>
          </button>
          <button class="btn btn-sm btn-tiktok pub-btn" data-clip-id="${escapeHtml(c.id)}" data-platform="tiktok_studio"
            data-tooltip="Copia Titulo + Descricao + Hashtags, abre TikTok Studio Upload e seleciona o .mp4 no Explorer.">
            <span>Publicar no TikTok Studio</span>
          </button>
          <button class="btn btn-sm btn-fb pub-btn" data-clip-id="${escapeHtml(c.id)}" data-platform="facebook_reels"
            data-tooltip="Copia Titulo + Descricao + Hashtags, abre o compositor de Facebook Reels e seleciona o .mp4 no Explorer.">
            <span>Publicar no Facebook Reels</span>
          </button>
        </div>

        <!-- Individual & Full Copy Buttons -->
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-sm copy-title-btn" data-clip-id="${escapeHtml(c.id)}"
            data-tooltip="Copia apenas o Titulo Viral deste clipe para colar no campo de titulo.">
            <span>Copiar Titulo</span>
          </button>
          <button class="btn btn-sm copy-desc-tags-btn" data-clip-id="${escapeHtml(c.id)}"
            data-tooltip="Copia a Descricao persuasiva junto com todas as Hashtags estrategicas.">
            <span>Copiar Descricao + Hashtags</span>
          </button>
          <button class="btn btn-sm btn-primary copy-all-btn" data-clip-id="${escapeHtml(c.id)}"
            data-tooltip="Copia Titulo + Descricao + Hashtags juntos de uma vez.">
            <span>Copiar Tudo</span>
          </button>
          ${
            c.pinned_comment_cta || c.cta_comment
              ? `<button class="btn btn-sm copy-pinned-cta-btn" data-clip-id="${escapeHtml(c.id)}"
                  data-tooltip="Copia o comentario fixo pronto para colar e fixar nos comentarios sem atrapalhar o loop do video.">
                  <span>Copiar CTA Comentario Fixo</span>
                </button>`
              : ''
          }
        </div>

        <div class="flex gap-2" style="margin-top:auto;padding-top:6px;">
          <button class="btn btn-sm reveal-clip-btn" data-clip-id="${escapeHtml(c.id)}" style="flex:1;"
            data-tooltip="Abre o Windows Explorer com este arquivo MP4 ja selecionado para arrastar.">
            <span>Mostrar MP4</span>
          </button>
          <button class="btn btn-sm btn-danger del-clip-btn" data-clip-id="${escapeHtml(c.id)}"
            data-tooltip="Remove o clipe da biblioteca e apaga o arquivo MP4 do disco.">
            <span>Excluir</span>
          </button>
        </div>
      </article>`;
    })
    .join('');

  grid.querySelectorAll('.clip-video').forEach((vid) => {
    vid.addEventListener('play', () => {
      grid.querySelectorAll('.clip-video').forEach((other) => {
        if (other !== vid && !other.paused) other.pause();
      });
    });
  });

  grid.querySelectorAll('.btn-create-tiktok-25s').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const cid = btn.dataset.clipId;
      btn.disabled = true;
      const span = btn.querySelector('span');
      const prev = span ? span.textContent : '';
      if (span) span.textContent = 'Criando versao curta (25s)...';
      try {
        const created = await apiFetch(`/api/clips/${cid}/create-tiktok-25s`, { method: 'POST' });
        showToast(`Versao Curta TikTok (${Math.round(created.duration || 25)}s) criada e salva na Biblioteca!`);
        await loadClipsLibrary();
      } finally {
        btn.disabled = false;
        if (span) span.textContent = prev;
      }
    });
  });

  grid.querySelectorAll('.btn-create-tiktok-long').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const cid = btn.dataset.clipId;
      btn.disabled = true;
      const span = btn.querySelector('span');
      const prev = span ? span.textContent : '';
      if (span) span.textContent = 'Expandindo TikTok 61-90s...';
      try {
        const created = await apiFetch(`/api/clips/${cid}/create-tiktok-long`, { method: 'POST' });
        showToast(`Versao TikTok Monetizavel (${Math.round(created.duration || 75)}s) criada e salva na Biblioteca!`);
        await loadClipsLibrary();
      } finally {
        btn.disabled = false;
        if (span) span.textContent = prev;
      }
    });
  });

  grid.querySelectorAll('.more-options-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const cid = btn.dataset.clipId;
      const clip = STATE.clips.find((x) => x.id === cid);
      if (!clip) return;
      openMoreCopyOptionsModal({
        game_name: clip.detected_game || STATE.gameName || '',
        profile_id: clip.profile_id || STATE.selectedProfileId || 'generico',
        category: clip.category || 'combate_intenso',
        platform_target: _inferClipPlatform(clip),
        exclude_titles: STATE.clips.map((x) => x.title).filter(Boolean),
        onApply: async (opt) => {
          const updated = await apiFetch(`/api/clips/${cid}`, {
            method: 'PATCH',
            body: JSON.stringify({
              title: opt.title,
              description: opt.description,
              hashtags: opt.hashtags,
              hook_2s: opt.hook_2s,
              cta_comment: opt.cta_comment,
            }),
          });
          const idx = STATE.clips.findIndex((x) => x.id === cid);
          if (idx >= 0) STATE.clips[idx] = { ...STATE.clips[idx], ...updated };
          renderClipsLibrary();
        },
      });
    });
  });

  grid.querySelectorAll('.regen-clip-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const cid = btn.dataset.clipId;
      btn.disabled = true;
      const span = btn.querySelector('span');
      const prevLabel = span ? span.textContent : '';
      if (span) span.textContent = 'Gerando nova variacao com IA...';
      try {
        const updated = await apiFetch(`/api/clips/${cid}/regenerate-metadata`, { method: 'POST' });
        const idx = STATE.clips.findIndex((x) => x.id === cid);
        if (idx >= 0) STATE.clips[idx] = { ...STATE.clips[idx], ...updated };
        showToast('Nova variacao de Titulo, Descricao e Hashtags gerada!');
        renderClipsLibrary();
      } finally {
        btn.disabled = false;
        if (span) span.textContent = prevLabel;
      }
    });
  });
  grid.querySelectorAll('.pub-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const clip = STATE.clips.find((x) => x.id === btn.dataset.clipId);
      if (clip) oneClickPublish(btn.dataset.platform, clip);
    });
  });
  grid.querySelectorAll('.copy-title-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const c = STATE.clips.find((x) => x.id === btn.dataset.clipId);
      if (c) copyToClipboard(c.title || '', 'Titulo Viral copiado!');
    });
  });
  grid.querySelectorAll('.copy-desc-tags-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const c = STATE.clips.find((x) => x.id === btn.dataset.clipId);
      if (c) copyToClipboard(`${c.description || ''}\n\n${(c.hashtags || []).join(' ')}`.trim(), 'Descricao + Hashtags copiadas!');
    });
  });
  grid.querySelectorAll('.copy-all-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const c = STATE.clips.find((x) => x.id === btn.dataset.clipId);
      if (c) copyToClipboard(`${c.title || ''}\n\n${c.description || ''}\n\n${(c.hashtags || []).join(' ')}`.trim(), 'Kit Viral completo copiado!');
    });
  });
  grid.querySelectorAll('.copy-pinned-cta-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const c = STATE.clips.find((x) => x.id === btn.dataset.clipId);
      const txt = c?.pinned_comment_cta || c?.cta_comment || '';
      if (txt) copyToClipboard(txt, 'CTA de Comentario Fixo copiado!');
    });
  });
  grid.querySelectorAll('.reveal-clip-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const c = STATE.clips.find((x) => x.id === btn.dataset.clipId);
      if (c?.file_path) {
        apiFetch('/api/system/reveal-file', { method: 'POST', body: JSON.stringify({ file_path: c.file_path }) });
      }
    });
  });
  grid.querySelectorAll('.del-clip-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
      await apiFetch(`/api/clips/${btn.dataset.clipId}?delete_file=true`, { method: 'DELETE' });
      showToast('Clipe removido da biblioteca.');
      loadClipsLibrary();
    });
  });
}

// ---- View 3: Agendador & Publicacao + Piloto Automatico Diario YouTube Shorts ----

async function loadAutopilot() {
  try {
    const ap = await apiFetch('/api/youtube/autopilot', {}, { silentXRay: true });
    STATE.autopilot = ap;
    const ytSt = ap.youtube_status || {};
    const badge = document.getElementById('autopilot-yt-status-badge');
    if (badge) {
      if (ytSt.connected) {
        badge.className = 'badge badge-green';
        badge.textContent = `Conectado: ${ytSt.channel_title || 'Canal YouTube'}`;
      } else {
        badge.className = 'badge badge-amber';
        badge.textContent = 'Conta YouTube nao conectada (Configure em Configuracoes)';
      }
    }

    const enabledEl = document.getElementById('yt-autopilot-enabled');
    const timeEl = document.getElementById('yt-autopilot-time');
    const privEl = document.getElementById('yt-autopilot-privacy');
    const limitEl = document.getElementById('yt-autopilot-limit');
    const approvalEl = document.getElementById('yt-autopilot-approval');
    const usageLbl = document.getElementById('yt-daily-usage-label');
    if (enabledEl) enabledEl.checked = Boolean(ap.enabled);
    if (timeEl) timeEl.value = ap.daily_time || '18:30';
    if (privEl) privEl.value = ap.privacy_status || 'private';
    if (limitEl) limitEl.value = ap.daily_upload_limit || 2;
    if (approvalEl) approvalEl.checked = ap.require_human_approval !== false;
    if (usageLbl) usageLbl.textContent = `${ap.uploads_today || 0}/${ap.daily_upload_limit || 2} hoje`;

    const clipSel = document.getElementById('yt-autopilot-clip-select');
    if (clipSel) {
      clipSel.innerHTML =
        `<option value="">-- Selecione um clipe pronto da Biblioteca --</option>` +
        STATE.clips
          .filter((c) => c.file_exists)
          .map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml((c.title || c.file_name).slice(0, 62))}</option>`)
          .join('');
    }

    const qList = document.getElementById('yt-autopilot-queue-list');
    const queue = ap.queue || [];
    if (!qList) return;
    if (!queue.length) {
      qList.innerHTML = `<div class="empty-state" style="padding:18px;">Fila diaria vazia. Adicione clipes acima ou clique em <strong>Importar Biblioteca em Lote</strong> para publicar 1 Short por dia automaticamente.</div>`;
      return;
    }

    qList.innerHTML = queue
      .map((it, idx) => {
        const st = it.status || 'agendado';
        const isApproved = Boolean(it.approved);
        const stBadge =
          st === 'publicado'
            ? `<span class="badge badge-green">PUBLICADO</span>`
            : st === 'publicando'
            ? `<span class="badge badge-cyan">PUBLICANDO...</span>`
            : st === 'erro'
            ? `<span class="badge badge-red">ERRO</span>`
            : st === 'aguardando_aprovacao' || !isApproved
            ? `<span class="badge badge-amber">AGUARDANDO APROVACAO HUMANA</span>`
            : `<span class="badge badge-purple">APROVADO • ${escapeHtml(it.estimated_publish_at || ap.daily_time)}</span>`;

        const linkHtml = it.video_url
          ? `<a href="${escapeHtml(it.video_url)}" target="_blank" rel="noopener" class="badge badge-cyan" style="text-decoration:none;">Abrir Short Publicado ↗</a>`
          : '';
        const errHtml = it.error_message
          ? `<div class="text-xs mt-2" style="color:#FCA5A5;">Motivo: ${escapeHtml(it.error_message)}</div>`
          : '';

        return `
          <div class="toggle-row" style="align-items:center;flex-wrap:wrap;">
            <div style="flex:1;min-width:240px;">
              <div class="flex items-center gap-2 flex-wrap mb-2">
                <span class="text-xs" style="font-weight:700;">#${idx + 1}</span>
                ${stBadge}
                ${linkHtml}
              </div>
              <div style="font-weight:700;color:#fff;font-size:13px;">${escapeHtml(it.title)}</div>
              ${errHtml}
            </div>
            <div class="flex gap-2 flex-wrap">
              ${
                st !== 'publicado' && !isApproved
                  ? `<button class="btn btn-sm btn-success ap-approve-btn" data-ap-id="${escapeHtml(it.id)}"
                      data-tooltip="Confirma revisao humana liberando este clipe para envio no YouTube.">
                      <span>Aprovar para Publicacao</span>
                    </button>`
                  : ''
              }
              ${
                st !== 'publicado'
                  ? `<button class="btn btn-sm btn-yt ap-publish-now-btn" data-ap-id="${escapeHtml(it.id)}"
                      data-tooltip="Envia este clipe imediatamente para o YouTube Shorts (exige aprovacao humana e respeita limite diario).">
                      <span>Publicar Agora (API)</span>
                    </button>`
                  : ''
              }
              <button class="btn btn-sm btn-danger ap-remove-btn" data-ap-id="${escapeHtml(it.id)}"
                data-tooltip="Remove este clipe da fila do Piloto Automatico Diario.">Remover</button>
            </div>
          </div>`;
      })
      .join('');

    qList.querySelectorAll('.ap-approve-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          await apiFetch(`/api/youtube/autopilot/approve/${btn.dataset.apId}`, {
            method: 'POST',
            body: JSON.stringify({ approved: true }),
          });
          showToast('Clipe aprovado para publicacao no YouTube!');
          loadAutopilot();
        } finally {
          btn.disabled = false;
        }
      });
    });

    qList.querySelectorAll('.ap-publish-now-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const warnMsg =
          ap.audit_warning ||
          'Projetos de API sem auditoria do Google enviam videos como PRIVADOS por padrao — teste 1 video antes de agendar em lote.';
        const ok = window.confirm(
          `${warnMsg}\n\nConfirmar aprovacao humana explicita e enviar este video agora para o YouTube?`
        );
        if (!ok) return;
        btn.disabled = true;
        try {
          const res = await apiFetch(`/api/youtube/autopilot/publish-now/${btn.dataset.apId}`, {
            method: 'POST',
            body: JSON.stringify({ human_approved: true }),
          });
          showToast(`Short publicado no YouTube! ${res.result?.video_url || ''}`);
          loadAutopilot();
        } catch {
          loadAutopilot();
        } finally {
          btn.disabled = false;
        }
      });
    });

    qList.querySelectorAll('.ap-remove-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        await apiFetch(`/api/youtube/autopilot/queue/${btn.dataset.apId}`, { method: 'DELETE' });
        showToast('Clipe removido da fila diaria do YouTube.');
        loadAutopilot();
      });
    });
  } catch {
    // ignore silent error
  }
}

async function loadSchedule() {
  const [schedData, clipsData] = await Promise.all([apiFetch('/api/schedule'), apiFetch('/api/clips')]);
  STATE.schedule = schedData.items || [];
  STATE.clips = clipsData.clips || [];
  await loadAutopilot();
  const apCount = (STATE.autopilot?.queue || []).filter((q) => q.status === 'agendado').length;
  document.getElementById('nav-sched-count').textContent = String(STATE.schedule.length + apCount);

  const sel = document.getElementById('sched-clip-select');
  sel.innerHTML =
    `<option value="">-- Selecionar clipe renderizado --</option>` +
    STATE.clips.map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.title.slice(0, 56))}</option>`).join('');

  const list = document.getElementById('schedule-list');
  if (!STATE.schedule.length) {
    list.innerHTML = `<div class="empty-state">Nenhuma postagem agendada. Adicione acima para planejar seu calendario de postagens.</div>`;
    return;
  }

  list.innerHTML = STATE.schedule
    .map(
      (it) => `
      <div class="card flex items-center justify-between flex-wrap gap-3">
        <div>
          <div class="flex items-center gap-2 mb-2">
            <span class="badge ${it.status === 'publicado' ? 'badge-green' : 'badge-purple'}">${escapeHtml(it.status.toUpperCase())}</span>
            <span class="badge badge-cyan">${escapeHtml(it.platform.replace('_', ' ').toUpperCase())}</span>
            <span class="text-xs">${escapeHtml(it.scheduled_for.replace('T', ' '))}</span>
          </div>
          <div style="font-weight:700;color:#fff;">${escapeHtml(it.title)}</div>
          <div class="text-xs mt-2">${escapeHtml(it.description)}</div>
        </div>
        <div class="flex gap-2">
          <button class="btn btn-sm btn-primary sched-pub-btn" data-id="${escapeHtml(it.id)}"
            data-tooltip="Copia os textos, abre a plataforma de upload e revela o MP4 na pasta.">Publicar Agora</button>
          <button class="btn btn-sm btn-success sched-done-btn" data-id="${escapeHtml(it.id)}"
            data-tooltip="Marca esta postagem como ja publicada.">Marcar Publicado</button>
          <button class="btn btn-sm btn-danger sched-del-btn" data-id="${escapeHtml(it.id)}"
            data-tooltip="Remove esta postagem da fila de agendamento.">Remover</button>
        </div>
      </div>`
    )
    .join('');

  list.querySelectorAll('.sched-pub-btn').forEach((b) => {
    b.addEventListener('click', () => {
      const item = STATE.schedule.find((x) => x.id === b.dataset.id);
      if (item) oneClickPublish(item.platform, item);
    });
  });
  list.querySelectorAll('.sched-done-btn').forEach((b) => {
    b.addEventListener('click', async () => {
      await apiFetch(`/api/schedule/${b.dataset.id}`, { method: 'PATCH', body: JSON.stringify({ status: 'publicado' }) });
      showToast('Postagem marcada como publicada!');
      loadSchedule();
    });
  });
  list.querySelectorAll('.sched-del-btn').forEach((b) => {
    b.addEventListener('click', async () => {
      await apiFetch(`/api/schedule/${b.dataset.id}`, { method: 'DELETE' });
      loadSchedule();
    });
  });
}

// ---- View 4: Limpeza de Disco ----

async function loadDiskScan() {
  const data = await apiFetch('/api/disk/scan');
  STATE.disk = data;
  document.getElementById('disk-summary-bar').innerHTML = `
    <div>
      <div class="text-xs">ESPACO TOTAL DO CLIPRUSH</div>
      <div style="font-size:20px;font-weight:800;color:#fff;">${data.total_mb} MB</div>
    </div>
    <div>
      <div class="text-xs">ESPACO LIVRE NO DISCO D:</div>
      <div style="font-size:20px;font-weight:800;color:#10B981;">${data.disk_free_gb} GB</div>
    </div>`;

  const grid = document.getElementById('disk-buckets-grid');
  grid.innerHTML = (data.buckets || [])
    .map(
      (b) => `
      <label class="card card-interactive flex items-center justify-between gap-3"
        data-tooltip="Marque para incluir '${escapeHtml(b.name)}' na limpeza de disco.">
        <div>
          <div class="flex items-center gap-2">
            <strong style="color:#fff;">${escapeHtml(b.name)}</strong>
            <span class="badge ${b.safe_default ? 'badge-green' : 'badge-amber'}">${b.size_mb} MB (${b.file_count} arq.)</span>
          </div>
          <div class="text-xs mt-2">${escapeHtml(b.description)}</div>
          <div class="text-xs mt-2" style="font-family:Consolas,monospace;">${escapeHtml(b.path)}</div>
        </div>
        <input type="checkbox" class="disk-bucket-chk" value="${escapeHtml(b.id)}" ${b.safe_default ? 'checked' : ''} />
      </label>`
    )
    .join('');
}

// ---- View 5: Configuracoes & Conexao Oficial YouTube ----

async function loadYouTubeStatus() {
  try {
    const st = await apiFetch('/api/youtube/status', {}, { silentXRay: true });
    const badge = document.getElementById('yt-conn-badge');
    const chEl = document.getElementById('yt-channel-name');
    const secInput = document.getElementById('cfg-yt-secrets-path');
    const errEl = document.getElementById('yt-oauth-error');

    if (secInput && st.client_secrets_path) secInput.value = st.client_secrets_path;
    if (badge) {
      if (st.connected) {
        badge.className = 'badge badge-green';
        badge.textContent = 'CONECTADO';
      } else if (st.oauth_in_progress) {
        badge.className = 'badge badge-cyan';
        badge.textContent = 'AGUARDANDO LOGIN NO NAVEGADOR...';
      } else {
        badge.className = 'badge badge-amber';
        badge.textContent = st.client_secrets_exists ? 'PRONTO PARA CONECTAR' : 'NAO CONECTADO';
      }
    }
    if (chEl) {
      chEl.textContent = st.connected
        ? `Canal conectado: ${st.channel_title || 'YouTube'} (${st.channel_id || 'OAuth2 ativo'})`
        : st.client_secrets_exists
        ? 'client_secrets.json detectado! Clique em "Conectar Conta YouTube" para autorizar via OAuth2 local.'
        : 'Informe o caminho ou carregue seu arquivo client_secrets.json (Google Cloud OAuth 2.0 Desktop App).';
    }
    if (errEl) {
      if (st.last_error) {
        errEl.textContent = `Ultimo erro OAuth2: ${st.last_error}`;
        errEl.classList.remove('hidden');
      } else {
        errEl.classList.add('hidden');
      }
    }
    return st;
  } catch {
    return null;
  }
}

async function loadSettings() {
  const cfg = await apiFetch('/api/settings');
  STATE.settings = cfg;
  document.getElementById('cfg-ollama-url').value = cfg.ollama_url || 'http://localhost:11434';
  document.getElementById('cfg-ollama-model').value = cfg.ollama_model || 'qwen3.5:9b';
  document.getElementById('cfg-ffmpeg-path').value = cfg.ffmpeg_path || '';
  document.getElementById('cfg-output-dir').value = cfg.output_dir || '';
  document.getElementById('cfg-encoder').value = cfg.hardware_encoder || 'auto';
  document.getElementById('cfg-whisper-model').value = cfg.whisper_model || 'base';
  document.getElementById('cfg-min-sec').value = cfg.default_clip_min_sec || 25;
  document.getElementById('cfg-max-sec').value = cfg.default_clip_max_sec || 55;
  const setVal = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.value = v;
  };
  setVal('cfg-tk-long-min', cfg.tiktok_long_min_sec ?? 61);
  setVal('cfg-tk-long-max', cfg.tiktok_long_max_sec ?? 90);
  setVal('cfg-peak-hook-sec', cfg.peak_hook_duration_sec ?? 1.8);
  setVal('cfg-hook-text-sec', cfg.hook_text_duration_sec ?? 2.3);
  setVal('cfg-zoom-intensity', cfg.impact_zoom_intensity ?? 1.14);
  setVal('cfg-cpu-thresh', cfg.cpu_pause_threshold_pct ?? 80);
  const detEl = document.getElementById('cfg-detailed-mode');
  if (detEl) detEl.checked = Boolean(cfg.analysis_detailed_mode);
  document.getElementById('cfg-use-vision').checked = Boolean(cfg.use_ollama_vision);
  await loadYouTubeStatus();
}

async function saveSettingsForm() {
  const payload = {
    ollama_url: document.getElementById('cfg-ollama-url').value.trim(),
    ollama_model: document.getElementById('cfg-ollama-model').value.trim(),
    ffmpeg_path: document.getElementById('cfg-ffmpeg-path').value.trim(),
    output_dir: document.getElementById('cfg-output-dir').value.trim(),
    hardware_encoder: document.getElementById('cfg-encoder').value,
    whisper_model: document.getElementById('cfg-whisper-model').value,
    default_clip_min_sec: Number(document.getElementById('cfg-min-sec').value) || 25,
    default_clip_max_sec: Number(document.getElementById('cfg-max-sec').value) || 55,
    tiktok_long_min_sec: Number(document.getElementById('cfg-tk-long-min')?.value) || 61,
    tiktok_long_max_sec: Number(document.getElementById('cfg-tk-long-max')?.value) || 90,
    peak_hook_duration_sec: Number(document.getElementById('cfg-peak-hook-sec')?.value) || 1.8,
    hook_text_duration_sec: Number(document.getElementById('cfg-hook-text-sec')?.value) || 2.3,
    impact_zoom_intensity: Number(document.getElementById('cfg-zoom-intensity')?.value) || 1.14,
    cpu_pause_threshold_pct: Number(document.getElementById('cfg-cpu-thresh')?.value) || 80,
    analysis_detailed_mode: Boolean(document.getElementById('cfg-detailed-mode')?.checked),
    use_ollama_vision: document.getElementById('cfg-use-vision').checked,
    youtube_client_secrets_path: document.getElementById('cfg-yt-secrets-path').value.trim(),
  };
  await apiFetch('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
  showToast('Configuracoes salvas com sucesso!');
  refreshHealth();
  loadYouTubeStatus();
}

// ---- Wire All Static Controls ----

function bindEvents() {
  // Sidebar
  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => setView(btn.dataset.view));
  });

  // Stepper
  document.querySelectorAll('.step-btn').forEach((btn) => {
    btn.addEventListener('click', () => setStep(Number(btn.dataset.step)));
  });

  // Global Cancel Job button
  document.getElementById('btn-cancel-job')?.addEventListener('click', cancelActiveJob);

  // Header Actions
  document.getElementById('btn-open-output').addEventListener('click', () => {
    apiFetch('/api/system/open-output', { method: 'POST' });
    showToast('Abrindo pasta de saida no Windows Explorer...');
  });
  document.getElementById('chip-system').addEventListener('click', () => {
    openSystemHealthModal();
    refreshHealth().then(() => {
      if (document.getElementById('health-modal-backdrop')?.classList.contains('open')) {
        openSystemHealthModal();
      }
    });
  });
  document.getElementById('select-scene-mode')?.addEventListener('change', (e) => {
    STATE.scene_mode = e.target.value || 'keep_dialogue';
    apiFetch('/api/settings', { method: 'PUT', body: JSON.stringify({ scene_mode: STATE.scene_mode }) }, { silentXRay: true }).catch(() => {});
    showToast('Modo Cenas atualizado ✓');
  });
  document.getElementById('chip-ollama').addEventListener('click', async () => {
    await apiFetch('/api/system/start-ollama', { method: 'POST' });
    showToast('Verificando/Iniciando Ollama em segundo plano...');
    setTimeout(refreshHealth, 2500);
  });
  document.getElementById('chip-ffmpeg').addEventListener('click', () => setView('configuracoes'));

  // Dropzone (Native Windows Instant Picker on click + Drag-and-Drop / Fallback Input streaming)
  const dz = document.getElementById('dropzone');
  const fi = document.getElementById('file-input');
  const browseBtn = document.getElementById('btn-browse-native');
  const fallbackBtn = document.getElementById('btn-upload-fallback');

  dz.addEventListener('click', (e) => {
    if (e.target.closest('#btn-upload-fallback') || e.target === fi) return;
    browseNativeLocalFile().catch(() => {});
  });
  browseBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    browseNativeLocalFile().catch(() => {});
  });
  fallbackBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    fi.value = '';
    fi.click();
  });
  fi.addEventListener('change', (e) => {
    const file = e.target.files?.[0];
    if (file) {
      handleFileUpload(file).catch(() => {});
    }
    fi.value = '';
  });
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    dz.classList.remove('dragover');
    const dropped = e.dataTransfer?.files?.[0];
    if (dropped) handleFileUpload(dropped).catch(() => {});
  });

  document.getElementById('btn-import-local').addEventListener('click', handleLocalImport);
  document.getElementById('btn-step1-next').addEventListener('click', () => setStep(2));
  document.getElementById('btn-quick-analyze-step1').addEventListener('click', startAnalysis);
  document.getElementById('btn-auto-next10-step1').addEventListener('click', () => startAutoPipeline(10));
  document.getElementById('btn-auto-all-step1').addEventListener('click', () => startAutoPipeline(0));
  document.getElementById('btn-auto-quick-step1').addEventListener('click', () => startAutoPipeline(10));
  document.getElementById('btn-auto-next10-step2').addEventListener('click', () => startAutoPipeline(10));
  document.getElementById('btn-auto-all-step2').addEventListener('click', () => startAutoPipeline(0));

  // Step 2 Mode & Style Controls
  document.querySelectorAll('.mode-card').forEach((card) => {
    card.addEventListener('click', () => {
      STATE.mode = card.dataset.mode;
      document.querySelectorAll('.mode-card').forEach((c) => c.classList.toggle('selected', c === card));
      document.getElementById('mode1-config-panel').classList.toggle('hidden', STATE.mode !== 'viral_9_16');
      document.getElementById('mode2-config-panel').classList.toggle('hidden', STATE.mode !== 'long_16_9');
    });
  });

  document.querySelectorAll('.style-card').forEach((card) => {
    card.addEventListener('click', () => {
      STATE.style = card.dataset.style;
      document.querySelectorAll('.style-card').forEach((c) => c.classList.toggle('selected', c === card));
    });
  });

  document.querySelectorAll('#subtitle-style-selector .sub-style-pill').forEach((pill) => {
    pill.addEventListener('click', () => {
      STATE.subtitle_style = pill.dataset.subStyle;
      document.querySelectorAll('#subtitle-style-selector .sub-style-pill').forEach((p) => {
        p.classList.toggle('selected', p === pill);
      });
      showToast(`Estilo visual da legenda: ${pill.querySelector('.text-xs')?.textContent || STATE.subtitle_style}`);
    });
  });

  document.getElementById('toggle-hook').addEventListener('change', (e) => {
    STATE.enable_hook = e.target.checked;
  });
  document.getElementById('toggle-preview-flash').addEventListener('change', (e) => {
    STATE.enable_preview_flash = e.target.checked;
  });
  document.getElementById('toggle-infinite-loop').addEventListener('change', (e) => {
    STATE.enable_infinite_loop = e.target.checked;
  });
  document.getElementById('toggle-cta-comment').addEventListener('change', (e) => {
    STATE.enable_cta_comment = e.target.checked;
  });
  document.getElementById('toggle-impact-zoom').addEventListener('change', (e) => {
    STATE.enable_impact_zoom = e.target.checked;
  });
  document.getElementById('toggle-impact-sfx').addEventListener('change', (e) => {
    STATE.enable_impact_sfx = e.target.checked;
  });
  document.getElementById('toggle-smart-pacing').addEventListener('change', (e) => {
    STATE.enable_smart_pacing = e.target.checked;
  });
  document.getElementById('toggle-progress-bar').addEventListener('change', (e) => {
    STATE.enable_progress_bar = e.target.checked;
  });
  document.getElementById('toggle-audio-norm').addEventListener('change', (e) => {
    STATE.enable_audio_norm = e.target.checked;
  });
  document.getElementById('toggle-action-tracking').addEventListener('change', (e) => {
    STATE.enable_action_tracking = e.target.checked;
  });
  document.getElementById('toggle-slowmo').addEventListener('change', (e) => {
    STATE.enable_slowmo = e.target.checked;
  });
  document.getElementById('toggle-anti-factory')?.addEventListener('change', (e) => {
    STATE.enable_anti_factory = e.target.checked;
  });
  document.getElementById('toggle-seamless-loop')?.addEventListener('change', (e) => {
    STATE.enable_seamless_loop = e.target.checked;
  });

  document.querySelectorAll('input[name="subtitles_mode"]').forEach((radio) => {
    radio.addEventListener('change', () => {
      STATE.subtitles_mode = radio.value;
      document.getElementById('sub-opt-off').classList.toggle('selected', radio.value === 'off');
      document.getElementById('sub-opt-whisper').classList.toggle('selected', radio.value === 'whisper');
    });
  });

  document.getElementById('btn-step2-back').addEventListener('click', () => setStep(1));
  document.getElementById('btn-start-analysis').addEventListener('click', startAnalysis);

  // Step 3 Batch & Filters
  document.getElementById('btn-step3-reconfig').addEventListener('click', () => setStep(2));
  document.querySelectorAll('[data-batch]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const val = btn.dataset.batch;
      runBatchRender({ limit: val === 'all' ? null : Number(val) });
    });
  });
  document.getElementById('btn-batch-selected').addEventListener('click', () => {
    if (!STATE.selectedMomentIds.size) {
      showToast('Marque ao menos 1 momento na lista para exportar os selecionados.', 'error');
      return;
    }
    runBatchRender({ moment_ids: Array.from(STATE.selectedMomentIds) });
  });
  document.getElementById('btn-reset-history').addEventListener('click', async () => {
    if (!STATE.project) return;
    const res = await apiFetch(`/api/projects/${STATE.project.id}/reset-export-history`, { method: 'POST' });
    if (res?.project) {
      STATE.project = res.project;
      renderRankingView();
    }
    showToast('Historico deste video resetado! Todos os momentos voltaram a ficar como Ineditos.');
  });

  document.getElementById('btn-toggle-long-panel').addEventListener('click', () => {
    document.getElementById('long-export-panel').classList.toggle('hidden');
  });
  document.getElementById('btn-export-long').addEventListener('click', runLongExport);
  document.getElementById('btn-copy-chapters').addEventListener('click', () => {
    copyToClipboard(document.getElementById('long-chapters-box').value, 'Capitulos YouTube copiados!');
  });
  document.getElementById('btn-copy-seo').addEventListener('click', () => {
    copyToClipboard(document.getElementById('long-desc-box').value, 'Descricao SEO copiada!');
  });
  document.getElementById('btn-copy-tags').addEventListener('click', () => {
    copyToClipboard(document.getElementById('long-tags-box').value, 'Tags YouTube copiadas!');
  });

  document.querySelectorAll('#moment-filters .filter-pill').forEach((pill) => {
    pill.addEventListener('click', () => {
      STATE.categoryFilter = pill.dataset.cat;
      document.querySelectorAll('#moment-filters .filter-pill').forEach((p) => p.classList.toggle('active', p === pill));
      renderRankingView();
    });
  });

  // Step 1 Smart Multi-Platform Target Mode Selector
  const targetLabels = {
    hibrido: 'Hibrido IA Ativo',
    tiktok_reels: 'Foco TikTok & Reels (20s-32s)',
    tiktok_long: 'TikTok Monetizavel (61s-90s)',
    youtube_shorts: 'Foco YouTube Shorts (36s-55s)',
  };
  document.querySelectorAll('#target-mode-selector .target-mode-card').forEach((card) => {
    card.addEventListener('click', () => {
      STATE.target_mode = card.dataset.targetMode || 'hibrido';
      document.querySelectorAll('#target-mode-selector .target-mode-card').forEach((c) => {
        c.classList.toggle('selected', c === card);
      });
      const badge = document.getElementById('active-target-mode-badge');
      if (badge) badge.textContent = targetLabels[STATE.target_mode] || STATE.target_mode;
      showToast(`Destino selecionado: ${targetLabels[STATE.target_mode]}`);
    });
  });

  // Library quick platform filters
  document.querySelectorAll('#library-platform-filters .filter-pill').forEach((pill) => {
    pill.addEventListener('click', () => {
      STATE.libraryFilter = pill.dataset.libFilter || 'all';
      document.querySelectorAll('#library-platform-filters .filter-pill').forEach((p) => {
        p.classList.toggle('active', p === pill);
      });
      renderClipsLibrary();
    });
  });

  // Library
  document.getElementById('btn-refresh-clips').addEventListener('click', loadClipsLibrary);

  // Scheduler
  document.getElementById('sched-clip-select').addEventListener('change', (e) => {
    const c = STATE.clips.find((x) => x.id === e.target.value);
    if (!c) return;
    document.getElementById('sched-title').value = c.title || '';
    document.getElementById('sched-desc').value = `${c.description || ''} ${(c.hashtags || []).join(' ')}`.trim();
  });

  document.getElementById('btn-add-schedule').addEventListener('click', async () => {
    const title = document.getElementById('sched-title').value.trim();
    const dt = document.getElementById('sched-datetime').value;
    if (!title || !dt) {
      showToast('Preencha o Titulo Viral e a Data/Horario para agendar.', 'error');
      return;
    }
    const clipId = document.getElementById('sched-clip-select').value || null;
    const clip = STATE.clips.find((x) => x.id === clipId);
    await apiFetch('/api/schedule', {
      method: 'POST',
      body: JSON.stringify({
        clip_id: clipId,
        title,
        description: document.getElementById('sched-desc').value.trim(),
        hashtags: clip?.hashtags || [],
        platform: document.getElementById('sched-platform').value,
        scheduled_for: dt,
        file_path: clip?.file_path || '',
      }),
    });
    showToast('Postagem adicionada a fila de publicacao!');
    document.getElementById('sched-title').value = '';
    document.getElementById('sched-desc').value = '';
    loadSchedule();
  });

  // Disk Cleanup
  document.getElementById('btn-scan-disk').addEventListener('click', loadDiskScan);
  document.getElementById('btn-clean-disk').addEventListener('click', async () => {
    const ids = Array.from(document.querySelectorAll('.disk-bucket-chk:checked')).map((el) => el.value);
    if (!ids.length) {
      showToast('Selecione ao menos uma pasta para limpar.', 'error');
      return;
    }
    const res = await apiFetch('/api/disk/clean', {
      method: 'POST',
      body: JSON.stringify({ bucket_ids: ids }),
    });
    showToast(`Limpeza concluida! ${res.freed_mb} MB liberados (${res.removed_files} arquivos).`);
    loadDiskScan();
  });

  // Settings, YouTube Official OAuth2 & X-Ray Test
  document.getElementById('btn-save-settings').addEventListener('click', saveSettingsForm);
  document.getElementById('btn-test-xray').addEventListener('click', () => {
    apiFetch('/api/system/test-xray', { method: 'POST' }).catch(() => {});
  });

  const ytWizardBackdrop = document.getElementById('yt-wizard-backdrop');
  const openYouTubeWizard = () => ytWizardBackdrop?.classList.add('open');
  const closeYouTubeWizard = () => ytWizardBackdrop?.classList.remove('open');

  document.getElementById('btn-wizard-close')?.addEventListener('click', closeYouTubeWizard);
  ytWizardBackdrop?.addEventListener('click', (e) => {
    if (e.target === ytWizardBackdrop) closeYouTubeWizard();
  });

  const ytSecretsInput = document.getElementById('yt-secrets-file-input');
  document.getElementById('btn-upload-yt-secrets').addEventListener('click', () => ytSecretsInput.click());
  document.getElementById('btn-wizard-upload-secrets')?.addEventListener('click', () => ytSecretsInput.click());

  ytSecretsInput.addEventListener('change', async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    const res = await apiFetch('/api/youtube/upload-secrets', { method: 'POST', body: fd });
    closeYouTubeWizard();
    showToast(`client_secrets.json importado em ${res.path}! Agora clique em Conectar Conta YouTube.`);
    loadYouTubeStatus();
  });

  document.getElementById('btn-wizard-studio-express')?.addEventListener('click', async () => {
    closeYouTubeWizard();
    const latestClip = (STATE.clips || []).find((c) => c.file_exists);
    if (latestClip) {
      await oneClickPublish('youtube_studio', latestClip);
    } else {
      window.open('https://studio.youtube.com', '_blank', 'noopener');
      apiFetch('/api/system/open-output', { method: 'POST' }, { silentXRay: true }).catch(() => {});
      showToast('Modo Studio Express ativado: YouTube Studio e pasta de saida abertos!');
    }
    setView('biblioteca');
  });

  document.getElementById('btn-yt-connect').addEventListener('click', async () => {
    const customPath = document.getElementById('cfg-yt-secrets-path').value.trim();
    const res = await apiFetch('/api/youtube/connect', {
      method: 'POST',
      body: JSON.stringify({ client_secrets_path: customPath }),
    });
    if (res.needs_secrets) {
      openYouTubeWizard();
      return;
    }
    showToast(res.message || 'Navegador aberto para conectar YouTube via OAuth2!');
    // Poll briefly until OAuth finishes
    const poller = setInterval(async () => {
      const st = await loadYouTubeStatus();
      if (!st || st.connected || !st.oauth_in_progress) {
        clearInterval(poller);
        if (st?.connected) showToast(`Conta YouTube conectada: ${st.channel_title}!`);
      }
    }, 2000);
  });

  document.getElementById('btn-yt-disconnect').addEventListener('click', async () => {
    await apiFetch('/api/youtube/disconnect', { method: 'POST' });
    showToast('Conta YouTube desconectada.');
    loadYouTubeStatus();
  });

  // YouTube Daily Autopilot Controls
  document.getElementById('btn-save-autopilot').addEventListener('click', async () => {
    const res = await apiFetch('/api/youtube/autopilot/config', {
      method: 'PUT',
      body: JSON.stringify({
        enabled: document.getElementById('yt-autopilot-enabled').checked,
        daily_time: document.getElementById('yt-autopilot-time').value || '18:30',
        privacy_status: document.getElementById('yt-autopilot-privacy').value || 'private',
        require_human_approval: Boolean(document.getElementById('yt-autopilot-approval')?.checked),
        daily_upload_limit: Number(document.getElementById('yt-autopilot-limit')?.value) || 2,
      }),
    });
    showToast(
      res.enabled
        ? `Piloto Diario ATIVO (${res.daily_time}, max ${res.daily_upload_limit}/dia)! Lembre-se: projetos sem auditoria Google enviam como Privado.`
        : 'Configuracoes do Piloto Diario salvas com protecao de conta.'
    );
    loadAutopilot();
  });

  document.getElementById('yt-autopilot-enabled').addEventListener('change', async (e) => {
    if (e.target.checked) {
      showToast('Aviso: Projetos sem auditoria Google enviam videos como PRIVADOS — teste 1 video antes do lote.', 'success');
    }
    await apiFetch('/api/youtube/autopilot/config', {
      method: 'PUT',
      body: JSON.stringify({ enabled: e.target.checked }),
    });
    showToast(e.target.checked ? 'Piloto Automatico Diario ativado!' : 'Piloto Automatico Diario pausado.');
    loadAutopilot();
  });

  document.getElementById('yt-autopilot-approval')?.addEventListener('change', async (e) => {
    await apiFetch('/api/youtube/autopilot/config', {
      method: 'PUT',
      body: JSON.stringify({ require_human_approval: e.target.checked }),
    });
    showToast(
      e.target.checked
        ? 'Aprovacao humana obrigatoria ATIVADA para proteger sua conta.'
        : 'Aprovacao humana obrigatoria desativada (use com cautela).'
    );
    loadAutopilot();
  });

  document.getElementById('btn-autopilot-add-one').addEventListener('click', async () => {
    const cid = document.getElementById('yt-autopilot-clip-select').value;
    if (!cid) {
      showToast('Selecione um clipe da Biblioteca para adicionar a fila diaria.', 'error');
      return;
    }
    const res = await apiFetch('/api/youtube/autopilot/queue', {
      method: 'POST',
      body: JSON.stringify({ clip_ids: [cid] }),
    });
    showToast(`${res.added_count || 0} clipe adicionado a fila diaria do YouTube!`);
    loadSchedule();
  });

  document.getElementById('btn-autopilot-import-all').addEventListener('click', async () => {
    const res = await apiFetch('/api/youtube/autopilot/queue', {
      method: 'POST',
      body: JSON.stringify({ import_all_library: true }),
    });
    showToast(`${res.added_count || 0} clipe(s) importado(s) da Biblioteca para a fila diaria!`);
    loadSchedule();
  });
}

// ---- Boot ----

async function boot() {
  initThemeSwitcher();
  initTooltips();
  initXRayPanel();
  initSystemHealthModal();
  initMoreCopyOptionsModal();
  initGuideAndTour();
  bindEvents();
  initGameSheetUI(STATE, (profileId) => {
    STATE.selectedProfileId = profileId;
    renderProfilesGrid();
  });
  initOverlayEditor(STATE);
  initWatcherUI((proj) => {
    applyLoadedProject(proj);
    loadRecentProjects();
  });
  initAnalyticsUI(STATE);
  initGrowthUI(STATE);
  initBackupZipUI();
  initDiagnosticsUI();

  // Default datetime input to tomorrow 18:00
  const tomorrow = new Date(Date.now() + 86400000);
  const pad = (n) => String(n).padStart(2, '0');
  document.getElementById('sched-datetime').value =
    `${tomorrow.getFullYear()}-${pad(tomorrow.getMonth() + 1)}-${pad(tomorrow.getDate())}T18:00`;

  await Promise.all([refreshHealth(), loadProfiles(), loadRecentProjects(), loadClipsLibrary(), loadSchedule()]);
  setInterval(refreshHealth, 15000);
}

boot();
