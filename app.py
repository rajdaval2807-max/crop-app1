from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import os
import joblib
import pandas as pd
from datetime import datetime, timedelta
import requests

app = Flask(__name__)
app.secret_key = "supersecretkey"

DB_NAME = "usersnew1.db"

# Load ML model
model = joblib.load("crop_recommendation_model.pkl")

# Weather API Key
API_KEY = os.environ.get("WEATHER_API_KEY")

# Initialize database
def init_db():
    if not os.path.exists(DB_NAME):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        # Users table with role column
        c.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fullname TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user'
        )
        """)

        # Predictions table
        c.execute("""
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            crop TEXT,
            nitrogen REAL,
            phosphorus REAL,
            potassium REAL,
            temperature REAL,
            humidity REAL,
            ph REAL,
            rainfall REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """)

        # Crops table
        c.execute("""
        CREATE TABLE crops (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           name TEXT NOT NULL,
           scientific_name TEXT,
           category TEXT,
           best_season TEXT,
           optimal_growing_conditions TEXT,
           growth_duration TEXT,
           growing_stages TEXT,
           pest_requirements TEXT,
           water_required TEXT,
           description TEXT,
           image_url TEXT
        )
        """)
        #crops_info
        c.execute("""
          CREATE TABLE IF NOT EXISTS crops_info (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sowing_start TEXT,
            sowing_end TEXT
        )
        """)
        # Crop tasks
        c.execute("""
            CREATE TABLE IF NOT EXISTS crop_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crop_id INTEGER NOT NULL,
                task_type TEXT NOT NULL,
                day_offset INTEGER NOT NULL,
                notes TEXT,
                FOREIGN KEY(crop_id) REFERENCES crops_info(id)
            )
        """)
        # Custom events
        c.execute("""
           CREATE TABLE IF NOT EXISTS custom_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT NOT NULL,
            date TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

# Auto-generated events
        c.execute("""
         CREATE TABLE IF NOT EXISTS auto_events (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           user_id INTEGER,
           title TEXT NOT NULL,
           date TEXT NOT NULL,
           notes TEXT,
           crop_name TEXT,
           FOREIGN KEY(user_id) REFERENCES users(id)
           )
        """)

        conn.commit()
        conn.close()

# Weather API
def get_weather_forecast(location):
    # Get latitude & longitude from city/district name
    geocode_url = f"http://api.openweathermap.org/geo/1.0/direct?q={location},IN&limit=1&appid={API_KEY}"
    geo_response = requests.get(geocode_url)

    if geo_response.status_code != 200 or not geo_response.json():
        return None

    geo_data = geo_response.json()[0]
    lat, lon = geo_data['lat'], geo_data['lon']

    # Use 5-day forecast API (3-hour intervals)
    url = f"http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={API_KEY}&units=metric"
    response = requests.get(url)

    if response.status_code != 200:
        return None

    data = response.json()
    forecasts = data['list']  # every 3 hours, 40 entries (5 days)

    # Take the first 8 intervals (24 hours)
    today_forecast = forecasts[:8]

    temps = [f['main']['temp'] for f in today_forecast]
    hums = [f['main']['humidity'] for f in today_forecast]
    rains = [f.get('rain', {}).get('3h', 0) for f in today_forecast]  # rainfall per 3h

    # Calculate daily averages & total rainfall
    temperature = sum(temps) / len(temps)
    humidity = sum(hums) / len(hums)
    rainfall = sum(rains)  # total mm for 24h

    return round(temperature, 2), round(humidity, 2), round(rainfall, 2)


# Home (User)
@app.route('/')
def index():
    return render_template('index.html')

# Signup
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        fullname = request.form['fullname']
        email = request.form['email']
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if password != confirm_password:
            flash("Passwords do not match!", "danger")
            return redirect(url_for('signup'))

        hashed_pw = generate_password_hash(password)

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE email=?", (email,))
        if c.fetchone():
           flash("Email already registered. Please use another email.", "danger")
           return redirect(url_for('signup'))

        try:
            c.execute("INSERT INTO users (fullname, email, username, password, role) VALUES (?, ?, ?, ?, ?)",
                      (fullname, email, username, hashed_pw, "user"))
            conn.commit()
            flash("Account created successfully! Please log in.", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Username or Email already exists!", "danger")
            return redirect(url_for('signup'))
        finally:
            conn.close()

    return render_template('signup.html')

# Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE email = ?", (email,))

        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user[4], password):
            session['user_id'] = user[0]
            session['username'] = user[3]
            session['role'] = user[5]

            flash(f"Welcome, {user[1]}!", "success")

            if user[5] == "admin":
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('index'))
        else:
            flash("Invalid username or password", "danger")
            return redirect(url_for('login'))

    return render_template('login.html')

# Predict Crop
@app.route('/predict', methods=['GET', 'POST'])
def predict():
    if 'user_id' not in session:
        flash("Please log in to predict crops.", "warning")
        return redirect(url_for('login'))

    result = None
    temperature = humidity = rainfall = None

    if request.method == 'POST':
        N = int(request.form['N'])
        P = int(request.form['P'])
        K = int(request.form['K'])
        ph = float(request.form['ph'])
        location = request.form['location']

        weather = get_weather_forecast(location)
        if not weather:
            flash("Weather API error! Please check your city name.", "danger")
            return redirect(url_for('predict'))

        temperature, humidity, rainfall = weather

        input_data = pd.DataFrame([{
            'N': N,
            'P': P,
            'K': K,
            'temperature': temperature,
            'humidity': humidity,
            'ph': ph,
            'rainfall': rainfall
        }])

        result = model.predict(input_data)[0]

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            INSERT INTO predictions (user_id, crop, nitrogen, phosphorus, potassium, temperature, humidity, ph, rainfall)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (session['user_id'], result, N, P, K, temperature, humidity, ph, rainfall))
        conn.commit()
        conn.close()

    return render_template('predict.html', result=result,
                           temperature=temperature,
                           humidity=humidity,
                           rainfall=rainfall)

# ---------------- ADMIN PANEL ----------------
@app.route('/admin')
def admin_dashboard():
    if session.get('role') != 'admin':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('index'))
    return render_template('admin.html')

@app.route('/admin/crops')
def manage_crops():
    if session.get('role') != 'admin':
        return redirect(url_for('index'))
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM crops")
    crops = c.fetchall()
    conn.close()
    return render_template('manage_crops.html', crops=crops)

@app.route('/admin/crops/add', methods=['GET', 'POST'])
def add_crop():
    if session.get('role') != 'admin':
        return redirect(url_for('index'))
    if request.method == 'POST':
     name = request.form['name']
     scientific_name = request.form['scientific_name']
     category = request.form['category']
     best_season = request.form['best_season']
     optimal_growing_conditions = request.form['optimal_growing_conditions']
     growth_duration = request.form['growth_duration']
     growing_stages = request.form['growing_stages']
     pest_requirements = request.form['pest_requirements']
     water_required = request.form['water_required']
     description = request.form['description']
     image_url = request.form['image_url']

     conn = sqlite3.connect(DB_NAME)
     c = conn.cursor()
     c.execute("""
        INSERT INTO crops 
        (name, scientific_name, category, best_season, optimal_growing_conditions, 
         growth_duration, growing_stages, pest_requirements, water_required, 
         description, image_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     """, (name, scientific_name, category, best_season, optimal_growing_conditions,
          growth_duration, growing_stages, pest_requirements, water_required,
          description, image_url))
     conn.commit()
     conn.close()
     flash("Crop added successfully!", "success")
     return redirect(url_for('manage_crops'))

    return render_template('add_crop.html')

@app.route('/admin/crops/update/<int:crop_id>', methods=['GET', 'POST'])
def update_crop(crop_id):
    if session.get('role') != 'admin':
        return redirect(url_for('index'))

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # So we can use dict-like access
    c = conn.cursor()

    # Fetch crop
    c.execute("SELECT * FROM crops WHERE id=?", (crop_id,))
    crop = c.fetchone()

    if request.method == 'POST':
        name = request.form['name']
        scientific_name = request.form['scientific_name']
        category = request.form['category']
        best_season = request.form['best_season']
        optimal_growing_conditions = request.form['optimal_growing_conditions']
        growth_duration = request.form['growth_duration']
        growing_stages = request.form['growing_stages']
        pest_requirements = request.form['pest_requirements']
        water_required = request.form['water_required']
        description = request.form['description']
        image_url = request.form['image_url']

        c.execute("""
            UPDATE crops 
            SET name=?, scientific_name=?, category=?, best_season=?, 
                optimal_growing_conditions=?, growth_duration=?, growing_stages=?, 
                pest_requirements=?, water_required=?, description=?, image_url=?
            WHERE id=?
        """, (name, scientific_name, category, best_season,
              optimal_growing_conditions, growth_duration, growing_stages,
              pest_requirements, water_required, description, image_url, crop_id))
        conn.commit()
        conn.close()
        flash("Crop updated successfully!", "success")
        return redirect(url_for('manage_crops'))

    conn.close()
    return render_template('update_crop.html', crop=crop)


@app.route('/admin/crops/delete/<int:crop_id>')
def delete_crop(crop_id):
    if session.get('role') != 'admin':
        return redirect(url_for('index'))
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM crops WHERE id=?", (crop_id,))
    conn.commit()
    conn.close()
    flash("Crop deleted successfully!", "info")
    return redirect(url_for('manage_crops'))

# ---------------- USER FEATURE 1 ----------------
@app.route('/feature1')
def feature1():
    if 'user_id' not in session:
        flash("Please log in to access Feature 1.", "warning")
        return redirect(url_for('login'))
        
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM crops")
    crops = c.fetchall()
    conn.close()
    return render_template('feature1.html', crops=crops)


@app.route('/crop/<int:crop_id>')
def crop_detail(crop_id):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # allows dict-like access
    c = conn.cursor()
    c.execute("SELECT * FROM crops WHERE id=?", (crop_id,))
    crop = c.fetchone()
    conn.close()
    return render_template('crop_detail.html', crop=crop)


# ---------------- USER FEATURE 3 (Weather Forecast) ----------------
@app.route('/weather', methods=['GET', 'POST'])
def weather():
    if 'user_id' not in session:
        flash("Please log in to view weather forecast.", "warning")
        return redirect(url_for('login'))

    if request.method == "POST":
        city = request.form.get("city")
        if not city:
            flash("Please enter a city.", "danger")
            return redirect(url_for('weather'))

        # Current weather
        current_url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={API_KEY}&units=metric"
        current_response = requests.get(current_url).json()

        if current_response.get("cod") != 200:
            flash("City not found! Try again.", "danger")
            return redirect(url_for('weather'))

        current_weather = {
            "city": current_response["name"],
            "temp": current_response["main"]["temp"],
            "humidity": current_response["main"]["humidity"],
            "wind": current_response["wind"]["speed"],
            "status": current_response["weather"][0]["main"],           # e.g. Clouds
            "description": current_response["weather"][0]["description"], # e.g. scattered clouds
            "icon": current_response["weather"][0]["icon"]
        }

        # Forecast (next 7 days, pick 12:00 PM if available)
        forecast_url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={API_KEY}&units=metric"
        forecast_response = requests.get(forecast_url).json()

        forecast_list = []
        added_dates = set()
        for entry in forecast_response["list"]:
            date, time = entry["dt_txt"].split(" ")
            if time.startswith("12:00:00") and date not in added_dates:
                forecast_list.append({
                    "date": date,
                    "temp": entry["main"]["temp"],
                    "humidity": entry["main"]["humidity"],
                    "wind": entry["wind"]["speed"],
                    "status": entry["weather"][0]["main"],           # short form
                    "description": entry["weather"][0]["description"], # detailed
                    "icon": entry["weather"][0]["icon"]
                })
                added_dates.add(date)
            if len(forecast_list) >= 7:
                break

        return render_template("weather.html", current=current_weather, forecast=forecast_list)

    return render_template("weather.html", current=None, forecast=None)

#----------------------------------------------------------------------------------------------------------------------

@app.route("/home")
def home():
    if 'user_id' not in session:
        flash("Please log in to use the calender.", "warning")
        return redirect(url_for('login'))

    today = datetime.today().date()
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # for dict-like access
    c = conn.cursor()

    c.execute("SELECT * FROM custom_events WHERE user_id=?", (session['user_id'],))
    custom_events = c.fetchall()
    c.execute("SELECT * FROM auto_events WHERE user_id=?", (session['user_id'],))
    auto_events = c.fetchall()
    conn.close()

    custom_list = [
        {"id": e["id"], "title": e["title"], "start": e["date"], "notes": e["notes"], "type": "custom"}
        for e in custom_events if datetime.strptime(e["date"], "%Y-%m-%d").date() >= today
    ]

    auto_list = [
        {"id": e["id"], "title": e["title"], "start": e["date"], "notes": e["notes"], "type": "auto"}
        for e in auto_events if datetime.strptime(e["date"], "%Y-%m-%d").date() >= today
    ]

    all_events = custom_list + auto_list
    return render_template("home.html", events=all_events)

# ----------------- Day View -----------------
@app.route("/day/<date>")
def day_view(date):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM custom_events WHERE date=?", (date,))
    custom_events = c.fetchall()
    c.execute("SELECT * FROM auto_events WHERE date=?", (date,))
    auto_events = c.fetchall()
    conn.close()

    return render_template("day_view.html", date=date,
                           custom_events=custom_events,
                           auto_events=auto_events)

# ----------------- Delete Event -----------------
@app.route("/delete_event/<int:event_id>")
def delete_event(event_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM custom_events WHERE id=?", (event_id,))
    conn.commit()
    conn.close()
    flash("Event deleted successfully!", "success")
    return redirect(url_for("home"))

@app.route("/delete_auto_event/<int:event_id>", methods=["POST"])
def delete_auto_event(event_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM auto_events WHERE id=?", (event_id,))
    conn.commit()
    conn.close()
    flash("Auto event removed successfully!", "success")
    return redirect(url_for("auto_events_list"))

# ----------------- Admin Page -----------------
@app.route("/admin_cal")
def admin_cal():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM crops_info")
    crops = c.fetchall()
    conn.close()
    return render_template("admin_cal.html", crops=crops)


@app.route("/add_crop_cal", methods=["POST"])
def add_crop_cal():
    name = request.form["name"]
    sowing_start = request.form["sowing_start"]
    sowing_end = request.form["sowing_end"]

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO crops_info (name, sowing_start, sowing_end) VALUES (?, ?, ?)", 
              (name, sowing_start, sowing_end))
    conn.commit()
    conn.close()
    flash("Crop added successfully!", "success")
    return redirect(url_for("admin_cal"))


@app.route("/edit_crop_cal/<int:crop_id>", methods=["GET", "POST"])
def edit_crop_cal(crop_id):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM crops_info WHERE id=?", (crop_id,))
    crop = c.fetchone()

    if request.method == "POST":
        name = request.form["name"]
        c.execute("UPDATE crops_info SET name=? WHERE id=?", (name, crop_id))
        conn.commit()
        conn.close()
        flash("Crop updated successfully!", "success")
        return redirect(url_for("admin_cal"))

    conn.close()
    return render_template("edit_crop.html", crop=crop)

@app.route("/delete_crop_cal/<int:crop_id>")
def delete_crop_cal(crop_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM crop_tasks WHERE crop_id=?", (crop_id,))
    c.execute("DELETE FROM crops_info WHERE id=?", (crop_id,))
    conn.commit()
    conn.close()
    flash("Crop deleted successfully!", "success")
    return redirect(url_for("admin_cal"))

# ----------------- Crop Tasks -----------------
@app.route("/crop_tasks/<int:crop_id>/tasks")
def crop_tasks(crop_id):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM crops_info WHERE id=?", (crop_id,))
    crop = c.fetchone()
    c.execute("SELECT * FROM crop_tasks WHERE crop_id=?", (crop_id,))
    tasks = c.fetchall()
    conn.close()
    return render_template("crop_tasks.html", crop=crop, tasks=tasks)

@app.route("/add_task", methods=["POST"])
def add_task():
    crop_id = request.form["crop_id"]
    task_type = request.form["task_type"]
    day_offset = int(request.form["day_offset"])
    notes = request.form.get("notes", "")

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO crop_tasks (crop_id, task_type, day_offset, notes) VALUES (?, ?, ?, ?)",
              (crop_id, task_type, day_offset, notes))
    conn.commit()
    conn.close()
    flash("Task added successfully!", "success")
    return redirect(url_for("crop_tasks", crop_id=crop_id))

@app.route("/delete_task/<int:task_id>")
def delete_task(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT crop_id FROM crop_tasks WHERE id=?", (task_id,))
    crop_id = c.fetchone()[0]
    c.execute("DELETE FROM crop_tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    flash("Task deleted successfully!", "success")
    return redirect(url_for("crop_tasks", crop_id=crop_id))

# ----------------- Custom Events -----------------
@app.route("/custom_events", methods=["GET", "POST"])
def custom_events():
    if 'user_id' not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if request.method == "POST":
        title = request.form["title"]
        date = request.form["date"]
        notes = request.form.get("notes", "")
        c.execute("INSERT INTO custom_events (user_id, title, date, notes) VALUES (?, ?, ?, ?)",
                  (session['user_id'], title, date, notes))
        conn.commit()
        conn.close()
        flash("Custom event added!", "success")
        return redirect(url_for("custom_events"))

    # 🔹 FIXED: Only fetch events of current user
    c.execute("SELECT * FROM custom_events WHERE user_id=?", (session['user_id'],))
    events = c.fetchall()
    conn.close()
    return render_template("custom_events.html", events=events)

# ----------------- Auto Events -----------------
@app.route("/auto_events", methods=["GET", "POST"])
def auto_events():
    if 'user_id' not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        crop_name = request.form["crop_name"]
        sowing_date = request.form["sowing_date"]
        return redirect(url_for("generate_auto_events", crop_name=crop_name, sowing_date=sowing_date))

    return render_template("auto_events.html")



@app.route("/generate_auto_events/<crop_name>/<sowing_date>")
def generate_auto_events(crop_name, sowing_date):
    sowing_date_obj = datetime.strptime(sowing_date, "%Y-%m-%d")

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM crops_info WHERE name=?", (crop_name,))
    crop = c.fetchone()

    if not crop:
        flash("Crop not found!", "danger")
        conn.close()
        return redirect(url_for("auto_events"))

    # ✅ Season check (handles cross-year ranges like Oct–Feb)
    if crop["sowing_start"] and crop["sowing_end"]:
        sowing_md = sowing_date_obj.strftime("%m-%d")

        start_md = crop["sowing_start"]
        end_md = crop["sowing_end"]

        start = datetime.strptime(start_md, "%m-%d")
        end = datetime.strptime(end_md, "%m-%d")
        test = datetime.strptime(sowing_md, "%m-%d")

        if start <= end:
            # ✅ Normal case (e.g., Jun–Jul, Oct–Dec)
            in_season = (start <= test <= end)
        else:
            # ✅ Cross-year case (e.g., Oct–Feb)
            in_season = (test >= start or test <= end)

        if not in_season:
            flash(f"{crop_name} can only be sown between {start_md} and {end_md}.", "danger")
            conn.close()
            return redirect(url_for("auto_events"))

    # ✅ If within season, generate events
    auto_events = generate_crop_events(crop_name, sowing_date)
    for ev in auto_events:
        notes = ev.get("notes", "")
        c.execute("""
            INSERT INTO auto_events (user_id, title, date, notes, crop_name) 
            VALUES (?, ?, ?, ?, ?)
        """, (session['user_id'], ev["title"], ev["start"], notes, crop_name))

    conn.commit()
    conn.close()
    flash("Auto events generated successfully!", "success")
    return redirect(url_for("auto_events_list"))



@app.route("/auto_events_list")
def auto_events_list():
    if 'user_id' not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # 🔹 FIXED: Only fetch auto events of current user
    c.execute("SELECT * FROM auto_events WHERE user_id=?", (session['user_id'],))
    auto_events = c.fetchall()
    conn.close()
    return render_template("auto_events_list.html", auto_events=auto_events)

# ----------------- Function to generate crop events -----------------
def generate_crop_events(crop_name, sowing_date_str):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM crops_info WHERE name=?", (crop_name,))
    crop = c.fetchone()
    if not crop:
        return []

    sowing_date = datetime.strptime(sowing_date_str, "%Y-%m-%d")
    c.execute("SELECT * FROM crop_tasks WHERE crop_id=?", (crop["id"],))
    tasks = c.fetchall()
    conn.close()

    events = []
    for t in tasks:
        event_date = sowing_date + timedelta(days=t["day_offset"])
        events.append({
            "title": t["task_type"],
            "start": event_date.strftime("%Y-%m-%d"),
            "notes": t["notes"]
        })
    return events

#-----------------------------------------------------------------------------------------------------------
# ----------------- Role Management -----------------
@app.route('/admin/roles', methods=['GET', 'POST'])
def manage_roles():
    if session.get('role') != 'admin':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('index'))

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if request.method == "POST":
        user_id = request.form.get("user_id")
        new_role = request.form.get("role")
        c.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        conn.commit()
        flash("User role updated successfully!", "success")

    c.execute("SELECT id, fullname, username, email, role FROM users")
    users = c.fetchall()
    conn.close()

    return render_template("manage_roles.html", users=users)

# Logout
@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
