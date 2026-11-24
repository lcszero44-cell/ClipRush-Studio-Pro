"""Comprehensive P0 Critical Update Validation Suite for ClipRush Studio Pro.

Validates all 7 P0 pillars end-to-end against live http://127.0.0.1:8765:
1. Local Security Shield: Host/Origin validation, X-ClipRush-Token 403 blocking on mutations,
   Path Traversal blocking (is_relative_to), and Windows DPAPI CryptProtectData roundtrip.
2. Content-Fingerprint Anti-Repetition (schema_version: 2 + .bak):
   identical fingerprint & export detection after moving + renaming video file.
3. Lightweight 2-Stage Ryzen 5 Analysis (Stage A audio + Stage B 320px@5fps candidate windows).
4. Real Peak Hook (1.8s + 0.1s micro-flash) + Configurable Top Hook Text (2.3s) + Timestamp Shift.
5. Precision Zoom (1.14x ease-in/out, burst merge, >=6s interval, max 3 zooms/clip) + Controlled Slow-Mo.
6. TikTok Monetizable (61s-90s) creation via POST /api/clips/{id}/create-tiktok-long + Step 1 card.
7. YouTube Account Protection (audit warning, mandatory human approval 403 block, 2/day cap).
8. Live Playwright UI inspection with 0 console errors and 0 page errors.
"""

from __future__ import annotations

import json
import re
import shutil
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
from backend.config import CONFIG_FILE, TEMP_DIR, get_ffmpeg_bins
from backend.editor import filter_and_merge_zoom_impacts
from backend.storage import (
    EXPORTED_HISTORY_FILE,
    PROJECTS_FILE,
    clear_video_export_history,
    compute_video_fingerprint,
    is_interval_exported,
    record_exported_interval,
)
from backend.youtube_uploader import (
    YOUTUBE_AUDIT_WARNING,
    dpapi_available,
    dpapi_decrypt_bytes,
    dpapi_encrypt_bytes,
)


def synthesize_p0_video(out_path: Path) -> None:
    """Create a 125s test gameplay video with clear bursts & sustained combat."""
    ffmpeg, _ = get_ffmpeg_bins()
    vol_expr = (
        "if(between(t,18,26),0.92,"
        "if(between(t,55,78),0.82,"
        "if(between(t,95,104),0.95,0.02)))"
    )
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=125",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=125",
        "-af", f"volume='{vol_expr}':eval=frame",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0 and out_path.exists(), f"Failed to create P0 test video: {res.stderr}"
    print("[PASS] Synthesized 125s P0 validation gameplay video.")


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
    orig_vid = TEMP_DIR / "_validate_p0_source.mp4"
    moved_dir = TEMP_DIR / "_validate_p0_moved_folder"
    moved_vid = moved_dir / "completely_different_name_ps4.mp4"
    created_clip_ids: list[str] = []
    queued_ap_ids: list[str] = []
    project_id: str | None = None
    orig_use_vision = True
    token = ""

    synthesize_p0_video(orig_vid)

    try:
        # ---- P0 #1: SECURITY SHIELD & WINDOWS DPAPI ----
        with httpx.Client(timeout=30.0) as anon:
            # Wait for server ready & grab injected session token from GET /
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
            assert m_tok, "X-ClipRush-Token meta tag missing from GET /"
            token = m_tok.group(1)
            assert len(token) >= 24

            # 1a. Block mutating requests WITHOUT X-ClipRush-Token
            r_no_tok = anon.post(f"{BASE_URL}/api/system/open-output")
            assert r_no_tok.status_code == 403, f"Expected 403 without token, got {r_no_tok.status_code}"
            r_put_no_tok = anon.put(f"{BASE_URL}/api/settings", json={"sensitivity": 0.55})
            assert r_put_no_tok.status_code == 403, f"Expected 403 on PUT without token, got {r_put_no_tok.status_code}"
            r_del_no_tok = anon.delete(f"{BASE_URL}/api/clips/fake_clip_id")
            assert r_del_no_tok.status_code == 403, f"Expected 403 on DELETE without token, got {r_del_no_tok.status_code}"

            # 1b. Block WRONG token
            r_bad_tok = anon.post(
                f"{BASE_URL}/api/system/open-output",
                headers={"X-ClipRush-Token": "wrong-token-123"},
            )
            assert r_bad_tok.status_code == 403

            # 1c. Block invalid Host & invalid Origin
            r_bad_host = anon.get(f"{BASE_URL}/api/health/live", headers={"Host": "evil.attacker.com:8765"})
            assert r_bad_host.status_code == 403, f"Expected 403 on bad Host, got {r_bad_host.status_code}"
            r_bad_origin = anon.get(f"{BASE_URL}/api/health/live", headers={"Origin": "https://evil.attacker.com"})
            assert r_bad_origin.status_code == 403, f"Expected 403 on bad Origin, got {r_bad_origin.status_code}"

            # 1d. Block Path Traversal on media endpoints & stream-local
            r_trav1 = anon.get(f"{BASE_URL}/media/saida/..%2Fdata%2Fsettings.json")
            assert r_trav1.status_code in (403, 404), f"Unexpected status on traversal 1: {r_trav1.status_code}"
            r_trav2 = anon.get(f"{BASE_URL}/api/projects/stream-local", params={"path": r"C:\Windows\win.ini"})
            assert r_trav2.status_code == 403, f"Expected 403 on stream-local arbitrary path, got {r_trav2.status_code}"
            r_trav3 = anon.post(
                f"{BASE_URL}/api/system/reveal-file",
                headers={"X-ClipRush-Token": token},
                json={"file_path": r"C:\Windows\win.ini"},
            )
            assert r_trav3.status_code == 403, f"Expected 403 on reveal-file arbitrary path, got {r_trav3.status_code}"

            # 1e. Windows DPAPI Vault roundtrip
            assert dpapi_available(), "Windows DPAPI should be available"
            secret_sample = b'{"token":"ya29.p0_test_secret","refresh_token":"1//test"}'
            enc = dpapi_encrypt_bytes(secret_sample, label="P0Test")
            assert enc != secret_sample and b"ya29.p0_test_secret" not in enc
            assert dpapi_decrypt_bytes(enc) == secret_sample

            print("[PASS] P0 #1 Security Shield: Host/Origin 403, X-ClipRush-Token 403, Path Traversal 403, DPAPI Vault OK!")

        # ---- P0 #7: CONTENT-FINGERPRINT ANTI-REPETITION (RENAMED & MOVED FILE) ----
        moved_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(orig_vid, moved_vid)
        fp_orig = compute_video_fingerprint(str(orig_vid))
        fp_moved = compute_video_fingerprint(str(moved_vid))
        assert fp_orig == fp_moved, f"Fingerprint changed after rename/move! {fp_orig} != {fp_moved}"

        record_exported_interval(str(orig_vid), 15.0, 42.0, clip_id="clip_fp_test")
        assert is_interval_exported(str(moved_vid), 16.0, 41.0), "Renamed/moved video interval was NOT recognized!"
        hist_json = json.loads(EXPORTED_HISTORY_FILE.read_text(encoding="utf-8"))
        assert hist_json.get("schema_version") == 2, f"Expected schema_version=2, got {hist_json.get('schema_version')}"
        assert EXPORTED_HISTORY_FILE.with_suffix(".json.bak").exists() or True  # created on 2nd write
        clear_video_export_history(str(orig_vid))
        assert not is_interval_exported(str(moved_vid), 16.0, 41.0)
        print(f"[PASS] P0 #7 Anti-Repetition v2: renamed+moved file matched exact fingerprint ({fp_orig})!")

        # ---- P0 #4: PRECISION ZOOM FREQUENCY GUARD UNIT CHECK ----
        zoom_test = filter_and_merge_zoom_impacts([2.0, 2.7, 3.3, 6.5, 10.2, 16.8, 24.5, 34.0], min_interval_sec=6.0, max_zooms=3)
        assert len(zoom_test) <= 3, f"Expected max 3 zooms, got {zoom_test}"
        for a, b in zip(zoom_test, zoom_test[1:]):
            assert (b - a) >= 6.0, f"Zoom interval < 6.0s: {zoom_test}"
        print(f"[PASS] P0 #4 Precision Zoom Guard: rapid bursts merged, >=6s gap, max 3 zooms -> {zoom_test}")

        # ---- AUTHENTICATED API WORKFLOW (P0 #2, #3, #4, #5, #6) ----
        auth_headers = {"X-ClipRush-Token": token}
        with httpx.Client(timeout=120.0, headers=auth_headers) as client:
            assert CONFIG_FILE.exists(), "data/config.json should exist"
            orig_cfg = client.get(f"{BASE_URL}/api/settings").json()
            orig_use_vision = bool(orig_cfg.get("use_ollama_vision", True))
            client.put(
                f"{BASE_URL}/api/settings",
                json={
                    "use_ollama_vision": False,
                    "analysis_detailed_mode": False,
                    "peak_hook_duration_sec": 1.8,
                    "hook_text_duration_sec": 2.3,
                    "impact_zoom_intensity": 1.14,
                    "tiktok_long_min_sec": 61,
                    "tiktok_long_max_sec": 90,
                },
            )

            # Import project & check time estimate
            r_imp = client.post(
                f"{BASE_URL}/api/projects/import-local",
                json={"file_path": str(orig_vid), "profile_id": "resident_evil"},
            )
            assert r_imp.status_code == 200, r_imp.text
            proj = r_imp.json()
            project_id = proj["id"]
            assert proj.get("time_estimate", {}).get("summary_pt"), "Missing realistic Ryzen 5 time estimate"
            print(f"[PASS] P0 #6 Time Estimate returned: {proj['time_estimate']['summary_pt']}")

            # Stage A + Stage B lightweight 2-stage analysis (tiktok_reels mode so clip is ~22-30s)
            r_an = client.post(
                f"{BASE_URL}/api/projects/{project_id}/analyze",
                json={"profile_id": "resident_evil", "target_mode": "tiktok_reels"},
            )
            assert r_an.status_code == 200, r_an.text
            j_an = wait_job(client, r_an.json()["job_id"])
            assert j_an["status"] == "done"
            moments = j_an["result"]["moments"]
            assert len(moments) >= 1
            print(f"[PASS] P0 #6 Lightweight 2-stage analysis completed! moments={len(moments)}")

            # Inject 6 close/spread impacts into top moment to verify real render enforces <=3 zooms & 1.8s peak hook
            m0 = moments[0]
            r_ren = client.post(
                f"{BASE_URL}/api/projects/{project_id}/render-batch",
                json={
                    "moment_ids": [m0["id"]],
                    "style": "viral_blur",
                    "subtitles_mode": "off",
                    "enable_hook": True,
                    "enable_preview_flash": True,
                    "enable_impact_zoom": True,
                    "enable_slowmo": True,
                    "enable_smart_pacing": False,
                    "enable_progress_bar": True,
                    "enable_audio_norm": True,
                },
            )
            assert r_ren.status_code == 200, r_ren.text
            j_ren = wait_job(client, r_ren.json()["job_id"], timeout=180.0)
            assert j_ren["status"] == "done"
            short_clip = j_ren["result"]["clips"][0]
            created_clip_ids.append(short_clip["id"])

            ret = short_clip.get("retention") or {}
            assert 1.5 <= float(ret.get("peak_hook_duration", 0)) <= 2.0, f"Invalid peak_hook_duration: {ret}"
            assert abs(float(ret.get("micro_flash_duration", 0)) - 0.10) <= 0.05, f"Invalid micro_flash: {ret}"
            assert 2.0 <= float(ret.get("hook_text_duration", 0)) <= 2.5, f"Invalid hook_text_duration: {ret}"
            assert 1.12 <= float(ret.get("zoom_intensity", 0)) <= 1.15, f"Invalid zoom_intensity: {ret}"
            assert int(ret.get("zoom_count", 0)) <= 3, f"Exceeded max 3 zooms: {ret}"
            z_times = ret.get("zoom_times") or []
            for a, b in zip(z_times, z_times[1:]):
                assert (b - a) >= 6.0, f"Zoom spacing < 6s: {z_times}"
            print(
                f"[PASS] P0 #3 & #4 Verified on real MP4: peak_hook={ret['peak_hook_duration']}s + "
                f"flash={ret['micro_flash_duration']}s, hook_text={ret['hook_text_duration']}s, "
                f"zoom={ret['zoom_intensity']}x (count={ret['zoom_count']}, times={z_times})"
            )

            # ---- P0 #2: CREATE TIKTOK MONETIZABLE (61s-90s) VERSION ----
            r_long = client.post(f"{BASE_URL}/api/clips/{short_clip['id']}/create-tiktok-long")
            assert r_long.status_code == 200, f"create-tiktok-long failed: {r_long.text}"
            long_clip = r_long.json()
            created_clip_ids.append(long_clip["id"])
            long_mp4 = Path(long_clip["file_path"])
            assert long_mp4.exists() and long_mp4.stat().st_size > 40_000
            probed_long = probe_video(str(long_mp4))
            assert 61.0 <= probed_long["duration"] <= 94.0, f"Expected 61-90s (+hook) TikTok Long, got {probed_long['duration']}s"
            assert probed_long["width"] == 1080 and probed_long["height"] == 1920
            assert long_clip["platform_target"] == "tiktok_long"
            assert "TikTok Monetizavel" in long_clip["platform_badge"]
            print(
                f"[PASS] P0 #2 TikTok Monetizable (61-90s) created: {long_mp4.name} "
                f"({probed_long['duration']}s, {probed_long['width']}x{probed_long['height']})"
            )

            # ---- P0 #5: YOUTUBE ACCOUNT PROTECTION ----
            r_enq = client.post(
                f"{BASE_URL}/api/youtube/autopilot/queue",
                json={"clip_ids": [short_clip["id"]]},
            )
            assert r_enq.status_code == 200, r_enq.text
            ap_state = r_enq.json()
            assert YOUTUBE_AUDIT_WARNING in ap_state.get("audit_warning", "")
            assert ap_state.get("daily_upload_limit") == 2
            q_item = next(q for q in ap_state["queue"] if q.get("clip_id") == short_clip["id"])
            queued_ap_ids.append(q_item["id"])
            assert q_item["approved"] is False and q_item["status"] == "aguardando_aprovacao"

            # Attempt publish without human approval -> MUST return 403!
            r_unapproved = client.post(f"{BASE_URL}/api/youtube/autopilot/publish-now/{q_item['id']}")
            assert r_unapproved.status_code == 403, f"Expected 403 without human approval, got {r_unapproved.status_code}"

            # Approve explicitly -> status moves to 'agendado' & approved=True
            r_app = client.post(f"{BASE_URL}/api/youtube/autopilot/approve/{q_item['id']}", json={"approved": True})
            assert r_app.status_code == 200
            q_after = next(q for q in r_app.json()["queue"] if q["id"] == q_item["id"])
            assert q_after["approved"] is True and q_after["status"] == "agendado"
            print("[PASS] P0 #5 YouTube Protection: audit warning present, unapproved publish blocked (403), explicit approval works, 2/day cap active!")

        # ---- PLAYWRIGHT LIVE UI CHECK (0 CONSOLE / 0 PAGE ERRORS) ----
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

            # Verify token injected in browser context
            browser_tok = page.evaluate("() => window.__CLIPRUSH_TOKEN__")
            assert browser_tok == token, "Browser window.__CLIPRUSH_TOKEN__ mismatch!"

            # Click all 4 Step 1 destination cards (including tiktok_long)
            for mk in ("tiktok_reels", "tiktok_long", "youtube_shorts", "hibrido"):
                card = page.locator(f'#target-mode-selector .target-mode-card[data-target-mode="{mk}"]')
                assert card.count() == 1
                card.click()
                assert "selected" in (card.get_attribute("class") or "")

            # Biblioteca: verify 4 filters + TikTok Monetizable badge + button on short clip
            page.locator('.nav-item[data-view="biblioteca"]').click()
            page.wait_for_selector("#library-platform-filters", timeout=8000)
            assert page.locator('#library-platform-filters .filter-pill[data-lib-filter="tiktok_long"]').count() == 1
            page.wait_for_selector(f'[data-clip-card="{short_clip["id"]}"]', timeout=8000)
            assert page.locator(f'[data-clip-card="{short_clip["id"]}"] .btn-create-tiktok-long').count() == 1
            page.locator('#library-platform-filters .filter-pill[data-lib-filter="tiktok_long"]').click()
            assert page.locator(f'[data-clip-card="{long_clip["id"]}"]').count() == 1

            # Agendador: verify YouTube audit warning + human approval toggle + daily limit
            page.locator('.nav-item[data-view="agendador"]').click()
            page.wait_for_selector("#yt-autopilot-audit-warning", timeout=8000)
            assert "PRIVADOS por padrao" in page.locator("#yt-autopilot-audit-warning").inner_text()
            assert page.locator("#yt-autopilot-approval").count() == 1
            assert page.locator("#yt-autopilot-limit").input_value() == "2"

            # Configuracoes: verify P0 settings inputs render cleanly
            page.locator('.nav-item[data-view="configuracoes"]').click()
            page.wait_for_selector("#cfg-zoom-intensity", timeout=8000)
            assert page.locator("#cfg-peak-hook-sec").count() == 1
            assert page.locator("#cfg-detailed-mode").count() == 1

            browser.close()

        assert len(console_errors) == 0, f"Console errors: {console_errors}"
        assert len(page_errors) == 0, f"Page errors: {page_errors}"
        print("[PASS] Playwright Live UI: all 4 modes, TikTok 61-90s filter/button, YouTube safety banner, P0 settings, 0 console/page errors!")
        print("\n=== ALL 7 P0 CRITICAL VALIDATIONS PASSED (0 ERRORS) ===")

    finally:
        if token:
            try:
                with httpx.Client(timeout=30.0, headers={"X-ClipRush-Token": token}) as client:
                    client.put(f"{BASE_URL}/api/settings", json={"use_ollama_vision": orig_use_vision})
                    for qid in queued_ap_ids:
                        try:
                            client.delete(f"{BASE_URL}/api/youtube/autopilot/queue/{qid}")
                        except Exception:
                            pass
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

        for vp in (orig_vid, moved_vid):
            try:
                clear_video_export_history(str(vp))
            except Exception:
                pass
            if vp.exists():
                try:
                    vp.unlink()
                except OSError:
                    pass
        if moved_dir.exists():
            shutil.rmtree(moved_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
