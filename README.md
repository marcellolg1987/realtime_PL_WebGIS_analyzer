# realtime_PL_WebGIS_analyzer

This structure allow to test in Python language the real-time WebGIS fruition of Horizontal protection-level calculation from nmea GNSS IoT low-cost hardware. 
The file "strait_of_messina.nmea" provides a sample test of nmea acquisition, and the "protection_level_db2.py" provides the Python code necessary to load the nmea strings, calculate the horizontal protection level for each acquisition and send the results to a unity of storage (Postgres DB) and to a WebGIS visualizer. 
The file index3.html provides the WebGIS structure developed with Leaflet opensource JavaScript libraries, allowwing the real time visualization of the position provided by the nmea file, the radius of the protection level of the corresponding last acquisition and the table of the historical acquisitions stored in the Postgres DB.
The system requires the installation of Postgres DB, Apache webserver and Anaconda. 

Enjoy the code!!!
