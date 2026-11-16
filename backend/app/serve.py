"""Chạy API qua HTTPS:  python -m app.serve

Đề: "TLS 1.3 (HTTPS, MQTTS 8883), chứng chỉ Let's Encrypt cho API, HSTS bật". Đặt API_TLS_CERT và API_TLS_KEY
(đường dẫn tới fullchain.pem / privkey.pem của certbot) thì chạy HTTPS và phản hồi mang Strict-Transport-Security.
Không đặt thì chạy HTTP thường (phát triển). Cổng: API_PORT (mặc định 8000).
Let's Encrypt cấp chứng chỉ cho tên miền công khai nên không làm được trên máy phát triển: xem docs/DEPLOY.md.
"""
import os

import uvicorn

if __name__ == "__main__":
    cert, key = os.getenv("API_TLS_CERT"), os.getenv("API_TLS_KEY")
    options = {"ssl_certfile": cert, "ssl_keyfile": key} if cert and key else {}
    uvicorn.run("app.main:app", host=os.getenv("API_HOST", "0.0.0.0"), port=int(os.getenv("API_PORT", "8000")), **options)
