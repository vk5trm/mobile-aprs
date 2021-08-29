#! /usr/local/bin/python2
#-*- coding: utf-8 -*-

## Copyright 2011, 2012 Philip Crump M0DNY <pdc1g09@ecs.soton.ac.uk>

## GPS control code copied from ukhas wiki: http://ukhas.org.uk/guides:ublox6

## This program is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.

## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.

## You should have received a copy of the GNU General Public License
## along with this program.  If not, see <http://www.gnu.org/licenses/>.

print 'Loading..\n'

## Callsign of beacon <== CHANGE THIS
CALLSIGN = 'M0DNY-1'

# The beacon is only intended to reach an i-Gate, so a very limited path is needed
PATH = 'BEACON via WIDE1-1'
#PATH = 'BEACON via WIDE1-1 WIDE2-1' # Suitable for remote areas

SYMBOL_TABLE = '/' # Primary Symbol Table
SYMBOL = 'l' # Laptop
#SYMBOL = '>' # Car
#SYMBOL = 'U' # Bus

## APRS Comment <== CHANGE THIS
COMMENT = 'Experimental Raspberry Pi APRS-IS Beacon | www.thecraag.com'
COMMENT_PERIOD = 20 # Period of sending comment in minutes

BEACON_PERIOD = 6 # Period of sending position beacon in minutes

## Adds a timestamp to location data, only useful in very high network latency
## or low GPS signal environments.
TIMESTAMP = False # Not yet implemented

## Only valid for short (compressed) packets, this disables course/speed in favour of
## altitude reporting.
ALTITUDE = False # WARNING: Currently bugged. (Value reported is too large.)

## This outputs to file (for aprx beacon) instead of calling system 'beacon'
APRX = False # Standalone beacon by default
APRX_PATH = 'beacon.txt'

## Log to file - NOT YET IMPLEMENTED
## This will provide a log of all happennings (beacons, loss of gps, regain of gps,..)
LOG_PATH = './beacon_log.txt'

## This enables extra stdout outputs for bug-hunting.
DEBUG = True

# Make sure the comment is sent 'at least' once every comment_period
REAL_COMMENT_PERIOD = (COMMENT_PERIOD - BEACON_PERIOD) + 1 # add 1 minute

import gps, os, time, math, threading, re, serial, socket

UDP = True

UDP_ADDRESS = "srvr.aprs-is.net"
UDP_PORT = 8080

PASSWORD = "00000"

# Function to encode (shorten) the lat,lon values in the packets
def latlon_encode(value):
   div1 = math.floor(value/math.pow(91,3))
   remainder1 = math.fmod(value, math.pow(91,3))
   div2 = math.floor(remainder1/math.pow(91,2))
   remainder2 = math.fmod(remainder1, math.pow(91,2))
   div3 = math.floor(remainder2/91)
   remainder3 = math.fmod(remainder2, 91)
   return chr(math.trunc(div1+33)) + chr(math.trunc(div2+33)) + chr(math.trunc(div3+33)) + chr(math.trunc(remainder3+33))

# Thread that keeps a connection to gpsd, holds current GPS state dictionary
class GpsPoller(threading.Thread):
   def __init__(self):
       	threading.Thread.__init__(self)
       	self.ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
	self.ser.write("$PUBX,40,GLL,0,0,0,0*5C\n");
	self.ser.write("$PUBX,40,GGA,0,0,0,0*5A\n");
	self.ser.write("$PUBX,40,GSA,0,0,0,0*4E\n");
	self.ser.write("$PUBX,40,RMC,0,0,0,0*47\n");
	self.ser.write("$PUBX,40,GSV,0,0,0,0*59\n");
	self.ser.write("$PUBX,40,VTG,0,0,0,0*5E\n");
	## TODO: Set Nav Mode airborne 1G --------------------------- TODO
	## Clear buffer
	dummy = self.ser.readline()
	self.current_value = None
	self.stopped = False

   def stop(self): # Stop the thread at the next opportunity
       self.stopped = True

   def get_current_value(self): # Return GPS state dictionary
       return self.current_value

   def run(self):
       try:
            while not self.stopped:
		self.ser.write("$PUBX,00*33\n");
		line = self.ser.readline()
		try:
			self.full_string = line
			fields = line.split(",")
			#time = int(round(float(fields[2])))
			#self.time_hour=(time/10000);
			#self.time_minute=((time-(time_hour*10000))/100);
			#self.time_second=(time-(time_hour*10000)-(time_minute*100));
			#print str(time_hour) + ":" + str(time_minute) + ":" + str(time_second)
          		lat_minutes=int(math.floor(float(fields[3])/100))
          		lat_seconds=float(fields[3])-(lat_minutes*100)
          		self.gps_lat=lat_minutes+(lat_seconds/60.0)
          		if(fields[4]=='S'):
          		        self.gps_lat = -self.gps_lat
          		#print "Latitude: " + str(gps_lat)
          		lon_minutes=int(math.floor(float(fields[5])/100))
          		lon_seconds=float(fields[5])-(lon_minutes*100)
          		self.gps_lon=lon_minutes+(lon_seconds/60.0)
          		if(fields[6]=='W'):
                  		self.gps_lon = -self.gps_lon
          		self.altitude = float(fields[7]) # m
          		self.fix = fields[8] # "G3" = 3D fix
          		self.hacc = float(fields[9]) # m
          		self.vacc = float(fields[10]) # m
     		     	self.speed = float(fields[11]) # km/h
         	 	self.heading = float(fields[12]) # degrees
       		 	self.climb = -float(fields[13]) # m/s
         	 	self.sats = int(fields[18])
			if (abs(self.speed)<3): self.speed = 0
		except ValueError:
			print "Invalid String"
       except StopIteration:
            pass

# Main beacon thread, gets position from GpsPoller
class Beaconer(threading.Thread):
   def __init__(self):
       threading.Thread.__init__(self)
       #logfile = open(LOG_PATH, 'a')
       #logfile.write('Log Opened.')
       self.sock = socket.socket( socket.AF_INET, socket.SOCK_DGRAM ) #Open UDP socket
       self.status = "Starting"
       self.stopped = False
       self.no_gps_timer = 0
       self.beacon_timer = 0
       self.comment_timer = 0
       self.last_beacon = '' # DEBUG
       self.beacon_period = BEACON_PERIOD*60 # convert to seconds
       self.comment_period = REAL_COMMENT_PERIOD*60 # convert to seconds

   def stop(self): # Stop the thread at the next opportunity
       self.stopped = True
       #logfile.write('Log Closed.')
       #logfile.close()

   def update_position(self):
	## Updates this thread's variables from the poller thread
	self.fix = gpsp.fix
	if(gpsp.fix=="G3"):
		self.lat = gpsp.gps_lat
		self.lon = gpsp.gps_lon
		self.alt = gpsp.altitude
		self.hacc = gpsp.hacc
		self.speed = gpsp.speed
		self.heading = gpsp.heading
		self.sats = gpsp.sats
		self.full_string = gpsp.full_string

   def get_last_beacon(self): # Provides a timer since last beacon sent
       return self.beacon_timer

   def get_last_comment(self): # Provides a timer since last comment sent
       return self.comment_timer

   def get_last_fix(self): # Provides a timer since the last known 3D fix
       return self.no_gps_timer

   def get_fix(self):
	return self.fix

   def get_lat(self):
	return self.lat

   def get_lon(self):
	return self.lon

   def get_debug(self):
	return self.full_string

   def get_beacon_period(self): # Returns the beacon interval in seconds
       return self.beacon_period

   def get_comment_period(self): # Returns the comment interval in seconds
       return self.comment_period

   def get_beacon_debug(self): # Returns last aprs string sent
      return self.last_beacon

   def runbeacon(self):
       if APRX: # APRX - Always send comment
          beacon_string = self.short_beacon()+COMMENT
          self.comment_timer = time.time()
       elif math.trunc(time.time() - self.comment_timer)>=self.comment_period:
          beacon_string = self.short_beacon()+COMMENT+" | Sats: "+str(self.sats)
          self.comment_timer = time.time()
       else:
          beacon_string = self.short_beacon()
       self.last_beacon = beacon_string
       if APRX:
          self.save_beacon(beacon_string)
       elif UDP:
	  self.udp_beacon(beacon_string)
       else:
          self.send_beacon(beacon_string)
       self.beacon_timer = time.time()

   def send_beacon(self,aprs_string): # Sends beacon using system 'beacon'
       system_string = "/usr/sbin/beacon -c "+CALLSIGN+" -d '"+PATH+"' -s sm0 "+re.escape(aprs_string)
       os.system(system_string)
       #logfile.write('Beacon sent: ', aprs_string)

   def udp_beacon(self,aprs_string): # Sends beacon using system 'beacon'
       udp_string = "user " + CALLSIGN + " pass " + PASSWORD + " vers M0DNY-PiBeacon 0.1\n"
       udp_string += CALLSIGN + ">APRS,TCPIP*:" + aprs_string
       self.sock.sendto( udp_string, (UDP_ADDRESS, UDP_PORT))
       #logfile.write('UDP Beacon sent: ', aprs_string)

   def save_beacon(self,aprs_string): # Used for aprx, saves to file
       fout = open(APRX_PATH, "w")
       fout.truncate()
       fout.write(aprs_string)
       fout.close()
       #logfile.write('Beacon saved: ', aprs_string)

   def short_beacon(self): # Compressed beacon format
      if TIMESTAMP:
         aprs_prefix = '/' # According to the APRS spec (Position, timestamp, no messaging)
      else:
         aprs_prefix = '!' # According to the APRS spec (Position, no timestamp, no messaging)
      ## Sort out time strings
      #utctime = str(self.gps_status['time'])
      #day = utctime[8::10]
      #hour = utctime[11:13]
      #minute = utctime[14:16]
      #second = utctime[17:19]
      ## Sort out Latitude and Longitude
      lat_bytes = latlon_encode(380926*(90-self.lat))
      lon_bytes = latlon_encode(190463*(180+self.lon))
      short_packet_string = aprs_prefix + SYMBOL_TABLE
      if TIMESTAMP:
         short_packet_string += hour + minute + second + 'h'
      short_packet_string += lat_bytes + lon_bytes + SYMBOL
      if ALTITUDE:
         type_byte = chr(33+int('00110010',2)) # Set to GGA to enable altitude
         alt_value = math.log(math.trunc(self.alt*3.28))/math.log(1.002)
         alt_div = math.floor(alt_value/91)
         alt_remainder = math.fmod(math.trunc(alt_value), 91)
         alt_bytes = chr(33+math.trunc(alt_div)) + chr(33+math.trunc(alt_remainder))
         short_packet_string += alt_bytes + type_byte
      else:
         type_byte = chr(33+int('00111010',2)) # Set to RMC to enable course/speed
         course_byte = chr(33+math.trunc(self.heading/4))
         speed_byte = chr(33+math.trunc(math.log((self.speed*1.9438)+1)/math.log(1.08)))
         short_packet_string += course_byte + speed_byte + type_byte
      return short_packet_string

   def run(self):
       try:
            time.sleep(1) # Allow gpsd connection time to return status
            while not self.stopped:
		self.update_position()
                if self.fix=='G3': # Do we have a GPS fix?
                      self.no_gps_timer = 0
                      if APRX:
                         self.runbeacon()
                      elif math.trunc(time.time() - self.beacon_timer)>=self.beacon_period:
                         self.runbeacon()
                else: # No GPS fix
                   if self.no_gps_timer==0: self.no_gps_timer = time.time()
                time.sleep(1)
       except StopIteration:
            pass

## Main foreground process, just draws to the screen, grabs info from Beaconer thread
if __name__ == "__main__":
   try:
      gpsp = GpsPoller()
      gpsp.start()
      shout = Beaconer()
      shout.start()
      print "Waiting for GPS..\n"
      time.sleep(2)
      while 1:
         os.system('clear') # Clear the screen
         # Now draw screen depending on GPS status
         if shout.get_fix()=='G3':
            print 'Got 3D Fix!'
            print 'Lat: ', shout.get_lat()
            print 'Lon: ', shout.get_lon()
         elif shout.get_fix()=='Starting': # Beacon thread not yet initialised
            print 'Beacon starting up..'
         else:
	    print 'No fix.'
         print ''
         if shout.get_last_beacon()==0: # No beacon yet sent
            print 'No beacon yet sent.'
         else:
            if APRX:
               print APRX_PATH, ' updated ', math.trunc(time.time() - shout.get_last_beacon()), 'seconds ago.'
            else:
               print 'Beacon timer: ', math.trunc(time.time() - shout.get_last_beacon()), '/', shout.get_beacon_period(), ' seconds.'
         print ''
         print 'Current comment: ', COMMENT
         if shout.get_last_comment()==0: # No comment yet sent
            print 'No comment yet sent'
         else:
            if APRX==False: ## Can I do this with '!APRS' ?
               print 'Comment timer: ', math.trunc((time.time() - shout.get_last_comment())/60), '/', shout.get_comment_period()/60, ' minutes.'
         if DEBUG:
            #print shout.get_beacon_debug()
            #print shout.get_last_beacon()
	    print shout.get_debug()
         time.sleep(1) # Give the CPU some time to breathe
   except KeyboardInterrupt: # Catch Ctrl+C, stop both threads
      shout.stop()
      gpsp.stop()
      pass
