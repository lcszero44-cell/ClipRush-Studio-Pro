"""End-to-End & Playwright Validation for the Multi-Platform Smart Destination System.

Validates:
1. Step 1 Smart Platform Destination selector (3 cards: Modo Hibrido IA, Foco TikTok & Reels, Foco YouTube Shorts).
2. Analyzer duration & badge calibration across all 3 target modes:
   - tiktok_reels -> 20s-32s, "Ideal TikTok / Reels", TikTok hashtags (#fyp, #gaming, #ps4, #gameplay, #viral)
   - youtube_shorts -> 36s-55s, "Ideal YouTube Shorts", YouTube Shorts hashtags (#Shorts)
   - hibrido -> auto-classifies explosive burst (20s-32s) vs sustained fight/dialogue (36s-55s)
3. Library badges, 3 quick platform filters ("Todos" | "Ideal TikTok / Reels" | "Ideal YouTube Shorts").
4. Live Playwright click on "Criar Versao Curta TikTok (25s)" on a >35s YouTube Shorts clip,
   verifying a brand-new ~25s MP4 is generated (preserving 3.5s hook + 21.5s climax),
   appears in Library with green badge "Ideal TikTok / Reels (Versao 25s)", and streams with HTTP 206.
5. Zero browser console errors and zero uncaught page errors.
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

from backend.analyzer import probe_video
from backend.config import TEMP_DIR, get_ffmpeg_bins
from backend.storage import PROJECTS_FILE, clear_video_export_history


def create_multiplatform_test_video(out_path: Path) -> None:
    """Create a 190s synthetic gameplay video with:
    - t=18..27s: short explosive burst (9s) -> ideal TikTok / Reels (20-32s) in Hybrid mode
    - t=75..124s: long sustained 49s boss fight / cutscene -> ideal YouTube Shorts (36-55s) in Hybrid mode
    - t=155..164s: short climax burst (9s) -> ideal TikTok / Reels
    """
    ffmpeg, _ = get_ffmpeg_bins()
    vol_expr = (
        "if(between(t,18,27),0.92,"
        "if(between(t,75,124),0.72,"
        "if(between(t,155,164),0.95,0.01)))"
    )
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=190",
        "-f", "lavfi", "-i", "sine=frequency=850:sample_rate=48000:duration=190",
        "-af", f"volume='{vol_expr}':eval=frame",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0 and out_path.exists(), f"Failed to synthesize video: {res.stderr}"
    print("[PASS] Synthesized 190s test gameplay (short burst + 49s sustained scene + climax burst).")


def wait_job(client: httpx.Client, job_id: str, timeout: float = 150.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"{BASE_URL}/api/jobs/{job_id}")
        assert r.status_code == 200
        job = r.json()
        if job["status"] in ("done", "cancelled"):
            return job
        if job["status"] == "error":
            raise AssertionError(f"Job failed: {job.get('xray_error') or job.get('message')}")
        time.sleep(0.4)
    raise AssertionError(f"Job {job_id} timed out")


def main() -> None:
    sample_vid = TEMP_DIR / "_validate_multiplatform.mp4"
    created_clip_ids: list[str] = []
    project_id: str | None = None
    orig_use_vision = True

    create_multiplatform_test_video(sample_vid)

    try:
        with httpx.Client(timeout=90.0) as client:
            orig_cfg = client.get(f"{BASE_URL}/api/settings").json()
            orig_use_vision = bool(orig_cfg.get("use_ollama_vision", True))
            # Disable vision LLM for fast deterministic synthetic video check
            client.put(f"{BASE_URL}/api/settings", json={"use_ollama_vision": False})

            # Import project locally
            r_imp = client.post(
                f"{BASE_URL}/api/projects/import-local",
                json={"file_path": str(sample_vid), "profile_id": "resident_evil"},
            )
            assert r_imp.status_code == 200, r_imp.text
            project_id = r_imp.json()["id"]
            print(f"[PASS] Imported test project id={project_id}")

            # 1. Validate Foco TikTok & Reels (20s-32s)
            r_tk = client.post(
                f"{BASE_URL}/api/projects/{project_id}/analyze",
                json={"profile_id": "resident_evil", "target_mode": "tiktok_reels"},
            )
            assert r_tk.status_code == 200, r_tk.text
            j_tk = wait_job(client, r_tk.json()["job_id"])
            m_tk = j_tk["result"]["moments"]
            assert len(m_tk) >= 2, f"Expected >=2 moments in tiktok_reels mode, got {len(m_tk)}"
            for m in m_tk:
                assert 19.5 <= m["duration"] <= 32.5, f"TikTok duration out of 20-32s: {m['duration']}"
                assert m["platform_target"] == "tiktok_reels", f"Wrong platform_target: {m}"
                assert m["platform_badge"] == "Ideal TikTok / Reels", f"Wrong platform_badge: {m}"
                tags_l = [t.lower() for t in m.get("hashtags", [])]
                assert "#fyp" in tags_l and "#viral" in tags_l and "#ps4" in tags_l, f"Missing TikTok tags: {tags_l}"
            print(f"[PASS] target_mode=tiktok_reels verified! count={len(m_tk)}, durations={[m['duration'] for m in m_tk]}")

            # 2. Validate Foco YouTube Shorts (36s-55s)
            r_yt = client.post(
                f"{BASE_URL}/api/projects/{project_id}/analyze",
                json={"profile_id": "resident_evil", "target_mode": "youtube_shorts"},
            )
            assert r_yt.status_code == 200, r_yt.text
            j_yt = wait_job(client, r_yt.json()["job_id"])
            m_yt = j_yt["result"]["moments"]
            assert len(m_yt) >= 2, f"Expected >=2 moments in youtube_shorts mode, got {len(m_yt)}"
            for m in m_yt:
                assert 35.5 <= m["duration"] <= 55.5, f"YouTube Shorts duration out of 36-55s: {m['duration']}"
                assert m["platform_target"] == "youtube_shorts", f"Wrong platform_target: {m}"
                assert m["platform_badge"] == "Ideal YouTube Shorts", f"Wrong platform_badge: {m}"
                tags_l = [t.lower() for t in m.get("hashtags", [])]
                assert "#shorts" in tags_l, f"Missing #Shorts tag: {tags_l}"
            print(f"[PASS] target_mode=youtube_shorts verified! count={len(m_yt)}, durations={[m['duration'] for m in m_yt]}")

            # 3. Validate Modo Hibrido IA (mixed short 20-32s & long 36-55s)
            r_hyb = client.post(
                f"{BASE_URL}/api/projects/{project_id}/analyze",
                json={"profile_id": "resident_evil", "target_mode": "hibrido"},
            )
            assert r_hyb.status_code == 200, r_hyb.text
            j_hyb = wait_job(client, r_hyb.json()["job_id"])
            m_hyb = j_hyb["result"]["moments"]
            hyb_tk = [m for m in m_hyb if m["platform_target"] == "tiktok_reels"]
            hyb_yt = [m for m in m_hyb if m["platform_target"] == "youtube_shorts"]
            assert len(hyb_tk) >= 1 and len(hyb_yt) >= 1, (
                f"Hybrid mode should classify both short & long scenes! "
                f"Got {[(m['duration'], m['platform_target'], m['platform_badge']) for m in m_hyb]}"
            )
            for m in hyb_tk:
                assert 19.5 <= m["duration"] <= 32.5, f"Hybrid TikTok clip outside 20-32s: {m['duration']}"
            for m in hyb_yt:
                assert 35.5 <= m["duration"] <= 55.5, f"Hybrid YouTube clip outside 36-55s: {m['duration']}"
            print(
                f"[PASS] target_mode=hibrido verified! "
                f"TikTok/Reels={[m['duration'] for m in hyb_tk]}, YouTube Shorts={[m['duration'] for m in hyb_yt]}"
            )

            # 4. Render 1 long YouTube Shorts clip (>35s) + 1 short TikTok clip (<=32s) into Library
            chosen_yt = max(hyb_yt, key=lambda x: x["duration"])
            chosen_tk = min(hyb_tk, key=lambda x: x["duration"])
            r_ren = client.post(
                f"{BASE_URL}/api/projects/{project_id}/render-batch",
                json={
                    "moment_ids": [chosen_yt["id"], chosen_tk["id"]],
                    "style": "viral_blur",
                    "subtitles_mode": "off",
                    "enable_hook": True,
                    "enable_impact_zoom": False,
                    "enable_slowmo": False,
                    "enable_smart_pacing": False,
                    "enable_progress_bar": True,
                    "enable_audio_norm": True,
                },
            )
            assert r_ren.status_code == 200, r_ren.text
            j_ren = wait_job(client, r_ren.json()["job_id"], timeout=180.0)
            assert j_ren["status"] == "done"
            rendered = j_ren["result"]["clips"]
            assert len(rendered) == 2
            for c in rendered:
                created_clip_ids.append(c["id"])

            yt_clip = next(c for c in rendered if c["platform_target"] == "youtube_shorts")
            tk_clip = next(c for c in rendered if c["platform_target"] == "tiktok_reels")
            assert yt_clip["duration"] > 35.0, f"Expected YT clip > 35s, got {yt_clip['duration']}"
            assert "#shorts" in [t.lower() for t in yt_clip.get("hashtags", [])]
            assert "#fyp" in [t.lower() for t in tk_clip.get("hashtags", [])]
            print(
                f"[PASS] Rendered 2 real MP4s: YT Shorts ({yt_clip['duration']}s, id={yt_clip['id']}) "
                f"& TikTok ({tk_clip['duration']}s, id={tk_clip['id']})"
            )

        # 5. Live Playwright End-to-End UI Validation
        console_errors: list[str] = []
        page_errors: list[str] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            page.goto(BASE_URL, wait_until="networkidle")
            page.wait_for_selector("#target-mode-selector", timeout=8000)

            # 5a. Test selecting all 3 destination modes in Step 1
            expected_badges = [
                ("tiktok_reels", "Foco TikTok & Reels (20s-32s)"),
                ("youtube_shorts", "Foco YouTube Shorts (36s-55s)"),
                ("hibrido", "Hibrido IA Ativo"),
            ]
            for mode_key, badge_text in expected_badges:
                card = page.locator(f'#target-mode-selector .target-mode-card[data-target-mode="{mode_key}"]')
                assert card.count() == 1, f"Missing target mode card {mode_key}"
                card.click()
                assert "selected" in (card.get_attribute("class") or ""), f"Card {mode_key} not marked selected"
                badge_now = page.locator("#active-target-mode-badge").inner_text().strip()
                assert badge_now == badge_text, f"Expected badge '{badge_text}', got '{badge_now}'"
            print("[PASS] Playwright: all 3 Step 1 Smart Destination cards select cleanly and update status badge.")

            # 5b. Open Biblioteca de Clipes & verify badges + 3 filter pills
            page.locator('.nav-item[data-view="biblioteca"]').click()
            page.wait_for_selector("#library-platform-filters", timeout=8000)
            page.wait_for_selector(f'[data-clip-card="{yt_clip["id"]}"]', timeout=8000)

            filter_all = page.locator('#library-platform-filters .filter-pill[data-lib-filter="all"]')
            filter_tk = page.locator('#library-platform-filters .filter-pill[data-lib-filter="tiktok_reels"]')
            filter_yt = page.locator('#library-platform-filters .filter-pill[data-lib-filter="youtube_shorts"]')
            assert filter_all.count() == 1 and filter_tk.count() == 1 and filter_yt.count() == 1

            yt_card = page.locator(f'[data-clip-card="{yt_clip["id"]}"]')
            tk_card = page.locator(f'[data-clip-card="{tk_clip["id"]}"]')
            assert "Ideal YouTube Shorts" in yt_card.locator(".lib-platform-badge").inner_text()
            assert "Ideal TikTok / Reels" in tk_card.locator(".lib-platform-badge").inner_text()

            # Button "Criar Versao Curta TikTok (25s)" MUST exist on >35s YT clip, and NOT on short TikTok clip
            btn_25s = yt_card.locator(".btn-create-tiktok-25s")
            assert btn_25s.count() == 1, "Missing 'Criar Versao Curta TikTok (25s)' button on >35s clip"
            assert tk_card.locator(".btn-create-tiktok-25s").count() == 0, "Short <=32s clip should not show 25s button"

            # Verify Library filter "Ideal YouTube Shorts" hides TikTok card and shows YouTube card
            filter_yt.click()
            assert page.locator(f'[data-clip-card="{yt_clip["id"]}"]').count() == 1
            assert page.locator(f'[data-clip-card="{tk_clip["id"]}"]').count() == 0

            # 5c. Click "Criar Versao Curta TikTok (25s)" live in Playwright!
            filter_all.click()
            page.wait_for_selector(f'[data-clip-card="{yt_clip["id"]}"] .btn-create-tiktok-25s', timeout=8000)
            page.locator(f'[data-clip-card="{yt_clip["id"]}"] .btn-create-tiktok-25s').click()

            # Wait for new clip card with green badge "Ideal TikTok / Reels (Versao 25s)" to appear!
            badge_25s_loc = page.locator('.lib-platform-badge', has_text="Ideal TikTok / Reels (Versao 25s)")
            badge_25s_loc.first.wait_for(state="visible", timeout=45000)
            assert badge_25s_loc.count() >= 1
            print("[PASS] Playwright: clicked 'Criar Versao Curta TikTok (25s)' and saw new 25s card appear live!")

            # Verify Library filter "Ideal TikTok / Reels" shows the new 25s variant + original TikTok clip, hides YT clip
            filter_tk.click()
            assert page.locator(f'[data-clip-card="{yt_clip["id"]}"]').count() == 0
            assert page.locator(f'[data-clip-card="{tk_clip["id"]}"]').count() == 1
            assert page.locator('.lib-platform-badge', has_text="Ideal TikTok / Reels (Versao 25s)").count() >= 1
            filter_all.click()
            assert page.locator(f'[data-clip-card="{yt_clip["id"]}"]').count() == 1
            print("[PASS] Playwright: all 3 Library platform filters ('Todos', 'Ideal TikTok / Reels', 'Ideal YouTube Shorts') verified!")

            browser.close()

        assert len(console_errors) == 0, f"Browser console errors detected: {console_errors}"
        assert len(page_errors) == 0, f"Browser page errors detected: {page_errors}"
        print("[PASS] Playwright: 0 console errors and 0 page errors!")

        # 6. Verify the newly generated 25s MP4 file on disk & HTTP 206 streaming
        with httpx.Client(timeout=30.0) as client:
            all_clips = client.get(f"{BASE_URL}/api/clips").json()["clips"]
            variant_25s = next((c for c in all_clips if c.get("parent_clip_id") == yt_clip["id"]), None)
            assert variant_25s is not None, "Generated 25s variant clip not found in /api/clips!"
            created_clip_ids.append(variant_25s["id"])

            mp4_path = Path(variant_25s["file_path"])
            assert mp4_path.exists() and mp4_path.stat().st_size > 20_000, f"25s MP4 missing or too small: {mp4_path}"
            probed = probe_video(mp4_path)
            assert 23.0 <= probed["duration"] <= 27.5, f"Expected ~25s MP4 duration, got {probed['duration']}s"
            assert probed["width"] == 1080 and probed["height"] == 1920, f"Expected 1080x1920, got {probed['width']}x{probed['height']}"
            assert variant_25s["platform_target"] == "tiktok_reels"
            assert variant_25s["platform_badge"] == "Ideal TikTok / Reels (Versao 25s)"
            v_tags = [t.lower() for t in variant_25s.get("hashtags", [])]
            assert "#fyp" in v_tags and "#viral" in v_tags and "#gaming" in v_tags, f"25s variant missing TikTok tags: {v_tags}"

            rng = client.get(f"{BASE_URL}{variant_25s['media_url']}", headers={"Range": "bytes=0-65535"})
            assert rng.status_code == 206, f"Expected HTTP 206 Partial Content for 25s MP4, got {rng.status_code}"
            print(
                f"[PASS] Verified real 25s MP4 on disk: {mp4_path.name} "
                f"({probed['duration']}s, {probed['width']}x{probed['height']}, HTTP 206 OK, tags={v_tags[:5]})"
            )

        print("\n=== ALL MULTI-PLATFORM VALIDATIONS PASSED (0 ERRORS) ===")

    finally:
        # Always restore settings and clean up temporary test artifacts cleanly
        try:
            with httpx.Client(timeout=30.0) as client:
                client.put(f"{BASE_URL}/api/settings", json={"use_ollama_vision": orig_use_vision})
                for cid in set(created_clip_ids):
                    try:
                        client.delete(f"{BASE_URL}/api/clips/{cid}?delete_file=true")
                    except Exception:
                        pass
        except Exception:
            pass

        if project_id and PROJECTS_FILE.exists():
            try:
                projects = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
                projects = [pr for pr in projects if pr.get("id") != project_id]
                PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        try:
            clear_video_export_history(str(sample_vid))
        except Exception:
            pass

        if sample_vid.exists():
            try:
                sample_vid.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()
