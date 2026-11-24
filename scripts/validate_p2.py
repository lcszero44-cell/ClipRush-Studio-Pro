"""End-to-end validation suite for ClipRush Studio Pro P2."""

from __future__ import annotations

import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(r"D:\ClipRush_Pro_V2")
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8765"
DATA_DIR = ROOT / "data"


def wait_for_server(timeout: float = 25.0) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{BASE}/", timeout=3)
            if r.status_code == 200:
                m = re.search(r'<meta\s+name="cliprush-token"\s+content="([^"]+)"', r.text)
                if m:
                    return m.group(1)
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Server 127.0.0.1:8765 did not become ready in time.")


def snapshot_files(paths: list[Path]) -> dict[Path, bytes | None]:
    return {p: (p.read_bytes() if p.exists() else None) for p in paths}


def restore_files(snap: dict[Path, bytes | None]) -> None:
    for p, content in snap.items():
        if content is None:
            if p.exists():
                p.unlink()
        else:
            p.write_bytes(content)


def main() -> None:
    token = wait_for_server()
    headers = {"X-ClipRush-Token": token}
    print(f"[OK] Server online at {BASE} with CSRF token.")

    state_files = [
        DATA_DIR / "performance_metrics.json",
        DATA_DIR / "ranking_weights.json",
        DATA_DIR / "growth_plan.json",
    ]
    backup_snap = snapshot_files(state_files)

    try:
        # ---- 1. Analytics & Learned Ranking Weights ----
        r1 = requests.post(
            f"{BASE}/api/analytics/record",
            headers=headers,
            json={
                "title": "Headshot insano no Resident Evil 4",
                "game_name": "Resident Evil 4 Remake",
                "category": "combate_intenso",
                "platform": "tiktok_reels",
                "duration_sec": 26,
                "hook_style": "curiosidade",
                "views": 18500,
                "avg_watch_pct": 88.5,
                "retention_3s_pct": 79.0,
                "shares": 420,
            },
            timeout=10,
        )
        assert r1.status_code == 200, f"Manual record failed: {r1.text}"

        sample_csv = (
            "Video title,Video views,Average watch time,Watched full video,Shares,Duration\n"
            "Boss fight epica em God of War Ragnarok,42000,52s,68%,910,74\n"
            "Susto surreal em The Last of Us,9800,19s,72%,180,25\n"
        ).encode("utf-8")
        r_csv = requests.post(
            f"{BASE}/api/analytics/import-tiktok-csv",
            headers=headers,
            files={"file": ("tiktok_studio_export.csv", sample_csv, "text/csv")},
            timeout=10,
        )
        assert r_csv.status_code == 200, f"CSV import failed: {r_csv.text}"
        csv_data = r_csv.json()
        assert csv_data["imported_count"] == 2, f"Expected 2 CSV rows, got {csv_data}"

        dash = requests.get(f"{BASE}/api/analytics/dashboard", timeout=10).json()
        assert dash["summary"]["total_records"] >= 3
        for dim in ("by_game", "by_category", "by_duration", "by_hook_style"):
            assert len(dash[dim]) >= 1, f"Dimension {dim} is empty"
        weights = dash["learned_weights"]
        assert weights["sample_size"] >= 3
        assert (DATA_DIR / "ranking_weights.json").exists()

        from backend.analytics import get_learned_ranking_boost

        boost = get_learned_ranking_boost("combate_intenso", 26.0, "Resident Evil 4 Remake")
        assert isinstance(boost, (int, float)), f"Unexpected boost type: {boost}"
        print(
            f"[OK] Analytics + TikTok CSV + Ranking Learning: "
            f"{dash['summary']['total_records']} records, {dash['summary']['total_views']} views, boost={boost:+.2f}"
        )

        # ---- 2. Smart Agenda, A/B Testing & Monetization Goals ----
        r_ag = requests.post(
            f"{BASE}/api/growth/agenda/generate",
            headers=headers,
            json={"posts_per_day": 2, "days": 5},
            timeout=10,
        )
        assert r_ag.status_code == 200, r_ag.text
        slots = r_ag.json()["smart_slots"]
        assert len(slots) == 10, f"Expected 10 slots (2/day x 5d), got {len(slots)}"
        platforms_seen = {s["platform"] for s in slots}
        assert len(platforms_seen) >= 2, f"Platforms did not alternate: {platforms_seen}"

        first_slot = slots[0]["id"]
        r_chk = requests.patch(
            f"{BASE}/api/growth/agenda/{first_slot}",
            headers=headers,
            json={"checklist": {"safe_zone_ok": True, "audio_checked": True}},
            timeout=10,
        )
        assert r_chk.status_code == 200
        upd_slot = next(s for s in r_chk.json()["smart_slots"] if s["id"] == first_slot)
        assert upd_slot["checklist"]["safe_zone_ok"] is True

        r_ab = requests.post(
            f"{BASE}/api/growth/ab",
            headers=headers,
            json={"game_name": "Resident Evil 4 Remake", "category": "combate_intenso", "platform_target": "tiktok_reels"},
            timeout=10,
        )
        assert r_ab.status_code == 200, r_ab.text
        ab = r_ab.json()["test"]
        assert ab["variant_a"]["title"] != ab["variant_b"]["title"], "A/B variants must differ"

        r_win = requests.post(
            f"{BASE}/api/growth/ab/{ab['id']}/winner",
            headers=headers,
            json={"winner": "A", "notes": "Retencao 3s +14%"},
            timeout=10,
        )
        assert r_win.status_code == 200
        upd_ab = next(t for t in r_win.json()["ab_tests"] if t["id"] == ab["id"])
        assert upd_ab["winner"] == "A"

        r_goals = requests.put(
            f"{BASE}/api/growth/goals",
            headers=headers,
            json={
                "tiktok_followers_current": 2500,
                "tiktok_followers_target": 10000,
                "tiktok_views30d_current": 45000,
                "tiktok_views30d_target": 100000,
                "youtube_subs_current": 320,
                "youtube_watch_hours_current": 900,
                "youtube_shorts_views_current": 1500000,
            },
            timeout=10,
        )
        assert r_goals.status_code == 200
        gp = r_goals.json()["goals_progress"]
        assert gp["tiktok_followers_pct"] == 25.0
        assert gp["tiktok_views30d_pct"] == 45.0
        print(
            f"[OK] Smart Agenda ({len(slots)} slots, platforms={sorted(platforms_seen)}), "
            f"A/B Test (winner=A), Goals (TikTok {gp['tiktok_followers_pct']}% / {gp['tiktok_views30d_pct']}%)"
        )

        # ---- 3. Anti-Factory Variation & Copyright Risk ----
        from backend.editor import build_anti_factory_variation

        base_ov = {"color": "#FFD700", "animation": "fade", "y": 230}
        s0, o0 = build_anti_factory_variation(0, "fit", base_ov)
        s1, o1 = build_anti_factory_variation(1, "fit", base_ov)
        s2, o2 = build_anti_factory_variation(2, "fit", base_ov)
        assert len({s0, s1, s2}) == 3, f"Anti-factory styles must rotate: {[s0, s1, s2]}"
        assert len({o0["color"], o1["color"], o2["color"]}) == 3, "Hook colors must rotate"
        assert len({o0["animation"], o1["animation"], o2["animation"]}) == 3, "Animations must rotate"
        assert len({o0["y"], o1["y"], o2["y"]}) == 3, "Safe Zone Y must vary"
        print(f"[OK] Anti-Factory Variation verified across consecutive clips: {[s0, s1, s2]} / {[o0['color'], o1['color'], o2['color']]}")

        # ---- 4. Per-Project Storage Manager & Backup .zip (incl. Zip-Slip protection) ----
        disk_proj = requests.get(f"{BASE}/api/disk/projects", timeout=10).json()
        assert "projects" in disk_proj and "disk_free_gb" in disk_proj and "low_disk_warning" in disk_proj

        zip_resp = requests.get(f"{BASE}/api/backup/export", timeout=15)
        assert zip_resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(zip_resp.content))
        names = set(zf.namelist())
        assert "backup_manifest.json" in names, f"Manifest missing in backup zip: {names}"

        imp_resp = requests.post(
            f"{BASE}/api/backup/import",
            headers=headers,
            files={"file": ("backup.zip", zip_resp.content, "application/zip")},
            timeout=15,
        )
        assert imp_resp.status_code == 200 and imp_resp.json()["count"] >= 1

        # Verify Zip-Slip attempt is safely rejected (ignored, 0 restored)
        evil_buf = io.BytesIO()
        with zipfile.ZipFile(evil_buf, "w") as ezf:
            ezf.writestr("../../evil.json", '{"pwn": true}')
        evil_resp = requests.post(
            f"{BASE}/api/backup/import",
            headers=headers,
            files={"file": ("evil.zip", evil_buf.getvalue(), "application/zip")},
            timeout=10,
        )
        assert evil_resp.status_code == 400, f"Zip-slip should return 400, got {evil_resp.status_code}"
        print(
            f"[OK] Storage Manager ({len(disk_proj['projects'])} projects, {disk_proj['disk_free_gb']} GB free) "
            f"& Backup .zip roundtrip ({len(names)} entries, Zip-Slip blocked 400)"
        )

        # ---- 5. Diagnostics API ----
        diag = requests.get(f"{BASE}/api/diagnostics", timeout=10).json()
        assert "2.3.0" in diag["version"], f"Unexpected version: {diag['version']}"
        assert diag["port_8765_active"] is True
        assert diag["ffmpeg"]["ok"] is True
        assert len(diag["changelog"]) >= 3
        print(f"[OK] Diagnostics API: version={diag['version']}, FFmpeg={diag['ffmpeg']['source']}, RAM={diag['ram']['percent']}%")

        # ---- 6. Playwright Live UI + Live Synthetic Self-Test ----
        console_errors: list[str] = []
        page_errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: page_errors.append(str(e)))

            page.goto(BASE, wait_until="networkidle", timeout=20000)

            # Check Step 2 P2 toggles exist in DOM
            assert page.locator("#toggle-anti-factory").is_checked(), "Anti-factory toggle should be ON by default"
            assert page.locator("#toggle-seamless-loop").count() == 1

            # Navigate to Desempenho
            page.click('.nav-item[data-view="desempenho"]')
            page.wait_for_selector("#analytics-kpi-bar .card", timeout=10000)
            kpi_text = page.locator("#analytics-kpi-bar").inner_text()
            assert "VIEWS TOTAIS" in kpi_text
            dim_cards = page.locator("#analytics-dimensions-grid .card").count()
            assert dim_cards == 4, f"Expected 4 analytics cross-tab cards, got {dim_cards}"

            # Navigate to Agendador & Metas
            page.click('.nav-item[data-view="agendador"]')
            page.wait_for_selector("#goals-progress-bars .progress-fill", timeout=10000)
            assert page.locator("#smart-agenda-slots .slot-chk-box").count() >= 5
            assert page.locator("#ab-tests-list .ab-win-btn").count() >= 2

            # Navigate to Armazenamento & Backup
            page.click('.nav-item[data-view="limpeza"]')
            page.wait_for_selector("#btn-backup-export", timeout=10000)
            assert page.locator("#btn-backup-import").is_visible()

            # Navigate to Diagnostico & run live Synthetic Self-Test via UI button
            page.click('.nav-item[data-view="diagnostico"]')
            page.wait_for_selector("#diag-status-grid .health-item", timeout=10000)
            ver_txt = page.locator("#diag-version-badge").inner_text()
            assert "2.3.0" in ver_txt, f"Version badge mismatch: {ver_txt}"

            page.click("#btn-run-selftest")
            page.wait_for_function(
                "() => { const el = document.getElementById('diag-selftest-result'); return el && el.innerText.includes('100% de sucesso'); }",
                timeout=60000,
            )
            selftest_txt = page.locator("#diag-selftest-result").inner_text()
            assert "100% de sucesso" in selftest_txt
            print(f"[OK] Playwright Live UI + Synthetic Self-Test PASSED:\n{selftest_txt}")

            browser.close()

        assert not console_errors, f"Console errors detected: {console_errors}"
        assert not page_errors, f"Page errors detected: {page_errors}"
        print("[OK] Playwright finished with 0 console errors and 0 page errors.")
        print("\n=== ALL P2 VALIDATIONS PASSED 100% ===")

    finally:
        restore_files(backup_snap)
        print("[OK] Temporary P2 test data cleaned up; user data restored.")


if __name__ == "__main__":
    main()
