import os
import socket
import time
import threading
from datetime import datetime

from flask import Flask, jsonify
from flask_cors import CORS
import numpy as np

try:
    import psycopg2
except ImportError:
    psycopg2 = None

try:
    import serial
except ImportError:
    serial = None


# ============================================================
# CONFIGURAZIONE
# ============================================================

# ModalitÃ  disponibili:
# - SERIAL_BT: lettura NMEA da Bluetooth SPP, visto da Windows come porta COM
# - UDP: l'app Android invia NMEA all'IP del PC sulla porta NMEA_PORT
# - TCP_SERVER: il PC resta in ascolto e il telefono si collega al PC
# - FILE: modalitÃ  test con file .nmea
NMEA_MODE = os.getenv("NMEA_MODE", "SERIAL_BT").upper()

# Porta su cui Python riceve le stringhe NMEA dal cellulare.
# Uso 10110 per non andare in conflitto con Flask, che resta su 5000.
NMEA_HOST = os.getenv("NMEA_HOST", "0.0.0.0")
NMEA_PORT = int(os.getenv("NMEA_PORT", "10110"))

# Configurazione Bluetooth SPP / porta seriale virtuale Windows.
# Sostituisci COM7 con la porta indicata in Gestione dispositivi.
SERIAL_PORT = os.getenv("SERIAL_PORT", "COM5")
SERIAL_BAUDRATE = int(os.getenv("SERIAL_BAUDRATE", "9600"))
SERIAL_TIMEOUT = float(os.getenv("SERIAL_TIMEOUT", "1"))

# Server Flask
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

# File di test opzionale
NMEA_FILE = os.getenv("NMEA_FILE", "strait_of_messina.nmea")

# Database PostgreSQL/PostGIS
DB_ENABLED = os.getenv("DB_ENABLED", "1") == "1"
DB_NAME = os.getenv("DB_NAME", "nmea_data")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

# Parametri HPL
sigma_pr = 3

LambdaChi2 = [
    20.9045661706659, 23.8175105579726, 25.9352361962499, 27.6828269529144,
    29.2060056524435, 30.5737444848352, 31.8258863478660, 32.9874815583810,
    34.0761648712568, 35.1032984450701, 36.0781336202314, 37.0094904073507,
    37.9011789748630, 38.7582736803920, 39.5842982183494, 40.3833547907658,
    41.1572167069017, 42.3133963316800, 43.8211959645175, 45.3157466181259
]


# ============================================================
# STATO GLOBALE
# ============================================================

app = Flask(__name__)
CORS(app)

state_lock = threading.Lock()

latest_hpl_value = None
latest_position = {"lat": None, "lon": None}
latest_satellites = []
latest_nmea_line = None
latest_update_utc = None

# Accumula i satelliti letti dai messaggi GSV piÃ¹ recenti.
# Chiave: talker+PRN, valore: dict(prn, elev_deg, az_deg, cn0, constellation)
satellite_cache = {}


# ============================================================
# PARSING NMEA
# ============================================================

def strip_checksum(line: str) -> str:
    """Rimuove checksum e spazi da una stringa NMEA."""
    line = line.strip()
    if "*" in line:
        line = line.split("*", 1)[0]
    return line


def nmea_latlon_to_decimal(value: str, hemisphere: str, is_lon: bool) -> float:
    """
    Converte coordinate NMEA:
    lat ddmm.mmmm, lon dddmm.mmmm -> gradi decimali.
    """
    if not value or not hemisphere:
        raise ValueError("Coordinate NMEA vuote")

    deg_len = 3 if is_lon else 2
    degrees = float(value[:deg_len])
    minutes = float(value[deg_len:])
    decimal = degrees + minutes / 60.0

    if hemisphere in ("S", "W"):
        decimal = -decimal

    return decimal


def parse_nmea_for_position(line: str):
    """
    Estrae lat/lon da GGA o RMC.
    Supporta talker diversi: GP, GN, GA, GL, GB/BD.
    """
    clean = strip_checksum(line)
    parts = clean.split(",")

    if not parts or not parts[0].startswith("$"):
        return None, None

    sentence = parts[0][-3:]

    try:
        if sentence == "GGA":
            # $GNGGA,time,lat,N,lon,E,fix,...
            lat = nmea_latlon_to_decimal(parts[2], parts[3], is_lon=False)
            lon = nmea_latlon_to_decimal(parts[4], parts[5], is_lon=True)
            return lat, lon

        if sentence == "RMC":
            # $GNRMC,time,status,lat,N,lon,E,...
            status = parts[2]
            if status != "A":
                return None, None
            lat = nmea_latlon_to_decimal(parts[3], parts[4], is_lon=False)
            lon = nmea_latlon_to_decimal(parts[5], parts[6], is_lon=True)
            return lat, lon

    except (ValueError, IndexError):
        return None, None

    return None, None


def constellation_from_talker(talker: str) -> str:
    return {
        "GP": "GPS",
        "GA": "Galileo",
        "GL": "GLONASS",
        "GB": "BeiDou",
        "BD": "BeiDou",
        "GN": "Mixed",
        "GQ": "QZSS",
    }.get(talker, talker)


def parse_nmea_for_gsv_satellites(line: str):
    """
    Estrae satelliti da GSV:
    $GPGSV,total_msg,msg_num,total_sats,PRN,elev,az,CN0,...
    Supporta GPGSV, GAGSV, GLGSV, GBGSV, BDGSV, GNGSV.
    """
    clean = strip_checksum(line)
    parts = clean.split(",")

    if not parts or len(parts[0]) < 6:
        return []

    talker = parts[0][1:3]
    sentence = parts[0][-3:]

    if sentence != "GSV":
        return []

    sats = []
    # Dopo i primi 4 campi, i satelliti sono in gruppi da 4.
    # Alcuni telefoni aggiungono campi extra finali: li ignoriamo.
    for i in range(4, len(parts) - 3, 4):
        try:
            prn = parts[i]
            elev = float(parts[i + 1])
            az = float(parts[i + 2])
            cn0 = float(parts[i + 3]) if parts[i + 3] else None

            if prn == "":
                continue

            sats.append({
                "key": f"{talker}_{prn}",
                "prn": prn,
                "constellation": constellation_from_talker(talker),
                "elev_deg": elev,
                "az_deg": az,
                "cn0": cn0,
                "last_seen_utc": datetime.utcnow().isoformat()
            })
        except (ValueError, IndexError):
            continue

    return sats


def update_satellite_cache(sats):
    """Aggiorna la cache dei satelliti e rimuove quelli troppo vecchi."""
    now = time.time()

    for sat in sats:
        sat["last_seen_ts"] = now
        satellite_cache[sat["key"]] = sat

    # Mantieni solo satelliti visti negli ultimi 5 secondi
    old_keys = [
        key for key, sat in satellite_cache.items()
        if now - sat.get("last_seen_ts", 0) > 5
    ]
    for key in old_keys:
        satellite_cache.pop(key, None)


def get_current_elev_az():
    """Restituisce elevazione/azimuth in radianti dai satelliti piÃ¹ recenti."""
    sats = list(satellite_cache.values())

    # Evita satelliti con elevazione troppo bassa o dati incompleti
    valid = [
        s for s in sats
        if s.get("elev_deg") is not None
        and s.get("az_deg") is not None
        and s["elev_deg"] >= 5.0
    ]

    elev = np.radians([s["elev_deg"] for s in valid])
    az = np.radians([s["az_deg"] for s in valid])

    return elev, az, valid


# ============================================================
# CALCOLO HPL
# ============================================================

def compute_slope_max_h(H, W=None):
    """
    Traduce fedelmente la funzione MATLAB SLOPE.m:
        A = inv(H' W H) H' W
        S = I - H A
        SWS = S' W S
        SlopeH = sqrt(A(1,:)^2 + A(2,:)^2) / sqrt(diag(SWS))
        Slope_maxH = max(SlopeH)

    W Ã¨ opzionale; se non fornita, viene usata la matrice identitÃ .
    """
    H = np.asarray(H, dtype=float)
    n_obs = H.shape[0]

    if W is None:
        W = np.eye(n_obs)
    else:
        W = np.asarray(W, dtype=float)

    # In caso di geometria non invertibile o quasi singolare, uso pinv come
    # fallback numericamente stabile rispetto a inv().
    normal_matrix = H.T @ W @ H
    try:
        normal_inv = np.linalg.inv(normal_matrix)
    except np.linalg.LinAlgError:
        normal_inv = np.linalg.pinv(normal_matrix)

    A = normal_inv @ H.T @ W
    S = np.eye(n_obs) - H @ A
    SWS = S.T @ W @ S

    diag_sws = np.diag(SWS)

    # Evita divisioni non definite in assenza di ridondanza utile.
    valid = diag_sws > 0
    if not np.any(valid):
        return None

    horizontal_error_amplification = np.sqrt(A[0, :] ** 2 + A[1, :] ** 2)
    slope_h = np.full(n_obs, np.nan, dtype=float)
    slope_h[valid] = horizontal_error_amplification[valid] / np.sqrt(diag_sws[valid])

    if np.all(np.isnan(slope_h)):
        return None

    return float(np.nanmax(slope_h))


def compute_HPL(elev, az):
    """
    Calcola HPL usando la formulazione RAIM/SLOPE originale del modello MATLAB.

    HPL = Slope_maxH * sigma_pr * sqrt(lambda)

    dove Slope_maxH viene calcolato come nella funzione MATLAB SLOPE.m,
    non come norma del massimo per colonna della matrice H.
    """
    nr_PR = len(elev)
    if nr_PR < 5:
        # Con 4 incognite servono almeno 5 satelliti per avere ridondanza RAIM.
        return None

    lambda_index = min(nr_PR - 4, len(LambdaChi2) - 1)

    H = np.column_stack([
        -np.cos(elev) * np.cos(az),
        -np.cos(elev) * np.sin(az),
        np.sin(elev),
        np.ones(nr_PR)
    ])

    Slope_maxH = compute_slope_max_h(H)
    if Slope_maxH is None:
        return None

    return float(Slope_maxH * sigma_pr * np.sqrt(LambdaChi2[lambda_index]))


# ============================================================
# DATABASE
# ============================================================

def get_db_connection():
    if psycopg2 is None:
        raise RuntimeError("psycopg2 non installato. Installa psycopg2-binary oppure imposta DB_ENABLED=0.")

    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )


def store_nmea_data(lat, lon, hpl, raw_nmea=None, n_sats=None):
    """
    Inserisce i dati nel DB.
    Compatibile con la tabella originale:
    nmea_acquisitions(latitude, longitude, hpl)
    Se vuoi salvare anche raw_nmea e n_sats, aggiungi colonne dedicate e adatta la query.
    """
    if not DB_ENABLED:
        return

    if lat is None or lon is None:
        return

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO nmea_acquisitions (latitude, longitude, hpl)
            VALUES (%s, %s, %s)
            """,
            (lat, lon, hpl)
        )

        conn.commit()
        cursor.close()
        conn.close()

    except Exception as e:
        print(f"[DB] Errore salvataggio: {e}")


# ============================================================
# GESTIONE LINEE NMEA
# ============================================================

def process_nmea_line(line: str):
    """
    Processa una singola stringa NMEA ricevuta dal cellulare.
    Aggiorna posizione, satelliti e HPL.
    """
    global latest_hpl_value, latest_position, latest_satellites
    global latest_nmea_line, latest_update_utc

    line = line.strip()
    if not line.startswith("$"):
        return

    lat, lon = parse_nmea_for_position(line)
    sats = parse_nmea_for_gsv_satellites(line)

    with state_lock:
        latest_nmea_line = line
        latest_update_utc = datetime.utcnow().isoformat()

        if lat is not None and lon is not None:
            latest_position = {"lat": lat, "lon": lon}

        if sats:
            update_satellite_cache(sats)

        elev, az, valid_sats = get_current_elev_az()
        latest_satellites = valid_sats

        if len(elev) >= 4:
            latest_hpl_value = compute_HPL(elev, az)

        # Salvo nel DB solo quando ho una posizione valida.
        current_lat = latest_position.get("lat")
        current_lon = latest_position.get("lon")
        current_hpl = latest_hpl_value
        current_n_sats = len(valid_sats)

    if lat is not None and lon is not None:
        store_nmea_data(current_lat, current_lon, current_hpl, line, current_n_sats)

    print(
        f"[NMEA] pos=({latest_position.get('lat')}, {latest_position.get('lon')}) "
        f"sats={len(latest_satellites)} hpl={latest_hpl_value}"
    )


# ============================================================
# INPUT REAL-TIME DA CELLULARE
# ============================================================


def serial_bluetooth_nmea_loop():
    """
    Ricezione NMEA da Bluetooth SPP.
    Su Windows il collegamento SPP appare come porta COM virtuale.
    Esempio:
    - SERIAL_PORT=COM7
    - SERIAL_BAUDRATE=9600
    """
    if serial is None:
        raise RuntimeError(
            "pyserial non installato. Installa con: pip install pyserial"
        )

    print(f"[SERIAL_BT] Apertura porta {SERIAL_PORT} a {SERIAL_BAUDRATE} baud")

    while True:
        try:
            with serial.Serial(
                port=SERIAL_PORT,
                baudrate=SERIAL_BAUDRATE,
                timeout=SERIAL_TIMEOUT
            ) as ser:
                print(f"[SERIAL_BT] Connesso a {SERIAL_PORT}")

                while True:
                    raw = ser.readline()
                    if not raw:
                        continue

                    line = raw.decode("ascii", errors="ignore").strip()
                    if line:
                        process_nmea_line(line)

        except Exception as e:
            print(f"[SERIAL_BT] Errore: {e}")
            print("[SERIAL_BT] Riprovo tra 3 secondi...")
            time.sleep(3)


def udp_nmea_loop():
    """
    Ricezione NMEA via UDP.
    Sul telefono imposta:
    - protocollo: UDP
    - host/IP: IP del PC collegato all'hotspot
    - porta: 10110
    """
    print(f"[UDP] In ascolto su {NMEA_HOST}:{NMEA_PORT}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((NMEA_HOST, NMEA_PORT))

    while True:
        data, addr = sock.recvfrom(8192)
        text = data.decode("ascii", errors="ignore")

        # Alcune app inviano piÃ¹ righe NMEA nello stesso pacchetto
        for line in text.replace("\r", "\n").split("\n"):
            if line.strip():
                process_nmea_line(line)


def tcp_server_nmea_loop():
    """
    Ricezione NMEA via TCP server.
    Utile se l'app Android supporta TCP client verso il PC.
    """
    print(f"[TCP] Server in ascolto su {NMEA_HOST}:{NMEA_PORT}")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((NMEA_HOST, NMEA_PORT))
    server.listen(1)

    while True:
        conn, addr = server.accept()
        print(f"[TCP] Connessione da {addr}")

        buffer = ""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break

                buffer += data.decode("ascii", errors="ignore")
                lines = buffer.replace("\r", "\n").split("\n")
                buffer = lines[-1]

                for line in lines[:-1]:
                    if line.strip():
                        process_nmea_line(line)

        except Exception as e:
            print(f"[TCP] Errore connessione: {e}")
        finally:
            conn.close()
            print("[TCP] Connessione chiusa")


def file_nmea_loop():
    """ModalitÃ  test da file NMEA."""
    print(f"[FILE] Lettura simulata da {NMEA_FILE}")

    with open(NMEA_FILE, "r", encoding="utf-8", errors="ignore") as f:
        while True:
            line = f.readline()
            if not line:
                f.seek(0)
                continue

            process_nmea_line(line)
            time.sleep(1)


def start_nmea_thread():
    if NMEA_MODE == "SERIAL_BT":
        target = serial_bluetooth_nmea_loop
    elif NMEA_MODE == "UDP":
        target = udp_nmea_loop
    elif NMEA_MODE == "TCP_SERVER":
        target = tcp_server_nmea_loop
    elif NMEA_MODE == "FILE":
        target = file_nmea_loop
    else:
        raise ValueError("NMEA_MODE deve essere SERIAL_BT, UDP, TCP_SERVER o FILE")

    thread = threading.Thread(target=target, daemon=True)
    thread.start()


# ============================================================
# API FLASK
# ============================================================

@app.route("/hpl")
def get_hpl():
    with state_lock:
        return jsonify({
            "hpl": latest_hpl_value,
            "position": latest_position,
            "n_sats": len(latest_satellites),
            "satellites": latest_satellites,
            "latest_nmea": latest_nmea_line,
            "latest_update_utc": latest_update_utc
        })


@app.route("/historical", methods=["GET"])
def get_historical_data():
    if not DB_ENABLED:
        return jsonify({"error": "Database disabilitato con DB_ENABLED=0"}), 503

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT latitude, longitude, hpl, timestamp
            FROM nmea_acquisitions
            ORDER BY timestamp DESC
            """
        )

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        data = [
            {
                "lat": row[0],
                "lon": row[1],
                "hpl": row[2],
                "timestamp": row[3].isoformat() if row[3] else None
            }
            for row in rows
        ]

        return jsonify(data)

    except Exception as e:
        print(f"[DB] Errore lettura historical: {e}")
        return jsonify({"error": "Failed to fetch historical data"}), 500



@app.route("/")
def web_map():
    """Mini WebGIS di controllo: visualizza posizione corrente e HPL."""
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>GNSS HPL Monitor</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    html, body, #map { height: 100%; margin: 0; }
    #panel {
      position: absolute; top: 10px; left: 10px; z-index: 9999;
      background: white; padding: 10px 12px; border-radius: 8px;
      font-family: Arial, sans-serif; font-size: 14px;
      box-shadow: 0 2px 10px rgba(0,0,0,.25);
      min-width: 260px;
    }
  </style>
</head>
<body>
<div id="panel">
  <b>GNSS HPL Monitor</b><br>
  Lat/Lon: <span id="pos">in attesa...</span><br>
  HPL: <span id="hpl">in attesa...</span><br>
  Satelliti: <span id="sats">0</span><br>
  Update UTC: <span id="upd">-</span>
</div>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
  const map = L.map('map').setView([38.19, 15.55], 12);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  let marker = null;
  let hplCircle = null;
  let track = [];
  let polyline = L.polyline(track).addTo(map);

  async function update() {
    try {
      const r = await fetch('/hpl');
      const d = await r.json();
      const lat = d.position && d.position.lat;
      const lon = d.position && d.position.lon;
      document.getElementById('hpl').textContent = d.hpl == null ? 'null' : d.hpl.toFixed(2) + ' m';
      document.getElementById('sats').textContent = d.n_sats ?? 0;
      document.getElementById('upd').textContent = d.latest_update_utc || '-';
      if (lat != null && lon != null) {
        document.getElementById('pos').textContent = lat.toFixed(7) + ', ' + lon.toFixed(7);
        const p = [lat, lon];
        if (!marker) {
          marker = L.marker(p).addTo(map);
          map.setView(p, 16);
        } else {
          marker.setLatLng(p);
        }
        if (d.hpl != null) {
          if (!hplCircle) hplCircle = L.circle(p, {radius: d.hpl}).addTo(map);
          else hplCircle.setLatLng(p).setRadius(d.hpl);
        }
        track.push(p);
        if (track.length > 1000) track.shift();
        polyline.setLatLngs(track);
      }
    } catch(e) {
      console.error(e);
    }
  }
  setInterval(update, 1000);
  update();
</script>
</body>
</html>
"""

@app.route("/status")
def get_status():
    return jsonify({
        "nmea_mode": NMEA_MODE,
        "nmea_host": NMEA_HOST,
        "nmea_port": NMEA_PORT,
        "serial_port": SERIAL_PORT,
        "serial_baudrate": SERIAL_BAUDRATE,
        "flask_host": FLASK_HOST,
        "flask_port": FLASK_PORT,
        "db_enabled": DB_ENABLED
    })


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("Avvio ricezione NMEA da cellulare...")
    print(f"ModalitÃ : {NMEA_MODE}")
    if NMEA_MODE == "SERIAL_BT":
        print(f"Porta seriale Bluetooth: {SERIAL_PORT} @ {SERIAL_BAUDRATE} baud")
    else:
        print(f"Porta NMEA: {NMEA_PORT}")
    print(f"Endpoint HPL: http://localhost:{FLASK_PORT}/hpl")

    start_nmea_thread()
    app.run(host=FLASK_HOST, port=FLASK_PORT)


