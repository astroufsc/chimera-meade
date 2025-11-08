# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2006-present Paulo Henrique Silva <ph.silva@gmail.com>

import datetime as dt
import os
import pickle
import threading
import time

import serial
from chimera.core.constants import SYSTEM_CONFIG_DIRECTORY
from chimera.core.exceptions import ChimeraException, ObjectNotFoundException
from chimera.core.lock import lock
from chimera.instruments.telescope import TelescopeBase
from chimera.interfaces.telescope import AlignMode, TelescopeStatus
from chimera.util.coord import Coord
from chimera.util.enum import Enum
from chimera.util.position import Epoch, Position

Direction = Enum("E", "W", "N", "S")
SlewRate = Enum("GUIDE", "CENTER", "FIND", "MAX")


class MeadeException(ChimeraException):
    pass


class Meade(TelescopeBase):
    __config__ = {"azimuth180Correct": True}

    def __init__(self):
        super().__init__()

        self._tty = None
        self._slewRate = None
        self._abort = threading.Event()
        self._slewing = False

        self._errorNo = 0
        self._errorString = ""

        self._lastAlignMode = None
        self._parked = False

        self._target_az = None
        self._target_alt = None

        # debug log
        self._debugLog = None
        try:
            self._debugLog = open(
                os.path.join(SYSTEM_CONFIG_DIRECTORY, "meade-debug.log"), "w"
            )
        except OSError as e:
            self.log.warning("Could not create meade debug file (%s)" % str(e))

        # how much arcseconds / second for every slew rate
        # and direction
        self._calibration: dict[SlewRate, dict[Direction, int]] = {}
        self._calibration_time = 5.0
        self._calibrationFile = os.path.join(
            SYSTEM_CONFIG_DIRECTORY, "move_calibration.bin"
        )

        for rate in SlewRate:
            self._calibration[rate] = {}
            for direction in Direction:
                self._calibration[rate][direction] = 1

    # -- ILifeCycle implementation --

    def __start__(self):
        self.open()

        # try to read saved calibration data
        if os.path.exists(self._calibrationFile):
            try:
                self._calibration = pickle.loads(open(self._calibrationFile).read())
                self.calibrated = True
            except Exception as e:
                self.log.warning("Problems reading calibration persisted data (%s)" % e)

        return True

    def __stop__(self):
        if self.is_slewing():
            self.abort_slew()

        self.close()

    def __main__(self):
        pass

    # -- ITelescope implementation

    def _check_meade(self):
        tmp = self._tty.timeout
        self._tty.timeout = 5

        align = self.get_align_mode()

        self._tty.timeout = tmp

        if align < 0:
            raise MeadeException(
                "Couldn't find a Meade telescope on '%s'." % self["device"]
            )

        return True

    def _init_telescope(self):
        self.set_align_mode(self["align_mode"])

        # activate HPP (high precision poiting). We really need this!!
        self._set_high_precision()

        # set default slew rate
        self.set_slew_rate(self["slew_rate"])

        try:
            site = self.getManager().getProxy("/Site/0")

            self.set_lat(site["latitude"])
            self.set_long(site["longitude"])
            self.set_local_time(dt.datetime.now().time())
            self.set_utc_offset(site.utcoffset())
            self.set_date(dt.date.today())
        except ObjectNotFoundException:
            self.log.warning(
                "Cannot initialize telescope. "
                "Site object not available. Telescope"
                " attitude cannot be determined."
            )

    @lock
    def open(self):
        self._tty = serial.Serial(
            self["device"],
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self["timeout"],
            xonxoff=False,
            rtscts=False,
        )

        try:
            self._tty.open()

            self._check_meade()

            # if self["auto_align"]:
            #    self.autoAlign ()

            # manualy initialize scope
            if self["skip_init"]:
                self.log.info("Skipping init as requested.")
            else:
                self._init_telescope()

            return True

        except (OSError, serial.SerialException):
            raise MeadeException("Error while opening %s." % self["device"])

    @lock
    def close(self):
        if self._tty.isOpen():
            self._tty.close()
            return True
        else:
            return False

    # --

    @lock
    def auto_align(self):
        self._write(":Aa#")

        while not self._tty.inWaiting():
            time.sleep(1)

        # FIXME: bad LX200 behaviour
        # tmp = self._read(1)

        return True

    @lock
    def get_align_mode(self):
        self._write("\x06")  # ACK

        ret = self._read(1)

        # damn stupid '0' at the start of the mode
        if ret == "0":
            ret = self._read(1, flush=False)

        if not ret or ret not in "APL":
            raise MeadeException("Couldn't get the alignment mode. Is this a Meade??")

        if ret == "A":
            return AlignMode.ALT_AZ
        elif ret == "P":
            return AlignMode.POLAR
        elif ret == "L":
            return AlignMode.LAND

    @lock
    def set_align_mode(self, mode):
        if mode == self.get_align_mode():
            return True

        if mode == AlignMode.ALT_AZ:
            self._write(":AA#")
        elif mode == AlignMode.POLAR:
            self._write(":AP#")
        elif mode == AlignMode.LAND:
            self._write(":AL#")

        self._readbool()

        return True

    @lock
    def slew_to_ra_dec(self, position):
        position = position.toEpoch(Epoch.NOW)

        self._validateRaDec(position)

        if self.is_slewing():
            # never should happens 'cause @lock
            raise MeadeException("Telescope already slewing.")

        self.set_target_ra_dec(position.ra, position.dec)

        status = TelescopeStatus.OK

        try:
            status = self._slew_to_ra_dec()
            return True
        finally:
            self.slewComplete(self.get_position_ra_dec(), status)

        return False

    def _slew_to_ra_dec(self):
        self._slewing = True
        self._abort.clear()

        # slew
        self._write(":MS#")

        # to handle timeout
        start_time = time.time()

        err = self._readbool()

        if err:
            # check error message
            msg = self._readline()
            self._slewing = False
            raise MeadeException(msg[:-1])

        # slew possible
        target = self.get_target_ra_dec()

        return self._wait_slew(start_time, target)

    @lock
    def slew_to_alt_az(self, position):
        self._validateAltAz(position)

        self.set_slew_rate(self["slew_rate"])

        if self.is_slewing():
            # never should happens 'cause @lock
            raise MeadeException("Telescope already slewing.")

        last_align_mode = self.get_align_mode()

        self.set_target_alt_az(position.alt, position.az)

        status = TelescopeStatus.OK

        try:
            self.set_align_mode(AlignMode.ALT_AZ)
            status = self._slew_to_alt_az()
            return True
        finally:
            self.slewComplete(self.get_position_ra_dec(), status)
            self.set_align_mode(last_align_mode)

        return False

    def _slew_to_alt_az(self):
        self._slewing = True
        self._abort.clear()

        # slew
        self._write(":MA#")

        # to handle timeout
        start_time = time.time()

        err = self._readbool()

        if err:
            # check error message
            self._slewing = False
            raise MeadeException(
                "Couldn't slew to ALT/AZ: '%s'." % self.get_target_alt_az()
            )

        # slew possible
        target = self.get_target_alt_az()

        return self._wait_slew(start_time, target, local=True)

    def _wait_slew(self, start_time, target, local=False):
        self.slewBegin(target)

        while True:
            # check slew abort event
            if self._abort.isSet():
                self._slewing = False
                return TelescopeStatus.ABORTED

            # check timeout
            if time.time() >= (start_time + self["max_slew_time"]):
                self.abort_slew()
                self._slewing = False
                raise MeadeException("Slew aborted. Max slew time reached.")

            if local:
                position = self.get_position_alt_az()
            else:
                position = self.get_position_ra_dec()

            if target.within(position, eps=Coord.fromAS(60)):
                time.sleep(self["stabilization_time"])
                self._slewing = False
                return TelescopeStatus.OK

            time.sleep(self["slew_idle_time"])

        return TelescopeStatus.ERROR

    def abort_slew(self):
        if not self.is_slewing():
            return True

        self._abort.set()

        self.stop_move_all()

        time.sleep(self["stabilization_time"])

    def is_slewing(self):
        return self._slewing

    def _move(self, direction, duration=1.0, slew_rate=None):
        if slew_rate is None:
            slew_rate = SlewRate.GUIDE

        if duration <= 0:
            raise ValueError("Slew duration cannot be less than 0.")

        # FIXME: concurrent slew commands? YES.. it should works!
        if self.is_slewing():
            # REALLY? no.
            raise MeadeException("Telescope is slewing. Cannot move.")

        if slew_rate:
            self.set_slew_rate(slew_rate)

        start_pos = self.get_position_ra_dec()

        self._slewing = True
        self._write(":M%s#" % str(direction).lower())

        start = time.time()
        finish = start + duration

        self.log.debug("[move] delta: %f s" % (finish - start,))

        while time.time() < finish:
            pass  # busy wait!

        # FIXME: slew limits
        self._stop_move(direction)
        self._slewing = False

        def calc_delta(start, end):
            return Coord.fromD(end.angsep(start))

        delta = calc_delta(start_pos, self.get_position_ra_dec())
        self.log.debug("[move] moved %f arcsec" % delta.AS)

        return True

    def _stop_move(self, direction):
        self._write(":Q%s#" % str(direction).lower())

        rate = self.get_slew_rate()
        # FIXME: stabilization time depends on the slewRate!!!
        if rate == SlewRate.GUIDE:
            time.sleep(0.1)
            return True

        elif rate == SlewRate.CENTER:
            time.sleep(0.2)
            return True

        elif rate == SlewRate.FIND:
            time.sleep(0.3)
            return True

        elif rate == SlewRate.MAX:
            time.sleep(0.4)
            return True

    def is_move_calibrated(self):
        return os.path.exists(self._calibrationFile)

    @lock
    def calibrate_move(self):
        # FIXME: move to a safe zone to do calibrations.
        def calc_delta(start, end):
            return end.angsep(start)

        def calibrate(direction, rate):
            start = self.get_position_ra_dec()
            self._move(direction, self._calibration_time, rate)
            end = self.get_position_ra_dec()

            return calc_delta(start, end)

        for rate in SlewRate:
            for direction in Direction:
                self.log.debug("Calibrating %s %s" % (rate, direction))

                total = 0

                for i in range(2):
                    total += calibrate(direction, rate).AS

                self.log.debug("> %f" % (total / 2.0))
                self._calibration[rate][direction] = total / 2.0

        # save calibration
        try:
            f = open(self._calibrationFile, "w")
            f.write(pickle.dumps(self._calibration))
            f.close()
        except Exception as e:
            self.log.warning("Problems persisting calibration data. (%s)" % e)

        self.log.info("Calibration was OK.")

    def _calc_duration(self, arc, direction, rate):
        """
        Calculates the time spent (returned number) to move by arc in a
        given direction at a given rate
        """

        if not self.is_move_calibrated():
            self.log.info("Telescope fine movement not calibrated. Calibrating now...")
            self.calibrate_move()

        self.log.debug("[move] asked for %s arcsec" % float(arc))

        return arc * (self._calibration_time / self._calibration[rate][direction])

    @lock
    def move_east(self, offset, slew_rate=None):
        return self._move(
            Direction.E, self._calc_duration(offset, Direction.E, slew_rate), slew_rate
        )

    @lock
    def move_west(self, offset, slew_rate=None):
        return self._move(
            Direction.W, self._calc_duration(offset, Direction.W, slew_rate), slew_rate
        )

    @lock
    def move_north(self, offset, slew_rate=None):
        return self._move(
            Direction.N, self._calc_duration(offset, Direction.N, slew_rate), slew_rate
        )

    @lock
    def move_south(self, offset, slew_rate=None):
        return self._move(
            Direction.S, self._calc_duration(offset, Direction.S, slew_rate), slew_rate
        )

    @lock
    def stop_move_east(self):
        return self._stop_move(Direction.E)

    @lock
    def stop_move_west(self):
        return self._stop_move(Direction.W)

    @lock
    def stop_move_north(self):
        return self._stop_move(Direction.N)

    @lock
    def stop_move_south(self):
        return self._stop_move(Direction.S)

    @lock
    def stop_move_all(self):
        self._write(":Q#")
        return True

    @lock
    def get_ra(self):
        self._write(":GR#")
        ret = self._readline()

        # meade bugs: sometimes, after use Move commands, getRa
        # returns a 1 before the RA, so we just check this and discard
        # it here
        if len(ret) > 9:
            ret = ret[1:]

        return Coord.fromHMS(ret[:-1])

    @lock
    def get_dec(self):
        self._write(":GD#")
        ret = self._readline()

        # meade bugs: same as getRa
        if len(ret) > 10:
            ret = ret[1:]

        ret = ret.replace("\xdf", ":")

        return Coord.fromDMS(ret[:-1])

    @lock
    def get_position_ra_dec(self):
        return Position.fromRaDec(self.get_ra(), self.get_dec())

    @lock
    def get_position_alt_az(self):
        return Position.fromAltAz(self.get_alt(), self.get_az())

    @lock
    def get_target_ra_dec(self):
        return Position.fromRaDec(self.get_target_ra(), self.get_target_dec())

    @lock
    def get_target_alt_az(self):
        return Position.fromAltAz(self.get_target_alt(), self.get_target_az())

    @lock
    def set_target_ra_dec(self, ra, dec):
        self.set_target_ra(ra)
        self.set_target_dec(dec)

        return True

    @lock
    def set_target_alt_az(self, alt, az):
        self.set_target_az(az)
        self.set_target_alt(alt)

        return True

    @lock
    def get_target_ra(self):
        self._write(":Gr#")
        ret = self._readline()

        return Coord.fromHMS(ret[:-1])

    @lock
    def set_target_ra(self, ra):
        if not isinstance(ra, Coord):
            ra = Coord.fromHMS(ra)

        self._write(":Sr%s#" % ra.strfcoord("%(h)02d\xdf%(m)02d:%(s)02d"))

        ret = self._readbool()

        if not ret:
            raise MeadeException("Invalid RA '%s'" % ra)

        return True

    @lock
    def set_target_dec(self, dec):
        if not isinstance(dec, Coord):
            dec = Coord.fromDMS(dec)

        self._write(":Sd%s#" % dec.strfcoord("%(d)02d\xdf%(m)02d:%(s)02d"))

        ret = self._readbool()

        if not ret:
            raise MeadeException("Invalid DEC '%s'" % dec)

        return True

    @lock
    def get_target_dec(self):
        self._write(":Gd#")
        ret = self._readline()

        ret = ret.replace("\xdf", ":")

        return Coord.fromDMS(ret[:-1])

    @lock
    def get_az(self):
        self._write(":GZ#")
        ret = self._readline()
        ret = ret.replace("\xdf", ":")

        c = Coord.fromDMS(ret[:-1])

        if self["azimuth180Correct"]:
            if c.toD() >= 180:
                c = c - Coord.fromD(180)
            else:
                c = c + Coord.fromD(180)

        return c

    @lock
    def get_alt(self):
        self._write(":GA#")
        ret = self._readline()
        ret = ret.replace("\xdf", ":")

        return Coord.fromDMS(ret[:-1])

    def get_target_alt(self):
        return self._target_alt

    @lock
    def set_target_alt(self, alt):
        if not isinstance(alt, Coord):
            alt = Coord.fromD(alt)

        self._write(":Sa%s#" % alt.strfcoord("%(d)02d\xdf%(m)02d'%(s)02d"))

        ret = self._readbool()

        if not ret:
            raise MeadeException("Invalid Altitude '%s'" % alt)

        self._target_alt = alt

        return True

    def get_target_az(self):
        return self._target_az

    @lock
    def set_target_az(self, az):
        if not isinstance(az, Coord):
            az = Coord.fromDMS(az)

        if self["azimuth180Correct"]:
            if az.toD() >= 180:
                az = az - Coord.fromD(180)
            else:
                az = az + Coord.fromD(180)

        self._write(":Sz%s#" % az.strfcoord("%(d)03d\xdf%(m)02d:%(s)02d", signed=False))

        ret = self._readbool()

        if not ret:
            raise MeadeException(
                "Invalid Azimuth '%s'" % az.strfcoord("%(d)03d\xdf%(m)02d")
            )

        self._target_az = az

        return True

    @lock
    def get_lat(self):
        self._write(":Gt#")
        ret = self._readline()
        ret = ret.replace("\xdf", ":")[:-1]

        return Coord.fromDMS(ret)

    @lock
    def set_lat(self, lat):
        if not isinstance(lat, Coord):
            lat = Coord.fromDMS(lat)

        lat_str = lat.strfcoord("%(d)02d\xdf%(m)02d")

        self._write(":St%s#" % lat_str)

        ret = self._readbool()

        if not ret:
            raise MeadeException("Invalid Latitude '%s' ('%s')" % (lat, lat_str))

        return True

    @lock
    def get_long(self):
        self._write(":Gg#")
        ret = self._readline()
        ret = ret.replace("\xdf", ":")[:-1]

        return Coord.fromDMS(ret)

    @lock
    def set_long(self, coord):
        if not isinstance(coord, Coord):
            coord = Coord.fromDMS(coord)

        self._write(":Sg%s#" % coord.strfcoord("%(d)03d\xdf%(m)02d"))

        ret = self._readbool()

        if not ret:
            raise MeadeException("Invalid Longitude '%s'" % int)

        return True

    @lock
    def get_date(self):
        self._write(":GC#")
        ret = self._readline()
        return dt.datetime.strptime(ret[:-1], "%m/%d/%y").date()

    @lock
    def set_date(self, date):
        if type(date) == float:
            date = dt.date.fromtimestamp(date)

        self._write(":SC%s#" % date.strftime("%m/%d/%y"))

        ret = self._read(1)

        if ret == "0":
            # discard junk null byte
            self._read(1)
            raise MeadeException("Couldn't set date, invalid format '%s'" % date)

        elif ret == "1":
            # discard junk message and wait Meade finish update of internal
            # databases
            tmp_timeout = self._tty.timeout
            self._tty.timeout = 60
            self._readline()  # junk message

            self._readline()

            self._tty.timeout = tmp_timeout
            return True

    @lock
    def get_local_time(self):
        self._write(":GL#")
        ret = self._readline()
        return dt.datetime.strptime(ret[:-1], "%H:%M:%S").time()

    @lock
    def set_local_time(self, local):
        if type(local) == float:
            local = dt.datetime.fromtimestamp(local).time()

        self._write(":SL%s#" % local.strftime("%H:%M:%S"))

        ret = self._readbool()

        if not ret:
            raise MeadeException("Invalid local time '%s'." % local)

        return True

    @lock
    def get_local_sidereal_time(self):
        self._write(":GS#")
        ret = self._readline()
        return dt.datetime.strptime(ret[:-1], "%H:%M:%S").time()

    @lock
    def set_local_sidereal_time(self, local):
        self._write(":SS%s#" % local.strftime("%H:%M:%S"))

        ret = self._readbool()

        if not ret:
            raise MeadeException("Invalid Local sidereal time '%s'." % local)

        return True

    @lock
    def get_utc_offset(self):
        self._write(":GG#")
        ret = self._readline()
        return ret[:-1]

    @lock
    def set_utc_offset(self, offset):
        offset = "%+02.1f" % offset

        self._write(":SG%s#" % offset)

        ret = self._readbool()

        if not ret:
            raise MeadeException("Invalid UTC offset '%s'." % offset)

        return True

    @lock
    def get_current_tracking_rate(self):
        self._write(":GT#")

        ret = self._readline()

        if not ret:
            raise MeadeException("Couldn't get the tracking rate")

        ret = float(ret[:-1])

        return ret

    @lock
    def set_current_tracking_rate(self, trk):
        trk = "%02.1f" % trk

        if len(trk) == 3:
            trk = "0" + trk

        self._write(":ST%s#" % trk)

        ret = self._readbool()

        if not ret:
            raise MeadeException("Invalid tracking rate '%s'." % trk)

        self._write(":TM#")

        return ret

    @lock
    def start_tracking(self):
        if self.get_align_mode() in (AlignMode.POLAR, AlignMode.ALT_AZ):
            return True

        self.set_align_mode(self._lastAlignMode)
        return True

    @lock
    def stop_tracking(self):
        if self.get_align_mode() == AlignMode.LAND:
            return True

        self._lastAlignMode = self.get_align_mode()
        self.set_align_mode(AlignMode.LAND)
        return True

    def is_tracking(self):
        if self.get_align_mode() != AlignMode.LAND:
            return True

        return False

    def _set_high_precision(self):
        self._write(":GR#")
        ret = self._readline()[:-1]

        if len(ret) == 7:  # low precision
            self._write(":U#")

        return True

    # -- ITelescopeSync implementation --

    @lock
    def sync_ra_dec(self, position):
        self.set_target_ra_dec(position.ra, position.dec)

        self._write(":CM#")

        ret = self._readline()

        if not ret:
            raise MeadeException(
                "Error syncing on '%s' '%s'." % (position.ra, position.dec)
            )

        self.syncComplete(self.get_position_ra_dec())

        return True

    @lock
    def set_slew_rate(self, rate):
        if rate == SlewRate.GUIDE:
            self._write(":RG#")
        elif rate == SlewRate.CENTER:
            self._write(":RC#")
        elif rate == SlewRate.FIND:
            self._write(":RM#")
        elif rate == SlewRate.MAX:
            self._write(":Sw4#")
            if not self._readbool():
                raise ValueError("Invalid slew rate")

            self._write(":RS#")
        else:
            raise ValueError("Invalid slew rate '%s'." % rate)

        self._slewRate = rate

        return True

    def get_slew_rate(self):
        return self._slewRate

    # -- park

    def get_park_position(self):
        return Position.fromAltAz(self["park_position_alt"], self["park_position_az"])

    @lock
    def set_park_position(self, position):
        self["park_position_az"], self["park_position_alt"] = position.D

        return True

    def is_parked(self):
        return self._parked

    @lock
    def park(self):
        if self.is_parked():
            return True

        # 1. slew to park position FIXME: allow different park
        # positions and conversions from ra/dec -> az/alt

        site = self.getManager().getProxy("/Site/0")

        self.slew_to_ra_dec(
            Position.fromRaDec(str(self.get_local_sidereal_time()), site["latitude"])
        )

        # 2. stop tracking
        self.stop_tracking()

        # 3. power off
        # self.powerOff ()

        self._parked = True

        self.parkComplete()

        return True

    @lock
    def unpark(self):
        if not self.is_parked():
            return True

        # 1. power on
        # self.powerOn ()

        # 2. start tracking
        self.start_tracking()

        # 3. set location, date and time
        self._init_telescope()

        # 4. sync on park position (not really necessary when parking
        # on DEC=0, RA=LST

        # convert from park position to RA/DEC using the last LST set on 2.
        # ra = 0
        # dec = 0

        # if not self.sync (ra, dec):
        #    return False

        self.unparkComplete()

        self._parked = False

        return True

    # low-level
    def _debug(self, msg):
        if self._debugLog:
            print(
                f"{time.time()} {threading.currentThread().getName()} {msg}",
                file=self._debugLog,
            )
            self._debugLog.flush()

    def _read(self, n=1, flush=True):
        if not self._tty.isOpen():
            raise OSError("Device not open")

        if flush:
            self._tty.flushInput()

        ret = self._tty.read(n)
        self._debug("[read ] %s" % repr(ret))
        return ret

    def _readline(self, eol="#"):
        if not self._tty.isOpen():
            raise OSError("Device not open")

        ret = self._tty.readline(None, eol)
        self._debug("[read ] %s" % repr(ret))
        return ret

    def _readbool(self):
        try:
            ret = int(self._read(1))
        except ValueError:
            return False

        if not ret:
            return False

        return True

    def _write(self, data, flush=True):
        if not self._tty.isOpen():
            raise OSError("Device not open")

        if flush:
            self._tty.flushOutput()

        self._debug("[write] %s" % repr(data))

        return self._tty.write(data)
