from xl.nls import gettext as _
from xl import providers, event
from xl.hal import Handler
from xl.devices import Device
import logging
logger = logging.getLogger(__name__)

PROVIDER = None

import dbus, threading, os, struct
from fcntl import ioctl
from xl import playlist, track, common, transcoder
from xl import settings
import os.path

try:
    import DiscID, CDDB
    CDDB_AVAIL=True
except:
    CDDB_AVAIL=False

class NoCddbError(Exception):
    pass

TOC_HEADER_FMT = 'BB'
TOC_ENTRY_FMT = 'BBBix'
ADDR_FMT = 'BBB' + 'x' * (struct.calcsize('i') - 3)
CDROMREADTOCHDR = 0x5305
CDROMREADTOCENTRY = 0x5306
CDROM_LEADOUT = 0xAA
CDROM_MSF = 0x02
CDROM_DATA_TRACK = 0x04

def enable(exaile):
    global PROVIDER
    PROVIDER = CDHandler()
    providers.register("hal", PROVIDER)


def disable(exaile):
    global PROVIDER
    providers.unregister("hal", PROVIDER)
    PROVIDER = None

class CDTocParser(object):
    #based on code from http://carey.geek.nz/code/python-cdrom/cdtoc.py
    def __init__(self, device):
        self.device = device

        # raw_tracks becomes a list of tuples in the form
        # (track number, minutes, seconds, frames, total frames, data)
        # minutes, seconds, frames and total trames are absolute offsets
        # data is 1 if this is a track containing data, 0 if audio
        self.raw_tracks = []

        self.read_toc()

    def read_toc(self):
        fd = os.open(self.device, os.O_RDONLY)
        toc_header = struct.pack(TOC_HEADER_FMT, 0, 0)
        toc_header = ioctl(fd, CDROMREADTOCHDR, toc_header)
        start, end = struct.unpack(TOC_HEADER_FMT, toc_header)

        self.raw_tracks = []

        for trnum in range(start, end + 1) + [CDROM_LEADOUT]:
            entry = struct.pack(TOC_ENTRY_FMT, trnum, 0, CDROM_MSF, 0)
            entry = ioctl(fd, CDROMREADTOCENTRY, entry)
            track, adrctrl, format, addr = struct.unpack(TOC_ENTRY_FMT, entry)
            m, s, f = struct.unpack(ADDR_FMT, struct.pack('i', addr))
                
            adr = adrctrl & 0xf
            ctrl = (adrctrl & 0xf0) >> 4

            data = 0
            if ctrl & CDROM_DATA_TRACK:
                data = 1

            self.raw_tracks.append( (track, m, s, f, (m*60+s) * 75 + f, data) ) 

    def get_raw_info(self):
        return self.raw_tracks[:]

    def get_track_lengths(self):
        offset = self.raw_tracks[0][4]
        lengths = []
        for track in self.raw_tracks[1:]:
            lengths.append((track[4]-offset)/75)
            offset = track[4]
        return lengths

class CDPlaylist(playlist.Playlist):
    def __init__(self, name=_("Audio Disc"), device=None):
        playlist.Playlist.__init__(self, name=name)

        if not device:
            self.device = "/dev/cdrom"
        else:
            self.device = device
        
        self.open_disc()

    def open_disc(self):

        toc = CDTocParser(self.device)
        lengths = toc.get_track_lengths()

        songs = {}
        
        for count, length in enumerate(lengths):
            count += 1
            song = track.Track()
            song.set_loc("cdda://%d#%s" % (count, self.device))
            song['title'] = "Track %d" % count
            song['tracknumber'] = count
            song['__length'] = length
            songs[song.get_loc()] = song

        sort_tups = [ (int(s['tracknumber'][0]),s) for s in songs.values() ]
        sort_tups.sort()

        sorted = [ s[1] for s in sort_tups ]

        self.add_tracks(sorted)

        if CDDB_AVAIL:
            self.get_cddb_info()
    
    @common.threaded
    def get_cddb_info(self):
        try:
            disc = DiscID.open(self.device)
            self.info = DiscID.disc_id(disc) 
            status, info = CDDB.query(self.info)
        except IOError:
            return

        if status in (210, 211):
            info = info[0]
            status = 200
        if status != 200:
            return
        
        (status, info) = CDDB.read(info['category'], info['disc_id'])
        
        title = info['DTITLE'].split(" / ")
        for i in range(self.info[1]):
            self.ordered_tracks[i]['title'] = \
                    info['TTITLE' + `i`].decode('iso-8859-15', 'replace')
            self.ordered_tracks[i]['album'] = \
                    title[1].decode('iso-8859-15', 'replace')
            self.ordered_tracks[i]['artist'] = \
                    title[0].decode('iso-8859-15', 'replace')
            self.ordered_tracks[i]['year'] = \
                    info['EXTD'].replace("YEAR: ", "")
            self.ordered_tracks[i]['genre'] = \
                    info['DGENRE']

        self.set_name(title[1].decode('iso-8859-15', 'replace'))
        event.log_event('cddb_info_retrieved', self, True)

class CDDevice(Device):
    """
        represents a CD
    """
    class_autoconnect = True

    def __init__(self, dev="/dev/cdrom"):
        Device.__init__(self, dev)
        self.name = _("Audio Disc")
        self.dev = dev

    def _get_panel_type(self):
        import sys
        sys.path.append(os.path.dirname(__file__))
        # exaile won't call this method unless the gui is running, so it's ok
        # to import gui code here
        try:
            import _cdguipanel
            return _cdguipanel.CDPanel 
        except ImportError:
            return 'flatplaylist'
        except:
            # something horrible went wrong
            import traceback
            traceback.print_exc()
            return 'flatplaylist'
        finally:
            sys.path.pop()

    panel_type = property(_get_panel_type)

    def connect(self):
        cdpl = CDPlaylist(device=self.dev)
        self.playlists.append(cdpl)
        self.connected = True

    def disconnect(self):
        self.playlists = []
        self.connected = False

class CDHandler(Handler):
    name = "cd"
    def is_type(self, device, capabilities):
        if "volume.disc" in capabilities:
            return True
        return False

    def get_udis(self, hal):
        udis = hal.hal.FindDeviceByCapability("volume.disc")
        return udis

    def device_from_udi(self, hal, udi):
        cd_obj = hal.bus.get_object("org.freedesktop.Hal", udi)
        cd = dbus.Interface(cd_obj, "org.freedesktop.Hal.Device")
        if not cd.GetProperty("volume.disc.has_audio"):
            return

        device = str(cd.GetProperty("block.device"))

        cddev = CDDevice(dev=device)

        return cddev

class CDImporter(object):
    def __init__(self, tracks):
        self.tracks = [ t for t in tracks if 
                t.get_loc_for_io().startswith("cdda") ]
        self.duration = float(sum( [ t['__length'] for t in self.tracks ] ))
        self.transcoder = transcoder.Transcoder()
        self.current = None
        self.progress = 0.0

        self.running = False

        self.outpath = settings.get_option("cd_import/outpath", 
                "%s/${artist}/${album}/${tracknumber} - ${title}" % \
                os.getenv("HOME"))

        self.format = settings.get_option("cd_import/format",
                                "Ogg Vorbis")
        self.quality = settings.get_option("cd_import/quality", -1)

        self.cont = None

    @common.threaded
    def do_import(self):
        self.running = True

        self.cont = threading.Event()

        self.transcoder.set_format(self.format)
        if self.quality != -1:
            self.transcoder.set_quality(self.quality)
        self.transcoder.end_cb = self._end_cb

        for tr in self.tracks:
            self.cont.clear()
            self.current = tr
            loc = tr.get_loc_for_io()
            track, device = loc[7:].split("#")
            src = "cdparanoiasrc track=%s device=\"%s\""%(track, device)
            self.transcoder.set_raw_input(src)
            self.transcoder.set_output(self.get_output_location(tr))
            self.transcoder.start_transcode()
            self.cont.wait()
            if not self.running:
                break
            tr.set_loc(self.get_output_location(tr))
            tr.write_tags()
            tr.read_tags() # make sure everything is reloaded nicely
            try:
                incr = tr['__length'] / self.duration
                self.progress += incr
            except:
                pass
        self.progress = 100.0

    def _end_cb(self):
        self.cont.set()

    def get_output_location(self, tr):
        parts = self.outpath.split(os.sep)
        parts2 = []
        replacedict = {}
        for tag in common.VALID_TAGS:
            replacedict["${%s}"%tag] = tag
        for part in parts:
            for k, v in replacedict.iteritems():
                val = tr[v]
                if type(val) in (list, tuple):
                    val = u" & ".join(val) 
                part = part.replace(k, str(val))
            part = part.replace(os.sep, "") # strip os.sep
            parts2.append(part)
        dirpath = "/" + os.path.join(*parts2[:-1]) 
        if not os.path.exists(dirpath):
            os.makedirs(dirpath)
        ext = transcoder.FORMATS[self.transcoder.dest_format]['extension']
        path = "/" + os.path.join(*parts2) + "." + ext
        return path

    def stop(self):
        self.running = False
        self.transcoder.stop()

    def get_progress(self):
        incr = self.current['__length'] / self.duration
        pos = self.transcoder.get_time()/float(self.current['__length'])
        return self.progress + pos*incr

# vim: et sts=4 sw=4



