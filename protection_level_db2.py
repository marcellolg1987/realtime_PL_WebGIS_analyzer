import re
from flask import Flask, jsonify
from flask_cors import CORS
import numpy as np
import time
import threading

# Constants and LambdaChi2 array
LambdaChi2 = [
    20.9045661706659, 23.8175105579726, 25.9352361962499, 27.6828269529144, 
    29.2060056524435, 30.5737444848352, 31.8258863478660, 32.9874815583810, 
    34.0761648712568, 35.1032984450701, 36.0781336202314, 37.0094904073507, 
    37.9011789748630, 38.7582736803920, 39.5842982183494, 40.3833547907658, 
    41.1572167069017, 42.3133963316800, 43.8211959645175, 45.3157466181259
]
sigma_pr = 3
app = Flask(__name__)
CORS(app)
latest_hpl_value = None  # Global variable to store the latest HPL value and coordinates
latest_position = {'lat': 0.0, 'lon': 0.0}  # Store coordinates here
    

def parse_nmea_for_position(line):
    """Parses a single NMEA line to extract position data (latitude, longitude)."""
    lat, lon = None, None
    if line.startswith('$GPGGA') or line.startswith('$GPRMC'):
        parts = line.strip().split(',')
        try:
            lat = float(parts[2][:2]) + float(parts[2][2:]) / 60.0
            if parts[3] == 'S':
                lat = -lat
            lon = float(parts[4][:3]) + float(parts[4][3:]) / 60.0
            if parts[5] == 'W':
                lon = -lon
        except (ValueError, IndexError):
            print("Failed to parse position")
            pass  # Ignore if parsing fails
    print(f"Parsed lat, lon: {lat}, {lon}")
    return lat, lon

def parse_nmea_for_elev_az(line):
    """Parses a single NMEA line to extract elevation and azimuth data."""
    elev = []
    az = []
    if line.startswith('$GPGSV'):
        parts = line.strip().split(',')
        num_sats = (len(parts) - 4) // 4
        for i in range(num_sats):
            try:
                elev.append(float(parts[4 + 4*i + 1]))
                az.append(float(parts[4 + 4*i + 2]))
            except ValueError:
                pass
    print(f"Parsed elevation: {elev}, azimuth: {az}")
    return np.radians(elev), np.radians(az)

def compute_HPL(elev, az, nr_PR):
    """Computes HPL based on elevations and azimuths."""
    H = np.column_stack([
        -np.cos(elev) * np.cos(az), 
        -np.cos(elev) * np.sin(az), 
        np.sin(elev), 
        np.ones(nr_PR)
    ])
    
    Slope_maxH = np.linalg.norm(np.max(H, axis=0))
    HPL_value = Slope_maxH * sigma_pr * np.sqrt(LambdaChi2[nr_PR - 4])
    print(f"Computed HPL: {HPL_value}")
    return HPL_value



@app.route('/hpl')
def get_hpl():
    """Endpoint to get the latest HPL value and position."""
    return jsonify(hpl=latest_hpl_value, position=latest_position)




import psycopg2

# Database connection
def get_db_connection():
    return psycopg2.connect(
        dbname="nmea_data",
        user="postgres",
        password="password",
        host="localhost",
        port="5432"
    )

# Insert data into the database
def store_nmea_data(lat, lon, hpl):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        print(f"Inserting into DB: latitude={lat}, longitude={lon}, hpl={hpl}")
        cursor.execute(
            "INSERT INTO nmea_acquisitions (latitude, longitude, hpl) VALUES (%s, %s, %s)",
            (lat, lon, hpl)
        )
        conn.commit()
        print("Insert successful")
    except Exception as e:
        print(f"Error storing data: {e}")
    finally:
        cursor.close()
        conn.close()



def nmea_simulation_loop():
    global latest_hpl_value, latest_position
    with open('strait_of_messina.nmea', 'r') as f:
        while True:
            line = f.readline().strip()
            if not line:
                f.seek(0)  # Restart from the beginning if end is reached
                line = f.readline().strip()

            elev, az = parse_nmea_for_elev_az(line)
            lat, lon = parse_nmea_for_position(line)
            if lat is not None and lon is not None:
                latest_position = {'lat': lat, 'lon': lon}
            
            nr_PR = len(elev)
            if 4 <= nr_PR <= 24:  # Compute HPL if enough satellites are present
                latest_hpl_value = compute_HPL(elev, az, nr_PR)

            store_nmea_data(lat, lon, latest_hpl_value)
            time.sleep(1)  # Simulate 1 Hz acquisition
            



@app.route('/historical', methods=['GET'])
def get_historical_data():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT latitude, longitude, hpl, timestamp FROM nmea_acquisitions ORDER BY timestamp DESC")
        rows = cursor.fetchall()
        if not rows:
            return jsonify([])  # Return empty list if no data
        data = [{'lat': row[0], 'lon': row[1], 'hpl': row[2], 'timestamp': row[3].isoformat()} for row in rows]
    except Exception as e:
        print(f"Error fetching data: {e}")
        return jsonify({"error": "Failed to fetch historical data"}), 500
    finally:
        cursor.close()
        conn.close()
    return jsonify(data)
    
if __name__ == '__main__':
    threading.Thread(target=nmea_simulation_loop).start()
    app.run(host="0.0.0.0", port=5000)
    

    
    

