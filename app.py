import os
import re
import secrets
import sqlite3
import uuid
from datetime import date, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
LEGACY_DEMO_USER_ID = "undip-demo-user"
CATEGORIES = (
    "Elektronik",
    "Kartu Identitas",
    "Aksesori",
    "Alat Tulis",
    "Tas",
    "Lainnya",
)
REPORT_TYPES = ("Hilang", "Ditemukan")
STATUSES = ("Hilang", "Ditemukan", "Proses Klaim", "Sudah Kembali ke Pemilik")
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".webm"}
MAX_PHOTO_BYTES = 4 * 1024 * 1024
MAX_VIDEO_BYTES = 12 * 1024 * 1024


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or os.urandom(32),
        DATABASE=str(BASE_DIR / "instance" / "undip_lost_found.sqlite3"),
        UPLOAD_FOLDER=str(BASE_DIR / "static" / "uploads"),
        MAX_CONTENT_LENGTH=MAX_PHOTO_BYTES + MAX_VIDEO_BYTES + 128 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    initialize_database(app)

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.before_request
    def load_user_and_validate_csrf():
        user_id = session.get("user_id")
        g.user = None
        if user_id is not None:
            g.user = get_db().execute(
                "SELECT id, name, email FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if g.user is None:
                session.clear()
        get_csrf_token()
        if request.method == "POST" and app.config.get("WTF_CSRF_ENABLED", True):
            token = session.get("csrf_token")
            submitted_token = request.form.get("csrf_token", "")
            if not token or not secrets.compare_digest(token, submitted_token):
                abort(400, description="Token keamanan formulir tidak valid. Muat ulang halaman lalu coba lagi.")

    @app.errorhandler(413)
    def photo_too_large(_error):
        flash("Ukuran media terlalu besar. Foto maksimal 4 MB dan video maksimal 12 MB.", "error")
        return redirect(request.path if request.path.startswith("/") else url_for("report_form"), 303)

    @app.context_processor
    def template_values():
        return {
            "categories": CATEGORIES,
            "filter_statuses": REPORT_TYPES,
            "current_user": g.get("user"),
            "csrf_token": get_csrf_token,
            "format_date_id": format_date_id,
        }

    @app.get("/daftar")
    @app.post("/daftar")
    def register():
        if g.user:
            return redirect(url_for("my_reports"))
        if request.method == "POST":
            name = request.form.get("nama", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            errors = []
            if not name or len(name) > 100:
                errors.append("Nama wajib diisi (maksimal 100 karakter).")
            if not email or len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                errors.append("Masukkan alamat email yang valid.")
            if len(password) < 8:
                errors.append("Kata sandi harus terdiri dari minimal 8 karakter.")
            if get_db().execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                errors.append("Email tersebut sudah terdaftar. Silakan masuk.")
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("auth.html", mode="register", form_data=request.form)
            try:
                cursor = get_db().execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                    (name, email, generate_password_hash(password)),
                )
            except sqlite3.IntegrityError:
                flash("Email tersebut sudah terdaftar. Silakan masuk.", "error")
                return render_template("auth.html", mode="register", form_data=request.form)
            session.clear()
            session["user_id"] = cursor.lastrowid
            flash("Akun berhasil dibuat. Selamat datang!", "success")
            return redirect(url_for("my_reports"))
        return render_template("auth.html", mode="register", form_data={})

    @app.get("/masuk")
    @app.post("/masuk")
    def login():
        if g.user:
            return redirect(url_for("my_reports"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_db().execute(
                "SELECT id, password_hash FROM users WHERE email = ?",
                (email,),
            ).fetchone()
            if user is None or not check_password_hash(user["password_hash"], password):
                flash("Email atau kata sandi tidak cocok.", "error")
                return render_template("auth.html", mode="login", form_data=request.form)
            session.clear()
            session["user_id"] = user["id"]
            flash("Berhasil masuk.", "success")
            next_url = request.args.get("next") or request.form.get("next", "")
            if is_safe_next_url(next_url):
                return redirect(next_url)
            return redirect(url_for("my_reports"))
        return render_template("auth.html", mode="login", form_data={})

    @app.post("/keluar")
    def logout():
        session.clear()
        flash("Kamu sudah keluar dari akun.", "success")
        return redirect(url_for("index"))

    @app.get("/")
    def index():
        db = get_db()
        stats = {
            "lost": db.execute(
                "SELECT COUNT(*) FROM reports WHERE report_type = 'Hilang'"
            ).fetchone()[0],
            "found": db.execute(
                "SELECT COUNT(*) FROM reports WHERE report_type = 'Ditemukan'"
            ).fetchone()[0],
            "returned": db.execute(
                """SELECT COUNT(*) FROM reports
                   WHERE status = 'Sudah Kembali ke Pemilik' AND is_demo = 0"""
            ).fetchone()[0],
        }
        reports = db.execute(
            """SELECT * FROM reports
               ORDER BY incident_date DESC, created_at DESC, id DESC LIMIT 6"""
        ).fetchall()
        return render_template("index.html", stats=stats, reports=reports)

    @app.get("/cari")
    def search():
        keyword = request.args.get("q", "").strip()
        category = request.args.get("kategori", "").strip()
        location = request.args.get("lokasi", "").strip()
        status = request.args.get("status", "").strip()
        filters = []
        values = []
        if keyword:
            filters.append("(name LIKE ? OR description LIKE ?)")
            values.extend((f"%{keyword}%", f"%{keyword}%"))
        if category in CATEGORIES:
            filters.append("category = ?")
            values.append(category)
        if location:
            filters.append("location LIKE ?")
            values.append(f"%{location}%")
        if status in REPORT_TYPES:
            filters.append("report_type = ?")
            values.append(status)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        reports = get_db().execute(
            f"""SELECT * FROM reports {where_clause}
                ORDER BY incident_date DESC, created_at DESC, id DESC""",
            values,
        ).fetchall()
        return render_template(
            "search.html",
            reports=reports,
            keyword=keyword,
            selected_category=category,
            selected_location=location,
            selected_status=status,
        )

    @app.route("/lapor", methods=["GET", "POST"])
    @app.route("/lapor/<int:report_id>/edit", methods=["GET", "POST"])
    @login_required
    def report_form(report_id=None):
        report = None
        if report_id is not None:
            report = get_owned_report(report_id)
            if report is None:
                abort(404)
        if request.method == "POST":
            data, errors = validate_report_form(request.form)
            photo = request.files.get("foto")
            video = request.files.get("video")
            photo_filename = report["photo_filename"] if report else None
            video_filename = report["video_filename"] if report else None
            has_uploaded_photo = bool(photo and photo.filename)
            has_uploaded_video = bool(video and video.filename)
            if not (
                has_uploaded_photo
                or has_uploaded_video
                or photo_filename
                or video_filename
            ):
                errors.append("Unggah minimal satu media barang: foto atau video.")
            if has_uploaded_photo:
                photo_size = photo.stream.seek(0, os.SEEK_END)
                photo.stream.seek(0)
                if photo_size > MAX_PHOTO_BYTES:
                    errors.append("Ukuran foto maksimal 4 MB.")
                extension = Path(secure_filename(photo.filename)).suffix.lower()
                if extension not in ALLOWED_IMAGE_EXTENSIONS:
                    errors.append("Foto harus berformat JPG, PNG, atau WEBP.")
                elif photo_size <= MAX_PHOTO_BYTES and not is_valid_image(photo, extension):
                    errors.append("Isi foto tidak sesuai dengan format JPG, PNG, atau WEBP.")
                elif photo_size <= MAX_PHOTO_BYTES:
                    photo_filename = f"{uuid.uuid4().hex}{extension}"
            if has_uploaded_video:
                video_size = video.stream.seek(0, os.SEEK_END)
                video.stream.seek(0)
                if video_size > MAX_VIDEO_BYTES:
                    errors.append("Ukuran video maksimal 12 MB.")
                extension = Path(secure_filename(video.filename)).suffix.lower()
                if extension not in ALLOWED_VIDEO_EXTENSIONS:
                    errors.append("Video harus berformat MP4 atau WEBM.")
                elif video_size <= MAX_VIDEO_BYTES and not is_valid_video(video, extension):
                    errors.append("Isi video tidak sesuai dengan format MP4 atau WEBM.")
                elif video_size <= MAX_VIDEO_BYTES:
                    video_filename = f"{uuid.uuid4().hex}{extension}"
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("report_form.html", report=report, form_data=request.form)
            if has_uploaded_photo:
                photo.save(Path(app.config["UPLOAD_FOLDER"]) / photo_filename)
            if has_uploaded_video:
                video.save(Path(app.config["UPLOAD_FOLDER"]) / video_filename)

            db = get_db()
            if report:
                db.execute(
                    """UPDATE reports
                       SET report_type = ?,
                           status = CASE WHEN status IN ('Hilang', 'Ditemukan') THEN ? ELSE status END,
                           name = ?, category = ?, description = ?,
                           location = ?, incident_date = ?, reporter_name = ?,
                           reporter_contact = ?, photo_filename = ?, video_filename = ?
                       WHERE id = ? AND owner_id = ?""",
                    (
                        data["report_type"],
                        data["report_type"],
                        data["name"],
                        data["category"],
                        data["description"],
                        data["location"],
                        data["incident_date"],
                        data["reporter_name"],
                        data["reporter_contact"],
                        photo_filename,
                        video_filename,
                        report_id,
                        str(g.user["id"]),
                    ),
                )
                if report["photo_filename"] and report["photo_filename"] != photo_filename:
                    remove_uploaded_photo(report["photo_filename"], app.config["UPLOAD_FOLDER"])
                if report["video_filename"] and report["video_filename"] != video_filename:
                    remove_uploaded_photo(report["video_filename"], app.config["UPLOAD_FOLDER"])
                flash("Laporan berhasil diperbarui.", "success")
                return redirect(url_for("my_reports"))
            cursor = db.execute(
                """INSERT INTO reports
                   (report_type, status, name, category, description, location,
                    incident_date, reporter_name, reporter_contact, photo_filename, video_filename,
                    owner_id, is_demo)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    data["report_type"],
                    data["report_type"],
                    data["name"],
                    data["category"],
                    data["description"],
                    data["location"],
                    data["incident_date"],
                    data["reporter_name"],
                    data["reporter_contact"],
                    photo_filename,
                    video_filename,
                    str(g.user["id"]),
                ),
            )
            flash("Laporan berhasil disimpan. Terima kasih sudah membantu!", "success")
            return redirect(url_for("report_detail", report_id=cursor.lastrowid))
        return render_template("report_form.html", report=report, form_data={})

    @app.get("/barang/<int:report_id>")
    def report_detail(report_id):
        report = get_report(report_id)
        if report is None:
            abort(404)
        return render_template("detail.html", report=report)

    @app.post("/barang/<int:report_id>/klaim")
    @login_required
    def submit_claim(report_id):
        report = get_report(report_id)
        if report is None:
            abort(404)
        if report["owner_id"] == str(g.user["id"]):
            flash("Kamu tidak dapat mengajukan klaim pada laporan milikmu sendiri.", "error")
            return redirect(url_for("report_detail", report_id=report_id))
        if report["is_demo"] or report["owner_id"] == LEGACY_DEMO_USER_ID:
            flash("Laporan demonstrasi tidak menerima klaim sungguhan.", "error")
            return redirect(url_for("report_detail", report_id=report_id))
        if report["status"] == "Sudah Kembali ke Pemilik":
            flash("Laporan ini sudah ditandai kembali ke pemilik.", "error")
            return redirect(url_for("report_detail", report_id=report_id))
        claimant_name = request.form.get("nama", "").strip()
        claimant_contact = request.form.get("kontak", "").strip()
        message = request.form.get("pesan", "").strip()
        interaction_errors = validate_interaction(claimant_name, claimant_contact, message)
        if interaction_errors:
            for error in interaction_errors:
                flash(error, "error")
            return redirect(url_for("report_detail", report_id=report_id))
        db = get_db()
        db.execute(
            """INSERT INTO claims (report_id, claimant_name, claimant_contact, message)
               VALUES (?, ?, ?, ?)""",
            (report_id, claimant_name, claimant_contact, message),
        )
        db.execute(
            """UPDATE reports SET status = 'Proses Klaim'
               WHERE id = ? AND status != 'Sudah Kembali ke Pemilik'""",
            (report_id,),
        )
        flash("Pengajuan terkirim. Status belum selesai sampai pengembalian dikonfirmasi.", "success")
        return redirect(url_for("report_detail", report_id=report_id))

    @app.post("/barang/<int:report_id>/hubungi")
    @login_required
    def contact_reporter(report_id):
        report = get_report(report_id)
        if report is None:
            abort(404)
        if report["owner_id"] == str(g.user["id"]):
            flash("Kamu tidak dapat mengirim pesan kepada dirimu sendiri.", "error")
            return redirect(url_for("report_detail", report_id=report_id))
        if report["is_demo"] or report["owner_id"] == LEGACY_DEMO_USER_ID:
            flash("Laporan demonstrasi tidak menerima pesan sungguhan.", "error")
            return redirect(url_for("report_detail", report_id=report_id))
        if report["status"] == "Sudah Kembali ke Pemilik":
            flash("Laporan ini sudah ditandai kembali ke pemilik.", "error")
            return redirect(url_for("report_detail", report_id=report_id))
        name = request.form.get("nama", "").strip()
        contact = request.form.get("kontak", "").strip()
        message = request.form.get("pesan", "").strip()
        interaction_errors = validate_interaction(name, contact, message)
        if interaction_errors:
            for error in interaction_errors:
                flash(error, "error")
            return redirect(url_for("report_detail", report_id=report_id))
        get_db().execute(
            """INSERT INTO contact_requests (report_id, sender_name, sender_contact, message)
               VALUES (?, ?, ?, ?)""",
            (report_id, name, contact, message),
        )
        flash("Pesan berhasil dikirim kepada pelapor melalui dashboard simulasi.", "success")
        return redirect(url_for("report_detail", report_id=report_id))

    @app.get("/laporan-saya")
    @login_required
    def my_reports():
        db = get_db()
        reports = db.execute(
            """SELECT * FROM reports WHERE owner_id = ?
               ORDER BY incident_date DESC, created_at DESC, id DESC""",
            (str(g.user["id"]),),
        ).fetchall()
        messages = db.execute(
            """SELECT c.*, r.name AS report_name FROM contact_requests c
               JOIN reports r ON r.id = c.report_id
               WHERE r.owner_id = ? ORDER BY c.created_at DESC""",
            (str(g.user["id"]),),
        ).fetchall()
        claims = db.execute(
            """SELECT c.*, r.name AS report_name FROM claims c
               JOIN reports r ON r.id = c.report_id
               WHERE r.owner_id = ? ORDER BY c.created_at DESC""",
            (str(g.user["id"]),),
        ).fetchall()
        return render_template("my_reports.html", reports=reports, messages=messages, claims=claims)

    @app.post("/laporan-saya/<int:report_id>/hapus")
    @login_required
    def delete_report(report_id):
        report = get_owned_report(report_id)
        if report is None:
            abort(404)
        get_db().execute("DELETE FROM reports WHERE id = ?", (report_id,))
        remove_uploaded_photo(report["photo_filename"], app.config["UPLOAD_FOLDER"])
        remove_uploaded_photo(report["video_filename"], app.config["UPLOAD_FOLDER"])
        flash("Laporan berhasil dihapus.", "success")
        return redirect(url_for("my_reports"))

    @app.post("/laporan-saya/<int:report_id>/kembali")
    @login_required
    def confirm_return(report_id):
        report = get_owned_report(report_id)
        if report is None:
            abort(404)
        if not request.form.get("konfirmasi"):
            flash("Konfirmasi bahwa barang benar-benar sudah diterima pemilik diperlukan.", "error")
        elif report["status"] != "Proses Klaim":
            flash("Laporan hanya dapat diselesaikan setelah ada proses klaim.", "error")
        else:
            get_db().execute(
                """UPDATE reports SET status = 'Sudah Kembali ke Pemilik'
                   WHERE id = ? AND owner_id = ?""",
                (report_id, str(g.user["id"])),
            )
            flash("Pengembalian dikonfirmasi. Terima kasih telah membantu!", "success")
        return redirect(url_for("my_reports"))

    @app.get("/tentang")
    def about():
        return render_template("about.html")

    return app


def get_db():
    if "db" not in g:
        from flask import current_app

        g.db = sqlite3.connect(current_app.config["DATABASE"], isolation_level=None)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def get_report(report_id):
    return get_db().execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()


def get_owned_report(report_id):
    return get_db().execute(
        "SELECT * FROM reports WHERE id = ? AND owner_id = ?",
        (report_id, str(g.user["id"])),
    ).fetchone()


def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def format_date_id(value):
    try:
        parsed_date = date.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    month_names = (
        "Januari",
        "Februari",
        "Maret",
        "April",
        "Mei",
        "Juni",
        "Juli",
        "Agustus",
        "September",
        "Oktober",
        "November",
        "Desember",
    )
    return f"{parsed_date.day} {month_names[parsed_date.month - 1]} {parsed_date.year}"


def validate_interaction(name, contact, message):
    errors = []
    if not name or len(name) > 100:
        errors.append("Nama wajib diisi dan maksimal 100 karakter.")
    if not contact or len(contact) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact):
        errors.append("Masukkan email yang valid (maksimal 254 karakter).")
    if not message or len(message) > 1000:
        errors.append("Pesan wajib diisi dan maksimal 1.000 karakter.")
    return errors


def is_safe_next_url(target):
    parsed = urlsplit(target)
    return (
        not parsed.scheme
        and not parsed.netloc
        and target.startswith("/")
        and not target.startswith("//")
        and "\\" not in target
    )


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.get("user") is None:
            flash("Masuk atau buat akun terlebih dahulu untuk melanjutkan.", "error")
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped_view


def is_valid_image(upload, extension):
    header = upload.stream.read(12)
    upload.stream.seek(0)
    if extension in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    return False


def is_valid_video(upload, extension):
    header = upload.stream.read(12)
    upload.stream.seek(0)
    if extension == ".mp4":
        return len(header) >= 8 and header[4:8] == b"ftyp"
    if extension == ".webm":
        return header.startswith(b"\x1a\x45\xdf\xa3")
    return False


def remove_uploaded_photo(filename, upload_folder):
    if not filename:
        return
    photo_name = Path(filename).name
    if photo_name != filename:
        return
    photo_path = Path(upload_folder) / photo_name
    if photo_path.is_file():
        photo_path.unlink()


def validate_report_form(form):
    data = {
        "report_type": form.get("jenis", "").strip(),
        "name": form.get("nama_barang", "").strip(),
        "category": form.get("kategori", "").strip(),
        "description": form.get("deskripsi", "").strip(),
        "location": form.get("lokasi", "").strip(),
        "incident_date": form.get("tanggal", "").strip(),
        "reporter_name": form.get("nama_pelapor", "").strip(),
        "reporter_contact": form.get("kontak_pelapor", "").strip(),
    }
    errors = []
    if data["report_type"] not in REPORT_TYPES:
        errors.append("Pilih jenis laporan yang tersedia.")
    if not data["name"]:
        errors.append("Nama barang wajib diisi.")
    elif len(data["name"]) > 100:
        errors.append("Nama barang maksimal 100 karakter.")
    if data["category"] not in CATEGORIES:
        errors.append("Pilih kategori barang yang tersedia.")
    if not data["description"]:
        errors.append("Deskripsi atau ciri-ciri barang wajib diisi.")
    elif len(data["description"]) > 1200:
        errors.append("Deskripsi maksimal 1.200 karakter.")
    if not data["location"]:
        errors.append("Lokasi kejadian wajib diisi.")
    elif len(data["location"]) > 150:
        errors.append("Lokasi maksimal 150 karakter.")
    try:
        incident_date = date.fromisoformat(data["incident_date"])
    except ValueError:
        errors.append("Tanggal kejadian wajib diisi dengan tanggal yang valid.")
    else:
        if incident_date > date.today():
            errors.append("Tanggal kejadian tidak boleh di masa depan.")
    if not data["reporter_name"]:
        errors.append("Nama pelapor wajib diisi.")
    elif len(data["reporter_name"]) > 100:
        errors.append("Nama pelapor maksimal 100 karakter.")
    if not data["reporter_contact"]:
        errors.append("Kontak pelapor wajib diisi.")
    elif len(data["reporter_contact"]) > 150:
        errors.append("Kontak pelapor maksimal 150 karakter.")
    elif not is_valid_contact(data["reporter_contact"]):
        errors.append("Masukkan email yang valid atau nomor telepon minimal 7 digit.")
    return data, errors


def is_valid_contact(value):
    if "@" in value:
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))
    digits = re.sub(r"\D", "", value)
    return bool(re.fullmatch(r"[+()\d .-]+", value) and 7 <= len(digits) <= 15)


def initialize_database(app):
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type TEXT NOT NULL CHECK(report_type IN ('Hilang', 'Ditemukan')),
            status TEXT NOT NULL CHECK(status IN
                ('Hilang', 'Ditemukan', 'Proses Klaim', 'Sudah Kembali ke Pemilik')),
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            location TEXT NOT NULL,
            incident_date TEXT NOT NULL,
            reporter_name TEXT NOT NULL,
            reporter_contact TEXT NOT NULL,
            photo_filename TEXT,
            video_filename TEXT,
            owner_id TEXT NOT NULL,
            is_demo INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
            claimant_name TEXT NOT NULL,
            claimant_contact TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS contact_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
            sender_name TEXT NOT NULL,
            sender_contact TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    report_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(reports)").fetchall()
    }
    if "video_filename" not in report_columns:
        connection.execute("ALTER TABLE reports ADD COLUMN video_filename TEXT")
    count = connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    if count == 0:
        today = date.today()
        samples = [
            ("Hilang", "Hilang", "Dompet kulit cokelat", "Aksesori", "Dompet kecil warna cokelat tua, berisi beberapa kartu. Tidak mencantumkan nomor kartu di laporan.", "Perpustakaan Widya Puraya", 3, "Nadia", "Kontak demo tersedia melalui formulir"),
            ("Ditemukan", "Ditemukan", "Tumbler biru navy", "Lainnya", "Tumbler stainless warna biru tua, ada stiker kecil berbentuk awan.", "Gedung Kuliah Bersama", 1, "Raka", "Kontak demo tersedia melalui formulir"),
            ("Hilang", "Hilang", "Kalkulator ilmiah", "Elektronik", "Kalkulator scientific warna hitam, terdapat goresan kecil di sisi kanan.", "Fakultas Teknik", 2, "Alya", "Kontak demo tersedia melalui formulir"),
            ("Ditemukan", "Ditemukan", "Kartu mahasiswa", "Kartu Identitas", "Kartu mahasiswa ditemukan di dekat kantin. Identitas lengkap tidak ditampilkan di sini.", "Kantin FEB", 0, "Bima", "Kontak demo tersedia melalui formulir"),
            ("Hilang", "Hilang", "Earphone putih", "Elektronik", "Earphone nirkabel putih dalam kotak pengisi daya putih.", "Masjid Kampus Undip", 4, "Dimas", "Kontak demo tersedia melalui formulir"),
            ("Ditemukan", "Ditemukan", "Tas ransel abu-abu", "Tas", "Tas punggung abu-abu dengan gantungan kunci berbentuk bintang.", "Halte kampus", 5, "Sinta", "Kontak demo tersedia melalui formulir"),
        ]
        connection.executemany(
            """INSERT INTO reports
               (report_type, status, name, category, description, location,
                incident_date, reporter_name, reporter_contact, owner_id, is_demo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            [
                (
                    kind,
                    status,
                    name,
                    category,
                    description,
                    location,
                    (today - timedelta(days=days)).isoformat(),
                    reporter,
                    contact,
                    LEGACY_DEMO_USER_ID,
                )
                for kind, status, name, category, description, location, days, reporter, contact in samples
            ],
        )
    connection.commit()
    connection.close()


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")