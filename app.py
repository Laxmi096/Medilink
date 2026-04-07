import os
import requests
import uuid
import json
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, send_from_directory, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from functools import wraps
from sqlalchemy import or_
from PIL import Image
import pytesseract

# FHIR Imports
from fhir.resources.bundle import Bundle, BundleEntry
from fhir.resources.patient import Patient
from fhir.resources.humanname import HumanName
from fhir.resources.documentreference import DocumentReference, DocumentReferenceContent
from fhir.resources.attachment import Attachment
from fhir.resources.codeableconcept import CodeableConcept
from fhir.resources.coding import Coding

# --- TESSERACT CONFIGURATION ---
# Note: This path might need to be adjusted depending on your system.
# For Windows, it's often in Program Files. For Linux/macOS, it's usually in the system PATH.
try:
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
except Exception as e:
    print(f"Pytesseract config error (this is expected if not on Windows, ensure Tesseract is in your PATH): {e}")

# --- APP CONFIGURATION ---
UPLOAD_FOLDER = 'uploads'
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a_very_secret_key_that_should_be_changed'
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:reegal@localhost/medilink'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# IMPORTANT: It is highly recommended to store your API key in an environment variable
# instead of hardcoding it in your application code for security reasons.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyCGgh1oN7zcShhAa6lS4CsofAdBCcEli6I") # Replace with your actual key or set env var

# Create upload directories if they don't exist
if not os.path.exists(UPLOAD_FOLDER): os.makedirs(UPLOAD_FOLDER)
if not os.path.exists(os.path.join(app.root_path, 'static', 'profile_pics')):
    os.makedirs(os.path.join(app.root_path, 'static', 'profile_pics'))

# --- EXTENSIONS INITIALIZATION ---
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = "You must be logged in to access this page."
login_manager.login_message_category = 'danger'

# --- DATABASE MODELS ---
doctor_patient_link = db.Table('doctor_patient_link',
    db.Column('doctor_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('patient_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False, unique=True)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), nullable=False, default='patient')
    profile_image = db.Column(db.String(20), nullable=False, default='default.jpg')
    health_metrics = db.relationship('HealthMetric', backref='patient', lazy=True, cascade="all, delete-orphan")
    prescriptions = db.relationship('Prescription', foreign_keys='Prescription.patient_id', backref='patient', lazy=True, cascade="all, delete-orphan")
    patients = db.relationship('User', secondary=doctor_patient_link,
        primaryjoin=(doctor_patient_link.c.doctor_id == id),
        secondaryjoin=(doctor_patient_link.c.patient_id == id),
        backref=db.backref('doctors', lazy='dynamic'), lazy='dynamic')

class MedicalRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    record_type = db.Column(db.String(100), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    extracted_text = db.Column(db.Text, nullable=True)
    ai_summary = db.Column(db.Text, nullable=True)
    patient_rel = db.relationship('User', foreign_keys=[patient_id], backref=db.backref('records', cascade="all, delete-orphan"))
    uploader = db.relationship('User', foreign_keys=[uploaded_by_id], backref='uploaded_records')

class HealthMetric(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    metric_type = db.Column(db.String(50), nullable=False)
    value_systolic = db.Column(db.Integer)
    value_diastolic = db.Column(db.Integer)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

class Prescription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    medication_name = db.Column(db.String(100), nullable=False)
    dosage = db.Column(db.String(100), nullable=False)
    frequency = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    end_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    doctor = db.relationship('User', foreign_keys=[doctor_id])

# --- USER LOADER & DECORATORS ---
@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename): return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg'}

# --- PUBLIC & AUTHENTICATION ROUTES ---
@app.route("/")
def index(): return render_template('index.html')

@app.route("/register", methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username, email, password, role = request.form.get('username'), request.form.get('email'), request.form.get('password'), request.form.get('role')
        if role not in ['patient', 'doctor', 'admin']: role = 'patient'
        if User.query.filter_by(email=email).first():
            flash('Email address already registered.', 'danger'); return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('Username is already taken.', 'danger'); return redirect(url_for('register'))
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(username=username, email=email, password=hashed_password, role=role)
        db.session.add(new_user); db.session.commit()
        flash(f'Account created for {username} as a {role.title()}! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route("/login", methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email, password = request.form.get('email'), request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user, remember=True)
            return redirect(url_for('dashboard'))
        else:
            flash('Login failed. Please check your email and password.', 'danger')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# --- PROTECTED APPLICATION ROUTES ---
@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == 'admin':
        total_users = User.query.count()
        patient_count = User.query.filter_by(role='patient').count()
        doctor_count = User.query.filter_by(role='doctor').count()
        record_count = MedicalRecord.query.count()
        users = User.query.order_by(User.id.desc()).all()
        return render_template('admin_dashboard.html', users=users, total_users=total_users, patient_count=patient_count, doctor_count=doctor_count, record_count=record_count)
    
    elif current_user.role == 'doctor':
        return render_template('doctor_dashboard.html')
    
    else: # Patient
        today = datetime.utcnow().date()
        recent_prescriptions = Prescription.query.filter(
            Prescription.patient_id == current_user.id,
            Prescription.end_date >= today
        ).order_by(Prescription.start_date.desc()).limit(3).all()
        return render_template('patient_dashboard.html', recent_prescriptions=recent_prescriptions)


@app.route("/profile", methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        user = current_user
        new_username = request.form.get('username')
        if new_username != user.username and User.query.filter_by(username=new_username).first():
            flash('Username already taken.', 'danger'); return redirect(url_for('profile'))
        user.username = new_username
        
        new_password = request.form.get('new_password')
        if new_password: user.password = bcrypt.generate_password_hash(new_password).decode('utf-8')

        if 'profile_photo' in request.files:
            photo = request.files['profile_photo']
            if photo.filename != '':
                try:
                    filename = f"user_{user.id}.jpg" 
                    filepath = os.path.join(app.root_path, 'static/profile_pics', filename)
                    img = Image.open(photo)
                    img.thumbnail((200, 200))
                    img.save(filepath, "JPEG")
                    user.profile_image = filename
                except Exception as e:
                    flash(f"Error uploading image: {e}", "danger")

        db.session.commit()
        flash('Your profile has been updated!', 'success')
        return redirect(url_for('profile'))
    return render_template('profile.html')

@app.route("/add_metric", methods=['POST'])
@login_required
def add_metric():
    if current_user.role == 'patient':
        systolic = request.form.get('systolic', type=int)
        diastolic = request.form.get('diastolic', type=int)
        if systolic and diastolic:
            new_metric = HealthMetric(patient_id=current_user.id, metric_type='blood_pressure', value_systolic=systolic, value_diastolic=diastolic)
            db.session.add(new_metric); db.session.commit()
            flash('New health reading added!', 'success')
    return redirect(url_for('my_analytics'))

@app.route("/upload", methods=['GET', 'POST'])
@login_required
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files or request.files['file'].filename == '':
            flash('No selected file', 'danger'); return redirect(request.url)
        
        file = request.files['file']
        record_type = request.form.get('record_type')
        patient_id = request.form.get('patient_id') if current_user.role == 'doctor' else current_user.id
        
        if file and patient_id:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            new_record = MedicalRecord(patient_id=int(patient_id), uploaded_by_id=current_user.id, record_type=record_type, filename=filename, extracted_text="File is not an image. Use Smart Scan for OCR.", ai_summary="File is not an image. Use Smart Scan for an AI summary.")
            db.session.add(new_record); db.session.commit()
            flash('File successfully uploaded!', 'success')
            
            if current_user.role == 'doctor': 
                return redirect(url_for('view_patient_records', patient_id=patient_id))
            return redirect(url_for('my_records'))

    patients = User.query.filter_by(role='patient').all() if current_user.role == 'doctor' else None
    return render_template('upload_record.html', patients=patients)

@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- DEDICATED FEATURE PAGES ---
@app.route("/my_records")
@login_required
def my_records():
    if current_user.role != 'patient': return redirect(url_for('dashboard'))
    records = MedicalRecord.query.filter_by(patient_id=current_user.id).order_by(MedicalRecord.created_at.desc()).all()
    return render_template('my_records.html', records=records)

@app.route("/my_analytics")
@login_required
def my_analytics():
    if current_user.role != 'patient': return redirect(url_for('dashboard'))
    bp_metrics = HealthMetric.query.filter_by(patient_id=current_user.id, metric_type='blood_pressure').order_by(HealthMetric.recorded_at.asc()).limit(10).all()
    if not bp_metrics: 
        bp_data = { "labels": ["Day 1", "Day 2", "Day 3"], "systolic": [120, 125, 122], "diastolic": [80, 82, 81] }
    else: 
        bp_data = { "labels": [m.recorded_at.strftime('%b %d') for m in bp_metrics], "systolic": [m.value_systolic for m in bp_metrics], "diastolic": [m.value_diastolic for m in bp_metrics] }
    return render_template('my_analytics.html', bp_data_json=json.dumps(bp_data))

@app.route("/ai_assistant")
@login_required
def ai_assistant():
    if current_user.role != 'patient': return redirect(url_for('dashboard'))
    return render_template('ai_assistant.html')

@app.route("/doctor/patients")
@login_required
def view_patients():
    if current_user.role != 'doctor': return redirect(url_for('dashboard'))
    search_term = request.args.get('search', '')
    if search_term:
        search_query = f"%{search_term}%"
        patients = User.query.filter(User.role=='patient', or_(User.username.like(search_query), User.email.like(search_query))).all()
    else:
        patients = User.query.filter_by(role='patient').all()
    return render_template('view_patients.html', patients=patients, search_term=search_term)

@app.route("/doctor/analytics")
@login_required
def doctor_analytics():
    if current_user.role != 'doctor': return redirect(url_for('dashboard'))
    patient_count = User.query.filter_by(role='patient').count()
    prescriptions_written = Prescription.query.count()
    records_uploaded = MedicalRecord.query.count()
    return render_template('doctor_analytics.html', patient_count=patient_count, prescriptions_written=prescriptions_written, records_uploaded=records_uploaded)

@app.route("/doctor/patient_records/<int:patient_id>")
@login_required
def view_patient_records(patient_id):
    if current_user.role != 'doctor': return redirect(url_for('dashboard'))
    patient = User.query.filter_by(id=patient_id, role='patient').first_or_404()
    records = MedicalRecord.query.filter_by(patient_id=patient.id).order_by(MedicalRecord.created_at.desc()).all()
    prescriptions = Prescription.query.filter_by(patient_id=patient.id).order_by(Prescription.end_date.desc()).all()
    bp_metrics = HealthMetric.query.filter_by(patient_id=patient.id, metric_type='blood_pressure').order_by(HealthMetric.recorded_at.asc()).limit(10).all()
    if not bp_metrics: 
        health_data = { "labels": ["Day 1", "Day 2", "Day 3"], "systolic": [120, 125, 122], "diastolic": [80, 82, 81] }
    else: 
        health_data = { "labels": [m.recorded_at.strftime('%b %d') for m in bp_metrics], "systolic": [m.value_systolic for m in bp_metrics], "diastolic": [m.value_diastolic for m in bp_metrics] }
    return render_template('patient_records.html', patient=patient, records=records, health_data_json=json.dumps(health_data), prescriptions=prescriptions)

@app.route("/doctor/prescribe/<int:patient_id>", methods=['GET', 'POST'])
@login_required
def prescribe(patient_id):
    if current_user.role != 'doctor': return redirect(url_for('dashboard'))
    patient = User.query.filter_by(id=patient_id, role='patient').first_or_404()
    if request.method == 'POST':
        medication_name = request.form.get('medication_name')
        dosage = request.form.get('dosage')
        frequency = request.form.get('frequency')
        duration = request.form.get('duration', type=int, default=7)
        notes = request.form.get('notes')
        start_date = datetime.utcnow().date()
        end_date = start_date + timedelta(days=duration)
        new_prescription = Prescription(patient_id=patient.id, doctor_id=current_user.id, medication_name=medication_name, dosage=dosage, frequency=frequency, start_date=start_date, end_date=end_date, notes=notes)
        db.session.add(new_prescription); db.session.commit()
        flash(f'Prescription for {medication_name} has been added for {patient.username}.', 'success')
        return redirect(url_for('view_patient_records', patient_id=patient.id))
    return render_template('prescribe.html', patient=patient)

@app.route("/my_prescriptions")
@login_required
def my_prescriptions():
    if current_user.role != 'patient': return redirect(url_for('dashboard'))
    prescriptions = Prescription.query.filter_by(patient_id=current_user.id).order_by(Prescription.end_date.desc()).all()
    return render_template('my_prescriptions.html', prescriptions=prescriptions)

@app.route("/admin/delete_user/<int:user_id>", methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.role == 'admin' or user.id == current_user.id: 
        flash('Admins cannot be deleted.', 'danger')
        return redirect(url_for('dashboard'))
    db.session.delete(user); db.session.commit()
    flash(f'User {user.username} has been deleted.', 'success')
    return redirect(url_for('dashboard'))

# --- SMART SCAN ROUTES ---
@app.route("/smart_scan", methods=['GET', 'POST'])
@login_required
def smart_scan():
    if request.method == 'POST':
        if 'file' not in request.files or request.files['file'].filename == '':
            flash('No file selected for scanning.', 'danger'); return redirect(request.url)
        file = request.files['file']
        if file and allowed_file(file.filename):
            filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            try:
                img = Image.open(filepath)
                raw_text = pytesseract.image_to_string(img)
                if not raw_text.strip(): raw_text = "OCR could not detect any text in the image."
            except Exception as e:
                flash(f"Could not process image file: {e}", "danger"); return redirect(request.url)

            summary_prompt = f"Summarize the following medical report text in simple terms for a patient...\n\n--- TEXT ---\n{raw_text}\n--- END ---"
            api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
            payload = {"contents": [{"parts": [{"text": summary_prompt}]}]}
            ai_summary = "AI summary could not be generated for this report."
            try:
                response = requests.post(api_url, json=payload, headers={'Content-Type': 'application/json'})
                response.raise_for_status()
                ai_summary = response.json()['candidates'][0]['content']['parts'][0]['text']
            except Exception as e:
                print(f"Gemini API Error for summary: {e}")

            new_record = MedicalRecord(patient_id=current_user.id, uploaded_by_id=current_user.id, record_type="AI Scanned Report", filename=filename, extracted_text=raw_text, ai_summary=ai_summary)
            db.session.add(new_record); db.session.commit()
            return redirect(url_for('scan_result', record_id=new_record.id))
        else:
            flash("Invalid file type. Please upload a PNG or JPG image.", "danger")
            return redirect(request.url)
    return render_template('smart_scan.html')

@app.route("/scan_result/<int:record_id>")
@login_required
def scan_result(record_id):
    record = MedicalRecord.query.get_or_404(record_id)
    if record.patient_id != current_user.id and current_user.role != 'doctor':
        flash("You are not authorized to view this record.", "danger"); return redirect(url_for('dashboard'))
    return render_template('scan_result.html', record=record)

# --- API & EXPORT ROUTES ---
@app.route("/ask_ai", methods=['POST'])
@login_required
def ask_ai():
    user_message = request.json.get('message')
    if not user_message: return jsonify({'error': 'No message provided.'}), 400
    system_prompt = "You are a helpful AI medical assistant. You provide clear, simple explanations of medical topics. You do not provide medical advice or diagnoses. Always advise the user to consult with their doctor for any health concerns."
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": user_message}]}], "systemInstruction": {"parts": [{"text": system_prompt}]}}
    try:
        response = requests.post(api_url, json=payload, headers={'Content-Type': 'application/json'})
        response.raise_for_status()
        result = response.json()
        ai_text = result['candidates'][0]['content']['parts'][0]['text']
        return jsonify({'response': ai_text})
    except Exception as e:
        print(f"API Error: {e}")
        return jsonify({'error': 'Sorry, the AI assistant is currently unavailable.'}), 500

@app.route("/export/fhir")
@login_required
def export_fhir():
    if current_user.role != 'patient': return redirect(url_for('dashboard'))
    user = current_user
    fhir_patient = Patient(id=str(user.id), name=[HumanName(text=user.username)])
    records = MedicalRecord.query.filter_by(patient_id=user.id).all()
    bundle_entries = [BundleEntry(fullUrl=f"Patient/{fhir_patient.id}", resource=fhir_patient)]
    for record in records:
        file_url = url_for('uploaded_file', filename=record.filename, _external=True)
        doc_ref = DocumentReference(id=str(record.id), status="current", subject={"reference": f"Patient/{user.id}"}, type=CodeableConcept(coding=[Coding(display=record.record_type)]), content=[DocumentReferenceContent(attachment=Attachment(url=file_url, title=record.filename))])
        bundle_entries.append(BundleEntry(fullUrl=f"DocumentReference/{doc_ref.id}", resource=doc_ref))
    bundle = Bundle(id=str(uuid.uuid4()), type="collection", timestamp=datetime.utcnow().isoformat() + "Z", entry=bundle_entries)
    return Response(bundle.json(indent=2), mimetype='application/fhir+json', headers={'Content-Disposition': f'attachment;filename=fhir_export_{user.username}.json'})

# --- DATABASE SEEDING ---
def seed_database():
    if User.query.count() > 0: return
    print("Seeding database with dummy data...")
    upload_path = app.config['UPLOAD_FOLDER']
    pw_hash = bcrypt.generate_password_hash('password123').decode('utf-8')
    
    patient_vsi = User(username='Vsilaxmi', email='vsilaxmi@gmail.com', password=pw_hash, role='patient')
    patient_rita = User(username='Rita Sharma', email='rita@gmail.com', password=pw_hash, role='patient')
    patient_katrina = User(username='Katrina S', email='katrinaS@gmail.com', password=pw_hash, role='patient')
    doctor_arun = User(username='Dr. Arun Gupta', email='arun.gupta@example.com', password=pw_hash, role='doctor')
    admin_user = User(username='admin', email='admin@medilink.com', password=bcrypt.generate_password_hash('admin123').decode('utf-8'), role='admin')
    
    db.session.add_all([patient_vsi, patient_rita, patient_katrina, doctor_arun, admin_user])
    db.session.commit()
    
    doctor_arun.patients.append(patient_vsi)
    doctor_arun.patients.append(patient_rita)
    doctor_arun.patients.append(patient_katrina)

    vsi_report_content = "Patient Name: Vsilaxmi\nTest: MRI Scan - Brain\nHospital: Apollo Hospital"
    vsi_filename = f"record_{uuid.uuid4()}.txt";
    with open(os.path.join(upload_path, vsi_filename), 'w') as f: f.write(vsi_report_content)
    record1 = MedicalRecord(patient_id=patient_vsi.id, uploaded_by_id=doctor_arun.id, record_type="MRI Scan (Migraine)", filename=vsi_filename, extracted_text=vsi_report_content)
    
    rita_report_content = "Patient Name: Rita Sharma\nTest: Complete Blood Count\nHospital: Fortis Hospital"
    rita_filename = f"record_{uuid.uuid4()}.txt"
    with open(os.path.join(upload_path, rita_filename), 'w') as f: f.write(rita_report_content)
    record2 = MedicalRecord(patient_id=patient_rita.id, uploaded_by_id=doctor_arun.id, record_type="Blood Report (Anemia)", filename=rita_filename, extracted_text=rita_report_content)
    
    katrina_report_content = "Patient Name: Katrina S\nTest: Allergy Panel\nHospital: Lilavati Hospital"
    katrina_filename = f"record_{uuid.uuid4()}.txt"
    with open(os.path.join(upload_path, katrina_filename), 'w') as f: f.write(katrina_report_content)
    record3 = MedicalRecord(patient_id=patient_katrina.id, uploaded_by_id=doctor_arun.id, record_type="Allergy Test", filename=katrina_filename, extracted_text=katrina_report_content)
    db.session.add_all([record1, record2, record3])
    
    bp1 = HealthMetric(patient_id=patient_vsi.id, metric_type='blood_pressure', value_systolic=130, value_diastolic=85, recorded_at=datetime(2025, 9, 1))
    bp2 = HealthMetric(patient_id=patient_vsi.id, metric_type='blood_pressure', value_systolic=128, value_diastolic=82, recorded_at=datetime(2025, 9, 2))
    bp_k1 = HealthMetric(patient_id=patient_katrina.id, metric_type='blood_pressure', value_systolic=135, value_diastolic=90, recorded_at=datetime(2025, 9, 1))
    db.session.add_all([bp1, bp2, bp_k1])

    pres1 = Prescription(patient_id=patient_vsi.id, doctor_id=doctor_arun.id, medication_name='Sumatriptan', dosage='50mg', frequency='As needed for migraine', start_date=datetime(2025, 9, 1).date(), end_date=datetime(2026, 9, 1).date())
    pres2 = Prescription(patient_id=patient_rita.id, doctor_id=doctor_arun.id, medication_name='Iron Supplement', dosage='65mg', frequency='Once daily with food', start_date=datetime(2025, 9, 3).date(), end_date=datetime(2025, 12, 3).date())
    pres3 = Prescription(patient_id=patient_katrina.id, doctor_id=doctor_arun.id, medication_name='Cetirizine', dosage='10mg', frequency='Once daily at night', start_date=datetime(2025, 9, 5).date(), end_date=datetime(2025, 10, 5).date())
    db.session.add_all([pres1, pres2, pres3])
    
    db.session.commit()
    print("Database seeded successfully!")

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_database()
    app.run(debug=True)
