import streamlit as st
import librosa
import librosa.display
import os
import mido
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import colorConverter
import plotly.graph_objects as go
import plotly.express as px

from turtle import bgcolor
import re
from pathlib import Path


class MidiFile(mido.MidiFile):
    def __init__(self, midifile, verbose=False):
        self.sr = 10   # down sampling rate from MIDI to time axis
        self.meta = {}
        self.max_nch = 16
        self.intensity_range = [100,0] # [min, max] adjusted by get_roll()
        self.note_range = [127,0] # [min, max] adjusted by get_roll()

        #print("Filename: ", midifile)
        mido.MidiFile.__init__(self, midifile)
        self.fpath = Path(midifile)

        
        self.events, self.nch = self.get_events(verbose)
        self.roll = self.get_roll(self.events)


        self.length_ticks = self.get_total_ticks()
        self.length_seconds = mido.tick2second(
            self.length_ticks, self.ticks_per_beat, self.get_tempo())
        self.ticks_per_sec = self.length_ticks/self.length_seconds

        st.sidebar.write('## midi file')

        st.sidebar.write("Num. of tracks: ", len(self.tracks))
        st.sidebar.write("Num. of active channels: ", self.nch)
        st.sidebar.write("intensity range [0, 100]: [{}, {}]".format(
            self.intensity_range[0], self.intensity_range[1]))
        st.sidebar.write("note range [0, 127]: [{}, {}]".format(
            self.note_range[0], self.note_range[1]))
        st.sidebar.write("ticks/beat: ", self.ticks_per_beat)
        st.sidebar.write("ticks/second: ", self.ticks_per_sec)
        st.sidebar.write("Tick length: [ticks]", self.length_ticks)
        st.sidebar.write("Time length [s]: ", self.length_seconds)


    @st.cache
    def get_tempo(self):
        try:
            return self.meta["set_tempo"]["tempo"]
        except:
            return 500000

    @st.cache
    def get_total_ticks(self):
        max_ticks = 0
        for channel in range(self.nch):
            ticks = sum(msg.time for msg in self.events[channel])
            if ticks > max_ticks:
                max_ticks = ticks
        return max_ticks

    @st.cache
    def get_events(self, verbose=False):
        """
        Extract self.max_nch (default: 16) channel data from MIDI and return a list.
        Lyrics and meta data used in extra channels are not include in the list.

        Returns:
            list : [[ch1],[ch2]....[ch16]] # Note that empty channel is removed!
        """
        if verbose:
            print(self)

        mid = self
        events =  [[] for i in range(self.max_nch)]
        
        for track in mid.tracks:
            for msg in track:
                try:
                    channel = msg.channel
                    events[channel].append(msg)
                except AttributeError:
                    try:
                        if type(msg) != type(mido.UnknownMetaMessage):
                            self.meta[msg.type] = msg.dict()
                        else:
                            pass
                    except:
                        print("error", type(msg))
        events = list(filter(None, events)) # remove emtpy channel
        
        return events, len(events)

    @st.cache
    def get_roll(self, events, verbose=False):
        """
        Convert event (channel) data to piano roll data
        """
        length_ticks = self.get_total_ticks()  # get total length in tick unit

        roll = np.zeros(
            (self.nch, 128, length_ticks // self.sr), dtype="int8")
        register_note = [int(-1)]*128        # register the state (on/off) of each key
        register_timbre = np.ones(self.nch)  # register the state (program_change) of each channel

        for idx, channel in enumerate(events):
            time_counter = 0
            volume = 100

            if verbose:
                print("channel", idx, "start")

            for msg in channel:
                if msg.type == "control_change":
                    if msg.is_cc(7):  # if msg.control == 7: Main Volume
                        volume = 100*msg.value //127  # [0, 100]
             
                    if msg.is_cc(11):  # if msg.control == 11: Expression Controller
                        # volume[0,100] x expression[0,127]/127
                        volume *= msg.value // 127

                if msg.type == "program_change":
                    register_timbre[idx] = msg.program
                    if verbose:
                        print("channel", idx, "pc", msg.program, "time",
                              time_counter, "duration", msg.time)

                if msg.type == "note_on":
                    if verbose:
                        print("on ", msg.note, "time", time_counter,
                              "duration", msg.time, "velocity", msg.velocity)

                    # note_on_start_time = time_counter // self.sr
                    note_on_end_time = (time_counter + msg.time) // self.sr
                    intensity = volume * msg.velocity // 127
                    
                    if self.intensity_range[0] > intensity: # update minimum intensity
                        self.intensity_range[0] = intensity
                    if self.intensity_range[1] < intensity: # update maximum intensity
                        self.intensity_range[1] = intensity

                    if register_note[msg.note] != -1:  # not after note_off
                        last_end_time, last_intensity = register_note[msg.note]
                        roll[idx, msg.note,
                             last_end_time:note_on_end_time] = last_intensity

                    register_note[msg.note] = (note_on_end_time, intensity)

                    if self.note_range[0] > msg.note: # update minimum note
                        self.note_range[0] = msg.note
                    if  self.note_range[1] < msg.note: # update maximum note
                        self.note_range[1] = msg.note

                if msg.type == "note_off":
                    if verbose:
                        print("off", msg.note, "time", time_counter,
                              "duration", msg.time, "velocity", msg.velocity)

                    # note_off_start_time = time_counter // self.sr
                    note_off_end_time = (time_counter + msg.time) // self.sr
                    last_end_time, last_intensity = register_note[msg.note]
                    roll[idx, msg.note,
                         last_end_time:note_off_end_time] = last_intensity

                    register_note[msg.note] = -1  # reinitialize register

                time_counter += msg.time

            # if there is a note not closed at the end of a channel, close it
            for key, data in enumerate(register_note):
                if data != -1:
                    note_on_end_time = data[0]
                    intensity = data[1]
                    # note_off_start_time = time_counter // self.sr
                    roll[idx, key, note_on_end_time:] = intensity
                register_note[idx] = -1

        return roll

    def _grp_init(self, figsize=(15, 9), xlim=None, ylim=None, bgcolor='white'):
        """
        Display basic information and initialize graphics. 
        Called by draw_roll()
        """
        dsp_len_seconds = xlim[1]-xlim[0]   
        xticks_interval_sec = dsp_len_seconds // 10 if dsp_len_seconds > 10 else dsp_len_seconds/10
        xticks_interval = mido.second2tick(
            xticks_interval_sec, self.ticks_per_beat, self.get_tempo()) / self.sr  # [ticks/interval]
        print("xticks_interval_sec: ", xticks_interval_sec)
        print("xticks_interval: {} [ticks/label]".format(xticks_interval))

        # Initialize graphics
        plt.rcParams["font.size"] = 20
        fig = plt.figure(figsize=figsize)
        ax = fig.add_subplot(111)
        ax.axis("equal")
        ax.set_facecolor(bgcolor)

        nxticks = int(self.length_ticks//xticks_interval)
        plt.xticks(
            [int(x * xticks_interval) for x in range(nxticks)],
            [round(x * xticks_interval_sec, 2) for x in range(nxticks)]
        )
        plt.yticks([y*16 for y in range(8)], [y*16 for y in range(8)])

        ax.set_xlabel("time [s]")
        ax.set_ylabel("note")
        xlim_ticks=[0,self.length_ticks-1]
        if xlim != None:
            xticks_per_sec = xticks_interval/xticks_interval_sec
            print("ticks/second 2:", xticks_per_sec)
            xlim_ticks=np.array(xlim)*xticks_per_sec
            ax.set_xlim(xlim_ticks)

        if ylim == None:
            ylim=[0, 127]
        elif ylim == "Auto" or ylim == "auto":
            ylim=[self.note_range[0]-1, self.note_range[1]+1]
        ax.set_ylim(ylim)
            
        ax.set_xlabel("time [s]")
        ax.set_ylabel("note")
        
        return fig, ax, xlim_ticks

    def get_colormap_selector(self, cmap_name=None, bgcolor='white'):
        """ Define color map for each channel """
        cmap_list=(None,'Greys', 'Purples', 'Blues', 'Greens', 'Oranges', 'Reds',
            'YlOrBr', 'YlOrRd', 'OrRd', 'PuRd', 'RdPu', 'BuPu',
            'GnBu', 'PuBu', 'YlGnBu', 'PuBuGn', 'BuGn', 'YlGn')
        
        try:
            default_idx=cmap_list.index(cmap_name)
        except ValueError:
            default_idx=None

        cmap_name=st.sidebar.selectbox('colormap', cmap_list, index=default_idx)

        if cmap_name==None:
            transparent = colorConverter.to_rgba(bgcolor)
            colors = [
                mpl.colors.to_rgba(mpl.colors.hsv_to_rgb(
                    (i / self.nch, 1, 1)), alpha=1)
                for i in range(self.nch)
            ]
            cmaps = [
                mpl.colors.LinearSegmentedColormap.from_list(
                    'my_cmap', [transparent, colors[i]], 128)
                for i in range(self.nch)
            ]
        else:
            cmap = plt.cm.get_cmap(cmap_name)
            cmaps = [ cmap for i in range(self.nch) ]

        """
        make look up table (LUT) data, e.g., (K=3)
            array([[0. , 0. , 0. , 0. ],
                [0.5, 0. , 0. , 0.2],
                [1. , 0. , 0. , 0.4],
                [0. , 0. , 0. , 0.6],
                [1. , 0. , 0. , 0.8],
                [0. , 0. , 0. , 1. ]])
            The first 3 rows are colormap, and the last 3 rows are the colours
            for data low and high out-of-range values and for masked values.
            https://stackoverflow.com/questions/18035411/meaning-of-the-colormap-lut-list-in-matplotlib-color
        """
        for i in range(self.nch):
            cmaps[i]._init()
            alphas = np.linspace(0, 1, cmaps[i].N + 3) # about 3 extra rows, see the example above
            cmaps[i]._lut[:, -1] = alphas

        return cmaps   

    def get_bgcolor_slider(self, bgcolor='white'):
        bgcolors=('white','black')
        default_idx=bgcolors.index(bgcolor)
        bgcolor=st.sidebar.selectbox('background color', bgcolors, index=default_idx)
        return bgcolor

        
    def get_xlim_slider(self,xlim):
        if xlim == None:
            xlim = [0, int(self.length_seconds)]

        xlim = st.slider('Time range [s]: ', min_value=0, max_value=int(
            self.length_seconds), value=(xlim[0], xlim[1]))

        return xlim

    def draw_roll(self, figsize=(15, 9), xlim=None, ylim=None, cmaps=None, bgcolor='white', vlines=None, colorbar=False):
        """Create piano roll image.

        Args:
            figsize (tuple or list): figure size
            
            xlim: Time range to be displayed [s]
                None (not specified) : Full range
                tuple or list : (xmin, xmax) [s]
            
            ylim: Range of notes to be displayed in vertical axis
                None (not specified) : Full range of notes
                "Auto" or "auto" : automatic range adjustment
                tuple or list : range of notes to be displayed [s]
 
            bgcolor (string): name of background color
            
            colorbar (boolean): enable colorbar of intensity
        """

        if xlim == None:
            xlim = [0,int(self.length_seconds)]

        fig, ax1, xlim_ticks = self._grp_init(
            figsize=figsize, xlim=xlim, ylim=ylim, bgcolor=bgcolor)
        
        if cmaps == None:
            self.get_colormap_selector('Purple')

        for i in range(self.nch):
            try:
                print("xlim_ticks", xlim_ticks)
                target_roll = self.roll[i, :, :int(xlim_ticks[1])]
                #target_roll = self.roll[i, :, :]

                max_intensity = np.max(np.array(target_roll))
                print("max_intensity:", max_intensity)
                im = ax1.imshow(self.roll[i], origin="lower",
                                interpolation='nearest', cmap=cmaps[i], aspect='auto', clim=[0, max_intensity])
                if vlines != None:
                    ax1.vlines(vlines,ylim[0],ylim[1])
                if colorbar:
                    fig.colorbar(im)
            except IndexError:
                pass

        # draw color bar for channel color
        # if colorbar:
        #     cmap = mpl.colors.LinearSegmentedColormap.from_list(
        #         'my_cmap', colors, self.nch)
        #     ax2 = fig.add_axes([0.1, 0.9, 0.8, 0.05])
        #     cbar = mpl.colorbar.ColorbarBase(ax2, cmap=cmap,
        #                                      orientation='horizontal',
        #                                      ticks=list(range(self.nch)))

        #ax1.set_title(self.fpath.name)
        #plt.draw()
        #plt.ion()
        with st.container():
            st.pyplot(fig)
        plt.savefig("outputs/"+self.fpath.name+".png", bbox_inches="tight")
        #plt.show(block=True)

def get_dirs(folder_path):
    dirs = [ f for f in os.listdir(folder_path) if os.path.isdir(folder_path+"/"+f) ]
    return (sorted(dirs))

# def file_selector(folder_path):
#     filenames = os.listdir(folder_path)
#     selected_filename = st.selectbox('Select a file', filenames)
#     return os.path.join(folder_path, selected_filename)

def show_wav(file):
    wav,sr = librosa.load(file)
    wav_seconds=int(len(wav)/sr)
    st.sidebar.write('## audio file')
    st.sidebar.write('sampling rate [Hz]: ', sr)
    st.audio(file)

def main():
    dir = "data/pedb2_v0.0.1.b/"
    #target="bac-inv001-o-p2"
    target = "bac-wtc101-p-a-p1"

    st.set_page_config(layout='wide')

    dirs = get_dirs(dir)
    target = st.sidebar.selectbox('Select file to visualize', dirs)
    
    st.write(target)

    path_wav = "{0}/{1}/{1}.wav".format(dir, target)
    show_wav(path_wav)


    path_mid = "{0}/{1}/{1}.mid".format(dir, target)
    mid = MidiFile(path_mid)

    # events = mid.get_events()
    # roll = mid.get_roll(verbose=False)

    # cmap_list: colormap name
    # https://matplotlib.org/stable/tutorials/colors/colormaps.html
    default_xlim=[0,4]
    bgcolor = mid.get_bgcolor_slider(bgcolor='white')
    cmaps = mid.get_colormap_selector(cmap_name='Purples',bgcolor=bgcolor)
    mid.draw_roll(figsize=(20, 4), xlim=None, ylim=[30,92], cmaps=cmaps, bgcolor=bgcolor, vlines=default_xlim,colorbar=None)

    xlim=mid.get_xlim_slider(xlim=default_xlim)
    mid.draw_roll(figsize=(20, 4), xlim=xlim, ylim=[30,92], cmaps=cmaps, bgcolor=bgcolor, colorbar=None)


if __name__ == "__main__":
    main()
