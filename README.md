# UNDIP Lost & Found

Prototipe layanan informasi untuk membantu sivitas akademika UNDIP melaporkan dan mencari barang hilang atau ditemukan. Ini adalah proyek demonstrasi mahasiswa, bukan layanan resmi UNDIP dan tidak terintegrasi dengan sistem kampus.

## Menjalankan secara lokal

1. Pastikan Python 3.10 atau lebih baru tersedia.
2. Dari folder `proyek-taskmate`, buat virtual environment (opsional tetapi disarankan), lalu aktifkan.
3. Pasang dependensi:

   ```powershell
   pip install -r requirements.txt
   ```

4. Jalankan aplikasi:

   ```powershell
   python app.py
   ```

5. Buka alamat yang ditampilkan Flask, biasanya `http://127.0.0.1:5000`.

SQLite dibuat otomatis di `instance/undip_lost_found.sqlite3` dan foto unggahan disimpan di `static/uploads/`. Keduanya tetap tersimpan setelah server dimulai ulang. Baris contoh otomatis dibuat saat database baru dibuat dan diberi label **DEMO**.
Mode debug Flask nonaktif secara default. Aktifkan hanya untuk debugging lokal dengan mengatur `FLASK_DEBUG=1` sebelum menjalankan aplikasi; jangan aktifkan di server publik.

## Membuat link publik dengan PythonAnywhere

Alamat `127.0.0.1` hanya bisa dibuka dari komputer yang menjalankan aplikasi. Agar orang lain bisa membukanya kapan saja, unggah proyek ini ke akun hosting. Setelah selesai, PythonAnywhere memberi alamat seperti `https://NAMA-AKUN.pythonanywhere.com`.

1. Buat akun PythonAnywhere, lalu buat aplikasi web Flask baru. Pilih versi Python yang tersedia dan paling baru.
2. Unggah isi folder `proyek-taskmate` ke folder proyek di akunmu. Jangan unggah folder `.git`, `__pycache__`, atau database lokal di `instance`.
3. Pada konsol Bash PythonAnywhere, masuk ke folder proyek, buat virtual environment, lalu pasang dependensi:

   ```bash
   mkvirtualenv --python=python3.13 undip-lost-found
   pip install -r requirements.txt
   ```

   Ganti `python3.13` dengan versi Python yang dipilih saat membuat aplikasi web jika berbeda.
4. Buka berkas konfigurasi WSGI yang ditunjukkan pada tab **Web**. Tambahkan kode ini dan ganti nama akun, lokasi folder proyek, serta teks rahasianya. Simpan rahasia hanya di konfigurasi WSGI pada akun hosting, jangan di berkas proyek yang dibagikan.

   ```python
   import os
   import sys

   project_dir = "/home/NAMA-AKUN/undip-lost-found"
   if project_dir not in sys.path:
       sys.path.insert(0, project_dir)
   os.environ["FLASK_SECRET_KEY"] = "GANTI_DENGAN_RAHASIA_ACAK_PANJANG"

   from wsgi import application
   ```

   Pastikan virtual environment aplikasi diatur ke `undip-lost-found`, aktifkan HTTPS pada tab **Web**, lalu tekan **Reload**.
6. Buka alamat `https://NAMA-AKUN.pythonanywhere.com` untuk mencoba website. Database dan foto baru akan tersimpan di folder proyek pada akun hosting tersebut.

`wsgi.py` menolak menjalankan aplikasi jika `FLASK_SECRET_KEY` belum diatur, dan mengaktifkan cookie sesi khusus HTTPS. Jangan gunakan database yang berisi data pribadi asli untuk demonstrasi publik; aplikasi ini masih prototipe mahasiswa tanpa verifikasi email atau moderasi.

## Fitur prototipe

- Beranda, pencarian, filter kata kunci/kategori/lokasi/status, dan urutan laporan terbaru.
- Pembuatan serta pengeditan laporan mewajibkan setidaknya satu foto JPG/PNG/WEBP hingga 4 MB atau video MP4/WEBM hingga 12 MB. Media laporan ditampilkan pada kartu pencarian dan halaman detail barang.
- Detail laporan, pengajuan klaim atau informasi penemuan, dan pesan privat yang tampil pada dashboard pemilik laporan.
- Pendaftaran akun, login/logout, dan dashboard per akun untuk mengedit/menghapus laporan sendiri serta mengonfirmasi pengembalian setelah klaim.
- Kata sandi disimpan menggunakan hash; formulir perubahan data memakai token CSRF.
- Kontak pribadi pelapor tidak ditampilkan di halaman publik.

Menu **Buat Laporan** digunakan untuk mengirim laporan baru. Menu **Laporan Saya** digunakan untuk melihat, mengedit, atau menghapus laporan yang sudah dikirim. Keduanya memiliki fungsi berbeda.

## Batasan dan pengembangan

Login dan akun lokal sudah tersedia. Prototipe ini belum memverifikasi email, menyediakan pemulihan kata sandi, mengirim surel/pesan sungguhan, atau memoderasi laporan. Data contoh dan laporan lama yang dibuat sebelum fitur akun tetap hanya untuk demonstrasi dan bukan milik akun pengguna. Konfirmasi pengembalian adalah konfirmasi pemilik laporan, bukan verifikasi petugas UNDIP. Pemeriksaan foto hanya memeriksa ekstensi dan signature dasar, bukan memindai atau mendekode penuh berkas. Sebelum penggunaan nyata, tambahkan verifikasi email dan pemulihan akun, mekanisme konfirmasi petugas/pemilik, validasi gambar penuh, kebijakan retensi data, dan penyimpanan aman untuk unggahan serta kontak.

Atur `FLASK_SECRET_KEY` ke nilai acak yang kuat dan tetap di antara proses server agar sesi login tidak berubah saat aplikasi dimulai ulang. Jangan gunakan server development Flask atau `FLASK_DEBUG=1` untuk deployment publik. Pada PythonAnywhere, unggah versi proyek terbaru dan gunakan virtualenv yang sama; aplikasi menambahkan kolom video baru pada database lama secara otomatis saat mulai.
