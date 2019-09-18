import numpy as np
from scipy.signal import periodogram
import matplotlib.pyplot as plt
import threading
from collections import deque

import u6
from LabJackPython import *

CHANNELS = [0,2]
SAMPLERATE = 10000

class StreamReader():
    def __init__(self):
        # A buffer for incoming data. Use deque for fast writes.
        self.buffer = deque()
        # Integration time in seconds
        self.t_integrate = 1
        self._device = u6.U6()
        self._device.getCalibrationData()
        self._barrier = threading.Barrier(2)
        self.data_ready = threading.Event()

    def __del__(self):
        if self._device is not None:
            try:
                self._device.streamStop()
            except:
                pass
            self._device.close()

    def trigger_acquisition(self):
        self.data_ready.clear()
        self._barrier.wait()

    def start_acquisition(self):
        self._acq_thread = threading.Thread(target=self._acquire_loop)
        self._barrier.reset()
        self._acq_thread.start()

    def stop_acquisition(self):
        self._barrier.abort()
        self._acq_thread.join()

    def fetch_data(self, another=True):
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
        nchannels = len(data.keys())
        npoints = max(map(len, data.values()))
        x = np.linspace(0, npoints/SAMPLERATE, npoints)
        print ("Dropped %d of %d samples." % (dropped, npoints * nchannels))
        if another:
            self.trigger_acquisition()
        return x, data

    def _acquire_loop(self):
        dev = self._device
        while True:
            # wait for barrier
            try:
                self._barrier.wait()
            except threading.BrokenBarrierError:
                break
            # do acquisition
            n = len(CHANNELS)
            dev.streamConfig(NumChannels=n, ChannelNumbers=CHANNELS, 
                             ChannelOptions=[0]*n, 
                             ResolutionIndex=0, ScanFrequency=SAMPLERATE)
            count = 0
            stream = dev.streamData(convert=False)
            dev.streamStart()
            while count < self.t_integrate * SAMPLERATE * n:
                if self._barrier.broken:
                    # Check for abort.
                    break
                raw = next(stream)
                # if (raw['errors'] + raw['missed']) > 0:
                #     print(raw['errors'], raw['missed'])
                #     for pkt, err in enumerate(raw['result'][11::64]):
                #         errNum = err
                #         if errNum != 0:
                #             #Error detected in this packet
                #             print ("Packet", pkt, "error:", errNum)
                count += raw['numPackets'] * self._device.streamSamplesPerPacket
                self.buffer.append(raw)
            self._device.streamStop()
            # set event
            self.data_ready.set()


def do_plot(x, data, lines, axs):
    if not lines:
        plt.ion()
        axs[1].set_yscale('log')
        for k,v in data.items():
            lines[k] = axs[0].plot(x, v)[0]
            f, p = periodogram(v, fs=(1 / (x[1] - x[0])), window='hann', scaling='density')
            lines['f_' + k] = axs[1].plot(f, p)[0]
    else:
        for k,l in lines.items():
            if k.startswith('f_'):
                f, p = periodogram(data[k[2:]], fs=(1 / (x[1] - x[0])), window='hann', scaling='density')
                l.set_xdata(f)
                l.set_ydata(p)
            else:
                l.set_xdata(x)
                l.set_ydata(data[k])
    return lines



#global RUN_FLAG 
RUN_FLAG = True

def on_close(evt):
    global RUN_FLAG
    RUN_FLAG = False

if __name__ == '__main__':
    s = StreamReader()
    lines = {}

    fig, axs = plt.subplots(2,1)
    fig.canvas.mpl_connect('close_event', on_close)

    d = s._device
    d.configIO(NumberTimersEnabled=1)
    d.configTimerClock(4, 0)
    d.getFeedback(u6.Timer0Config(7, 1))
    div = 0
    

    import time
    time.sleep(2)
    s.start_acquisition()
    s.trigger_acquisition()
    while True:
        d.getFeedback(u6.Timer0Config(LJ_tmFREQOUT, 127))
        #div = (div + 32) % 256
        while not s.data_ready.is_set() and RUN_FLAG:
            plt.pause(0.2)
        if not RUN_FLAG:
            # Break to prevent trying to update a plot that has been closed.
            break
        x, data = s.fetch_data()
        lines = do_plot(x, data, lines, axs)


    s.stop_acquisition()