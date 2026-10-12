import os

# TestClient は Host ヘッダーに "testserver" を使うため、テストでのみ許可する（本番の既定は localhost と 127.0.0.1）
os.environ.setdefault("RADAR_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
