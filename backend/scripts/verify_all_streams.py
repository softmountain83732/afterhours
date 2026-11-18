"""Direct end-to-end verification of the 4 Supabase tracks."""

import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

TRACKS = [
    ("8b4c99b2-e1ee-4369-8169-96b330bc085c", 2, "MONTAGEM YUESHI"),
    ("39f8df39-b44f-4aec-aa50-257599a6057e", 3, "弥渡山歌 (Midu Echoing)"),
    ("b7aedc3d-fe3f-4835-b363-d27f0a9c3569", 4, "Genesis"),
    ("5127721c-1f2e-4442-a616-32be829a39b9", 5, "Poison Tree"),
]

def main():
    print("=================================================================")
    print(" AFTERHOURS STREAMING VERIFICATION FOR ALL 4 VAULT TRACKS")
    print("=================================================================\n")

    all_passed = True
    results = []

    for track_id, msg_id, title in TRACKS:
        print(f"--- Track: {title} (ID: {track_id}, Msg: {msg_id}) ---")

        # 1. Full request / info
        full_res = client.get(f"/api/stream/{track_id}")
        assert full_res.status_code == 200, f"Expected 200, got {full_res.status_code}"
        content_type = full_res.headers.get("content-type")
        accept_ranges = full_res.headers.get("accept-ranges")
        total_size = int(full_res.headers.get("content-length", 0))

        print(f"  Full stream: Status {full_res.status_code}, Content-Type: {content_type}, Total Size: {total_size} bytes")
        assert "audio/" in content_type, f"Invalid content-type {content_type}"
        assert accept_ranges == "bytes", f"Missing accept-ranges: bytes"
        assert total_size > 100000, f"Suspiciously small file size: {total_size}"

        # 2. Range request 0-1023
        r_start = client.get(f"/api/stream/{track_id}", headers={"Range": "bytes=0-1023"})
        print(f"  Range 0-1023: Status {r_start.status_code}, Content-Range: {r_start.headers.get('content-range')}")
        assert r_start.status_code == 206, f"Expected 206, got {r_start.status_code}"
        assert r_start.headers.get("content-range") == f"bytes 0-1023/{total_size}"
        assert len(r_start.content) == 1024

        # Validate header bytes: AAC M4A starts with ftyp (ISO Base Media)
        magic = r_start.content[4:8]
        print(f"  Container signature: {r_start.content[:16].hex()} (magic tag: {magic})")
        assert magic in [b"ftyp", b"M4A ", b"isom", b"mp42"], f"Invalid audio magic: {magic}"
        # Ensure it is NOT the 1028-byte dummy file
        assert r_start.content[:4] != b"RIFF", "File starts with RIFF (dummy audio signature)"

        # 3. Seeking request into middle of file
        half = total_size // 2
        seek_end = half + 1023
        r_mid = client.get(f"/api/stream/{track_id}", headers={"Range": f"bytes={half}-{seek_end}"})
        print(f"  Seek range {half}-{seek_end}: Status {r_mid.status_code}, Content-Range: {r_mid.headers.get('content-range')}")
        assert r_mid.status_code == 206
        assert r_mid.headers.get("content-range") == f"bytes {half}-{seek_end}/{total_size}"
        assert len(r_mid.content) == 1024

        results.append({
            "track_id": track_id,
            "title": title,
            "msg_id": msg_id,
            "total_size": total_size,
            "content_type": content_type,
            "range_0_1023": "206 OK",
            "seek_half": f"206 OK ({half}-{seek_end})",
            "magic": str(magic),
            "status": "PASSED"
        })
        print("  -> [PASSED]\n")

    print("=================================================================")
    print(" ALL 4 TRACKS VERIFIED SUCCESSFULLY")
    print("=================================================================")
    for r in results:
        print(f"  [PASS] Msg {r['msg_id']}: '{r['title']}' | {r['total_size']:,} bytes | AAC M4A ({r['content_type']})")


if __name__ == "__main__":
    main()
