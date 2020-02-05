# LabJack Spectrum Analyzer

Uses the LabJack U6 and python to make a 1- to 4-channel spectrum analyzer.

## Prerequisites

* python > 3.7, with
  * matplotlib
  * numpy
  * scipy
  * tkinter
* LabJack Exodriver: https://labjack.com/support/software/installers/exodriver
* LabJack python: https://github.com/labjack/LabJackPython


## Installing

Download or clone this repository and simply invoke:
```
python labjacksa.py &
```

## Usage

### Overview 

The LabJack spectrum analyzer streams data from the LabJack U6 and computes its power spectrum using scipy, applying a Hann window to eliminate edge transients. The U6 can stream data at up to 50 kHz, multiplexing the ADC between channels: a single channel can be sampled at up to 50 kHz, two channels at 25 kHz, and so on. Sampling at higher frequencies means a higher frequency cutoff for the power spectrum; sampling for longer times moves the lower limit of the power spectrum to lower frequencies.

The user interface presents the following main areas (from top to bottom):
* a menu bar for file review and data-streaming settings
* the matplotlib navigation toolbar
* a matplotlib plot with showing a time series and its power spectrum
* a toolbar to select channels, and control acquisition and data saving
* a status bar

### Menu bar

* Open - opens a data file for review.
* Freq - sets the per-channel sampling frequency. Must be set <= 50 kHz / number of channels.
* Time - sets the sampling time. This represents the minimum sampling time. The U6 streams data in packets, and the requested sampling time may represent an non-integer number of packets; the actual sampling time may be longer, as we round up the number of packets to the next highest integer and do not discard any of the last packet.
* Scaling - sets the units and scaling prefactor. MathTeX may be used for formatting the units string. For example, if sampling an accelerometer + amplifier with a sensitivity of 0.1 m^2/s per volt, set the unit to "m$^2$/s", and the prefactor to 0.1.
* About - displays a copyright and license notice.

### Acquisition toolbar

Channels are selected using the checkboxes on the left side of this bar. Remember to limit the sampling rate appropriate to the number of channels selected: if it is too high, the status bar will display a message to remind you.

```Start``` and ```Stop``` buttons start and stop data acquisition.

```Save last``` saves the currently displayed data to a file.

Setting the ```save all``` check box will display a folder-select dialog. Use this to choose an existing folder, or enter a name to create a new folder, and click OK. Data will then be saved in timestamed files until the ```save all``` check box is cleared.

### Saved data format

Saving data dumps the raw (unprocessed, unscaled) channel data to a file as a JSON object, along with sampling information, and the scaling and units used for display. The JSON object has the following key/value pairs:

* prefactor - the data scaling prefactor 
* scaling - the data scaling unit
* rate - the effective sampling rate (i.e. 1/time between points in each channel)
* points - the number of points in each channel
* dropped - the number of points dropped due to buffer over-runs
* channels - a mapping of each active channel name to its data as a list of floating point numbers.

The accelerometer example given above would produce a file that looks like the following:

```
{"prefactor": 0.1, "unit": "m$^2$/s", "rate": 50000, "points": 100800, "dropped": 0, "channels": {"AIN0": [0.0017877728041639784, 0.0014722472351422766, 0.0021032983731856802, 0.002418823942207382, 0.0017877728041639784, 0.0014722472351422766, 
... ... ...
-0.005468447568546253, -0.005152972058112937, -0.004522021037246304, -0.0048374965476796206]}}
```

## Authors

* **Mick Phillips** - [MickP](https://github.com/mickp)

## License

This project is licensed under the GPL v3 License - see the [LICENSE.md](LICENSE.md) file for details

## Acknowledgments

* Thanks to LabJack for making a high-quality, low-cost data acquisition system with a well-supported python interface.