#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LabJackSpectrumAnalyzer

Turns a LabJack U6 into a simple spectrum analyzer!

Copyright (C) 2019 Mick Phillips <mick.phillips@gmail.com>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
import numpy as np
from scipy.signal import periodogram
import threading
from collections import deque
import json
import time
import tkinter.ttk
import os

from typing import List, Optional

import tkinter
from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg, NavigationToolbar2Tk)
# Implement the default Matplotlib key bindings.
from matplotlib.figure import Figure

import u6

MAXSAMPLERATE = 50000


class StreamReader():
    def __init__(self):
        # A buffer for incoming data. Use deque for fast writes.
        self.buffer = deque()
        # Integration time in seconds
        self._t_integrate = 2
        # Data required
        self.data_request = threading.Event()
        # Data ready for processing
        self.data_ready = threading.Event()
        # Collection thread should stop
        self.data_stop = threading.Event()
        # Connecton to U6 hardware.
        self._device = None
        # Data collection thread.
        self._acq_thread = None
        # List of channels to collect
        self._channels = []
        # Sampling rate
        self._rate = 5000
        # Status callback
        self.status = ""

    def connect(self):
        self._device = u6.U6()
        self._device.getCalibrationData()
        # Set up a frequency source for testing.
        self._device.configIO(NumberTimersEnabled=1)
        # 1MHz / 250 = 4 kHz
        self._device.configTimerClock(3, 250)
        # 4KHz / 2 * 16 = 125 Hz
        self._device.getFeedback(u6.Timer0Config(7, 16))

    def __del__(self):
        """Close connection to hardware"""
        if self._device is not None:
            try:
                self._device.streamStop()
            except Exception:
                pass
            self._device.close()

    def is_running(self):
        """Return True if acquisition thread is running"""
        return self._acq_thread is not None and self._acq_thread.is_alive()

    def set_channels(self, channels: List[int]):
        """Set list of channels to acquire"""
        if self.is_running():
            self.stop_acquisition()
            self._channels = channels
            self.start_acquisition()
        else:
            self._channels = channels

    def set_sampling(self, rate: Optional[int] = None,
                     time: Optional[float] = None):
        if self.is_running():
            self.stop_acquisition()
            restart = True
        else:
            restart = False
        if rate is not None:
            self._rate = rate
        if time is not None:
            self._t_integrate = time
        if restart:
            self.start_acquisition()

    def start_acquisition(self):
        """Start data acquisition thread"""
        if self._device is None:
            try:
                self.connect()
            except Exception:
                self.status = "No hardware connected."
                return False
        if len(self._channels) == 0:
            self.status = "No channels selected."
            return False
        if self._rate > (MAXSAMPLERATE / len(self._channels)):
            self.status = "Sample rate too high for %d channels." % \
                          len(self._channels)
            return False
        if self._acq_thread is None or not self._acq_thread.is_alive():
            self.data_stop.clear()
            self._acq_thread = threading.Thread(target=self._acquire_loop,
                                                daemon=True)
            self._acq_thread.start()
        self.data_request.set()
        return True

    def stop_acquisition(self):
        """Stop data acquisition thread"""
        self.data_request.clear()
        self.data_stop.set()
        if self._acq_thread:
            self._acq_thread.join(2*self._t_integrate)
            if self._acq_thread.is_alive():
                self.status = "Acquisiton thread timed out"

    def fetch_data(self):
        """Fetch data and start another acquisition once buffer is empty"""
        if not self.data_ready.wait(0.05):
            return {}
        self.data_ready.clear()
        data = {}
        dropped = 0
        while len(self.buffer) > 0:
            raw = self.buffer.popleft()
            dropped += raw['missed']
            processed = self._device.processStreamData(raw['result'])
            for k, v in processed.items():
                if k in data:
                    data[k].extend(v)
                else:
                    data[k] = v
        npoints = max(map(len, data.values()))
        return {'rate': self._rate, 'points': npoints,
                'channels': data, 'dropped': dropped}

    def get_status(self):
        return self.status

    def _acquire_loop(self):
        """Target for data acquisition thread."""
        dev = self._device
        try:
            dev.streamStop()
        except Exception:
            pass
        error = None
        while not self.data_stop.is_set():
            if not self.data_request.wait(0.01):
                self.status = "Waiting"
                continue
            # Do acquisition
            # Prevent data collection by client.
            self.data_ready.clear()
            self.buffer.clear()
            # Grab and store local copy of current sampling settings, as
            # these may change.
            channels = self._channels
            rate = self._rate
            nchannels = len(self._channels)
            if nchannels == 0:
                self.data_stop.set()
                break
            try:
                dev.streamConfig(NumChannels=nchannels,
                                 ChannelNumbers=channels,
                                 ChannelOptions=[0]*nchannels,
                                 ResolutionIndex=0, ScanFrequency=rate)
            except Exception as e:
                self.status = "Error: %s" % e
                continue
            npts = 0
            stream = dev.streamData(convert=False)
            self.status = "Streaming"
            dev.streamStart()
            while npts < self._t_integrate * self._rate * nchannels:
                if self.data_stop.is_set():
                    break
                try:
                    raw = next(stream)
                except Exception as e:
                    import traceback
                    import sys
                    traceback.print_exc(file=sys.stderr)
                    self.data_stop.set()
                    error = e
                    break
                if raw is None:
                    self.status = "Error: no data"
                    continue
                # if (raw['errors'] + raw['missed']) > 0:
                #     print(raw['errors'], raw['missed'])
                #     for pkt, err in enumerate(raw['result'][11::64]):
                #         errNum = err
                #         if errNum != 0:
                #             #Error detected in this packet
                #             print ("Packet", pkt, "error:", errNum)
                npts += raw['numPackets'] * self._device.streamSamplesPerPacket
                self.buffer.append(raw)
            dev.streamStop()
            if error is None:
                self.data_request.clear()
            if not self.data_stop.is_set():
                # set event
                self.data_ready.set()
        if error is None:
            self.status = "Stopped."
        else:
            self.status = "Aborted: %s" % error


class DataHandler():
    def __init__(self):
        self._path = None
        self._status = (None, "")

    def get_status(self):
        if self._status[0] is None:
            return ""
        elif time.time() - self._status[0] > 5:
            if self._path:
                sstr = "Saving all to %s." % os.path.basename(self._path)
                self._status = (time.time(), sstr)
            else:
                self._status = (None, "")
        return self._status[1]

    def save_continuous(self, data):
        if self._path is None:
            return
        import datetime
        ts = datetime.datetime.now().replace(microsecond=0).isoformat()
        ts.replace(':', '')
        i = 0
        while True:
            fpath = os.path.join(self._path, "{:s}_{:02d}.txt".format(ts, i))
            if not os.path.exists(fpath):
                break
            i += 1
        if self.save_one(fpath, data):
            # There was an error
            pass
        else:
            self.status = (time.time(),
                           "Saving all to %s." % os.path.basename(self._path))

    def load_one(self, fpath):
        with open(fpath, 'r') as fh:
            return json.load(fh)

    def save_one(self, fpath, data_in):
        # Set key order for output.
        data = dict.fromkeys(['prefactor', 'unit', 'rate',
                              'points', 'dropped', 'channels'])
        data.update(data_in)
        status = "Writing to file %s." % os.path.basename(fpath)
        with open(fpath, 'w') as fh:
            try:
                json.dump(data, fh)
                status = "Save complete."
            except Exception:
                status = "Error writing to %s." % os.path.basename(fpath)
        self._status = (time.time(), status)

    def set_save_all(self, fpath):
        self._path = fpath
        self._status = (time.time(),
                        "Saving all to %s." % os.path.basename(self._path))
        if not os.path.exists(fpath):
            try:
                os.makedirs(fpath)
            except Exception:
                self._status = (time.time(), "Error creating folders.")

    def clear_save_all(self):
        self._path = None


class LiveFigure(Figure):
    def __init__(self, *args, **kwargs):
        """Figure with t- and f-axes."""
        # Maintain a mapping
        self._lines = {}
        super().__init__(*args, **kwargs)
        self._axes_t = self.add_subplot(211)
        self._axes_f = self.add_subplot(212)
        self._axes_f.set_yscale('log')
        self._axes_t.set_xlabel('s')
        self._axes_t.xaxis.set_label_coords(1.01, -0.01)
        self._axes_f.set_xlabel('Hz')
        self._axes_f.xaxis.set_label_coords(1.01, -0.01)
        self._rescale = False

    def rescale(self):
        """Rescale on next update"""
        self._rescale = True

    def on_data(self, data={}):
        """Update the plots with incoming data"""
        x = np.linspace(0, data['points'] / data['rate'], data['points'])
        # Remove lines not found in data.
        for k in set(self._lines):
            if k.lstrip('f_') not in data['channels']:
                self._lines.pop(k).remove()
        # Add or update line for incoming data.
        for k, v_as_list in data['channels'].items():
            v = np.multiply(data['prefactor'], v_as_list)
            f, p = periodogram(v, fs=data['rate'],
                               window='hann', scaling='density')
            if k not in self._lines:
                pts = len(v)
                self._lines[k] = self._axes_t.plot(x[:pts], v)[0]
                self._lines['f_' + k] = self._axes_f.plot(f, p)[0]
            else:
                self._lines[k].set_xdata(x)
                self._lines[k].set_ydata(v)
                self._lines['f_' + k].set_xdata(f)
                self._lines['f_' + k].set_ydata(p)
            if not k.startswith('f_'):
                self._lines[k].set_label(k)
            else:
                self._lines[k].set_label(None)
        # Update the legend.
        self.legends = [self.legend(mode='expand', ncol=4)]
        # Rescale if requested.
        self._axes_t.set_ylabel(data['unit'])
        self._axes_f.set_ylabel(data['unit'] + " / $\\sqrt{\\mathrm{Hz}}$")
        if self._rescale:
            for ax in self.axes:
                ax.relim()
                ax.autoscale_view()
            self._rescale = False


class LJSAApp(tkinter.ttk.Frame):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from tkinter import TOP, BOTTOM, LEFT, RIGHT, BOTH
        from tkinter.ttk import Checkbutton, Button, Label, Frame
        # Acquire continuously
        self._continuous = False
        # Data source
        self._source = StreamReader()
        # File writer
        self._writer = DataHandler()
        # Last acquired data
        self.new_data = {}
        # Sampling frequency
        self._freq = tkinter.IntVar()
        self._freq.set(5000)
        # Sampling integration time
        self._time = tkinter.IntVar()
        self._time.set(2)
        # Data scaling
        self._scaling = {'prefactor': 1.0, 'unit': 'V'}
        # Flag: save all data to a folder
        self._save_all = tkinter.BooleanVar()
        # Channel enable flags
        self._channels = [tkinter.BooleanVar() for i in range(4)]
        self._channels[0].set(True)
        # Status display.
        self._status_label = tkinter.StringVar()
        # Main figure
        self._fig = LiveFigure()
        FigureCanvasTkAgg(self._fig, master=self)
        # Figure toolbar
        toolbar = NavigationToolbar2Tk(self._fig.canvas, self)
        toolbar.update()
        toolbar.pack(side=TOP)
        self._fig.canvas.get_tk_widget().pack(side=TOP, fill=BOTH, expand=1)
        # Area for channel selection, start/stop/save controls and status.
        buttonbar = Frame(self, relief=tkinter.SUNKEN)
        Label(buttonbar, text="Channels").pack(side=LEFT)
        for i, v in enumerate(self._channels):
            cb = Checkbutton(buttonbar, text="AIN%d" % i, variable=v,
                             command=self._on_channel_change)
            cb.pack(side=LEFT, expand=0, padx=4)
        # Buttons, right to left
        buttons = [
                   ("Save last", self._save_current),
                   ("Stop", self._source.stop_acquisition),
                   ("Start", self.start),
                   ]
        for label, fn in buttons:
            if label == "Start":
                b = Button(master=buttonbar, text=label)
                b.bind('<Button>', fn)
            else:
                b = Button(master=buttonbar, text=label, command=fn)
            b.pack(side=RIGHT)

        cb = Checkbutton(buttonbar, text="save all", variable=self._save_all,
                         command=self._on_save_all)
        cb.pack(side=RIGHT, expand=0, padx=8)
        buttonbar.pack(fill=tkinter.X)

        lbl = Label(self, relief=tkinter.SUNKEN, anchor=tkinter.W,
                    textvariable=self._status_label)
        lbl.pack(side=BOTTOM, fill=tkinter.X)

        # Menu to set sampling rate
        from collections import OrderedDict
        self._menus = OrderedDict()
        self._menus['freq'] = tkinter.Menu(self, tearoff=False)
        self._menus['time'] = tkinter.Menu(self, tearoff=False)
        self._menus['scaling'] = tkinter.Menu(self, tearoff=False)
        # Populate sample-freq menu
        self._fill_freq_menu()
        # Populate sample-time menu
        for t in [1, 2, 3, 4, 5, 10]:
            txt = "%.2f s" % t
            self._menus['time'].add_radiobutton(label=txt, value=t,
                                                variable=self._time)
        # Sampling settings menus
        menubar = tkinter.Menu(self.master)
        menubar.add_command(label="Open", command=self._on_open)
        for k, m in self._menus.items():
            menubar.add_cascade(label=k.capitalize(), menu=m)
        self._menus['scaling'].add_command(label='set unit',
                                           command=self._set_unit)
        self._menus['scaling'].add_command(label='set prefactor',
                                           command=self._set_prefactor)
        menubar.add_command(label="About", command=self._about)
        self.master.config(menu=menubar)
        # Traces on sampling variables to configure hardware and rescale axes.
        self._time.trace('w', lambda *_: self._source.set_sampling(time=self._time.get()))
        self._freq.trace('w', lambda *_: self._source.set_sampling(rate=self._freq.get()))
        self._time.trace('w', lambda *_: self._fig.rescale())
        self._freq.trace('w', lambda *_: self._fig.rescale())
        # Set channels on StreamReader to match initial selection.
        self._on_channel_change()
        # Start polling
        self._poll()

    def _about(self):
        from tkinter.messagebox import showinfo
        showinfo(title="About",
                 message=__doc__.replace('\n\n', '\r\r').replace('\n', ' '))

    def _set_unit(self):
        unit = tkinter.simpledialog.askstring("Scaling: unit",
                                              "Enter unit string", parent=self,
                                              initialvalue=self._scaling['unit'])
        if unit is not None:
            self._scaling['unit'] = unit

    def _set_prefactor(self):
        pref = tkinter.simpledialog.askfloat("Scaling: prefactor",
                                             "Enter prefactor as float",
                                             parent=self,
                                             initialvalue=self._scaling['prefactor'])
        if pref is not None:
            self._scaling['prefactor'] = pref
            self._fig.rescale()

    def _on_open(self):
        from tkinter import filedialog
        filename = filedialog.askopenfilename()
        if not filename:
            return
        data = self._writer.load_one(filename)
        self.new_data = {}
        self._fig.rescale()
        self._fig.on_data(data)
        self._fig.canvas.draw()

    def _on_save_all(self):
        if self._save_all.get():
            from tkinter import filedialog
            folder = filedialog.askdirectory(title="Choose folder or enter "
                                             "new folder name")
            if folder:
                self._writer.set_save_all(folder)
            else:
                self._save_all.set(False)
        else:
            self._writer.clear_save_all()

    def _fill_freq_menu(self):
        menu = self._menus.get('freq', None)
        if menu is None:
            return
        # Clear the menu
        while menu.index(0) == 0:
            menu.delete(0)
        n = sum(map(lambda c: c.get(), self._channels))
        maxfreq = MAXSAMPLERATE // n

        for f in [500, 1000, 2000, 5000] + list(range(10000, maxfreq+5000, 5000)):
            if f > 1000:
                txt = "%.2f kHz" % (f / 1000)
            else:
                txt = "%d Hz" % f
            menu.add_radiobutton(label=txt, value=f, variable=self._freq)

    def _save_current(self):
        data = self.new_data
        if not data:
            return
        from tkinter import filedialog
        fname = filedialog.asksaveasfilename(filetypes=(("plain text",
                                                         "*.txt"),))
        if fname:
            self._writer.save_one(fname, data)

    def start(self, evt):
        if evt.num == 1:
            self._continuous = True
        else:
            self._continuous = False
        self._source.start_acquisition()
        self._fig.rescale()

    def _poll(self):
        # Poll for new data then initiate next poll event
        new_data = self._source.fetch_data()
        if new_data:
            self._on_data(new_data)
            if self._continuous:
                self._source.start_acquisition()
        streamstatus = self._source.get_status()
        filestatus = self._writer.get_status()
        if self.new_data:
            ndropped = self.new_data['dropped']
            ntot = self.new_data['points'] * len(self.new_data['channels'])
            dropped = "    Dropped %d of %d points.    " % (ndropped, ntot)
        else:
            dropped = ""
        self._status_label.set("\t".join((streamstatus, dropped, filestatus)))
        self.after(100, self._poll)

    def _quit(self):
        self._source.stop_acquisition()
        self.quit()
        self.destroy()

    def _on_channel_change(self):
        """Update source channel config"""
        channels = [i for (i, c) in enumerate(self._channels) if c.get()]
        self._source.set_channels(channels)
        if sum(channels) > 0:
            self._fill_freq_menu()
        self._fig.rescale()

    def _on_data(self, data):
        """Process incoming data"""
        if not data:
            return
        self.new_data = data
        self.new_data.update(self._scaling)
        self._fig.on_data(data)
        if self._save_all.get():
            self._writer.save_continuous(data)
        try:
            self._fig.canvas.draw()
        except Exception as e:
            print("Error in _fig.canvas.draw():", e)


if __name__ == '__main__':
    root = tkinter.Tk()
    app = LJSAApp(root)
    app.pack(fill=tkinter.BOTH, expand=tkinter.YES)
    root.wm_title("LJSA")
    root.protocol("WM_DELETE_WINDOW", app._quit)
    root.mainloop()
