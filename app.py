import os
import cv2
import numpy as np
from flask import Flask, render_template, Response, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import face_recognition
import pickle
from datetime import datetime

app = Flask(__name__)
app.secret_key = "face_recognition_secret_key"
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///students.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

# Student model
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    roll_number = db.Column(db.String(20), unique=True, nullable=False)
    image_file = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"Student('{self.name}', '{self.roll_number}')"

with app.app_context():
    db.create_all()

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def generate_encodings():
    """Generate face encodings for all students in the database"""
    known_face_encodings = []
    known_face_names = []
    
    students = Student.query.all()
    for student in students:
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], student.image_file)
        if os.path.exists(image_path):
            image = face_recognition.load_image_file(image_path)
            try:
                # Try to find a face in the image
                face_encodings = face_recognition.face_encodings(image)
                if face_encodings:
                    encoding = face_encodings[0]  # Take the first face found
                    known_face_encodings.append(encoding)
                    known_face_names.append(student.name)
            except Exception as e:
                print(f"Error processing {student.name}'s image: {e}")
                continue
    
    # Save encodings to a file
    data = {
        "encodings": known_face_encodings,
        "names": known_face_names
    }
    with open("encodings.pickle", "wb") as f:
        pickle.dump(data, f)
    
    return known_face_encodings, known_face_names

def load_encodings():
    """Load face encodings from pickle file"""
    try:
        with open("encodings.pickle", "rb") as f:
            data = pickle.load(f)
        return data["encodings"], data["names"]
    except FileNotFoundError:
        return generate_encodings()

# Routes
@app.route('/')
def index():
    students = Student.query.all()
    return render_template('index.html', students=students)

@app.route('/add_student', methods=['GET', 'POST'])
def add_student():
    if request.method == 'POST':
        name = request.form['name']
        roll_number = request.form['roll_number']
        
        # Check if student already exists
        existing_student = Student.query.filter_by(roll_number=roll_number).first()
        if existing_student:
            flash('Student with this roll number already exists', 'danger')
            return redirect(url_for('add_student'))
        
        # Check if image was uploaded
        if 'image' not in request.files:
            flash('No image part', 'danger')
            return redirect(request.url)
        
        file = request.files['image']
        if file.filename == '':
            flash('No selected file', 'danger')
            return redirect(request.url)
        
        if file and allowed_file(file.filename):
            # Secure the filename and save
            filename = secure_filename(f"{roll_number}_{file.filename}")
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            # Verify that the image contains a face
            image = face_recognition.load_image_file(file_path)
            face_locations = face_recognition.face_locations(image)
            
            if not face_locations:
                os.remove(file_path)  # Remove the file if no face detected
                flash('No face detected in the uploaded image. Please try again.', 'danger')
                return redirect(request.url)
            
            # Add student to database
            student = Student(name=name, roll_number=roll_number, image_file=filename)
            db.session.add(student)
            db.session.commit()
            
            # Regenerate encodings
            generate_encodings()
            
            flash('Student added successfully', 'success')
            return redirect(url_for('index'))
        else:
            flash('Allowed image types are png, jpg, jpeg', 'danger')
            return redirect(request.url)
    
    return render_template('add_student.html')

@app.route('/train')
def train():
    encodings, names = generate_encodings()
    flash(f'Training completed. Encoded {len(encodings)} faces.', 'success')
    return redirect(url_for('index'))

def detect_faces():
    # Load the trained face encodings
    known_face_encodings, known_face_names = load_encodings()
    
    # Initialize webcam
    video_capture = cv2.VideoCapture(0)
    
    # Set smaller frame size for faster processing
    video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    
    if not video_capture.isOpened():
        raise RuntimeError("Could not start camera.")
    
    # Process frames from webcam
    while True:
        # Grab a single frame of video
        ret, frame = video_capture.read()
        if not ret:
            break
        
        # Convert the image from BGR color (which OpenCV uses) to RGB color
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Find all face locations and face encodings in the current frame
        face_locations = face_recognition.face_locations(rgb_frame)
        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
        
        # Loop through each detected face
        for (top, right, bottom, left), face_encoding in zip(face_locations, face_encodings):
            # See if the face is a match for the known face(s)
            name = "Unknown"
            
            if known_face_encodings:  # Check if we have any encodings
                matches = face_recognition.compare_faces(known_face_encodings, face_encoding)
                # Use the known face with the smallest distance to the new face
                face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
                best_match_index = np.argmin(face_distances)
                if matches[best_match_index]:
                    name = known_face_names[best_match_index]
            
            # Draw a box around the face
            cv2.rectangle(frame, (left, top), (right, bottom), (0, 0, 255), 2)
            
            # Draw a label with a name below the face
            cv2.rectangle(frame, (left, bottom - 35), (right, bottom), (0, 0, 255), cv2.FILLED)
            font = cv2.FONT_HERSHEY_DUPLEX
            cv2.putText(frame, name, (left + 6, bottom - 6), font, 1.0, (255, 255, 255), 1)
        
        # Convert to jpg for streaming
        ret, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()
        
        # Yield the frame for streaming
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/video_feed')
def video_feed():
    return Response(detect_faces(),
                   mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/webcam')
def webcam():
    return render_template('webcam.html')

@app.route('/delete_student/<int:student_id>', methods=['POST'])
def delete_student(student_id):
    student = Student.query.get_or_404(student_id)
    
    # Delete student image
    image_path = os.path.join(app.config['UPLOAD_FOLDER'], student.image_file)
    if os.path.exists(image_path):
        os.remove(image_path)
    
    # Delete student from database
    db.session.delete(student)
    db.session.commit()
    
    # Regenerate encodings
    generate_encodings()
    
    flash('Student deleted successfully', 'success')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)