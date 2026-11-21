/**
 * ClipRush Studio Pro - Main Modular Frontend Controller
 */
import { apiFetch, pollJob } from './api.js';
import { initTooltips } from './tooltips.js';
import { copyToClipboard, initXRayPanel, showToast } from './xray.js';

const STATE = {
  view: 'novo-projeto',
  step: 1,
  mode: 'viral_9_16', // viral_9_16 | long_16_9
  style: 'viral_blur', // viral_blur | tela_cheia | clipe_limpo
  subtitles_mode: 'off', // off | whisper
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
  if (viewName === 'agendador') loadSchedule();
  if (viewName === 'limpeza') loadDiskScan();
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
    const ollChip = document.getElementById('chip-ollama');
    const ollLbl = document.getElementById('chip-ollama-label');
    const ffChip = document.getElementById('chip-ffmpeg');
    const ffLbl = document.getElementById('chip-ffmpeg-label');

    sysChip.className = 'status-chip ok';
    if (st.ollama?.ok) {
      ollChip.className = 'status-chip ok';
      ollLbl.textContent = 'Qwen 3.5 OK';
    } else if (st.ollama?.online) {
      ollChip.className = 'status-chip warn';
      ollLbl.textContent = 'Ollama sem Qwen 3.5';
    } else {
      ollChip.className = 'status-chip warn';
      ollLbl.textContent = 'Qwen 3.5 Offline';
    }

    if (st.ffmpeg?.ok) {
      ffChip.className = 'status-chip ok';
      ffLbl.textContent = 'FFmpeg OK';
    } else {
      ffChip.className = 'status-chip err';
      ffLbl.textContent = 'FFmpeg Ausente';
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
    `${meta.duration_str || '00:00'} • ${meta.width || 1920}x${meta.height || 1080} • ${meta.fps || 30}fps • ${meta.size_mb || 0} MB`;
  document.getElementById('btn-step1-next').disabled = false;
  const autoBtn1 = document.getElementById('btn-auto-mode-step1');
  if (autoBtn1) autoBtn1.disabled = false;
  document.getElementById('auto-profile-badge').textContent = `Perfil: ${
    STATE.profiles.find((p) => p.id === STATE.selectedProfileId)?.short_name || STATE.selectedProfileId
  }`;
}

async function handleFileUpload(file) {
  if (!file) return;
  showToast(`Enviando "${file.name}"...`);
  const fd = new FormData();
  fd.append('file', file);
  fd.append('profile_id', STATE.selectedProfileId === 'generico' ? 'auto' : STATE.selectedProfileId);
  const proj = await apiFetch('/api/projects/upload', { method: 'POST', body: fd });
  applyLoadedProject(proj);
  showToast(`Gameplay "${proj.name}" carregada com sucesso!`);
  loadRecentProjects();
}

async function handleLocalImport() {
  const input = document.getElementById('local-path-input');
  const filePath = input.value.trim();
  if (!filePath) {
    showToast('Informe o caminho completo do arquivo de video no disco.', 'error');
    return;
  }
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
}

// ---- Analysis Job ----

async function startAnalysis() {
  if (!STATE.project) {
    showToast('Importe uma gameplay na Etapa 1 antes de analisar.', 'error');
    setStep(1);
    return;
  }
  setStep(2);
  const box = document.getElementById('analysis-progress-box');
  const msgEl = document.getElementById('analysis-progress-msg');
  const pctEl = document.getElementById('analysis-progress-pct');
  const fillEl = document.getElementById('analysis-progress-fill');
  const btn = document.getElementById('btn-start-analysis');

  box.classList.remove('hidden');
  btn.disabled = true;
  msgEl.textContent = 'Preparando motor multimodal (FFmpeg + OpenCV + Qwen 3.5)...';
  pctEl.textContent = '3%';
  fillEl.style.width = '3%';

  try {
    const res = await apiFetch(`/api/projects/${STATE.project.id}/analyze`, {
      method: 'POST',
      body: JSON.stringify({ profile_id: STATE.selectedProfileId }),
    });
    const updatedProject = await pollJob(res.job_id, (job) => {
      msgEl.textContent = job.message || 'Analisando...';
      pctEl.textContent = `${job.progress || 0}%`;
      fillEl.style.width = `${job.progress || 0}%`;
    });
    STATE.project = updatedProject;
    STATE.selectedMomentIds = new Set();
    showToast(`Analise concluida! ${updatedProject.moments?.length || 0} momentos epicos detectados.`);
    setStep(3);
  } finally {
    btn.disabled = false;
    setTimeout(() => box.classList.add('hidden'), 1200);
  }
}

async function startAutoPipeline() {
  if (!STATE.project) {
    showToast('Importe uma gameplay na Etapa 1 antes de iniciar o Modo Automatico.', 'error');
    setStep(1);
    return;
  }

  const isStep1 = STATE.step === 1;
  const box = document.getElementById(isStep1 ? 'auto-progress-box-step1' : 'analysis-progress-box');
  const msgEl = document.getElementById(isStep1 ? 'auto-progress-msg-step1' : 'analysis-progress-msg');
  const pctEl = document.getElementById(isStep1 ? 'auto-progress-pct-step1' : 'analysis-progress-pct');
  const fillEl = document.getElementById(isStep1 ? 'auto-progress-fill-step1' : 'analysis-progress-fill');

  const autoButtons = [
    document.getElementById('btn-auto-mode-step1'),
    document.getElementById('btn-auto-quick-step1'),
    document.getElementById('btn-auto-mode-step2'),
    document.getElementById('btn-start-analysis'),
  ].filter(Boolean);

  autoButtons.forEach((b) => (b.disabled = true));
  box.classList.remove('hidden');
  msgEl.textContent = 'Modo Automatico: iniciando analise multimodal + Qwen 3.5 + render Top 5...';
  pctEl.textContent = '3%';
  fillEl.style.width = '3%';

  try {
    const res = await apiFetch(`/api/projects/${STATE.project.id}/auto-pipeline`, {
      method: 'POST',
      body: JSON.stringify({
        profile_id: STATE.selectedProfileId,
        top_n: 5,
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
      }),
    });

    const out = await pollJob(res.job_id, (job) => {
      msgEl.textContent = job.message || 'Processando Modo Automatico...';
      pctEl.textContent = `${job.progress || 0}%`;
      fillEl.style.width = `${job.progress || 0}%`;
    });

    if (out?.project) {
      STATE.project = out.project;
    }
    showToast(`Modo Automatico concluido! ${out?.count || 0} clipe(s) prontos na Biblioteca para publicar.`);
    await loadClipsLibrary();
    setView('biblioteca');
  } finally {
    autoButtons.forEach((b) => (b.disabled = false));
    setTimeout(() => box.classList.add('hidden'), 1400);
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
  summary.textContent = `${proj.moments.length} momentos epicos encontrados (${proj.boring_skipped_sec || 0}s de menus/loadings ignorados) • Perfil: ${profileObj?.name || proj.profile_id}`;

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
            <div class="flex items-center justify-between gap-2">
              <span class="badge ${catBadgeClass}">${escapeHtml(m.category_label)}</span>
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
  const box = document.getElementById('render-progress-box');
  const msgEl = document.getElementById('render-progress-msg');
  const pctEl = document.getElementById('render-progress-pct');
  const fillEl = document.getElementById('render-progress-fill');

  box.classList.remove('hidden');
  msgEl.textContent = 'Iniciando renderizacao continua em lote...';
  pctEl.textContent = '2%';
  fillEl.style.width = '2%';

  try {
    const res = await apiFetch(`/api/projects/${STATE.project.id}/render-batch`, {
      method: 'POST',
      body: JSON.stringify({
        limit,
        moment_ids,
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
      }),
    });
    const out = await pollJob(res.job_id, (job) => {
      msgEl.textContent = job.message || 'Renderizando...';
      pctEl.textContent = `${job.progress || 0}%`;
      fillEl.style.width = `${job.progress || 0}%`;
    });
    showToast(`${out.count || 0} clipe(s) exportados com sucesso! Disponiveis na Biblioteca.`);
    await loadClipsLibrary();
  } finally {
    setTimeout(() => box.classList.add('hidden'), 1800);
  }
}

async function runLongExport() {
  if (!STATE.project) return;
  const box = document.getElementById('render-progress-box');
  const msgEl = document.getElementById('render-progress-msg');
  const pctEl = document.getElementById('render-progress-pct');
  const fillEl = document.getElementById('render-progress-fill');

  box.classList.remove('hidden');
  msgEl.textContent = 'Exportando Gameplay 16:9 (1920x1080) e Thumbnail...';
  pctEl.textContent = '5%';
  fillEl.style.width = '5%';

  try {
    const res = await apiFetch(`/api/projects/${STATE.project.id}/render-long`, { method: 'POST' });
    await pollJob(res.job_id, (job) => {
      msgEl.textContent = job.message || 'Renderizando 1080p...';
      pctEl.textContent = `${job.progress || 0}%`;
      fillEl.style.width = `${job.progress || 0}%`;
    });
    showToast('Gameplay 16:9 e Thumbnail 1080p salvas na pasta de saida!');
    await loadClipsLibrary();
  } finally {
    setTimeout(() => box.classList.add('hidden'), 1800);
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

function renderClipsLibrary() {
  const grid = document.getElementById('clips-library-grid');
  if (!STATE.clips.length) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1;">
        <div style="font-size:15px;font-weight:700;color:#fff;margin-bottom:6px;">Sua Biblioteca de Clipes esta vazia</div>
        <div>Renderize clipes no Modo 1 (9:16) ou Gameplay 16:9 no Novo Projeto para visualizar e publicar por aqui.</div>
      </div>`;
    return;
  }

  grid.innerHTML = STATE.clips
    .map(
      (c) => `
      <article class="card flex" style="flex-direction:column;gap:10px;" data-clip-card="${escapeHtml(c.id)}">
        <div class="flex items-center justify-between gap-2 flex-wrap">
          <div class="flex items-center gap-2">
            <span class="badge ${c.mode === 'long_16_9' ? 'badge-cyan' : 'badge-purple'}">${escapeHtml(c.category_label || c.style || 'Clipe')}</span>
            ${c.detected_game ? `<span class="badge badge-green">${escapeHtml(c.detected_game)}</span>` : ''}
          </div>
          <span class="text-xs">${c.file_size_mb || 0} MB • ${Math.round(c.duration || 0)}s</span>
        </div>

        ${
          c.file_exists
            ? `<video class="clip-video" controls preload="metadata" src="${escapeHtml(c.media_url)}"></video>`
            : `<div class="empty-state" style="padding:20px;">Arquivo movido ou apagado do disco.</div>`
        }

        <div class="clip-title-text" style="font-weight:700;color:#fff;font-size:13.5px;">${escapeHtml(c.title)}</div>
        <div class="text-xs clip-desc-text" style="color:var(--text-secondary);">${escapeHtml(c.description)}</div>
        <div class="text-xs clip-tags-text" style="color:#818CF8;">${escapeHtml((c.hashtags || []).join(' '))}</div>

        <!-- Regenerate Metadata 1-Click -->
        <button class="btn btn-sm btn-cyan regen-clip-btn" data-clip-id="${escapeHtml(c.id)}"
          data-tooltip="Usa o Qwen 3.5 Vision para gerar uma nova sugestao criativa de Titulo, Descricao e Hashtags sem re-renderizar o video.">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          <span>Refazer Texto / Gerar Outra Opcao</span>
        </button>

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
      </article>`
    )
    .join('');

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
    if (enabledEl) enabledEl.checked = Boolean(ap.enabled);
    if (timeEl) timeEl.value = ap.daily_time || '18:30';
    if (privEl) privEl.value = ap.privacy_status || 'public';

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
        const stBadge =
          st === 'publicado'
            ? `<span class="badge badge-green">PUBLICADO</span>`
            : st === 'publicando'
            ? `<span class="badge badge-cyan">PUBLICANDO...</span>`
            : st === 'erro'
            ? `<span class="badge badge-red">ERRO</span>`
            : `<span class="badge badge-purple">AGENDADO • ${escapeHtml(it.estimated_publish_at || ap.daily_time)}</span>`;

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
            <div class="flex gap-2">
              ${
                st !== 'publicado'
                  ? `<button class="btn btn-sm btn-yt ap-publish-now-btn" data-ap-id="${escapeHtml(it.id)}"
                      data-tooltip="Envia este clipe imediatamente para o YouTube Shorts usando a API oficial conectada.">
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

    qList.querySelectorAll('.ap-publish-now-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          const res = await apiFetch(`/api/youtube/autopilot/publish-now/${btn.dataset.apId}`, { method: 'POST' });
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
  document.getElementById('cfg-min-sec').value = cfg.default_clip_min_sec || 12;
  document.getElementById('cfg-max-sec').value = cfg.default_clip_max_sec || 45;
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
    default_clip_min_sec: Number(document.getElementById('cfg-min-sec').value) || 12,
    default_clip_max_sec: Number(document.getElementById('cfg-max-sec').value) || 45,
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

  // Header Actions
  document.getElementById('btn-open-output').addEventListener('click', () => {
    apiFetch('/api/system/open-output', { method: 'POST' });
    showToast('Abrindo pasta de saida no Windows Explorer...');
  });
  document.getElementById('chip-system').addEventListener('click', () => {
    refreshHealth();
    showToast('Status do sistema atualizado!');
  });
  document.getElementById('chip-ollama').addEventListener('click', async () => {
    await apiFetch('/api/system/start-ollama', { method: 'POST' });
    showToast('Verificando/Iniciando Ollama em segundo plano...');
    setTimeout(refreshHealth, 2500);
  });
  document.getElementById('chip-ffmpeg').addEventListener('click', () => setView('configuracoes'));

  // Dropzone & File Input
  const dz = document.getElementById('dropzone');
  const fi = document.getElementById('file-input');
  dz.addEventListener('click', () => fi.click());
  fi.addEventListener('change', (e) => handleFileUpload(e.target.files?.[0]));
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    dz.classList.remove('dragover');
    handleFileUpload(e.dataTransfer?.files?.[0]);
  });

  document.getElementById('btn-import-local').addEventListener('click', handleLocalImport);
  document.getElementById('btn-step1-next').addEventListener('click', () => setStep(2));
  document.getElementById('btn-quick-analyze-step1').addEventListener('click', startAnalysis);
  document.getElementById('btn-auto-mode-step1').addEventListener('click', startAutoPipeline);
  document.getElementById('btn-auto-quick-step1').addEventListener('click', startAutoPipeline);
  document.getElementById('btn-auto-mode-step2').addEventListener('click', startAutoPipeline);

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
        privacy_status: document.getElementById('yt-autopilot-privacy').value || 'public',
      }),
    });
    showToast(
      res.enabled
        ? `Piloto Automatico Diario ATIVO todos os dias as ${res.daily_time}!`
        : 'Configuracoes do Piloto Diario salvas (pausado).'
    );
    loadAutopilot();
  });

  document.getElementById('yt-autopilot-enabled').addEventListener('change', async (e) => {
    await apiFetch('/api/youtube/autopilot/config', {
      method: 'PUT',
      body: JSON.stringify({ enabled: e.target.checked }),
    });
    showToast(e.target.checked ? 'Piloto Automatico Diario ativado!' : 'Piloto Automatico Diario pausado.');
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
  initTooltips();
  initXRayPanel();
  bindEvents();

  // Default datetime input to tomorrow 18:00
  const tomorrow = new Date(Date.now() + 86400000);
  const pad = (n) => String(n).padStart(2, '0');
  document.getElementById('sched-datetime').value =
    `${tomorrow.getFullYear()}-${pad(tomorrow.getMonth() + 1)}-${pad(tomorrow.getDate())}T18:00`;

  await Promise.all([refreshHealth(), loadProfiles(), loadRecentProjects(), loadClipsLibrary(), loadSchedule()]);
  setInterval(refreshHealth, 15000);
}

boot();
