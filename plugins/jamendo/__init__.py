# Copyright (C) 2009 Erin Drummond
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
#
#
# The developers of the Exaile media player hereby grant permission
# for non-GPL compatible GStreamer and Exaile plugins to be used and
# distributed together with GStreamer and Exaile. This permission is
# above and beyond the permissions granted by the GPL license by which
# Exaile is covered. If you modify this code, you may extend this
# exception to your version of the code, but you are not obligated to
# do so. If you do not wish to do so, delete this exception statement
# from your version.

import gobject
import gtk
import jamtree
import jamapi
import menu
import os
import urllib
import hashlib
from xlgui import guiutil
from xl import track as xltrack
from xl import xdg
from xl import event
from xl import settings
from xl.cover import CoverSearchMethod, NoCoverFoundException
from xlgui import panel

JAMENDO_NOTEBOOK_PAGE = None

def enable(exaile):
    if (exaile.loading):
        event.add_callback(_enable, 'exaile_loaded')
    else:
        _enable(None, exaile, None)

def _enable(eventname, exaile, nothing):
    global JAMENDO_NOTEBOOK_PAGE
    JAMENDO_NOTEBOOK_PAGE = JamendoPanel(exaile.gui.main.window, exaile)
    exaile.gui.add_panel(*JAMENDO_NOTEBOOK_PAGE.get_panel())    
    exaile.covers.add_search_method(JamendoCoverSearch())
    
def disable(exaile):
    global JAMENDO_NOTEBOOK_PAGE
    exaile.gui.remove_panel(JAMENDO_NOTEBOOK_PAGE._child)
    exaile.covers.remove_search_method_by_name('jamendo')
   
class JamendoPanel(panel.Panel):
   
    __gsignals__ = {
        'append-items': (gobject.SIGNAL_RUN_LAST, None, (object,)),
        'download-items': (gobject.SIGNAL_RUN_LAST, None, (object,)),
    }

    ui_info = (os.path.dirname(__file__) + "/glade/jamendo_panel.glade", 'JamendoPanelWindow')    

    def __init__(self, parent, exaile):
        panel.Panel.__init__(self, parent)
        
        self.parent = parent
        self.name = "Jamendo"
        self.exaile = exaile
        
        self.STATUS_READY = "Ready"
        self.STATUS_SEARCHING = "Searching Jamendo catalogue..."
        self.STATUS_RETRIEVING_DATA = "Retrieving song data..."
     
        self.setup_widgets()

    #find out whats selected and add the tracks under it to the playlist
    def add_to_playlist(self):        
        sel = self.get_selected_item()
        if isinstance(sel, jamtree.Artist):
            if not sel.expanded:
                self.expand_artist(sel, True)
                return
                        
            for album in sel.albums:
                if not album.expanded:
                    self.expand_album(album, True)
                    return
            
            for album in sel.albums:
                track_list = []
                for track in album.tracks:
                    track_list.append(track)
                self.add_tracks_to_playlist(track_list)
        
        if isinstance(sel, jamtree.Album):
            if not sel.expanded:
                self.expand_album(sel, True)
                return
            track_list = []
            for track in sel.tracks:
                track_list.append(track)
            self.add_tracks_to_playlist(track_list)
                            
        if isinstance(sel, jamtree.Track):            
            self.add_track_to_playlist(sel)

    #is called when the user wants to download something
    def download_selected(self):
        print('It would be really cool if this worked, unfortunately I still need to implement it.')

    #initialise the widgets
    def setup_widgets(self):
        #connect to the signals we listen for
        self.builder.connect_signals({
            'search_entry_activated' : self.on_search_entry_activated,
            'search_entry_icon_release' : self.clear_search_terms,
            'refresh_button_clicked' : self.on_search_entry_activated,
            'search_combobox_changed' : self.on_search_combobox_changed,
            'ordertype_combobox_changed' : self.on_ordertype_combobox_changed,
            'orderdirection_combobox_changed' : self.on_orderdirection_combobox_changed,
            'results_combobox_changed' : self.on_results_combobox_changed
        })
        
        #set up the rightclick menu
        self.menu = menu.JamendoMenu()
        self.menu.connect('append-items', lambda *e:
            self.emit('append-items', self.add_to_playlist()))
        self.menu.connect('download-items', lambda *e:
            self.emit('download-items', self.download_selected()))
                
        #setup images
        window = gtk.Window()
        self.artist_image = gtk.gdk.pixbuf_new_from_file(xdg.get_data_path("images/artist.png"))
        self.album_image = window.render_icon('gtk-cdrom', gtk.ICON_SIZE_SMALL_TOOLBAR)
        self.title_image = gtk.gdk.pixbuf_new_from_file(xdg.get_data_path('images/track.png'))

        #setup search combobox
        self.search_combobox = self.builder.get_object('searchComboBox')
        self.search_combobox.set_active(settings.get_option('plugin/jamendo/searchtype',0))
        
        #get handle on search entrybox
        self.search_textentry = self.builder.get_object('searchEntry')
        self.search_textentry.set_text(settings.get_option('plugin/jamendo/searchterms',""))
        
        #setup order_by comboboxes
        self.orderby_type_combobox = self.builder.get_object('orderTypeComboBox')
        self.orderby_type_combobox.set_active(settings.get_option('plugin/jamendo/ordertype',0))
        self.orderby_direction_combobox = self.builder.get_object('orderDirectionComboBox')
        self.orderby_direction_combobox.set_active(settings.get_option('plugin/jamendo/orderdirection',0))
                        
        #setup num_results combobox
        self.numresults_combobox = self.builder.get_object('numResultsComboBox')
        self.numresults_combobox.set_active(settings.get_option('plugin/jamendo/numresults',5))

        #setup status label
        self.status_label = self.builder.get_object('statusLabel')
        self.set_status(self.STATUS_READY)
      
        #setup results treeview
        self.treeview = guiutil.DragTreeView(self)
        self.treeview.connect("row-expanded", self.row_expanded)
        self.treeview.set_headers_visible(False)
        container = self.builder.get_object('treeview_box')
        scroll = gtk.ScrolledWindow()
        scroll.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        scroll.add(self.treeview)
        scroll.set_shadow_type(gtk.SHADOW_IN)
        container.pack_start(scroll, True, True)
        container.show_all()

        selection = self.treeview.get_selection()
        selection.set_mode(gtk.SELECTION_SINGLE)
        pb = gtk.CellRendererPixbuf()
        cell = gtk.CellRendererText()
        col = gtk.TreeViewColumn('Text')
        col.pack_start(pb, False)
        col.pack_start(cell, True)
        col.set_attributes(pb, pixbuf=0)
        col.set_attributes(cell, text=1)
        self.treeview.append_column(col)

        self.model = gtk.TreeStore(gtk.gdk.Pixbuf, str, gobject.TYPE_PYOBJECT)
        self.treeview.set_model(self.model)
    
    def set_status(self, message):
        self.status_label.set_text(message)
    
    def on_search_combobox_changed(self, box, params=None):        
        settings.set_option('plugin/jamendo/searchtype', box.get_active())
    
    def on_ordertype_combobox_changed(self, box, params=None):
        settings.set_option('plugin/jamendo/ordertype', box.get_active())
    
    def on_orderdirection_combobox_changed(self, box, params=None):
        settings.set_option('plugin/jamendo/orderdirection', box.get_active())

    def on_results_combobox_changed(self, box, params=None):
        settings.set_option('plugin/jamendo/numresults', box.get_active())
        
    
    #is called whenever the user expands a row in the TreeView
    def row_expanded(self, tree, iter, path):              
        sel = self.get_selected_item()                
        if not sel.expanded:
            #unexpand node, will get expanded once contents are loaded
            self.expand_node(sel, False)
            if isinstance(sel, jamtree.Artist):
                self.expand_artist(sel, False)
                                            
            if isinstance(sel, jamtree.Album):
                self.expand_album(sel, False)
            
            if isinstance(sel, jamtree.Track):
                self.add_track_to_playlist(sel)
    
    #Expand an artist node (fetch albums for that artist)
    #artist: The jamtree.Artist object you want to expand the node for
    #add_to_playlist: Whether or not add_to_playlist() should be called when done
    def expand_artist(self, artist, add_to_playlist = False):
        self.set_status(self.STATUS_RETRIEVING_DATA)
        artist.expanded = True
        jamapi_thread = jamapi.get_albums(artist, self.expand_artist_callback, add_to_playlist)
        jamapi_thread.start()        
    
    #Callback function for when the jamapi thread started in expand_artist() completes
    #artist: The jamtree.Artist object that should have had its albums populated by the jamapi thread    
    def expand_artist_callback(self, artist, add_to_playlist = False):        
        self.remove_dummy(artist)        
        for album in artist.albums:
            parent = self.model.append(artist.row_pointer, (self.album_image, album.name, album))
            album.row_pointer = parent
            self.model.append(parent, (self.title_image, "", ""))
        if add_to_playlist:
            self.add_to_playlist();
        self.expand_node(artist)
        self.set_status(self.STATUS_READY)
    
    #Expand an Album node (fetch tracks for album)
    #album: the Album object to get tracks for
    #add_to_playlist: Whether or not add_to_playlist() should be called when done
    def expand_album(self, album, add_to_playlist = False):
        self.set_status(self.STATUS_RETRIEVING_DATA)
        album.expanded = True
        jamapi_thread = jamapi.get_tracks(album, self.expand_album_callback, add_to_playlist)
        jamapi_thread.start()
    
    #Callback function for when the jamapi thread started in expand_album() completes
    #album: The jamtree.Album object that should have had its tracks populated by the jamapi thread 
    def expand_album_callback(self, album, add_to_playlist = False):
        self.remove_dummy(album)        
        for track in album.tracks:
            parent = self.model.append(album.row_pointer, (self.title_image, track.name, track))
            track.row_pointer = parent
        if (add_to_playlist):
            self.add_to_playlist()
        self.expand_node(album)
        self.set_status(self.STATUS_READY)
    
    #removes the first child node of a node
    def remove_dummy(self, node):
        iter = node.row_pointer
        dummy = self.model.iter_children(iter)
        self.model.remove(dummy)
    
    #expands a TreeView node
    def expand_node(self, node, expand=True):
        iter = node.row_pointer
        path = self.model.get_path(iter)
        if expand:
            self.treeview.expand_row(path, False)
        else:
            self.treeview.collapse_row(path)   
    
    # is called when a user doubleclicks an item in the TreeView
    def button_press(self, widget, event):                        
                
        if event.type == gtk.gdk._2BUTTON_PRESS:                                     
            self.add_to_playlist()
                
        elif event.button == 3:
            self.menu.popup(event)        
    
    #is called by the search thread when it completed  
    def response_callback(self, collection):
        self.set_status(self.STATUS_READY)
        for item in collection:
            #add item to treeview
            image = self.artist_image          
            
            if isinstance(item, jamtree.Album):
                image = self.album_image
            if isinstance(item, jamtree.Track):
                image = self.title_image
            
            parent = self.model.append(None, (image, item.name, item))            
            item.row_pointer = parent
            
            if not isinstance(item, jamtree.Track):
                self.model.append(parent, (self.artist_image, "", ""))
        
    #retrieve and display search results
    def on_search_entry_activated(self, widget):
        self.set_status(self.STATUS_SEARCHING)
        
        #clear existing search
        self.model.clear()

        #get type of search
        search_type = self.search_combobox.get_active_text()
        orderby = self.orderby_type_combobox.get_active_text()
        direction = self.orderby_direction_combobox.get_active_text()        
        orderby += "_" + direction
        numresults = self.numresults_combobox.get_active_text()
        search_term = self.search_textentry.get_text()
        
        #save search term
        settings.set_option('plugin/jamendo/searchterms', search_term)
            
        results = None
        if search_type == 'Artist':
            resultthread = jamapi.get_artist_list(search_term, orderby, numresults, self.response_callback)
            resultthread.start();            

        if search_type == 'Album':
            resultthread = jamapi.get_album_list(search_term, orderby, numresults, self.response_callback)
            resultthread.start()

        if search_type == 'Genre/Tags':
            resultthread = jamapi.get_artist_list_by_genre(search_term, orderby, numresults, self.response_callback)
            resultthread.start()
        
        if search_type == 'Track':
            resultthread = jamapi.get_track_list(search_term, orderby, numresults, self.response_callback)
            resultthread.start()

    # clear the search box and results
    def clear_search_terms(self, entry, icon_pos, event):
        entry.set_text('')        
     
    # get the Object (Artist, Album, Track) associated with the currently
    # selected item in the TreeView
    def get_selected_item(self):
        iter = self.treeview.get_selection().get_selected()[1]
        return self.model.get_value(iter, 2)
    
    # get the path for the currently selected item in the TreeView
    def get_selected_item_path(self):
        iter = self.treeview.get_selection().get_selected()[1]
        return self.model.get_path(iter)

    #get the type of an object
    def typeof(self, something):
        return something.__class__

    #add a track to the playlist based on its url.
    #track: a jamtree.Track object
    def add_track_to_playlist(self, track):
        self.add_tracks_to_playlist([track])

    #add a bunch of tracks to the playlist at once
    #track_list: a python list of jamtree.Track objects
    def add_tracks_to_playlist(self, track_list):
        #convert list to list of xl.Track objects as opposed to jamtree.Track objects
        xltrack_list = []
        for track in track_list:
            tr = xltrack.Track(track.url, scan=False)
            tr.set_tag_raw('title', track.name)
            tr.set_tag_raw('artist', track.artist_name)
            tr.set_tag_raw('album', track.album_name)
            xltrack_list.append(tr)
        self.exaile.gui.main.get_selected_playlist().playlist.add_tracks(xltrack_list)

    #dragdrop stuff
    def drag_data_received(self, *e):
        print('in drag_data_recieved')
        pass

    def drag_data_delete(self, *e):
        print('in drag_data_delete')
        pass

    def drag_get_data(self, treeview, context, selection, target_id, etime):        
        self.add_to_playlist()
        pass

#The following is a custom CoverSearchMethod to retrieve covers from Jamendo
#It is designed to only to get covers for streaming tracks
class JamendoCoverSearch(CoverSearchMethod):
    name = 'jamendo'
    type = 'remote'

    def __init__(self):
        CoverSearchMethod.__init__(self)

    def find_covers(self, track, limit=-1):
        jamendo_url = track.get_loc_for_io()

        cache_dir = self.manager.cache_dir
        if (not jamendo_url) or (not ('http://' and 'jamendo' in jamendo_url)):
            raise NoCoverFoundException

        #http://stream10.jamendo.com/stream/61541/ogg2/02%20-%20PieRreF%20-%20Hologram.ogg?u=0&h=f2b227d38d
        split=jamendo_url.split('/')
        track_num = split[4]
        image_url = jamapi.get_album_image_url_from_track(track_num)

        if not image_url:
            raise NoCoverFoundException

        local_name = hashlib.sha1(split[6]).hexdigest() + ".jpg"
        covername = os.path.join(cache_dir, local_name)
        urllib.urlretrieve(image_url, covername)

        return [covername]
    