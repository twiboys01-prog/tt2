[app]
title = TikTok DL
package.name = tiktokdl
package.domain = org.jea
source.dir = .
source.include_exts = py
source.exclude_dirs = .github,bin,.buildozer,downloads
version = 1.0
requirements = python3,kivy==2.3.0,yt-dlp,certifi,openssl,pyjnius,android
orientation = portrait
fullscreen = 0

android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 0
