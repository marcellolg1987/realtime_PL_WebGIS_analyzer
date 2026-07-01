# GNSS Integrity WebGIS Framework

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)]()
[![Platform](https://img.shields.io/badge/Platform-Windows%2011-lightgrey.svg)]()
[![Database](https://img.shields.io/badge/Database-PostgreSQL-blue.svg)]()
[![WebGIS](https://img.shields.io/badge/WebGIS-Leaflet-green.svg)]()
[![License](https://img.shields.io/badge/License-MIT-brightgreen.svg)]()

An open-source framework for **real-time GNSS integrity monitoring** and **WebGIS visualization** using Android smartphones, Bluetooth SPP communication and RAIM-based Horizontal Protection Level (HPL) computation.

## Overview

The framework acquires live NMEA observations from an Android smartphone through Bluetooth Serial Port Profile (SPP), computes the Horizontal Protection Level (HPL) using a RAIM/SLOPE algorithm implemented in Python, stores the results in PostgreSQL and publishes them through a Flask REST API for visualization in a Leaflet WebGIS.

## System Architecture

```text
GNSS Satellites
      │
Android Smartphone
      │
Bluetooth SPP (Virtual COM)
      │
Python + Anaconda
      │
 ┌────┴────┐
 │         │
PostgreSQL Flask REST API
      │
      └────► Leaflet WebGIS
```

## Main Features

- Real-time GNSS acquisition
- Bluetooth SPP communication
- NMEA parsing
- RAIM/SLOPE HPL computation
- PostgreSQL storage
- Flask REST API
- Leaflet WebGIS
- Vessel trajectory visualization
- HPL protection circle
- HDOP / PDOP monitoring
- Integrity statistics

## Repository Structure

```text
realtime_PL_WebGIS_analyzer/
├── protection_level_com5_webgis_db.py
├── index.html
├── requirements.txt
├── README.md
├── figures/
└── screenshots/
```

## Software Stack

| Component | Technology |
|------------|------------|
| Language | Python 3 |
| Environment | Anaconda |
| REST API | Flask |
| Database | PostgreSQL |
| WebGIS | Leaflet |
| Mobile App | GPS NMEA Tether |
| Communication | Bluetooth SPP |
| OS | Windows 11 |

## Installation

```bash
git clone https://github.com/marcellolg1987/realtime_PL_WebGIS_analyzer.git
cd realtime_PL_WebGIS_analyzer
pip install -r requirements.txt
```

## Running

Set the serial port:

```python
SERIAL_PORT = "COM5"
```

Run:

```bash
python protection_level_com5_webgis_db.py
```

Open:

http://127.0.0.1:5000/

## License

MIT License
