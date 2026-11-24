/**
 * ClipRush Studio Pro - P2 Growth, Analytics, Smart Agenda, A/B Testing, Goals,
 * Per-Project Storage Manager, Zip Backup/Restore & Synthetic Self-Test Diagnostics.
 */

import { apiFetch, getSessionToken } from './api.js';
import { flashButtonFeedback, showToast } from './xray.js';

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---- 1. Aba Desempenho & Aprendizado do Ranking ----

function renderDimTable(title, rows) {
  if (!rows || !rows.length) {
    return `<div class="card"><strong style="font-size:13.5px;">${escapeHtml(title)}</strong><div class="text-xs mt-2">Sem dados ainda.</div></div>`;
  }
  const body = rows
    .slice(0, 6)
    .map(
      (r) => `
      <div class="flex items-center justify-between gap-2" style="padding:6px 0;border-bottom:1px solid var(--border-subtle);font-size:12px;">
        <span style="font-weight:600;">${escapeHtml(r.label)} <span class="text-xs">(${r.count})</span></span>
        <span class="text-xs">${r.total_views} views · ${r.avg_watch_pct}% assist. · ${r.avg_retention_3s_pct}% 3s · <strong>${r.perf_score}</strong></span>
      </div>`
    )
    .join('');
  return `<div class="card"><strong style="font-size:13.5px;display:block;margin-bottom:6px;">${escapeHtml(title)}</strong>${body}</div>`;
}

export async function loadAnalyticsView(STATE) {
  try {
    const data = await apiFetch('/api/analytics/dashboard');
    const sum = data.summary || {};
    const kpi = document.getElementById('analytics-kpi-bar');
    if (kpi) {
      kpi.innerHTML = `
        <div class="card"><div class="text-xs">REGISTROS ANALISADOS</div><div style="font-size:20px;font-weight:800;">${sum.total_records || 0}</div></div>
        <div class="card"><div class="text-xs">VIEWS TOTAIS</div><div style="font-size:20px;font-weight:800;color:#22D3EE;">${(sum.total_views || 0).toLocaleString('pt-BR')}</div></div>
        <div class="card"><div class="text-xs">% MEDIA ASSISTIDA</div><div style="font-size:20px;font-weight:800;color:#10B981;">${sum.avg_watch_pct || 0}%</div></div>
        <div class="card"><div class="text-xs">RETENCAO 3s INICIAIS</div><div style="font-size:20px;font-weight:800;color:#A78BFA;">${sum.avg_retention_3s_pct || 0}%</div></div>`;
    }

    const insEl = document.getElementById('analytics-insights-box');
    const w = data.learned_weights || {};
    if (insEl) {
      const items = (w.insights_pt || []).map((t) => `<li>${escapeHtml(t)}</li>`).join('');
      insEl.innerHTML = `
        <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
          <strong style="font-size:14px;">Aprendizado Automatico do Ranking (Pesos Ativos)</strong>
          <span class="badge badge-green">${w.sample_size || 0} amostras no modelo</span>
        </div>
        <ul class="text-xs" style="margin:0;padding-left:18px;line-height:1.6;">${items}</ul>`;
    }

    const dims = document.getElementById('analytics-dimensions-grid');
    if (dims) {
      dims.innerHTML =
        renderDimTable('Por Jogo', data.by_game) +
        renderDimTable('Por Tipo de Evento', data.by_category) +
        renderDimTable('Por Duracao (25s vs 36-55s vs 61-90s)', data.by_duration) +
        renderDimTable('Por Estilo de Gancho', data.by_hook_style);
    }

    // Populate clip selector for manual entry
    const cSel = document.getElementById('perf-clip-select');
    if (cSel && Array.isArray(STATE?.clips)) {
      cSel.innerHTML =
        `<option value="">-- Clipe da Biblioteca (opcional) --</option>` +
        STATE.clips.map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml((c.title || c.file_name).slice(0, 56))}</option>`).join('');
    }

    const recList = document.getElementById('analytics-records-list');
    if (recList) {
      const recs = data.records || [];
      if (!recs.length) {
        recList.innerHTML = `<div class="empty-state" style="padding:20px;">Nenhuma metrica registrada ainda. Adicione manualmente acima ou importe o CSV do TikTok Studio.</div>`;
      } else {
        recList.innerHTML = recs
          .slice(0, 20)
          .map(
            (r) => `
            <div class="toggle-row" style="flex-wrap:wrap;">
              <div style="flex:1;min-width:220px;">
                <div class="flex items-center gap-2 flex-wrap mb-1">
                  <span class="badge badge-purple">${escapeHtml(r.game_name)}</span>
                  <span class="badge badge-cyan">${escapeHtml(r.duration_bucket)}</span>
                  <span class="badge badge-green">Score ${r.perf_score}</span>
                </div>
                <div style="font-weight:700;font-size:13px;">${escapeHtml(r.title)}</div>
                <div class="text-xs">${r.views} views · ${r.avg_watch_pct}% assistido · ${r.retention_3s_pct}% retencao 3s · ${r.shares} shares (${escapeHtml(r.source)})</div>
              </div>
              <button class="btn btn-sm btn-danger perf-del-btn" data-pid="${escapeHtml(r.id)}"><span>Remover</span></button>
            </div>`
          )
          .join('');
        recList.querySelectorAll('.perf-del-btn').forEach((b) => {
          b.addEventListener('click', async () => {
            await apiFetch(`/api/analytics/record/${b.dataset.pid}`, { method: 'DELETE' });
            loadAnalyticsView(STATE);
          });
        });
      }
    }
  } catch {
    // handled by apiFetch
  }
}

export function initAnalyticsUI(STATE) {
  document.getElementById('perf-clip-select')?.addEventListener('change', (e) => {
    const c = (STATE.clips || []).find((x) => x.id === e.target.value);
    if (!c) return;
    document.getElementById('perf-title').value = c.title || '';
    document.getElementById('perf-game').value = c.detected_game || '';
    document.getElementById('perf-duration').value = Math.round(c.duration || 28);
    if (c.category) document.getElementById('perf-category').value = c.category;
  });

  document.getElementById('btn-perf-save')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    const payload = {
      clip_id: document.getElementById('perf-clip-select')?.value || '',
      title: document.getElementById('perf-title').value.trim() || 'Clipe Gameplay',
      game_name: document.getElementById('perf-game').value.trim() || STATE.gameName || 'Universal',
      category: document.getElementById('perf-category').value,
      platform: document.getElementById('perf-platform').value,
      duration_sec: Number(document.getElementById('perf-duration').value) || 28,
      hook_style: document.getElementById('perf-hook-style').value,
      views: Number(document.getElementById('perf-views').value) || 0,
      avg_watch_pct: Number(document.getElementById('perf-watch-pct').value) || 0,
      retention_3s_pct: Number(document.getElementById('perf-ret3-pct').value) || 0,
      shares: Number(document.getElementById('perf-shares').value) || 0,
    };
    await apiFetch('/api/analytics/record', { method: 'POST', body: JSON.stringify(payload) });
    flashButtonFeedback(btn, 'Salvo ✓');
    showToast('Metrica registrada e pesos do ranking atualizados ✓');
    loadAnalyticsView(STATE);
  });

  const csvInput = document.getElementById('perf-tiktok-csv-input');
  document.getElementById('btn-perf-import-csv')?.addEventListener('click', () => csvInput?.click());
  csvInput?.addEventListener('change', async () => {
    const f = csvInput.files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    const res = await apiFetch('/api/analytics/import-tiktok-csv', { method: 'POST', body: fd });
    csvInput.value = '';
    showToast(`${res.imported_count || 0} registro(s) importado(s) do CSV TikTok Studio ✓`);
    loadAnalyticsView(STATE);
  });

  document.getElementById('btn-perf-sync-yt')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      const res = await apiFetch('/api/analytics/sync-youtube', { method: 'POST' });
      showToast(`${res.synced_count || 0} video(s) sincronizado(s)! ${res.note || ''}`);
      loadAnalyticsView(STATE);
    } finally {
      btn.disabled = false;
    }
  });
}

// ---- 2. Agenda Inteligente, Teste A/B & Painel de Metas ----

export async function loadGrowthPanel(STATE) {
  try {
    const st = await apiFetch('/api/growth/state', {}, { silentXRay: true });

    // Goals Progress Bars
    const g = st.goals || {};
    const p = st.goals_progress || {};
    const setV = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.value = v ?? 0;
    };
    setV('goal-tk-fol-cur', g.tiktok_followers_current);
    setV('goal-tk-fol-tgt', g.tiktok_followers_target);
    setV('goal-tk-v30-cur', g.tiktok_views30d_current);
    setV('goal-tk-v30-tgt', g.tiktok_views30d_target);
    setV('goal-yt-sub-cur', g.youtube_subs_current);
    setV('goal-yt-hrs-cur', g.youtube_watch_hours_current);
    setV('goal-yt-shv-cur', g.youtube_shorts_views_current);

    const bars = document.getElementById('goals-progress-bars');
    if (bars) {
      const items = [
        { label: `TikTok Seguidores (${g.tiktok_followers_current}/${g.tiktok_followers_target})`, pct: p.tiktok_followers_pct || 0 },
        { label: `TikTok Views 30d (${g.tiktok_views30d_current}/${g.tiktok_views30d_target})`, pct: p.tiktok_views30d_pct || 0 },
        { label: `YouTube Inscritos (${g.youtube_subs_current}/${g.youtube_subs_target})`, pct: p.youtube_subs_pct || 0 },
        { label: `YouTube Horas (${g.youtube_watch_hours_current}/${g.youtube_watch_hours_target})`, pct: p.youtube_watch_hours_pct || 0 },
        { label: `YouTube Shorts Views (${g.youtube_shorts_views_current}/${g.youtube_shorts_views_target})`, pct: p.youtube_shorts_views_pct || 0 },
      ];
      bars.innerHTML = items
        .map(
          (it) => `
          <div style="margin-bottom:8px;">
            <div class="flex justify-between text-xs mb-1"><span>${escapeHtml(it.label)}</span><strong>${it.pct}%</strong></div>
            <div class="progress-track"><div class="progress-fill" style="width:${Math.min(100, it.pct)}%;"></div></div>
          </div>`
        )
        .join('');
    }

    // Smart Agenda Slots + Pre-Publication Checklist
    const slotsEl = document.getElementById('smart-agenda-slots');
    if (slotsEl) {
      const slots = st.smart_slots || [];
      if (!slots.length) {
        slotsEl.innerHTML = `<div class="text-xs">Clique em <strong>Gerar Agenda Inteligente</strong> para alternar jogos e plataformas (Shorts / TikTok 25s / TikTok 61–90s) automaticamente.</div>`;
      } else {
        const chkLabels = [
          ['safe_zone_ok', 'Safe Zone OK'],
          ['audio_checked', 'Audio -14 LUFS'],
          ['hashtags_verified', 'Hashtags OK'],
          ['copyright_reviewed', 'Direitos Autorais'],
          ['pinned_cta_ready', 'CTA Comentario Fixo'],
        ];
        slotsEl.innerHTML = slots
          .slice(0, 12)
          .map((s) => {
            const ch = s.checklist || {};
            const chks = chkLabels
              .map(
                ([k, lbl]) => `
                <label class="text-xs flex items-center gap-1" style="cursor:pointer;">
                  <input type="checkbox" class="slot-chk-box" data-slot-id="${escapeHtml(s.id)}" data-chk-key="${k}" ${ch[k] ? 'checked' : ''} />
                  <span>${lbl}</span>
                </label>`
              )
              .join('');
            return `
              <div class="toggle-row" style="flex-direction:column;align-items:stretch;gap:6px;">
                <div class="flex items-center justify-between flex-wrap gap-2">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="badge badge-cyan">${escapeHtml(s.date)} ${escapeHtml(s.time)}</span>
                    <span class="badge badge-purple">${escapeHtml(s.platform_label)}</span>
                    <span class="badge badge-green">${escapeHtml(s.game_name)}</span>
                  </div>
                  <span class="badge ${s.status === 'pronto' ? 'badge-green' : 'badge-amber'}">${escapeHtml((s.status || 'planejado').toUpperCase())}</span>
                </div>
                <div style="font-weight:700;font-size:12.5px;">${escapeHtml(s.title)}</div>
                <div class="flex flex-wrap gap-3">${chks}</div>
              </div>`;
          })
          .join('');

        slotsEl.querySelectorAll('.slot-chk-box').forEach((box) => {
          box.addEventListener('change', async () => {
            const sid = box.dataset.slotId;
            const key = box.dataset.chkKey;
            await apiFetch(`/api/growth/agenda/${sid}`, {
              method: 'PATCH',
              body: JSON.stringify({ checklist: { [key]: box.checked } }),
            });
            loadGrowthPanel(STATE);
          });
        });
      }
    }

    // A/B Tests
    const abSel = document.getElementById('ab-clip-select');
    if (abSel && Array.isArray(STATE?.clips)) {
      abSel.innerHTML =
        `<option value="">-- Selecionar clipe (ou gerar livre) --</option>` +
        STATE.clips.map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml((c.title || c.file_name).slice(0, 56))}</option>`).join('');
    }

    const abList = document.getElementById('ab-tests-list');
    if (abList) {
      const tests = st.ab_tests || [];
      if (!tests.length) {
        abList.innerHTML = `<div class="text-xs">Nenhum Teste A/B criado ainda. Clique acima para gerar 2 variantes concorrentes de titulo/gancho.</div>`;
      } else {
        abList.innerHTML = tests
          .slice(0, 8)
          .map(
            (t) => `
            <div class="card" style="padding:12px;">
              <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
                <strong style="font-size:13px;">${escapeHtml(t.clip_title)}</strong>
                ${t.winner ? `<span class="badge badge-green">Vencedora: Variante ${escapeHtml(t.winner)} ✓</span>` : `<span class="badge badge-amber">Em Teste</span>`}
              </div>
              <div class="grid-2">
                <div class="toggle-row" style="flex-direction:column;align-items:stretch;">
                  <span class="badge badge-purple" style="align-self:flex-start;">VARIANTE A · ${escapeHtml(t.variant_a?.hook_2s)}</span>
                  <div style="font-weight:700;font-size:12.5px;margin-top:4px;">${escapeHtml(t.variant_a?.title)}</div>
                  <button class="btn btn-sm btn-success mt-2 ab-win-btn" data-ab-id="${escapeHtml(t.id)}" data-win="A"><span>Venceu A ✓</span></button>
                </div>
                <div class="toggle-row" style="flex-direction:column;align-items:stretch;">
                  <span class="badge badge-cyan" style="align-self:flex-start;">VARIANTE B · ${escapeHtml(t.variant_b?.hook_2s)}</span>
                  <div style="font-weight:700;font-size:12.5px;margin-top:4px;">${escapeHtml(t.variant_b?.title)}</div>
                  <button class="btn btn-sm btn-success mt-2 ab-win-btn" data-ab-id="${escapeHtml(t.id)}" data-win="B"><span>Venceu B ✓</span></button>
                </div>
              </div>
            </div>`
          )
          .join('');

        abList.querySelectorAll('.ab-win-btn').forEach((btn) => {
          btn.addEventListener('click', async () => {
            await apiFetch(`/api/growth/ab/${btn.dataset.abId}/winner`, {
              method: 'POST',
              body: JSON.stringify({ winner: btn.dataset.win }),
            });
            showToast(`Variante ${btn.dataset.win} registrada como vencedora ✓`);
            loadGrowthPanel(STATE);
          });
        });
      }
    }
  } catch {
    // silent
  }
}

export function initGrowthUI(STATE) {
  document.getElementById('btn-gen-smart-agenda')?.addEventListener('click', async () => {
    const ppd = Number(document.getElementById('smart-agenda-ppd')?.value) || 2;
    await apiFetch('/api/growth/agenda/generate', {
      method: 'POST',
      body: JSON.stringify({ posts_per_day: ppd, days: 7 }),
    });
    showToast(`Agenda Inteligente (${ppd} posts/dia) gerada ✓`);
    loadGrowthPanel(STATE);
  });

  document.getElementById('btn-create-ab-test')?.addEventListener('click', async () => {
    const cid = document.getElementById('ab-clip-select')?.value || '';
    await apiFetch('/api/growth/ab', {
      method: 'POST',
      body: JSON.stringify({
        clip_id: cid,
        game_name: STATE.gameName || 'Resident Evil 4 Remake',
        platform_target: STATE.target_mode === 'youtube_shorts' ? 'youtube_shorts' : 'tiktok_reels',
      }),
    });
    showToast('Teste A/B criado com 2 variantes unicas ✓');
    loadGrowthPanel(STATE);
  });

  document.getElementById('btn-save-goals')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    const payload = {
      tiktok_followers_current: Number(document.getElementById('goal-tk-fol-cur')?.value) || 0,
      tiktok_followers_target: Number(document.getElementById('goal-tk-fol-tgt')?.value) || 10000,
      tiktok_views30d_current: Number(document.getElementById('goal-tk-v30-cur')?.value) || 0,
      tiktok_views30d_target: Number(document.getElementById('goal-tk-v30-tgt')?.value) || 100000,
      youtube_subs_current: Number(document.getElementById('goal-yt-sub-cur')?.value) || 0,
      youtube_watch_hours_current: Number(document.getElementById('goal-yt-hrs-cur')?.value) || 0,
      youtube_shorts_views_current: Number(document.getElementById('goal-yt-shv-cur')?.value) || 0,
    };
    await apiFetch('/api/growth/goals', { method: 'PUT', body: JSON.stringify(payload) });
    flashButtonFeedback(btn, 'Salvo ✓');
    showToast('Metas de monetizacao atualizadas ✓');
    loadGrowthPanel(STATE);
  });
}

// ---- 3. Per-Project Storage Manager & Backup/Restore .zip ----

export async function loadProjectStorageBreakdown() {
  try {
    const data = await apiFetch('/api/disk/projects', {}, { silentXRay: true });
    const warn = document.getElementById('low-disk-warning-banner');
    if (warn) {
      warn.classList.toggle('hidden', !data.low_disk_warning);
    }
    const list = document.getElementById('disk-projects-list');
    if (!list) return;
    const projs = data.projects || [];
    if (!projs.length) {
      list.innerHTML = `<div class="text-xs">Nenhum projeto registrado ocupando disco.</div>`;
      return;
    }
    list.innerHTML = projs
      .map(
        (pr) => `
        <div class="toggle-row" style="flex-wrap:wrap;">
          <div style="flex:1;min-width:220px;">
            <div style="font-weight:700;font-size:13px;">${escapeHtml(pr.name)}</div>
            <div class="text-xs">Total: <strong>${pr.total_gb} GB</strong> (${pr.total_mb} MB) · Upload copiado: ${pr.upload_mb} MB · ${pr.render_count} renders (${pr.render_mb} MB)</div>
          </div>
          <div class="flex gap-2">
            ${
              pr.has_copied_upload
                ? `<button class="btn btn-sm btn-danger proj-del-btn" data-pid="${escapeHtml(pr.project_id)}" data-target="uploads"><span>Apagar Original Copiado</span></button>`
                : ''
            }
            ${
              pr.render_count > 0
                ? `<button class="btn btn-sm btn-danger proj-del-btn" data-pid="${escapeHtml(pr.project_id)}" data-target="renders"><span>Apagar Renders Antigos</span></button>`
                : ''
            }
          </div>
        </div>`
      )
      .join('');

    list.querySelectorAll('.proj-del-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const tgt = btn.dataset.target;
        const ok = window.confirm(
          tgt === 'uploads'
            ? 'Confirmar exclusao definitiva da copia enviada deste video original?'
            : 'Confirmar exclusao definitiva dos clipes renderizados deste projeto?'
        );
        if (!ok) return;
        const res = await apiFetch(`/api/disk/projects/${btn.dataset.pid}?target=${tgt}`, { method: 'DELETE' });
        showToast(`Liberados ${res.freed_mb || 0} MB (${res.removed_files || 0} arquivo(s)) ✓`);
        loadProjectStorageBreakdown();
      });
    });
  } catch {
    // ignore
  }
}

export function initBackupZipUI() {
  document.getElementById('btn-backup-export')?.addEventListener('click', async () => {
    const resp = await fetch('/api/backup/export', { cache: 'no-store' });
    if (!resp.ok) {
      showToast('Falha ao exportar backup .zip.', 'error');
      return;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ClipRush_Backup_${new Date().toISOString().slice(0, 10)}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast('Backup .zip baixado ✓');
  });

  const inp = document.getElementById('backup-import-input');
  document.getElementById('btn-backup-import')?.addEventListener('click', () => inp?.click());
  inp?.addEventListener('change', async () => {
    const f = inp.files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    const res = await apiFetch('/api/backup/import', { method: 'POST', body: fd });
    inp.value = '';
    showToast(`Restaurados ${res.count || 0} arquivo(s) de configuracao/historico ✓`);
  });
}

// ---- 4. Tela de Diagnostico, Versao, Changelog & Autoteste Sintetico ----

export async function loadDiagnosticsView() {
  try {
    const d = await apiFetch('/api/diagnostics');
    const verEl = document.getElementById('diag-version-badge');
    if (verEl) verEl.textContent = `Versao ${d.version}`;

    const grid = document.getElementById('diag-status-grid');
    if (grid) {
      grid.innerHTML = `
        <div class="health-item"><div class="text-xs">PORTA LOCAL 8765</div><div style="font-weight:700;">${d.port_8765_active ? '✓ Ativa em 127.0.0.1:8765' : '✕ Inativa'}</div></div>
        <div class="health-item"><div class="text-xs">FFMPEG</div><div style="font-weight:700;">${d.ffmpeg?.ok ? '✓ OK' : '✕ Ausente'} (${escapeHtml(d.ffmpeg?.source || '-')})</div></div>
        <div class="health-item"><div class="text-xs">OLLAMA / QWEN 3.5</div><div style="font-weight:700;">${escapeHtml(d.qwen_state?.label || '-')}</div></div>
        <div class="health-item"><div class="text-xs">DISCO LIVRE</div><div style="font-weight:700;${d.low_disk_warning ? 'color:#EF4444;' : ''}">${d.disk_free_gb} GB / ${d.disk_total_gb} GB ${d.low_disk_warning ? '(ALERTA <10 GB!)' : ''}</div></div>
        <div class="health-item"><div class="text-xs">MEMORIA RAM &amp; CPU</div><div style="font-weight:700;">RAM ${d.ram?.used_gb}/${d.ram?.total_gb} GB (${d.ram?.percent}%) · CPU ${d.cpu_percent}%</div></div>
        <div class="health-item"><div class="text-xs">BATERIA &amp; COFRE DPAPI</div><div style="font-weight:700;">${escapeHtml(d.battery?.label || '-')} · DPAPI: ${d.dpapi_vault ? 'Ativo ✓' : 'Off'}</div></div>`;
    }

    const chEl = document.getElementById('diag-changelog-list');
    if (chEl) {
      chEl.innerHTML = (d.changelog || [])
        .map(
          (c) => `
          <div class="card" style="padding:12px;">
            <div class="flex items-center justify-between mb-2">
              <strong style="font-size:13.5px;">Versao ${escapeHtml(c.version)}</strong>
              <span class="badge badge-purple">${escapeHtml(c.date)}</span>
            </div>
            <ul class="text-xs" style="margin:0;padding-left:18px;line-height:1.55;">
              ${(c.highlights || []).map((h) => `<li>${escapeHtml(h)}</li>`).join('')}
            </ul>
          </div>`
        )
        .join('');
    }
  } catch {
    // handled by apiFetch
  }
}

export function initDiagnosticsUI() {
  document.getElementById('btn-run-selftest')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    const out = document.getElementById('diag-selftest-result');
    btn.disabled = true;
    const span = btn.querySelector('span');
    const prev = span ? span.textContent : '';
    if (span) span.textContent = 'Executando Autoteste Sintetico...';
    if (out) {
      out.classList.remove('hidden');
      out.innerHTML = `<div class="text-xs">Gerando video sintetico, analisando, testando motor de copy e renderizando 9:16 Lanczos + Capa...</div>`;
    }
    try {
      const res = await apiFetch('/api/diagnostics/selftest', { method: 'POST' });
      if (out) {
        out.innerHTML = `
          <div class="flex items-center justify-between mb-2">
            <strong style="color:${res.ok ? '#10B981' : '#EF4444'};">${escapeHtml(res.summary_pt)}</strong>
            <span class="badge badge-cyan">${res.total_ms} ms</span>
          </div>
          ${(res.steps || [])
            .map(
              (s) => `
              <div class="flex items-center justify-between text-xs" style="padding:4px 0;border-bottom:1px solid var(--border-subtle);">
                <span>${s.ok ? '✓' : '✕'} ${escapeHtml(s.step)} ${s.detail ? `(${escapeHtml(s.detail)})` : ''}</span>
                <strong>${s.ms} ms</strong>
              </div>`
            )
            .join('')}`;
      }
      showToast(res.summary_pt, res.ok ? 'success' : 'error');
    } finally {
      btn.disabled = false;
      if (span) span.textContent = prev;
    }
  });
}
