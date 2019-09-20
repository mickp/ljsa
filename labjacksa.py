import numpy as np
from scipy.signal import periodogram
import matplotlib.pyplot as plt
import threading
from collections import deque

from typing import List

import tkinter
from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg, NavigationToolbar2Tk)
# Implement the default Matplotlib key bindings.
from matplotlib.backend_bases import key_press_handler
from matplotlib.figure import Figure

import u6
from LabJackPython import *

MAXSAMPLERATE = 50000

#TODO: check if acquisition runs when screen is locked. If not, 
# rework with callbacks or another thread.

class StreamReader():
    def __init__(self):
        # A buffer for incoming data. Use deque for fast writes.
        self.buffer = deque()
        # Integration time in seconds
        self.t_integrate = 2
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
            except:
                pass
            self._device.close()

    def is_running(self):
        """Return True if acquisition thread is running"""
        return self._acq_thread is not None and self._acq_thread.is_alive()

    def set_channels(self, channels : List[int]):
        """Set list of channels to acquire"""
        self._channels = channels

    def set_sample_rate(self, rate : int):
        self._rate = rate

    def start_acquisition(self):
        """Start data acquisition thread"""
        if self._acq_thread is None or not self._acq_thread.is_alive():
            self.data_stop.clear()
            self._acq_thread = threading.Thread(target=self._acquire_loop, daemon=True)
            self._acq_thread.start()
        self.data_request.set()

    def stop_acquisition(self):
        """Stop data acquisition thread"""
        self.data_request.clear()
        self.data_stop.set()
        if self._acq_thread:
            self._acq_thread.join()

    def fetch_data(self, another=True):
        """Fetch data and start another acquisition once buffer is empty"""
        self.data_ready.wait()
        data = {}
        dropped = 0
        while len(self.buffer) > 0:
            raw = self.buffer.popleft()
            dropped += raw['missed']
            processed = self._device.processStreamData(raw['result'])
            for k,v in processed.items():
                if k in data:
                    data[k].extend(v)
                else:
                    data[k] = v
        if another and self._acq_thread.is_alive():
            self.start_acquisition()
        nchannels = len(data.keys())
        npoints = max(map(len, data.values()))
        print ("Dropped %d of %d samples." % (dropped, npoints * nchannels))
        return {'rate':self._rate, 'n':npoints, 
                'channels':data, 'dropped':dropped}

    def _acquire_loop(self):
        """Target for data acquisition thread."""
        dev = self._device
        try:
            dev.streamStop()
        except:
            pass
        while not self.data_stop.is_set():
            self.data_request.wait()
            # do acquisition
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
            dev.streamConfig(NumChannels=nchannels, ChannelNumbers=channels, 
                             ChannelOptions=[0]*nchannels, 
                             ResolutionIndex=0, ScanFrequency=rate)
            count = 0
            stream = dev.streamData(convert=False)
            dev.streamStart()
            while (not self.data_stop.is_set()) \
                   and count < self.t_integrate * self._rate * nchannels:
                try:
                    raw = next(stream)
                except Exception as e:
                    self.data_stop.set()
                    print("Error:", e)
                    break
                # if (raw['errors'] + raw['missed']) > 0:
                #     print(raw['errors'], raw['missed'])
                #     for pkt, err in enumerate(raw['result'][11::64]):
                #         errNum = err
                #         if errNum != 0:
                #             #Error detected in this packet
                #             print ("Packet", pkt, "error:", errNum)
                count += raw['numPackets'] * self._device.streamSamplesPerPacket
                self.buffer.append(raw)
            dev.streamStop()
            self.data_request.clear()
            if not self.data_stop.is_set():
                # set event
                self.data_ready.set()


class MyFigure(Figure):
    def __init__(self, *args, **kwargs):
        """Figure with t- and f-axes."""
        # Maintain a mapping 
        self._lines = {}
        super().__init__(*args, **kwargs)
        self._axes_t = self.add_subplot(211)
        self._axes_f = self.add_subplot(212)
        self._axes_f.set_yscale('log')
        self._rescale = False

    def rescale(self):
        """Rescale on next update"""
        self._rescale = True

    def on_data(self, data={}):
        """Update the plots with incoming data"""
        x = np.linspace(0, data['n'] / data['rate'], data['n'])
        labels = {}
        # Remove lines not found in data.
        for k in set(self._lines):
            if k.lstrip('f_') not in data['channels']:
                self._lines.pop(k).remove()
        # Add or update line for incoming data.
        for k,v in data['channels'].items():
            f, p = periodogram(v, fs=data['rate'], window='hann', scaling='density')
            if k not in self._lines:
                self._lines[k] = self._axes_t.plot(x, v)[0]
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
        if self._rescale:
            for ax in self.axes:
                ax.relim()
                ax.autoscale_view()
            self._rescale = False


class MyApp(tkinter.Frame):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from tkinter import Spinbox, Checkbutton, Button, TOP, BOTH, LEFT, RIGHT

        self._source = StreamReader()
        self.last_data = {}
        self.new_data = {}

        self._fig = MyFigure()
        self._canvas = FigureCanvasTkAgg(self._fig, master=self)
        self._canvas.draw()
    
        toolbar = NavigationToolbar2Tk(self._canvas, self)
        toolbar.update()    
        toolbar.pack(side=TOP)
        self._canvas.get_tk_widget().pack(side=TOP, fill=BOTH, expand=1)
        
        buttonbar = tkinter.Frame(self)
        tkinter.Label(buttonbar, text="Channels").pack(side=LEFT)
        self._channels = [tkinter.BooleanVar() for i in range(4)]
        self._channels[0].set(True)
        for i, v in enumerate(self._channels, 1):
            cb = Checkbutton(buttonbar, text="%d" % i, variable=v,
                             command=self._on_channel_change)
            cb.pack(side=LEFT, expand=0)

        self._rate = tkinter.StringVar()
        self._rate.set("5000")
        sb = Spinbox(buttonbar, from_=1000, to=50000,
                                increment=100, textvariable=self._rate,
                                width=8,
                                command=self._on_rate_change)
        sb.pack(side=LEFT, expand=0)
        sb.bind('<Return>', self._on_rate_change)

        Button(master=buttonbar, text="Stop", 
               command=self._source.stop_acquisition).pack(side=RIGHT)
        Button(master=buttonbar, text="Start", 
               command=self._source.start_acquisition).pack(side=RIGHT)
        self._acquiring = tkinter.BooleanVar()
        cb = Checkbutton(buttonbar, text="running", 
                         state=tkinter.DISABLED, variable=self._acquiring)
        cb.pack(side=RIGHT, expand=0)
        self._status_label = tkinter.StringVar()
        lbl = tkinter.Label(buttonbar, textvariable=self._status_label)
        lbl.pack(side=RIGHT, expand=0)
        buttonbar.pack(fill=tkinter.X)

        try:
            self._source.connect()
        except:
            # TODO: disable or hide hardware-related controls.
            print("NO SOURCE")

        # Set channels on StreamReader to match initial selection.
        self._on_channel_change()
        # Start polling
        self._poll()

    def _poll(self):
        # Poll for new data then initiate next poll event
        self._acquiring.set(self._source.is_running())
        if self._source.data_ready.is_set():
            new_data = self._source.fetch_data()
            if new_data:
                self._on_data(new_data)
        self.after(100, self._poll)

    def _quit(self):
        self._source.stop_acquisition()
        self.quit()
        self.destroy()

    def _on_channel_change(self):
        """Update source channel config"""
        self._source.set_channels(
            [i for (i, c) in enumerate(self._channels) if c.get()])
        # Increasing number of channels may change upper rate limit.
        self._on_rate_change()

    def _on_rate_change(self, evt=None):
        """Update source sampling rate"""
        if evt is not None and evt.type == 'KeyPress':
            print("DROP FOCUS")
            evt.widget.master.focus()
        nchan = sum([c.get() for c in self._channels])
        maxrate = MAXSAMPLERATE // nchan
        if int(self._rate.get()) > maxrate:
            self._rate.set(str(maxrate))
        print(self._rate.get())
        self._source.set_sample_rate(int(self._rate.get()))
        self._fig.rescale()

    def _on_data(self, data):
        """Process incoming data"""
        if not data:
            self._status_label.set("")
            return
        self.last_data = self.new_data
        self.new_data = data
        self._fig.on_data(data)
        self._status_label.set("Dropped %d of %d points." % (
                                data['dropped'], 
                                data['n']*len(data['channels'])))
        self._canvas.draw()


if __name__ == '__main__':
    root = tkinter.Tk()
    app = MyApp(root)
    app.pack(fill=tkinter.BOTH, expand=tkinter.YES)
    root.wm_title("LJSA")
    root.protocol("WM_DELETE_WINDOW", app._quit)
    root.mainloop()
