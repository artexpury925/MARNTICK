# MARNTICK - Social Media Video Downloader

A modern Flask web app to download videos from **YouTube, Facebook, TikTok, Instagram, and Twitter/X**.

## Features

- User registration (username + email **or** phone + password)
- Login / Logout
- Forgot password (get verification code via WhatsApp **0140204950**)
- Secret admin panel at `/admin_login254`
- Admin can: view all users, ban/unban, generate verification codes
- Banned users see payment instructions (50 KES → Paybill 522522, Acc 6234870026939877)
- Analyze any supported link → choose quality (720p / 1080p / 4K when available) → download
- Beautiful dark neon UI (Tailwind CSS)

## Supported Platforms

- YouTube (youtube.com, youtu.be)
- Facebook (facebook.com, fb.watch)
- TikTok
- Instagram
- Twitter / X

## Quick Start (Termux / Local)

```bash
# 1. Go into the project
cd marntick

# 2. Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate   # on Termux: source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Install ffmpeg for better merging (Termux)
pkg install ffmpeg

# 5. Run the app
python app.py