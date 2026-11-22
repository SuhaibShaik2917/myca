# MyCA — Production Deployment Guide

This guide describes how to deploy the MyCA Autonomous Accounting and GST Compliance platform into production environments.

---

## 1. System Requirements

- **Operating System:** Linux (Ubuntu 22.04 LTS / 24.04 LTS, Debian 12, or RHEL 9)
- **Container Engine:** Docker Engine 24.0+ & Docker Compose v2.20+
- **Database Engine:** PostgreSQL 16.0+ (Required for forced RLS and JSONB operators)
- **Hardware Minimum:** 2 vCPU, 4 GB RAM, 20 GB SSD storage
- **Recommended Production:** 4 vCPU, 8 GB RAM, 50 GB NVMe storage

---

## 2. Architecture Overview

```
                      [ Public Internet ]
                               |
                               v (Port 443 / HTTPS)
                    [ Reverse Proxy / Cloudflare ]
                               |
            ---------------------------------------
            | (Port 80)                           | (Port 8000)
            v                                     v
   [ Frontend Container ]                [ Backend API Container ]
   (Nginx 1.27 + React SPA)              (FastAPI + Python 3.12)
            |                                     |
            ---------------------------------------
                               |
                               v (Internal Network Port 5432)
                    [ PostgreSQL 16 Database ]
                    - app_service_user (Forced RLS)
                    - postgres (Migrations Admin)
```

---

## 3. Step-by-Step Deployment Instructions

### Step 1: Clone Repository & Prepare Environment
```bash
git clone https://github.com/SuhaibShaik2917/myca.git
cd myca

# Copy production environment configuration template
cp .env.production.example .env.production

# Edit configuration with production credentials
nano .env.production
```

### Step 2: Configure Environment Variables
Ensure the following keys are populated in `.env.production`:
```ini
ENVIRONMENT=production
SECRET_KEY=generate_with_openssl_rand_hex_32
DATABASE_URL=postgresql://app_service_user:STRONG_PASSWORD@db:5432/myca
MIGRATION_DATABASE_URL=postgresql://postgres:ADMIN_PASSWORD@db:5432/myca
CORS_ORIGINS=["https://app.myca.in"]
GEMINI_API_KEY=AIzaSy...
GSP_SANDBOX_MODE=true
```

### Step 3: Launch with Docker Compose
```bash
docker compose --env-file .env.production up -d --build
```

### Step 4: Run Database Migrations
Once the database container is healthy:
```bash
docker compose exec backend alembic upgrade head
```

### Step 5: Verify Deployment Health
```bash
# Verify backend API health
curl -f http://localhost:8000/health
# Expected: {"status":"ok","project":"MyCA Financial Platform","version":"1.0.0","environment":"production"}

# Verify frontend web container
curl -f http://localhost/health
# Expected: healthy
```

---

## 4. Reverse Proxy & SSL Configuration (Nginx / Certbot)

Place the following configuration on your host or reverse proxy:
```nginx
server {
    listen 443 ssl http2;
    server_name app.myca.in;

    ssl_certificate /etc/letsencrypt/live/app.myca.in/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.myca.in/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```
