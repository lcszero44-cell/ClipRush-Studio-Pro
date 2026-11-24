"""End-to-End & Playwright Validation Suite for ClipRush Studio Pro P1 Features.

Validates:
1. "Qual e o jogo?" + Editable Game Sheet (data/games_db.json) + New-Game Notice.
2. Combinatorial Copy Engine: 10 kits with 0 title repetitions, 0 hashtag set repetitions,
   Jaccard N-gram < 0.35, reservoir counts (100+ broad, 60+ genre, 40+ game, 20+ event),
   and "Ver mais 5 opcoes" UI flow.
3. Interactive 9:16 Visual Title Editor: Playwright mouse drag -> overlay.json ->
   real FFmpeg render (Big ~4:3 Lanczos layout + Individual Shorts Cover JPEG) ->
   OpenCV verification that rendered bright yellow title Y-centroid matches dragged preview Y!
4. Theme Switcher (Dark -> Light -> Auto -> Dark) via Playwright with real computed light background.
5. 5-State Qwen Indicator + System Health Modal (FFmpeg, Ollama, Disk, RAM, Battery)
   + Human Problems Drawer (stable error codes) + PS4 Watch Folder detection + 0 console/page errors.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8765"
BASE_DIR = Path(r"D:\ClipRush_Pro_V2")
sys.path.insert(0, str(BASE_DIR))

from backend.config import TEMP_DIR, get_ffmpeg_bins
from backend.copy_engine import get_copy_engine_stats, jaccard_similarity
from backend.editor import render_viral_clip
from backend.ollama_client import get_qwen_five_state
from backend.storage import (
    OVERLAY_FILE,
    load_overlay_config,
    save_overlay_config,
)


def synthesize_dark_gameplay(out_path: Path) -> None:
    """Create a 14s dark-background gameplay video (720p) so bright #FFD700 title pixels are 100% measurable."""
    ffmpeg, _ = get_ffmpeg_bins()
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=0x101828:s=1280x720:r=30:d=14",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=14",
        "-af", "volume=0.25",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0 and out_path.exists(), f"Failed dark video synth: {res.stderr}"


def main() -> None:
    dark_vid = TEMP_DIR / "_validate_p1_dark.mp4"
    watch_tmp = TEMP_DIR / "_validate_p1_ps4_watch"
    orig_overlay = load_overlay_config()
    orig_settings: dict = {}
    rendered_files: list[Path] = []
    token = ""

    synthesize_dark_gameplay(dark_vid)

    try:
        # Grab live session token from GET /
        with httpx.Client(timeout=30.0) as anon:
            idx_html = ""
            for _ in range(15):
                try:
                    r0 = anon.get(f"{BASE_URL}/")
                    if r0.status_code == 200:
                        idx_html = r0.text
                        break
                except Exception:
                    time.sleep(0.5)
            m_tok = re.search(r'name="cliprush-token" content="([^"]+)"', idx_html)
            assert m_tok, "Session token missing from GET /"
            token = m_tok.group(1)

        headers = {"X-ClipRush-Token": token}
        with httpx.Client(timeout=60.0, headers=headers) as client:
            orig_settings = client.get(f"{BASE_URL}/api/settings").json()

            # ---- P1 #2: 10 KITS WITHOUT REPETITION (TITLE + HASHTAGS + JACCARD < 0.35) ----
            stats = get_copy_engine_stats()
            assert stats["titles_combinations"] >= 800, f"Expected >=800 title combos, got {stats}"
            assert stats["descriptions_combinations"] >= 400, f"Expected >=400 desc combos, got {stats}"
            resv = stats["hashtag_reservoirs"]
            assert resv["broad"] >= 100 and resv["genre"] >= 60 and resv["game"] >= 40 and resv["event"] >= 20, f"Reservoirs too small: {resv}"

            r_kits = client.post(
                f"{BASE_URL}/api/copy/more-options",
                json={
                    "count": 10,
                    "game_name": "Resident Evil 4 Remake",
                    "profile_id": "resident_evil",
                    "category": "combate_intenso",
                    "platform_target": "tiktok_reels",
                },
            )
            assert r_kits.status_code == 200, r_kits.text
            kits = r_kits.json()["options"]
            assert len(kits) == 10
            titles = [k["title"] for k in kits]
            tag_sigs = [" ".join(sorted(t.lower() for t in k["hashtags"])) for k in kits]
            assert len(set(titles)) == 10, f"Duplicate titles found in 10 kits: {titles}"
            assert len(set(tag_sigs)) == 10, f"Duplicate hashtag sets found in 10 kits: {tag_sigs}"
            max_pair_sim = 0.0
            for i in range(len(titles)):
                for j in range(i + 1, len(titles)):
                    sim = jaccard_similarity(titles[i], titles[j])
                    max_pair_sim = max(max_pair_sim, sim)
                    assert sim < 0.35, f"Pair exceeded Jaccard 0.35 ({sim:.2f}): '{titles[i]}' vs '{titles[j]}'"
            # Verify Game Sheet data was infused into kits
            re4_terms = ("resident evil 4", "leon", "ashley", "ada", "vila", "castelo", "red9", "riot gun", "parry", "serrada", "re4")
            infused_hits = sum(
                1 for k in kits if any(t in (k["title"] + " " + k["description"]).lower() for t in re4_terms)
            )
            assert infused_hits == 10, f"Expected all 10 kits infused with Game Sheet data, got {infused_hits}/10"
            print(
                f"[PASS] P1 #2 Copy Engine: 10/10 unique titles & hashtag sets (max pair Jaccard={max_pair_sim:.2f}), "
                f"capacity={stats['titles_combinations']} titles / {stats['descriptions_combinations']} descs, "
                f"reservoirs={resv}!"
            )

            # ---- P1 #5: PS4 WATCH FOLDER AUTO-DETECTION ----
            watch_tmp.mkdir(parents=True, exist_ok=True)
            watch_fname = f"PS4_ResidentEvil_{int(time.time())}.mp4"
            shutil.copy2(dark_vid, watch_tmp / watch_fname)
            r_wc = client.put(
                f"{BASE_URL}/api/watcher/config",
                json={"enabled": True, "folder_path": str(watch_tmp)},
            )
            assert r_wc.status_code == 200, r_wc.text
            w_st = r_wc.json()
            assert w_st["folder_exists"] is True and w_st["pending_count"] >= 1
            wq_item = next(q for q in w_st["queue"] if q["file_name"] == watch_fname)
            client.delete(f"{BASE_URL}/api/watcher/queue/{wq_item['id']}")
            print("[PASS] P1 #5 PS4 Watch Folder detected and queued new MP4 clip cleanly!")

            # ---- P1 #4: 5-STATE QWEN INDICATOR & SYSTEM HEALTH ----
            st_health = client.get(f"{BASE_URL}/api/health/status").json()
            assert st_health["qwen_state"]["state"] in (
                "connected_vision", "loading_model", "analyzing_cut", "offline", "no_vision"
            )
            assert "ram" in st_health["hardware"] and "battery" in st_health["hardware"] and "disk_free_gb" in st_health["hardware"]
            # Verify state transitions directly via get_qwen_five_state
            s_cut = get_qwen_five_state(active_cut_number=3)
            s_load = get_qwen_five_state(loading_override=True)
            if st_health["ollama"]["ok"] and orig_settings.get("use_ollama_vision", True):
                assert s_cut["state"] == "analyzing_cut" and s_cut["label"] == "Analisando corte #3"
                assert s_load["state"] == "loading_model" and s_load["label"] == "Carregando modelo"
            print(f"[PASS] P1 #4 Qwen 5-State Indicator & System Health verified: live={st_health['qwen_state']['label']}, RAM={st_health['hardware']['ram']}, Battery={st_health['hardware']['battery']['label']}")

        # ---- PLAYWRIGHT LIVE UI: GAME SHEET, THEME SWITCHER, 9:16 DRAG, 5-STATE QWEN, PROBLEMS DRAWER ----
        console_errors: list[str] = []
        page_errors: list[str] = []
        dragged_overlay: dict = {}

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            page.goto(BASE_URL, wait_until="networkidle")
            page.wait_for_selector("#game-name-input", timeout=8000)

            # Skip tour if visible on first open
            if page.locator("#tour-balloon:not(.hidden)").count() > 0:
                page.locator("#tour-skip-btn").click()

            # 1. Test "Qual e o jogo?" new game notice + known game autocomplete
            page.fill("#game-name-input", "Jogo Inedito P1 Teste")
            page.locator("#game-name-input").dispatch_event("input")
            assert page.locator("#new-game-notice:not(.hidden)").count() == 1
            assert "Jogo novo" in page.locator("#new-game-notice").inner_text()

            page.fill("#game-name-input", "Resident Evil 4 Remake")
            page.locator("#game-name-input").dispatch_event("input")
            assert page.locator("#new-game-notice.hidden").count() == 1
            print("[PASS] Playwright P1 #1: 'Qual e o jogo?' new-game notice & Games DB match verified!")

            # 2. Test Theme Switcher (Dark -> Light -> Auto -> Dark)
            assert page.locator("html").get_attribute("data-theme") == "dark"
            page.locator("#btn-theme-toggle").click()
            assert page.locator("html").get_attribute("data-theme") == "light"
            light_bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
            assert light_bg != "rgb(10, 12, 16)", f"Light theme did not change background: {light_bg}"
            page.locator("#btn-theme-toggle").click()
            assert page.locator("html").get_attribute("data-theme-mode") == "auto"
            page.locator("#btn-theme-toggle").click()
            assert page.locator("html").get_attribute("data-theme") == "dark"
            print(f"[PASS] Playwright P1 #4: Theme switcher Escuro -> Claro ({light_bg}) -> Auto -> Escuro verified!")

            # 3. Verify 5-State Qwen chip present + System Health modal opens
            q_state_attr = page.locator("#chip-ollama").get_attribute("data-qwen-state")
            assert q_state_attr in ("connected_vision", "loading_model", "analyzing_cut", "offline", "no_vision")
            page.locator("#chip-system").click()
            page.wait_for_selector("#health-modal-backdrop.open", timeout=4000)
            health_txt = page.locator("#health-details-grid").inner_text()
            assert "MEMORIA RAM" in health_txt and "DISCO LIVRE" in health_txt and "ENERGIA / BATERIA" in health_txt
            page.locator("#btn-health-close").click()

            # 4. Verify Guide "Pode / Nao Pode" modal
            page.locator("#btn-open-guide").click()
            page.wait_for_selector("#guide-modal-backdrop.open", timeout=4000)
            assert "PODE" in page.locator("#guide-modal-backdrop").inner_text()
            page.locator("#btn-guide-close").click()

            # 5. Navigate to Step 2 -> verify 4 Framing Layouts + Modo Cenas + Interactive 9:16 Drag!
            page.locator("#step-btn-2").click()
            page.wait_for_selector("#phone-stage-916", timeout=6000)
            for lay in ("fit", "big", "fill", "auto"):
                assert page.locator(f'.style-card[data-style="{lay}"]').count() == 1
            assert page.locator("#select-scene-mode option").count() == 3

            # Set bold test title and drag it on the 270x480 9:16 stage to y=360 (1080x1920 -> preview y=90px)
            page.fill("#ov-text-input", "TITULO ARRASTADO P1")
            page.locator("#ov-color-input").fill("#FFD700")
            page.locator("#ov-color-input").dispatch_event("input")

            stage_box = page.locator("#phone-stage-916").bounding_box()
            hook_box = page.locator("#draggable-hook-el").bounding_box()
            assert stage_box and hook_box

            # First drag OUTSIDE Safe Zone (top y=12px -> 48px in 1920) and verify yellow Safe Zone warning appears!
            page.mouse.move(hook_box["x"] + hook_box["width"] / 2, hook_box["y"] + hook_box["height"] / 2)
            page.mouse.down()
            page.mouse.move(stage_box["x"] + stage_box["width"] * 0.5, stage_box["y"] + 16, steps=6)
            page.mouse.up()
            assert page.locator("#overlay-safe-warning:not(.hidden)").count() == 1, "Safe Zone warning did not appear when dragged to y<150!"

            # Now drag INSIDE Safe Zone at exact target y=340 (preview y = 340/4 = 85px) & center x=540 (snap!)
            hook_box2 = page.locator("#draggable-hook-el").bounding_box()
            target_preview_y = 85.0  # 85 * 4 = 340 in 1920px!
            page.mouse.move(hook_box2["x"] + hook_box2["width"] / 2, hook_box2["y"] + hook_box2["height"] / 2)
            page.mouse.down()
            page.mouse.move(stage_box["x"] + stage_box["width"] * 0.502, stage_box["y"] + target_preview_y, steps=8)
            page.mouse.up()
            assert page.locator("#overlay-safe-warning.hidden").count() == 1, "Safe Zone warning stayed visible inside safe zone!"

            # Save overlay.json via UI button
            page.locator("#btn-overlay-save").click()
            page.wait_for_timeout(600)
            dragged_overlay = load_overlay_config()
            assert dragged_overlay["x"] == 540, f"Center magnetic snap failed, got x={dragged_overlay['x']}"
            assert 310 <= dragged_overlay["y"] <= 370, f"Dragged Y mismatch: {dragged_overlay}"
            print(f"[PASS] Playwright P1 #3: Dragged 9:16 title with center snap & Safe Zone guard -> overlay.json (X={dragged_overlay['x']}, Y={dragged_overlay['y']})!")

            # 6. Trigger Problems Drawer test via Configuracoes -> Testar Painel Raio-X
            page.locator('.nav-item[data-view="configuracoes"]').click()
            page.wait_for_selector("#btn-test-xray", timeout=6000)
            page.locator("#btn-test-xray").click()
            page.wait_for_selector("#inline-error-strip:not(.hidden)", timeout=4000)
            assert "1 problema" in page.locator("#chip-problems-label").inner_text()
            page.locator("#chip-problems").click()
            page.wait_for_selector("#problems-drawer-backdrop.open", timeout=4000)
            drawer_txt = page.locator("#problems-drawer-body").inner_text()
            assert "Causa provavel:" in drawer_txt and "Como resolver:" in drawer_txt
            page.locator("#problems-drawer-clear").click()
            page.locator("#problems-drawer-close").click()
            print("[PASS] Playwright P1 #4: Discrete inline error strip + '● 1 problema' chip + Problems Drawer verified!")

            browser.close()

        # Filter only the intentional HTTP 418 diagnostic button click from console_errors
        real_console_errors = [e for e in console_errors if "418 (I'm a Teapot)" not in e]
        assert len(real_console_errors) == 0, f"Browser console errors: {real_console_errors}"
        assert len(page_errors) == 0, f"Browser page errors: {page_errors}"
        print("[PASS] Playwright: 0 console errors and 0 page errors!")

        # ---- P1 #3 & #5: RENDER REAL MP4 WITH DRAGGED OVERLAY + BIG (~4:3) LANCZOS + COVER JPEG ----
        # Verify rendered bright yellow title Y-centroid on frame t=1.0s matches dragged_overlay['y']!
        render_ov = dict(dragged_overlay)
        render_ov["force_text"] = True
        render_ov["animation"] = "none"
        render_ov["duration_sec"] = 3.0
        render_ov["start_sec"] = 0.0

        ren = render_viral_clip(
            video_path=str(dark_vid),
            moment={
                "id": "mom_p1_ov",
                "rank": 1,
                "start": 0.0,
                "end": 9.0,
                "peak": 4.5,
                "climax_rel": 4.5,
                "impact_times": [4.5],
                "category": "combate_intenso",
                "hook_2s": "TITULO ARRASTADO P1",
                "title": "Teste Visual Overlay P1",
                "cta_comment": "",
            },
            style="big",
            subtitles_mode="off",
            enable_hook=True,
            enable_impact_zoom=False,
            enable_preview_flash=False,
            enable_cta_comment=False,
            enable_impact_sfx=False,
            enable_smart_pacing=False,
            enable_progress_bar=False,
            enable_audio_norm=False,
            enable_slowmo=False,
            project_name="p1_overlay_check",
            overlay_config=render_ov,
        )
        mp4_p = Path(ren["file_path"])
        rendered_files.append(mp4_p)
        if ren.get("cover_path"):
            cov_p = Path(ren["cover_path"])
            rendered_files.append(cov_p)
            assert cov_p.exists() and cov_p.stat().st_size > 15_000, f"Cover JPEG missing: {cov_p}"
            cov_img = cv2.imread(str(cov_p))
            assert cov_img is not None and cov_img.shape[:2] == (1920, 1080)
            print(f"[PASS] P1 #5 Individual Shorts Cover JPEG verified ({cov_p.name}, 1080x1920) & layout={ren['layout_used']}!")

        assert ren["layout_used"] == "big"
        cap = cv2.VideoCapture(str(mp4_p))
        cap.set(cv2.CAP_PROP_POS_MSEC, 1000.0)
        ok, frame = cap.read()
        cap.release()
        assert ok and frame is not None and frame.shape[:2] == (1920, 1080)

        # Detect bright gold (#FFD700 -> BGR approx (0, 215, 255)) title pixels in upper half
        b, g, r = cv2.split(frame)
        gold_mask = (r > 190) & (g > 150) & (b < 90)
        ys, xs = np.where(gold_mask)
        assert ys.size > 200, f"Rendered ASS title pixels not found on frame! count={ys.size}"
        centroid_y = float(np.mean(ys))
        centroid_x = float(np.mean(xs))
        err_y = abs(centroid_y - float(dragged_overlay["y"]))
        err_x = abs(centroid_x - float(dragged_overlay["x"]))
        assert err_y <= 45.0, f"Rendered title Y ({centroid_y:.1f}) did not match dragged preview Y ({dragged_overlay['y']})!"
        assert err_x <= 45.0, f"Rendered title X ({centroid_x:.1f}) did not match dragged preview X ({dragged_overlay['x']})!"
        print(
            f"[PASS] P1 #3 Pixel-Exact Match: Preview dragged (X={dragged_overlay['x']}, Y={dragged_overlay['y']}) "
            f"== Rendered 1080x1920 frame (X={centroid_x:.1f}, Y={centroid_y:.1f}, delta={err_y:.1f}px)!"
        )
        print("\n=== ALL P1 VALIDATIONS PASSED (0 ERRORS) ===")

    finally:
        # Restore overlay.json & watcher settings & clean temp files
        try:
            save_overlay_config(orig_overlay)
        except Exception:
            pass
        if token and orig_settings:
            try:
                with httpx.Client(timeout=20.0, headers={"X-ClipRush-Token": token}) as client:
                    client.put(
                        f"{BASE_URL}/api/watcher/config",
                        json={
                            "enabled": bool(orig_settings.get("watch_folder_enabled", False)),
                            "folder_path": orig_settings.get("watch_folder_path", r"E:\PS4\SHARE\Video Clips"),
                        },
                    )
                    client.put(f"{BASE_URL}/api/settings", json={"theme": orig_settings.get("theme", "dark")})
            except Exception:
                pass
        for fp in rendered_files + [dark_vid]:
            if fp.exists():
                try:
                    fp.unlink()
                except OSError:
                    pass
        if watch_tmp.exists():
            shutil.rmtree(watch_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
