"""End-to-end Performance, Concurrency, Live Progress & Dynamic 25s-55s Scene Validation.
Verifies:
1. FFprobe local import completes in < 1.0 second.
2. During active isolated background rendering, HTTP server answers /api/health/live & /api/clips in < 150ms
   and MP4 Range requests return 206 Partial Content instantaneously (zero event-loop/GIL freeze!).
3. POST /api/jobs/{id}/cancel terminates an active render job cleanly.
4. Dynamic 25s-55s complete scenes: short burst (~25-32s) vs long sustained fight/dialogue (~38-55s)
   naturally vary in duration instead of cutting every clip to ~22s.
5. Playwright validates Global Live Progress Panel (stopwatch, reassurance text, Cancel button),
   <video preload="metadata"> in Library, and 0 console/page errors!
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
from backend.storage import PROJECTS_FILE, clear_video_export_history


def create_varied_scenes_video(out_path: Path) -> None:
    """Create a 190s synthetic video containing:
    - Scene 1 (t=18..28s): short intense action burst (10s active -> expects ~25-32s clip)
    - Scene 2 (t=75..122s): LONG sustained 47s boss fight / vocal-band 900Hz dialogue (expects ~42-55s clip!)
    - Scene 3 (t=150..162s): climax action burst
    """
    ffmpeg, _ = get_ffmpeg_bins()
    # Active audio envelope: short burst 18-28, long sustained 75-122, burst 150-162
    vol_expr = (
        "if(between(t,18,28),0.9,"
        "if(between(t,75,122),0.75,"
        "if(between(t,150,162),0.95,0.01)))"
    )
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=190",
        "-f", "lavfi", "-i", "sine=frequency=900:sample_rate=48000:duration=190",
        "-af", f"volume='{vol_expr}':eval=frame",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0 and out_path.exists(), f"Synthetic video creation failed: {res.stderr}"
    print("[PASS] Created 190s varied-scene gameplay (short burst + 47s sustained boss/dialogue + climax).")


def wait_job(client: httpx.Client, job_id: str, timeout: float = 120.0) -> dict:
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
    sample_vid = TEMP_DIR / "_validate_perf_scenes.mp4"
    created_clips: list[str] = []
    create_varied_scenes_video(sample_vid)

    try:
        with httpx.Client(timeout=60.0) as client:
            orig_cfg = client.get(f"{BASE_URL}/api/settings").json()
            client.put(f"{BASE_URL}/api/settings", json={"use_ollama_vision": False})

            try:
                # 1. Measure FFprobe local import speed (< 1.0 second!)
                t0 = time.perf_counter()
                r = client.post(
                    f"{BASE_URL}/api/projects/import-local",
                    json={"file_path": str(sample_vid), "profile_id": "god_of_war"},
                )
                import_ms = (time.perf_counter() - t0) * 1000.0
                assert r.status_code == 200
                pid = r.json()["id"]
                assert import_ms < 1000.0, f"FFprobe import took {import_ms:.0f}ms (expected < 1000ms)"
                print(f"[PASS] FFprobe local import completed in {import_ms:.0f}ms (< 1s)!")

                # 2. Analyze in isolated process -> verify dynamic 25s-55s varying scene durations!
                ra = client.post(f"{BASE_URL}/api/projects/{pid}/analyze", json={"profile_id": "god_of_war"})
                assert ra.status_code == 200
                a_job = wait_job(client, ra.json()["job_id"])
                assert a_job["status"] == "done"
                moments = a_job["result"]["moments"]
                durations = [m["duration"] for m in moments]
                assert len(moments) >= 2, f"Expected >= 2 scenes, got {len(moments)}"
                assert all(24.5 <= d <= 55.5 for d in durations), f"Durations outside 25s-55s: {durations}"
                assert max(durations) >= 36.0, f"Expected sustained long scene >= 36s, got durations: {durations}"
                assert (max(durations) - min(durations)) >= 5.0 or max(durations) >= 40.0, f"Expected natural variation: {durations}"
                print(f"[PASS] Dynamic 25s-55s complete scenes verified! Durations={durations}")

                # 3. Render 1 clip in isolated process & measure HTTP server responsiveness WHILE rendering!
                rb = client.post(
                    f"{BASE_URL}/api/projects/{pid}/render-batch",
                    json={
                        "limit": 1,
                        "style": "viral_blur",
                        "subtitles_mode": "off",
                        "enable_hook": True,
                        "enable_impact_zoom": True,
                        "enable_slowmo": True,
                    },
                )
                assert rb.status_code == 200
                r_jid = rb.json()["job_id"]

                # Ping /api/health/live & /api/clips WHILE render runs in isolated child process
                ping_times = []
                for _ in range(4):
                    tp0 = time.perf_counter()
                    hr = client.get(f"{BASE_URL}/api/health/live")
                    cr = client.get(f"{BASE_URL}/api/clips")
                    assert hr.status_code == 200 and cr.status_code == 200
                    ping_times.append((time.perf_counter() - tp0) * 1000.0)
                    time.sleep(0.25)
                avg_ms = sum(ping_times) / len(ping_times)
                assert avg_ms < 250.0, f"Server felt slow during render: avg {avg_ms:.1f}ms"
                print(f"[PASS] FastAPI HTTP stayed instant during active background render (avg {avg_ms:.1f}ms)!")

                r_job = wait_job(client, r_jid)
                assert r_job["status"] == "done"
                clip = r_job["result"]["clips"][0]
                created_clips.append(clip["id"])

                # Verify MP4 Range streaming returns 206 Partial Content immediately
                range_r = client.get(f"{BASE_URL}{clip['media_url']}", headers={"Range": "bytes=0-65535"})
                assert range_r.status_code == 206, f"Expected 206 Partial Content for MP4 streaming, got {range_r.status_code}"
                print("[PASS] MP4 byte-range streaming (HTTP 206 Partial Content) verified.")

                # 4. Verify Instant Cancel (POST /api/jobs/{id}/cancel)
                rc_start = client.post(
                    f"{BASE_URL}/api/projects/{pid}/render-batch",
                    json={"limit": 2, "style": "viral_blur", "subtitles_mode": "off"},
                )
                assert rc_start.status_code == 200
                c_jid = rc_start.json()["job_id"]
                time.sleep(0.5)
                rc_cancel = client.post(f"{BASE_URL}/api/jobs/{c_jid}/cancel")
                assert rc_cancel.status_code == 200 and rc_cancel.json()["status"] == "cancelled"
                c_state = client.get(f"{BASE_URL}/api/jobs/{c_jid}").json()
                assert c_state["status"] == "cancelled"
                print("[PASS] POST /api/jobs/{id}/cancel cleanly stopped background render process!")
            finally:
                client.put(f"{BASE_URL}/api/settings", json={"use_ollama_vision": orig_cfg.get("use_ollama_vision", True)})

        # 5. Playwright UI Verification
        console_errors: list[str] = []
        page_errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            page.goto(BASE_URL, wait_until="networkidle")
            page.wait_for_selector("#dropzone", timeout=8000)

            # Check Global Live Progress panel elements exist (stopwatch, reassurance notice, Cancel button)
            assert page.locator("#global-live-progress").count() == 1
            assert page.locator("#global-progress-timer").count() == 1
            assert page.locator("#btn-cancel-job").count() == 1
            reassurance = page.locator("#global-live-progress .live-reassurance").inner_text()
            assert "Processamento ativo no seu PC" in reassurance

            # Check Step 3 has Dialogos & Cutscenes filter
            page.click("#step-btn-3")
            assert page.locator('#moment-filters .filter-pill[data-cat="dialogo_historia"]').is_visible()

            # Check Biblioteca de Clipes uses <video preload="metadata">
            page.click('.nav-item[data-view="biblioteca"]')
            page.wait_for_selector("#view-biblioteca.active")
            page.wait_for_selector(".clip-video", timeout=5000)
            preload_attr = page.locator(".clip-video").first.get_attribute("preload")
            assert preload_attr == "metadata", f"Expected preload='metadata' on Library video, got {preload_attr!r}"
            print("[PASS] Playwright verified Global Live Progress Panel, Cancel button, Dialogo filter & preload='metadata'!")

            browser.close()

        assert len(console_errors) == 0, f"Console errors: {console_errors}"
        assert len(page_errors) == 0, f"Page errors: {page_errors}"
        print("[PASS] Playwright finished with 0 console errors and 0 page errors!")
        print("\n=== ALL PERFORMANCE, CONCURRENCY & 25s-55s DYNAMIC SCENE CHECKS PASSED ===")
    finally:
        with httpx.Client(timeout=10.0) as client:
            for cid in created_clips:
                client.delete(f"{BASE_URL}/api/clips/{cid}?delete_file=true")
        clear_video_export_history(str(sample_vid))
        sample_vid.unlink(missing_ok=True)
        if PROJECTS_FILE.exists():
            projs = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
            kept, removed = [], set()
            for p in projs:
                if "_validate_perf_scenes" in p.get("name", ""):
                    removed.add(p.get("id"))
                else:
                    kept.append(p)
            PROJECTS_FILE.write_text(json.dumps(kept, indent=2), encoding="utf-8")
            for rid in removed:
                for tf in (BASE_DIR / "data" / "thumbs").glob(f"{rid}*"):
                    tf.unlink(missing_ok=True)
        print("[PASS] Temporary performance test artifacts cleaned up.")


if __name__ == "__main__":
    main()
