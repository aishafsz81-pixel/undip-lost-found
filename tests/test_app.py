import io
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import create_app


def csrf_post(client, path, data=None):
    with client.session_transaction() as session:
        token = session.get("csrf_token")
    if token is None:
        client.get("/")
        with client.session_transaction() as session:
            token = session["csrf_token"]
    payload = dict(data or {})
    payload["csrf_token"] = token
    return client.post(path, data=payload)


def authenticated_client(app, name, email, password):
    client = app.test_client()
    csrf_post(
        client,
        "/daftar",
        {"nama": name, "email": email, "password": password},
    )
    return client


class LostAndFoundAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "DATABASE": str(root / "test.sqlite3"),
                "UPLOAD_FOLDER": str(root / "uploads"),
            }
        )
        self.client = self.app.test_client()
        response = csrf_post(
            self.client,
            "/daftar",
            {"nama": "Pemilik Demo", "email": "owner@example.test", "password": "secure-pass-123"},
        )
        self.assertEqual(response.status_code, 302)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_debug_mode_is_disabled_by_default(self):
        self.assertFalse(self.app.debug)

    def test_guest_is_redirected_and_csrf_is_required(self):
        guest = self.app.test_client()
        response = guest.get("/laporan-saya")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/masuk?next=/laporan-saya", response.headers["Location"])
        self.assertEqual(guest.post("/daftar", data={"nama": "X"}).status_code, 400)

    def test_registration_login_logout_and_account_scoped_reports(self):
        report_id = self.create_report()
        other_user = authenticated_client(
            self.app,
            "Pengguna Lain",
            "other@example.test",
            "other-pass-123",
        )
        self.assertEqual(other_user.get("/laporan-saya").status_code, 200)
        self.assertNotIn(b"Payung hijau", other_user.get("/laporan-saya").data)
        self.assertEqual(other_user.get(f"/lapor/{report_id}/edit").status_code, 404)
        other_report = csrf_post(
            other_user,
            "/lapor",
            {
                "jenis": "Hilang",
                "nama_barang": "Buku catatan",
                "kategori": "Alat Tulis",
                "deskripsi": "Buku catatan bergaris.",
                "lokasi": "Perpustakaan",
                "tanggal": "2026-10-11",
                "nama_pelapor": "Pengguna Lain",
                "kontak_pelapor": "other@example.test",
                "foto": (io.BytesIO(b"\x89PNG\r\n\x1a\nother-user-photo"), "catatan.png"),
            },
        )
        self.assertEqual(other_report.status_code, 302)
        self.assertIn(b"Buku catatan", other_user.get("/laporan-saya").data)
        self.assertIn(b"Payung hijau", self.client.get("/laporan-saya").data)

        response = csrf_post(other_user, "/keluar")
        self.assertEqual(response.status_code, 302)
        response = csrf_post(
            other_user,
            "/masuk",
            {"email": "OTHER@example.test", "password": "other-pass-123"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/laporan-saya", response.headers["Location"])
        dashboard = other_user.get("/laporan-saya")
        self.assertIn(b"Buku catatan", dashboard.data)
        self.assertNotIn(b"Payung hijau", dashboard.data)

    def test_auth_pages_render_and_login_rejects_bad_credentials(self):
        guest = self.app.test_client()
        self.assertEqual(guest.get("/masuk").status_code, 200)
        self.assertEqual(guest.get("/daftar").status_code, 200)
        failed_login = csrf_post(
            guest,
            "/masuk",
            {"email": "owner@example.test", "password": "wrong-password"},
        )
        self.assertEqual(failed_login.status_code, 200)
        self.assertIn(b"Email atau kata sandi tidak cocok", failed_login.data)

    def test_registration_rejects_duplicate_email_and_short_password(self):
        duplicate_client = self.app.test_client()
        duplicate = csrf_post(
            duplicate_client,
            "/daftar",
            {"nama": "Orang Lain", "email": "OWNER@example.test", "password": "secure-pass-123"},
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn(b"Email tersebut sudah terdaftar", duplicate.data)
        another_client = self.app.test_client()
        short_password = csrf_post(
            another_client,
            "/daftar",
            {"nama": "Orang Baru", "email": "new@example.test", "password": "123"},
        )
        self.assertEqual(short_password.status_code, 200)
        self.assertIn(b"minimal 8 karakter", short_password.data)

    def test_password_is_stored_as_hash_and_auth_page_matches_published_demo(self):
        db = sqlite3.connect(self.app.config["DATABASE"])
        try:
            password_hash = db.execute(
                "SELECT password_hash FROM users WHERE email = ?",
                ("owner@example.test",),
            ).fetchone()[0]
        finally:
            db.close()
        self.assertNotEqual(password_hash, "secure-pass-123")
        self.assertTrue(password_hash.startswith("scrypt:"))
        registration_page = self.app.test_client().get("/daftar")
        self.assertIn(b"demonstrasi mahasiswa yang dipublikasikan", registration_page.data)
        self.assertNotIn(b"prototipe lokal", registration_page.data)

    def create_report(self, **overrides):
        data = {
            "jenis": "Ditemukan",
            "nama_barang": "Payung hijau",
            "kategori": "Lainnya",
            "deskripsi": "Payung lipat berwarna hijau.",
            "lokasi": "Fakultas Hukum",
            "tanggal": "2026-10-10",
            "nama_pelapor": "Mahasiswa Demo",
            "kontak_pelapor": "demo@example.test",
        }
        data.update(overrides)
        if "foto" not in overrides and "video" not in overrides:
            data["foto"] = (io.BytesIO(b"\x89PNG\r\n\x1a\nauto-test-photo"), "otomatis.png")
        response = csrf_post(self.client, "/lapor", data)
        self.assertEqual(response.status_code, 302)
        return int(response.headers["Location"].rsplit("/", 1)[1])

    def test_home_search_and_demo_seed_data(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Barang Hilang?", response.data)
        self.assertIn(b"DEMO", response.data)
        self.assertIn(b">Buat Laporan</a>", response.data)
        self.assertIn(b">Laporan Saya</a>", response.data)
        self.assertNotIn(b">Laporkan Barang</a>", response.data)

        response = self.client.get("/cari?q=tumbler&kategori=Lainnya")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Tumbler biru navy", response.data)
        self.assertNotIn(b"Kalkulator ilmiah", response.data)

        response = self.client.get("/cari?lokasi=Kantin&status=Ditemukan")
        self.assertIn(b"Kartu mahasiswa", response.data)
        self.assertNotIn(b"Tumbler biru navy", response.data)

    def test_search_location_is_free_text_and_status_filter_has_two_choices(self):
        response = self.client.get("/cari")
        self.assertIn(b'type="text" name="lokasi"', response.data)
        self.assertNotIn(b"<datalist", response.data)
        self.assertIn(b'<option value="Hilang"', response.data)
        self.assertIn(b'<option value="Ditemukan"', response.data)
        self.assertNotIn(b'<option value="Proses Klaim"', response.data)
        self.assertNotIn(b'<option value="Sudah Kembali ke Pemilik"', response.data)

        response = self.client.get("/cari?lokasi=Kantin%20FEB&status=Ditemukan")
        self.assertIn(b"Kartu mahasiswa", response.data)
        self.assertNotIn(b"Tumbler biru navy", response.data)

    def test_search_combines_all_filters_and_reset_clears_them(self):
        report_id = self.create_report(
            nama_barang="Dompet selempang biru",
            kategori="Aksesori",
            deskripsi="Dompet biru dengan resleting perak.",
            lokasi="Gedung Teknik",
            tanggal="2026-10-12",
        )
        response = self.client.get(
            "/cari?q=Dompet&kategori=Aksesori&lokasi=Teknik&status=Ditemukan"
        )
        self.assertIn(b"Dompet selempang biru", response.data)
        self.assertIn(b"12 Oktober 2026", response.data)
        self.assertNotIn(b"Tumbler biru navy", response.data)
        self.assertIn(b'href="/cari"', response.data)
        self.assertEqual(self.client.get("/cari").status_code, 200)
        self.assertIn(f"/barang/{report_id}".encode(), response.data)

    def test_report_validation_keeps_form_values_and_ignores_forged_status(self):
        future_date = (date.today() + timedelta(days=2)).isoformat()
        response = csrf_post(
            self.client,
            "/lapor",
            data={
                "jenis": "Hilang",
                "status": "Sudah Kembali ke Pemilik",
                "nama_barang": "  Kunci motor saya  ",
                "kategori": "Kategori palsu",
                "deskripsi": "Deskripsi yang harus tetap terlihat.",
                "lokasi": "Gedung Teknik",
                "tanggal": future_date,
                "nama_pelapor": "Mahasiswa Demo",
                "kontak_pelapor": "demo@example.test",
                "foto": (io.BytesIO(b"\x89PNG\r\n\x1a\ncontoh"), "foto.png"),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Pilih kategori barang yang tersedia", response.data)
        self.assertIn(b"Tanggal kejadian tidak boleh di masa depan", response.data)
        self.assertIn(b'value="  Kunci motor saya  "', response.data)
        self.assertIn(b"Deskripsi yang harus tetap terlihat.", response.data)

        invalid_contact = csrf_post(
            self.client,
            "/lapor",
            data={
                "jenis": "Hilang",
                "nama_barang": "Barang kontak tidak valid",
                "kategori": "Lainnya",
                "deskripsi": "Ciri barang.",
                "lokasi": "Fakultas Hukum",
                "tanggal": "2026-10-10",
                "nama_pelapor": "Mahasiswa Demo",
                "kontak_pelapor": "kontak palsu",
                "foto": (io.BytesIO(b"\x89PNG\r\n\x1a\ncontoh"), "foto.png"),
            },
        )
        self.assertIn(b"email yang valid atau nomor telepon", invalid_contact.data)

        report_id = self.create_report(status="Sudah Kembali ke Pemilik")
        report_detail = self.client.get(f"/barang/{report_id}")
        self.assertIn(b'<span class="status-chip status-open">Ditemukan</span>', report_detail.data)
        self.assertNotIn(b"Sudah Kembali ke Pemilik", report_detail.data)

    def test_reports_are_sorted_by_incident_date_and_dates_are_localized(self):
        older_id = self.create_report(
            nama_barang="Laporan kejadian lama",
            tanggal="2026-10-01",
        )
        latest_id = self.create_report(
            nama_barang="Laporan kejadian terbaru",
            tanggal="2026-10-13",
        )
        home = self.client.get("/")
        search = self.client.get("/cari")
        detail = self.client.get(f"/barang/{latest_id}")
        self.assertLess(
            search.data.index(b"Laporan kejadian terbaru"),
            search.data.index(b"Laporan kejadian lama"),
        )
        self.assertIn(b"Laporan kejadian terbaru", home.data)
        self.assertNotIn(b"Laporan kejadian lama", home.data)
        self.assertIn(b"13 Oktober 2026", detail.data)
        self.assertNotIn(b"2026-10-13", detail.data)
        self.assertIn(f"/barang/{older_id}".encode(), search.data)

    def test_home_stats_exclude_demo_returns_and_explain_demo_totals(self):
        report_id = self.create_report()
        with self.app.app_context():
            db = sqlite3.connect(self.app.config["DATABASE"])
            db.execute(
                "UPDATE reports SET status = 'Sudah Kembali ke Pemilik' WHERE id = ?",
                (report_id,),
            )
            db.execute(
                "UPDATE reports SET status = 'Sudah Kembali ke Pemilik' WHERE is_demo = 1"
            )
            db.commit()
            db.close()
        home = self.client.get("/")
        self.assertIn(b"Jumlah laporan mencakup data DEMO", home.data)
        self.assertIn(b"pengembalian hanya menghitung laporan nyata", home.data)
        self.assertIn(b"<strong>1</strong><span>Telah kembali ke pemilik", home.data)

    def test_type_status_filter_keeps_claims_visible_and_ignores_internal_status(self):
        report_id = self.create_report()
        claimant = authenticated_client(
            self.app,
            "Pemilik Barang",
            "claimant@example.test",
            "claim-pass-789",
        )
        csrf_post(
            claimant,
            f"/barang/{report_id}/klaim",
            data={"nama": "Pemilik", "kontak": "owner@example.test", "pesan": "Ciri khusus."},
        )

        found_results = self.client.get("/cari?status=Ditemukan")
        self.assertIn(b"Payung hijau", found_results.data)
        internal_status_results = self.client.get("/cari?status=Proses+Klaim")
        self.assertIn(b"Payung hijau", internal_status_results.data)

    def test_demo_report_messages_are_private_and_reach_demo_dashboard(self):
        report_id = self.create_report()
        other_client = self.app.test_client()
        csrf_post(
            other_client,
            "/daftar",
            {"nama": "Pengunjung", "email": "visitor@example.test", "password": "visitor-pass-456"},
        )
        csrf_post(
            other_client,
            f"/barang/{report_id}/hubungi",
            data={"nama": "Pengunjung", "kontak": "visitor@example.test", "pesan": "Saya menemukan payung."},
        )
        dashboard = self.client.get("/laporan-saya")
        self.assertIn(b"Saya menemukan payung.", dashboard.data)
        self.assertIn(b"visitor@example.test", dashboard.data)

    def test_detail_message_form_obeys_login_owner_and_demo_rules(self):
        report_id = self.create_report()
        guest = self.app.test_client()
        guest_detail = guest.get(f"/barang/{report_id}")
        self.assertIn(b"Masuk untuk menghubungi pelapor", guest_detail.data)
        self.assertNotIn(b"/hubungi", guest_detail.data)

        sender = authenticated_client(
            self.app,
            "Pengunjung",
            "visitor@example.test",
            "visitor-pass-456",
        )
        signed_in_detail = sender.get(f"/barang/{report_id}")
        self.assertIn(b"Pesan untuk pelapor", signed_in_detail.data)
        self.assertIn(b'Kontak untuk dibalas', signed_in_detail.data)
        self.assertIn(b'required maxlength="1000"', signed_in_detail.data)

        own_detail = self.client.get(f"/barang/{report_id}")
        self.assertIn(b"Ini laporan yang kamu buat", own_detail.data)
        self.assertNotIn(b"Pesan untuk pelapor", own_detail.data)

        demo_detail = sender.get("/barang/2")
        self.assertIn(b"Interaksi pada laporan contoh tidak dikirim", demo_detail.data)
        self.assertNotIn(b"Pesan untuk pelapor", demo_detail.data)

    def test_contact_message_rejects_invalid_data_and_saves_valid_message(self):
        report_id = self.create_report()
        sender = authenticated_client(
            self.app,
            "Pengunjung",
            "visitor@example.test",
            "visitor-pass-456",
        )
        invalid = csrf_post(
            sender,
            f"/barang/{report_id}/hubungi",
            data={"nama": "", "kontak": "bukan-email", "pesan": ""},
        )
        self.assertEqual(invalid.status_code, 302)
        detail = sender.get(invalid.headers["Location"])
        self.assertIn(b"Nama wajib diisi", detail.data)
        self.assertIn(b"email yang valid", detail.data)
        self.assertIn(b"Pesan wajib diisi", detail.data)
        self.assertNotIn(b"Saya menemukan barang ini.", self.client.get("/laporan-saya").data)

        valid = csrf_post(
            sender,
            f"/barang/{report_id}/hubungi",
            data={
                "nama": "Pengunjung",
                "kontak": "visitor@example.test",
                "pesan": "Saya menemukan barang ini.",
            },
        )
        self.assertEqual(valid.status_code, 302)
        inbox = self.client.get("/laporan-saya")
        self.assertIn(b"Saya menemukan barang ini.", inbox.data)
        self.assertIn(b"visitor@example.test", inbox.data)

    def test_full_report_search_claim_and_return_workflow(self):
        report_id = self.create_report(
            nama_barang="Dompet kain merah",
            kategori="Aksesori",
            deskripsi="Ada label nama di bagian dalam.",
            lokasi="Perpustakaan",
            tanggal="2026-10-12",
        )
        claimant = authenticated_client(
            self.app,
            "Pemilik Dompet",
            "claimant@example.test",
            "claim-pass-789",
        )
        results = claimant.get(
            "/cari?q=Dompet&kategori=Aksesori&lokasi=Perpustakaan&status=Ditemukan"
        )
        self.assertIn(b"Dompet kain merah", results.data)
        detail = claimant.get(f"/barang/{report_id}")
        self.assertIn(b"Saya merasa ini barang saya", detail.data)
        self.assertIn(b"12 Oktober 2026", detail.data)

        claim = csrf_post(
            claimant,
            f"/barang/{report_id}/klaim",
            {
                "nama": "Pemilik Dompet",
                "kontak": "claimant@example.test",
                "pesan": "Label bagian dalam bertuliskan namaku.",
            },
        )
        self.assertEqual(claim.status_code, 302)
        self.assertIn(b"Proses Klaim", self.client.get(f"/barang/{report_id}").data)
        owner_dashboard = self.client.get("/laporan-saya")
        self.assertIn(b"Pengajuan klaim / informasi", owner_dashboard.data)
        self.assertIn(b"Label bagian dalam bertuliskan namaku.", owner_dashboard.data)

        returned = csrf_post(
            self.client,
            f"/laporan-saya/{report_id}/kembali",
            {"konfirmasi": "yes"},
        )
        self.assertEqual(returned.status_code, 302)
        self.assertIn(b"Sudah Kembali ke Pemilik", self.client.get(f"/barang/{report_id}").data)

    def test_demo_report_is_read_only_and_does_not_accept_fake_claims(self):
        detail = self.client.get("/barang/2")
        self.assertIn(b"Interaksi pada laporan contoh tidak dikirim", detail.data)
        self.assertNotIn(b"Saya merasa ini barang saya", detail.data)
        response = csrf_post(
            self.client,
            "/barang/2/klaim",
            {"nama": "Pengguna", "kontak": "owner@example.test", "pesan": "Klaim contoh"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            b"Interaksi pada laporan contoh tidak dikirim",
            self.client.get("/barang/2").data,
        )

    def test_photo_upload_is_saved_and_invalid_extensions_are_rejected(self):
        png_content = b"\x89PNG\r\n\x1a\n" + b"demo-photo"
        report_id = self.create_report(foto=(io.BytesIO(png_content), "payung.png"))
        detail = self.client.get(f"/barang/{report_id}")
        self.assertIn(b"/static/uploads/", detail.data)
        saved_files = list((Path(self.temp_dir.name) / "uploads").iterdir())
        self.assertEqual(len(saved_files), 1)
        self.assertEqual(saved_files[0].read_bytes(), png_content)

        response = csrf_post(
            self.client,
            "/lapor",
            data={
                "jenis": "Hilang",
                "nama_barang": "Berkas",
                "kategori": "Lainnya",
                "deskripsi": "Berkas contoh.",
                "lokasi": "Fakultas Hukum",
                "tanggal": "2026-10-10",
                "nama_pelapor": "Mahasiswa Demo",
                "kontak_pelapor": "demo@example.test",
                "foto": (io.BytesIO(b"not-an-image"), "dokumen.exe"),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Foto harus berformat JPG, PNG, atau WEBP.", response.data)

        invalid_image = csrf_post(
            self.client,
            "/lapor",
            data={
                "jenis": "Hilang",
                "nama_barang": "Foto palsu",
                "kategori": "Lainnya",
                "deskripsi": "Konten bukan gambar.",
                "lokasi": "Fakultas Hukum",
                "tanggal": "2026-10-10",
                "nama_pelapor": "Mahasiswa Demo",
                "kontak_pelapor": "demo@example.test",
                "foto": (io.BytesIO(b"not a png file"), "bukan-gambar.png"),
            },
        )
        self.assertEqual(invalid_image.status_code, 200)
        self.assertIn(b"Isi foto tidak sesuai", invalid_image.data)

    def test_report_requires_at_least_one_photo_or_video(self):
        response = csrf_post(
            self.client,
            "/lapor",
            data={
                "jenis": "Ditemukan",
                "nama_barang": "Barang tanpa media",
                "kategori": "Lainnya",
                "deskripsi": "Laporan tanpa foto atau video.",
                "lokasi": "Fakultas Hukum",
                "tanggal": "2026-10-10",
                "nama_pelapor": "Mahasiswa Demo",
                "kontak_pelapor": "demo@example.test",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Unggah minimal satu media barang: foto atau video.", response.data)

    def test_report_video_upload_is_saved_and_displayed_on_listing_and_detail(self):
        video_content = b"\x1a\x45\xdf\xa3video-demo"
        form = self.client.get("/lapor")
        self.assertIn(b'enctype="multipart/form-data"', form.data)
        self.assertIn(b"Foto/video barang", form.data)
        self.assertIn(b"* Wajib", form.data)
        self.assertIn(b"Pilih minimal satu", form.data)
        self.assertNotIn(b"Opsional", form.data)
        self.assertIn(b'name="video"', form.data)
        self.assertIn(b"MP4, WEBM", form.data)
        report_id = self.create_report(video=(io.BytesIO(video_content), "payung.webm"))

        saved_files = list((Path(self.temp_dir.name) / "uploads").iterdir())
        self.assertEqual(len(saved_files), 1)
        self.assertEqual(saved_files[0].read_bytes(), video_content)

        home = self.client.get("/")
        self.assertIn(b"card-video-preview", home.data)
        detail = self.client.get(f"/barang/{report_id}")
        self.assertIn(b"report-video-detail", detail.data)
        self.assertIn(b"uploads/", detail.data)

    def test_report_video_rejects_invalid_signature_and_format(self):
        invalid_video = csrf_post(
            self.client,
            "/lapor",
            data={
                "jenis": "Hilang",
                "nama_barang": "Barang video palsu",
                "kategori": "Lainnya",
                "deskripsi": "Video contoh.",
                "lokasi": "Fakultas Hukum",
                "tanggal": "2026-10-10",
                "nama_pelapor": "Mahasiswa Demo",
                "kontak_pelapor": "demo@example.test",
                "video": (io.BytesIO(b"bukan video"), "palsu.mp4"),
            },
        )
        self.assertEqual(invalid_video.status_code, 200)
        self.assertIn(b"Isi video tidak sesuai dengan format", invalid_video.data)
        self.assertEqual(list((Path(self.temp_dir.name) / "uploads").iterdir()), [])

        wrong_extension = csrf_post(
            self.client,
            "/lapor",
            data={
                "jenis": "Hilang",
                "nama_barang": "Barang format salah",
                "kategori": "Lainnya",
                "deskripsi": "Bukan format video yang didukung.",
                "lokasi": "Fakultas Hukum",
                "tanggal": "2026-10-10",
                "nama_pelapor": "Mahasiswa Demo",
                "kontak_pelapor": "demo@example.test",
                "video": (io.BytesIO(b"not supported"), "video.avi"),
            },
        )
        self.assertEqual(wrong_extension.status_code, 200)
        self.assertIn(b"Video harus berformat MP4 atau WEBM", wrong_extension.data)

    def test_video_replacement_and_report_deletion_clean_up_uploads(self):
        first_video = b"\x1a\x45\xdf\xa3video-awal"
        second_video = b"\x00\x00\x00\x18ftypmp42video-baru"
        report_id = self.create_report(video=(io.BytesIO(first_video), "awal.webm"))
        original = next((Path(self.temp_dir.name) / "uploads").iterdir())

        response = csrf_post(
            self.client,
            f"/lapor/{report_id}/edit",
            data={
                "jenis": "Ditemukan",
                "nama_barang": "Payung hijau",
                "kategori": "Lainnya",
                "deskripsi": "Payung lipat berwarna hijau.",
                "lokasi": "Fakultas Hukum",
                "tanggal": "2026-10-10",
                "nama_pelapor": "Mahasiswa Demo",
                "kontak_pelapor": "demo@example.test",
                "video": (io.BytesIO(second_video), "baru.mp4"),
            },
        )
        self.assertEqual(response.status_code, 302)
        current_files = list((Path(self.temp_dir.name) / "uploads").iterdir())
        self.assertEqual(len(current_files), 1)
        self.assertNotEqual(current_files[0], original)
        self.assertEqual(current_files[0].read_bytes(), second_video)

        csrf_post(self.client, f"/laporan-saya/{report_id}/hapus")
        self.assertEqual(list((Path(self.temp_dir.name) / "uploads").iterdir()), [])

    def test_about_and_static_stylesheet_are_available(self):
        about = self.client.get("/tentang")
        stylesheet = self.client.get("/static/style.css")
        self.assertEqual(about.status_code, 200)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(b"bukan layanan resmi UNDIP", about.get_data())
        self.assertIn(b"Dibuat oleh", about.get_data())
        self.assertIn(b"Proyek Vibe Coding", about.get_data())
        self.assertIn(b"Dosen: Rofiq Cahyo Prayogo", about.get_data())
        stylesheet.get_data()
        stylesheet.close()

    def test_report_validation_persistence_and_private_contact(self):
        response = csrf_post(self.client, "/lapor", {"jenis": "Ditemukan"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Nama barang wajib diisi".encode(), response.data)

        report_id = self.create_report()
        detail = self.client.get(f"/barang/{report_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Payung hijau", detail.data)
        self.assertIn(b"Ini laporan yang kamu buat", detail.data)
        self.assertNotIn(b"demo@example.test", detail.data)

        self.client.get("/")
        persisted = self.client.get(f"/barang/{report_id}")
        self.assertIn(b"Payung hijau", persisted.data)

    def test_claim_requires_confirmation_before_returned_status(self):
        report_id = self.create_report()
        claimant = authenticated_client(
            self.app,
            "Pemilik Barang",
            "claimant@example.test",
            "claim-pass-789",
        )
        response = csrf_post(
            claimant,
            f"/barang/{report_id}/klaim",
            data={"nama": "Pemilik", "kontak": "owner@example.test", "pesan": "Ada stiker khusus."},
        )
        self.assertEqual(response.status_code, 302)
        detail = self.client.get(f"/barang/{report_id}")
        self.assertIn(b"Proses Klaim", detail.data)
        self.assertNotIn(b"Sudah Kembali ke Pemilik", detail.data)

        csrf_post(self.client, f"/laporan-saya/{report_id}/kembali", data={})
        detail = self.client.get(f"/barang/{report_id}")
        self.assertIn(b"Proses Klaim", detail.data)

        csrf_post(
            self.client,
            f"/laporan-saya/{report_id}/kembali",
            data={"konfirmasi": "yes"},
        )
        detail = self.client.get(f"/barang/{report_id}")
        self.assertIn(b"Sudah Kembali ke Pemilik", detail.data)

    def test_owned_report_can_be_edited_and_deleted(self):
        report_id = self.create_report(foto=(io.BytesIO(b"\x89PNG\r\n\x1a\noriginal"), "original.png"))
        response = csrf_post(
            self.client,
            f"/lapor/{report_id}/edit",
            data={
                "jenis": "Hilang",
                "nama_barang": "Payung hijau besar",
                "kategori": "Lainnya",
                "deskripsi": "Payung hijau dengan pegangan kayu.",
                "lokasi": "Fakultas Hukum",
                "tanggal": "2026-10-10",
                "nama_pelapor": "Mahasiswa Demo",
                "kontak_pelapor": "demo@example.test",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"Payung hijau besar", self.client.get("/laporan-saya").data)

        uploaded_files = Path(self.temp_dir.name) / "uploads"
        response = csrf_post(
            self.client,
            f"/lapor/{report_id}/edit",
            data={
                "jenis": "Hilang",
                "nama_barang": "Payung hijau besar",
                "kategori": "Lainnya",
                "deskripsi": "Payung hijau dengan pegangan kayu.",
                "lokasi": "Fakultas Hukum",
                "tanggal": "2026-10-10",
                "nama_pelapor": "Mahasiswa Demo",
                "kontak_pelapor": "demo@example.test",
                "foto": (io.BytesIO(b"\xff\xd8\xffnew-photo"), "payung.jpg"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(list(uploaded_files.iterdir())), 1)

        csrf_post(self.client, f"/laporan-saya/{report_id}/hapus")
        self.assertEqual(self.client.get(f"/barang/{report_id}").status_code, 404)
        self.assertEqual(self.client.get("/laporan-saya/1/edit").status_code, 404)
        self.assertEqual(list(uploaded_files.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
