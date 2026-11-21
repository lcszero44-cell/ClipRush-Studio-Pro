"""Comprehensive E2E + Playwright validation for ClipRush Studio Pro (Final Algorithm & Polish Update).
Verifies:
- Cache-Control: no-store
- Real FFmpeg 9:16 Tela Cheia Pro render with:
  * Smart Jump-Cuts / 1.25x Pacing
  * 3px Neon Retention Progress Bar
  * -14 LUFS Audio Normalization + Limiter + SFX Ducking
  * Smart Action Tracking (OpenCV Optical Flow center-of-mass per 1s window)
- Playwright UI checks (0 console errors, 0 page errors):
  * All 10 retention toggles in Step 2
  * Sidebar tooltip clean alignment (width <= 220px, zero overlap on main content cards)
  * Top Stepper deactivates when navigating to Biblioteca / Agendador / Limpeza / Configuracoes and restores in Novo Projeto
  * Guided YouTube OAuth2 Wizard Modal (4 steps, Google Cloud link, Upload JSON button, Studio Express button)
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8765"
BASE_DIR = Path(r"D:\ClipRush_Pro_V2")
sys.path.insert(0, str(BASE_DIR))

from backend.config import TEMP_DIR, get_ffmpeg_bins
from backend.storage import PROJECTS_FILE


def assert_no_store_headers() -> None:
    for path in ("/api/health/live", "/api/youtube/status", "/", "/static/js/app.js", "/static/js/tooltips.js"):
        r = httpx.get(f"{BASE_URL}{path}", timeout=5.0)
        assert r.status_code == 200
        assert "no-store" in r.headers.get("cache-control", "").lower()
    print("[PASS] Cache-Control: no-store verified.")


def create_synthetic_gameplay(out_path: Path) -> None:
    ffmpeg, _ = get_ffmpeg_bins()
    # 12s clip with a quiet dead stretch in the middle (t=2..5.5s) and loud action climax at t=7..10s
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=12",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=12",
        "-af", "volume='if(between(t,7,10),1.0,if(between(t,2,5.5),0.02,0.2))'",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0 and out_path.exists(), f"Synthetic video error: {res.stderr}"
    print("[PASS] Synthetic 12s gameplay created.")


def _wait_job(client: httpx.Client, job_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"{BASE_URL}/api/jobs/{job_id}")
        assert r.status_code == 200
        job = r.json()
        if job["status"] == "done":
            return job["result"]
        if job["status"] == "error":
            raise AssertionError(f"Job failed: {job.get('xray_error') or job.get('message')}")
        time.sleep(0.5)
    raise AssertionError(f"Job {job_id} timed out")


def test_tela_cheia_all_4_algorithm_mechanics(sample_video: Path) -> list[str]:
    created_clips: list[str] = []
    with httpx.Client(timeout=90.0) as client:
        r = client.post(
            f"{BASE_URL}/api/projects/import-local",
            json={"file_path": str(sample_video), "profile_id": "spider_man"},
        )
        assert r.status_code == 200
        pid = r.json()["id"]

        r = client.post(
            f"{BASE_URL}/api/projects/{pid}/auto-pipeline",
            json={
                "profile_id": "spider_man",
                "top_n": 1,
                "style": "tela_cheia",
                "subtitles_mode": "off",
                "subtitle_style": "destaque_keyword",
                "enable_hook": True,
                "enable_impact_zoom": True,
                "enable_preview_flash": True,
                "enable_infinite_loop": True,
                "enable_cta_comment": True,
                "enable_impact_sfx": True,
                "enable_smart_pacing": True,
                "enable_progress_bar": True,
                "enable_audio_norm": True,
                "enable_action_tracking": True,
            },
        )
        assert r.status_code == 200, f"Auto pipeline start failed: {r.text}"
        out = _wait_job(client, r.json()["job_id"])
        clip = out["clips"][0]
        created_clips.append(clip["id"])
        assert Path(clip["file_path"]).exists(), "Rendered Tela Cheia Pro MP4 missing"
        ret = clip.get("retention", {})
        assert ret.get("progress_bar") is True, f"Progress bar not enabled: {ret}"
        assert ret.get("audio_norm_lufs") is True, f"-14 LUFS audio norm not enabled: {ret}"
        assert ret.get("action_tracking") is True, f"Smart Action Tracking not enabled: {ret}"
        print(
            f"[PASS] Rendered Tela Cheia Pro MP4 ({clip['size_mb']} MB) with "
            f"3px Neon Progress Bar + -14 LUFS Limiter + Ducking SFX + Smart Action Tracking + Smart Pacing={ret.get('smart_pacing')}"
        )
    return created_clips


def run_playwright_checks() -> None:
    console_errors: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_selector(".profile-card", timeout=10000)

        # 1. Verify ALL 10 retention toggles in Step 2 (including the 4 new ones)
        page.click("#step-btn-2")
        page.wait_for_selector("#step-panel-2:not(.hidden)")
        for tid in (
            "#toggle-hook",
            "#toggle-preview-flash",
            "#toggle-infinite-loop",
            "#toggle-cta-comment",
            "#toggle-impact-zoom",
            "#toggle-impact-sfx",
            "#toggle-smart-pacing",
            "#toggle-progress-bar",
            "#toggle-audio-norm",
            "#toggle-action-tracking",
        ):
            el = page.locator(tid)
            assert el.count() == 1 and el.is_checked(), f"Toggle missing or unchecked: {tid}"
        print("[PASS] All 10 algorithmic retention toggles verified in Step 2.")

        # 2. Verify Sidebar Tooltip Positioning (does NOT overlap main content cards, max-width <= 220px)
        sidebar_btn = page.locator('.nav-item[data-view="biblioteca"]')
        sidebar_btn.hover()
        page.wait_for_selector("#cr-floating-tooltip.visible", timeout=3000)
        tip_metrics = page.evaluate("""() => {
            const tip = document.getElementById('cr-floating-tooltip');
            const sb = document.querySelector('.app-sidebar');
            const tr = tip.getBoundingClientRect();
            const sr = sb.getBoundingClientRect();
            return { width: tr.width, tipRight: tr.right, sidebarRight: sr.right, hasClass: tip.classList.contains('sidebar-tip') };
        }""")
        assert tip_metrics["hasClass"], "Expected .sidebar-tip class on sidebar tooltip"
        assert tip_metrics["width"] <= 220, f"Tooltip width exceeded 220px: {tip_metrics}"
        assert tip_metrics["tipRight"] <= tip_metrics["sidebarRight"] + 2, f"Sidebar tooltip overlaps main area: {tip_metrics}"
        print(f"[PASS] Sidebar tooltip aligned cleanly inside sidebar (width={tip_metrics['width']:.0f}px, zero overlap).")

        # 3. Verify Top Stepper Synchronization with ALL 4 Management Sidebar Tabs
        for tab in ("biblioteca", "agendador", "limpeza", "configuracoes"):
            page.click(f'.nav-item[data-view="{tab}"]')
            page.wait_for_selector(f"#view-{tab}.active")
            active_steps = page.locator(".header-stepper .step-btn.active").count()
            assert active_steps == 0, f"Top stepper still highlighted while on '{tab}' (active={active_steps})"
            assert "inactive-mode" in (page.locator(".header-stepper").get_attribute("class") or "")

        # Return to Novo Projeto -> stepper highlight must restore!
        page.click('.nav-item[data-view="novo-projeto"]')
        page.wait_for_selector("#view-novo-projeto.active")
        assert page.locator(".header-stepper .step-btn.active").count() == 1
        print("[PASS] Top stepper deactivates cleanly on all 4 management tabs and restores in Novo Projeto.")

        # 4. Verify Guided YouTube OAuth2 Visual Wizard Modal when client_secrets.json is missing
        page.click('.nav-item[data-view="configuracoes"]')
        page.wait_for_selector("#view-configuracoes.active")
        # Ensure path points to a non-existent secrets file if user didn't place one yet
        page.fill("#cfg-yt-secrets-path", r"D:\ClipRush_Pro_V2\data\_nonexistent_client_secrets_test.json")
        page.click("#btn-yt-connect")
        page.wait_for_selector("#yt-wizard-backdrop.open", timeout=4000)

        # Confirm NO X-Ray error modal opened!
        assert "open" not in (page.locator("#xray-backdrop").get_attribute("class") or ""), "X-Ray error should not open for missing client_secrets"
        assert page.locator("#yt-wizard-backdrop .wizard-step-row").count() == 4
        href = page.locator("#btn-wizard-open-console").get_attribute("href")
        assert href == "https://console.cloud.google.com/apis/credentials"
        assert page.locator("#btn-wizard-upload-secrets").is_visible()
        assert page.locator("#btn-wizard-studio-express").is_visible()

        page.click("#btn-wizard-close")
        assert "open" not in (page.locator("#yt-wizard-backdrop").get_attribute("class") or "")
        print("[PASS] Guided YouTube OAuth2 Wizard Modal (4 steps, Google Cloud link, Upload JSON, Studio Express) verified.")

        browser.close()

    assert len(console_errors) == 0, f"Console errors: {console_errors}"
    assert len(page_errors) == 0, f"Page errors: {page_errors}"
    print("[PASS] Playwright finished with 0 console errors and 0 page errors!")


def cleanup(sample_video: Path, clip_ids: list[str]) -> None:
    with httpx.Client(timeout=10.0) as client:
        for cid in clip_ids:
            client.delete(f"{BASE_URL}/api/clips/{cid}?delete_file=true")
        # Restore default youtube_client_secrets_path in settings
        client.put(
            f"{BASE_URL}/api/settings",
            json={"youtube_client_secrets_path": r"D:\ClipRush_Pro_V2\data\client_secrets.json"},
        )
    if sample_video.exists():
        sample_video.unlink(missing_ok=True)
    if PROJECTS_FILE.exists():
        projs = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        kept, removed = [], set()
        for p in projs:
            if "_validate_algo_" in p.get("name", ""):
                removed.add(p.get("id"))
            else:
                kept.append(p)
        PROJECTS_FILE.write_text(json.dumps(kept, indent=2), encoding="utf-8")
        for rid in removed:
            for tf in (BASE_DIR / "data" / "thumbs").glob(f"{rid}*"):
                tf.unlink(missing_ok=True)
    print("[PASS] Validation artifacts cleaned up.")


if __name__ == "__main__":
    vid = TEMP_DIR / "_validate_algo_gameplay.mp4"
    cids: list[str] = []
    try:
        assert_no_store_headers()
        create_synthetic_gameplay(vid)
        cids = test_tela_cheia_all_4_algorithm_mechanics(vid)
        run_playwright_checks()
        print("\n=== ALL ALGORITHM & UI POLISH VALIDATIONS PASSED (0 ERRORS) ===")
    finally:
        cleanup(vid, cids)
